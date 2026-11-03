#!/usr/bin/env python3
"""Claude / Codex icons, readable status labels and status times for the Herdr sidebar.

Usage: agent_labels.py apply | clear | event

  apply  write the icon, status labels and status time for every agent on this server
  clear  remove everything this plugin wrote
  event  handle a pane.agent_detected or pane.agent_status_changed hook
         (reads HERDR_PLUGIN_EVENT and HERDR_PLUGIN_EVENT_JSON)

Everything is written as display-only pane metadata, so agent status,
notifications and `herdr agent wait` are never affected.
"""

import contextlib
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - the plugin only targets Linux and macOS
    fcntl = None

SOURCE = "plugin:riba2534.agent-labels"
LOGO_TOKEN = "logo"
SINCE_TOKEN = "since"
STATES = ("working", "done", "blocked", "idle", "unknown")
TRANSCRIPT_SCAN_LIMIT = 64 * 1024 * 1024
TRANSCRIPT_CHUNK = 1024 * 1024

LABELS = {
    "zh-CN": {
        "working": "运行中",
        "done": "已完成·未查看",
        "blocked": "待你确认",
        "idle": "空闲",
        "unknown": "状态未知",
    },
    "en": {
        "working": "Running",
        "done": "Done · unread",
        "blocked": "Needs you",
        "idle": "Idle",
        "unknown": "Unknown",
    },
}

SINCE_FORMATS = {
    "zh-CN": {"today": "{hm}", "earlier": "{md} {hm}"},
    "en": {"today": "since {hm}", "earlier": "since {md} {hm}"},
}

# Nerd Fonts 3.5+ codicons: cod-claude and cod-openai.
DEFAULT_LOGOS = {
    "claude": "\uec82",
    "codex": "\uec81",
}


class HerdrError(Exception):
    pass


def log(message):
    print("agent-labels: " + message, file=sys.stderr)


# ---------------------------------------------------------------- config


