#!/usr/bin/env python3
"""Claude / Codex icons and readable status labels for the Herdr sidebar.

Usage: agent_labels.py apply | clear | event

  apply  write the icon and status labels for every agent on this server
  clear  remove everything this plugin wrote
  event  handle a pane.agent_detected hook (reads HERDR_PLUGIN_EVENT_JSON)

Everything is written as display-only pane metadata, so agent status,
notifications and `herdr agent wait` are never affected.
"""

import json
import os
import subprocess
import sys

SOURCE = "plugin:riba2534.agent-labels"
LOGO_TOKEN = "logo"
STATES = ("working", "done", "blocked", "idle", "unknown")

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

# Nerd Fonts 3.5+ codicons: cod-claude and cod-openai.
DEFAULT_LOGOS = {
    "claude": "\uec82",
    "codex": "\uec81",
}


class HerdrError(Exception):
    pass


def log(message):
    print("agent-labels: " + message, file=sys.stderr)


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


def apply_args(pane_id, agent, logo, labels):
    args = ["pane", "report-metadata", pane_id, "--source", SOURCE, "--agent", agent]
    if logo:
        args += ["--token", "{}={}".format(LOGO_TOKEN, logo)]
    else:
        args += ["--clear-token", LOGO_TOKEN]
    for state in STATES:
        args += ["--state-label", "{}={}".format(state, labels[state])]
    return args


def clear_args(pane_id):
    return [
        "pane", "report-metadata", pane_id, "--source", SOURCE,
        "--clear-token", LOGO_TOKEN, "--clear-state-labels",
    ]


def parse_event(raw):
    """Return (pane_id, agent, released) from a pane.agent_detected payload."""
    try:
        payload = json.loads(raw or "")
    except ValueError:
        return None, None, False
    if not isinstance(payload, dict):
        return None, None, False
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None, None, False
    return data.get("pane_id"), data.get("agent"), bool(data.get("released"))


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

    def panes(self):
        return self.call("pane", "list").get("result", {}).get("panes", [])


def apply_all(herdr, config, environ):
    labels = resolve_labels(config, environ)
    done = failed = 0
    for agent in herdr.agents():
        pane_id, kind = agent.get("pane_id"), agent.get("agent")
        if not pane_id or not kind:
            continue
        try:
            herdr.call(*apply_args(pane_id, kind, resolve_logo(kind, config), labels))
            done += 1
        except HerdrError as err:
            log(str(err))
            failed += 1
    print("agent-labels: labeled {} agent(s)".format(done))
    return 1 if failed else 0


def clear_all(herdr):
    done = failed = 0
    for pane in herdr.panes():
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue
        try:
            herdr.call(*clear_args(pane_id))
            done += 1
        except HerdrError as err:
            log(str(err))
            failed += 1
    print("agent-labels: cleared {} pane(s)".format(done))
    return 1 if failed else 0


def handle_event(herdr, config, environ):
    pane_id, kind, released = parse_event(environ.get("HERDR_PLUGIN_EVENT_JSON"))
    if not pane_id:
        log("event without pane_id, nothing to do")
        return 0
    if released or not kind:
        args = clear_args(pane_id)
    else:
        labels = resolve_labels(config, environ)
        args = apply_args(pane_id, kind, resolve_logo(kind, config), labels)
    try:
        herdr.call(*args)
    except HerdrError as err:
        log(str(err))
        return 1
    return 0


def main(argv=None, environ=None, run=subprocess.run):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    if len(argv) != 1 or argv[0] not in ("apply", "clear", "event"):
        log("usage: agent_labels.py apply|clear|event")
        return 2
    herdr = Herdr(environ.get("HERDR_BIN_PATH") or "herdr", run=run)
    config = load_config(environ.get("HERDR_PLUGIN_CONFIG_DIR"))
    try:
        if argv[0] == "apply":
            return apply_all(herdr, config, environ)
        if argv[0] == "clear":
            return clear_all(herdr)
        return handle_event(herdr, config, environ)
    except HerdrError as err:
        log(str(err))
        return 1


if __name__ == "__main__":
    sys.exit(main())