def load_config(config_dir):
    if not config_dir:
        return {}
    path = os.path.join(config_dir, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        log("ignoring {}: {}".format(path, err))
        return {}
    if not isinstance(config, dict):
        log("ignoring {}: expected a JSON object".format(path))
        return {}
    return config


def resolve_locale(config, environ):
    locale = config.get("locale", "auto")
    if locale in LABELS:
        return locale
    if locale != "auto":
        log("unknown locale {!r}, falling back to auto".format(locale))
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(key)
        if value:
            return "zh-CN" if value.lower().startswith("zh") else "en"
    return "en"


def resolve_labels(config, environ):
    labels = dict(LABELS[resolve_locale(config, environ)])
    overrides = config.get("labels") or {}
    if not isinstance(overrides, dict):
        log("ignoring labels: expected a JSON object")
        return labels
    for state, text in overrides.items():
        if state in labels and isinstance(text, str) and text.strip():
            labels[state] = text.strip()
        else:
            log("ignoring label override {!r}".format(state))
    return labels


def resolve_logo(agent, config):
    """Icon for an agent kind; unknown kinds show their name, "" hides the icon."""
    logos = dict(DEFAULT_LOGOS)
    overrides = config.get("logos") or {}
    if isinstance(overrides, dict):
        logos.update({k: v for k, v in overrides.items() if isinstance(v, str)})
    else:
        log("ignoring logos: expected a JSON object")
    logo = logos.get(agent, agent)
    return logo.strip() or None


# ---------------------------------------------------------------- status time


def render_since(since, now, locale):
    """Format when the current status started: the time alone today, date and time before."""
    if since is None:
        return None
    formats = SINCE_FORMATS[locale]
    moment = datetime.datetime.fromtimestamp(since)
    days = (datetime.datetime.fromtimestamp(now).date() - moment.date()).days
    key = "today" if days <= 0 else "earlier"
    return formats[key].format(hm=moment.strftime("%H:%M"), md=moment.strftime("%m-%d"))


def keeps_since(prev, status, session):
    """Whether a recorded start time still describes the agent's current status."""
    if not prev or prev.get("since") is None:
        return False
    if session and prev.get("session") and prev["session"] != session:
        return False
    # Viewing a finished agent turns done into idle; it still finished at the same time.
    return prev.get("status") == status or (prev.get("status") == "done" and status == "idle")


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    head, dot, rest = text.partition(".")
    if dot:
        # Python 3.9's fromisoformat only accepts 6 fraction digits.
        end = 0
        while end < len(rest) and rest[end].isdigit():
            end += 1
        text = head + "." + (rest[:end] + "000000")[:6] + rest[end:]
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def is_user_prompt(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {item.get("type") for item in content if isinstance(item, dict)}
        return "tool_result" not in kinds and bool(kinds & {"text", "image"})
    return False


def reversed_lines(path, limit):
    """Yield complete lines from the end of a file, reading at most `limit` bytes."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        stop = max(0, pos - limit)
        partial = b""
        while pos > stop:
            step = min(TRANSCRIPT_CHUNK, pos - stop)
            pos -= step
            fh.seek(pos)
            lines = (fh.read(step) + partial).split(b"\n")
            partial = lines.pop(0)
            for line in reversed(lines):
                if line:
                    yield line
        if pos == 0 and partial:
            yield partial


def last_prompt_time(path):
    """Timestamp of the last prompt you typed, scanning a Claude transcript backwards.

    A long turn writes megabytes of tool output after the prompt, so read back in
    chunks and only parse lines that can be user entries.
    """
    try:
        for raw in reversed_lines(path, TRANSCRIPT_SCAN_LIMIT):
            if b'"user"' not in raw:
                continue
            try:
                entry = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(entry, dict) and is_user_prompt(entry):
                return parse_timestamp(entry.get("timestamp"))
    except OSError:
        return None
    return None


def claude_transcript(session, environ):
    if not session:
        return None
    root = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    hits = glob.glob(os.path.join(glob.escape(root), "projects", "*", glob.escape(session) + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def estimate_since(kind, status, session, environ):
    """Best guess for agents that were already running before the plugin saw them.

    Only Claude Code transcripts are understood. A running agent started at your
    last prompt; any other status began when the transcript was last written.
    """
    if kind != "claude":
        return None
    path = claude_transcript(session, environ)
    if not path:
        return None
    if status == "working":
        return last_prompt_time(path)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def state_file_name(socket_path):
    """One record per Herdr server: the plugin state dir is shared by every session."""
    if not socket_path:
        return "since.json"
    return "since-{}.json".format(hashlib.sha1(socket_path.encode("utf-8")).hexdigest()[:12])


class StateStore:
    """Per-server record of when each agent entered its current status."""

    def __init__(self, state_dir, socket_path=None):
        self.state_dir = state_dir
        self.path = os.path.join(state_dir, state_file_name(socket_path)) if state_dir else None

    @contextlib.contextmanager
    def locked(self):
        if not self.path:
            yield {}
            return
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.path + ".lock", "a") as lock:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            state = self._read()
            yield state
            self._write(state)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                state = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as err:
            log("resetting {}: {}".format(self.path, err))
            return {}
        return state if isinstance(state, dict) else {}

    def _write(self, state):
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".since.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as err:
            log("cannot save {}: {}".format(self.path, err))
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    def remove(self):
        if self.path:
            with contextlib.suppress(OSError):
                os.unlink(self.path)


# ---------------------------------------------------------------- herdr calls


def apply_args(pane_id, agent, logo, labels, since_text=None):
    args = ["pane", "report-metadata", pane_id, "--source", SOURCE, "--agent", agent]
    args += token_args(LOGO_TOKEN, logo) + token_args(SINCE_TOKEN, since_text)
    for state in STATES:
        args += ["--state-label", "{}={}".format(state, labels[state])]
    return args


def since_args(pane_id, since_text):
    return ["pane", "report-metadata", pane_id, "--source", SOURCE] + token_args(SINCE_TOKEN, since_text)


def clear_args(pane_id):
    return [
        "pane", "report-metadata", pane_id, "--source", SOURCE,
        "--clear-token", LOGO_TOKEN, "--clear-token", SINCE_TOKEN, "--clear-state-labels",
    ]


def token_args(name, value):
    if value:
        return ["--token", "{}={}".format(name, value)]
    return ["--clear-token", name]


def parse_event(raw):
    """Return the event data object from a hook payload, or {}."""
    try:
        payload = json.loads(raw or "")
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {}


def event_name(environ, data):
    name = environ.get("HERDR_PLUGIN_EVENT") or str(data.get("type", ""))
    return name.replace("_", ".", 1) if name.startswith("pane_") else name


class Herdr:
    def __init__(self, binary, run=subprocess.run):
        self.binary = binary
        self.run = run

    def call(self, *args):
        try:
            proc = self.run(
                [self.binary] + list(args),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as err:
            raise HerdrError("cannot run {}: {}".format(self.binary, err))
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise HerdrError("herdr {} failed: {}".format(" ".join(args[:2]), detail))
        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else {}
        except ValueError:
            raise HerdrError("herdr {} returned invalid JSON".format(" ".join(args[:2])))

    def agents(self):
        return self.call("agent", "list").get("result", {}).get("agents", [])

    def agent(self, pane_id):
        return self.call("agent", "get", pane_id).get("result", {}).get("agent", {})

    def panes(self):
        return self.call("pane", "list").get("result", {}).get("panes", [])


def session_of(agent):
    return (agent.get("agent_session") or {}).get("value")


# ---------------------------------------------------------------- commands


class Plugin:
    def __init__(self, herdr, config, environ, now=None):
        self.herdr = herdr
        self.config = config
        self.environ = environ
        self.now = time.time() if now is None else now
        self.locale = resolve_locale(config, environ)
        self.store = StateStore(environ.get("HERDR_PLUGIN_STATE_DIR"), environ.get("HERDR_SOCKET_PATH"))

    def labels(self):
        return resolve_labels(self.config, self.environ)

    def since_text(self, record):
        return render_since(record.get("since") if record else None, self.now, self.locale)

    def report(self, args):
        try:
            self.herdr.call(*args)
            return True
        except HerdrError as err:
            log(str(err))
            return False

    def apply(self):
        """Label every agent on this server, keeping or estimating its status time."""
        labels = self.labels()
        agents = [a for a in self.herdr.agents() if a.get("pane_id") and a.get("agent")]
        with self.store.locked() as state:
            for pane in set(state) - {a["pane_id"] for a in agents}:
                del state[pane]
            for agent in agents:
                pane, status, session = agent["pane_id"], agent.get("agent_status"), session_of(agent)
                prev = state.get(pane)
                since = prev["since"] if keeps_since(prev, status, session) else estimate_since(
                    agent["agent"], status, session, self.environ)
                state[pane] = {"status": status, "session": session, "since": since}
                state[pane]["shown"] = self.since_text(state[pane])
            shown = {pane: record.get("shown") for pane, record in state.items()}
        failed = 0
        for agent in agents:
            kind = agent["agent"]
            args = apply_args(agent["pane_id"], kind, resolve_logo(kind, self.config), labels,
                              shown.get(agent["pane_id"]))
            failed += not self.report(args)
        print("agent-labels: labeled {} agent(s)".format(len(agents) - failed))
        return 1 if failed else 0

    def clear(self):
        # Forget the times first: clearing labels makes Herdr resend statuses, and
        # those hooks must find nothing to restore.
        self.store.remove()
        failed = done = 0
        for pane in self.herdr.panes():
            if pane.get("pane_id"):
                ok = self.report(clear_args(pane["pane_id"]))
                done += ok
                failed += not ok
        print("agent-labels: cleared {} pane(s)".format(done))
        return 1 if failed else 0

    def agent_detected(self, data):
        pane, kind = data.get("pane_id"), data.get("agent")
        if data.get("released") or not kind:
            with self.store.locked() as state:
                state.pop(pane, None)
            return 0 if self.report(clear_args(pane)) else 1
        try:
            agent = self.herdr.agent(pane)
        except HerdrError as err:
            log(str(err))
            agent = {}
        status, session = agent.get("agent_status"), session_of(agent)
        with self.store.locked() as state:
            prev = state.get(pane)
            since = prev["since"] if keeps_since(prev, status, session) else self.now
            state[pane] = {"status": status, "session": session, "since": since}
            state[pane]["shown"] = text = self.since_text(state[pane])
        args = apply_args(pane, kind, resolve_logo(kind, self.config), self.labels(), text)
        return 0 if self.report(args) else 1

    def status_changed(self, data):
        """Record when the status changed, then refresh every label whose text moved on.

        Herdr also sends this event when only the presentation changed (for example
        right after this plugin writes its labels); the status is the same then, so
        the recorded time stays. Viewing a finished agent turns done into idle and
        keeps its finish time too.
        """
        pane = data.get("pane_id")
        # Hooks run concurrently and events can arrive late, so trust the pane's
        # current state over the event payload.
        try:
            current = self.herdr.agent(pane)
        except HerdrError:
            current = {}
        if not current.get("agent"):
            # The agent left the pane; pane.agent_detected may already have cleared
            # everything, so clear the time again instead of recording a new one.
            with self.store.locked() as state:
                state.pop(pane, None)
            return 0 if self.report(since_args(pane, None)) else 1
        status = current.get("agent_status") or data.get("agent_status")
        if not status:
            return 0
        updates = {}
        with self.store.locked() as state:
            prev = state.get(pane)
            if prev is None:
                # Untracked: labels were cleared, or pane.agent_detected has not
                # recorded this agent yet. apply and pane.agent_detected own new records.
                return 0
            session = session_of(current) or prev.get("session")
            if session == prev.get("session") and (
                prev.get("status") == status or (prev.get("status") == "done" and status == "idle")
            ):
                prev["status"] = status
            else:
                state[pane] = {"status": status, "session": session, "since": self.now}
            for key, record in state.items():
                text = self.since_text(record)
                if text != record.get("shown"):
                    record["shown"] = text
                    updates[key] = text
        failed = 0
        for key, text in sorted(updates.items()):
            failed += not self.report(since_args(key, text))
        return 1 if failed else 0

    def event(self):
        data = parse_event(self.environ.get("HERDR_PLUGIN_EVENT_JSON"))
        if not data.get("pane_id"):
            log("event without pane_id, nothing to do")
            return 0
        name = event_name(self.environ, data)
        if name == "pane.agent_status_changed":
            return self.status_changed(data)
        if name == "pane.agent_detected":
            return self.agent_detected(data)
        log("ignoring event {!r}".format(name))
        return 0


def main(argv=None, environ=None, run=subprocess.run, now=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    if len(argv) != 1 or argv[0] not in ("apply", "clear", "event"):
        log("usage: agent_labels.py apply|clear|event")
        return 2
    herdr = Herdr(environ.get("HERDR_BIN_PATH") or "herdr", run=run)
    plugin = Plugin(herdr, load_config(environ.get("HERDR_PLUGIN_CONFIG_DIR")), environ, now)
    try:
        return getattr(plugin, argv[0])()
    except HerdrError as err:
        log(str(err))
        return 1


if __name__ == "__main__":
    sys.exit(main())
