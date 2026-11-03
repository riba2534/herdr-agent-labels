#!/usr/bin/env python3
"""Agent overview popup: every agent on every reachable machine, grouped by status.

Opened by the plugin's `board` action. On the machine whose Herdr client holds the
saved SSH machines (usually your laptop) it shows all of them; elsewhere it shows
the local server only.

  python3 board.py          interactive popup (curses)
  python3 board.py --dump   print one frame as plain text and exit
"""

import curses
import json
import locale
import os
import socket
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import agent_labels as al

GROUP_ORDER = ("blocked", "working", "done", "idle", "unknown")
STATUS_SYMBOLS = {"blocked": "×", "working": "◐", "done": "✓", "idle": "○", "unknown": "·"}
GENERIC_TITLES = {"", "claude code", "claude", "codex", "openai codex"}
REFRESH_SECONDS = 5
FETCH_TIMEOUT = 20

TEXT = {
    "zh-CN": {
        "title": "Agent 总览",
        "summary": "{machines} 台机器 · {agents} 个 Agent",
        "updated": "{time} 更新",
        "loading": "正在读取各台机器…",
        "help": "↑↓ 选择   Enter 跳转   i 折叠空闲   r 刷新   q 关闭",
        "failed": "{machine} 读取失败：{error}",
        "collapsed": "已折叠，按 i 展开",
        "remote": "已在 {machine} 上选中 {workspace}，在侧边栏切到 {machine} 就能看到",
        "local_only": "只显示本机；在 Local 上打开可以看到所有机器",
        "empty": "没有 Agent",
    },
    "en": {
        "title": "Agent overview",
        "summary": "{machines} machines · {agents} agents",
        "updated": "updated {time}",
        "loading": "Reading machines…",
        "help": "↑↓ select   Enter jump   i fold idle   r refresh   q close",
        "failed": "{machine}: {error}",
        "collapsed": "folded, press i to expand",
        "remote": "Selected {workspace} on {machine}; switch to {machine} in the sidebar to see it",
        "local_only": "Showing this machine only; open it on Local to see every machine",
        "empty": "No agents",
    },
}

# 256-color approximations of the sidebar palette.
COLORS = {
    "blocked": 211, "working": 222, "done": 116, "idle": 250, "unknown": 245,
    "workspace": 183, "time": 111, "claude": 173, "codex": 255, "dim": 245,
    "title": 75, "error": 203,
}
BASIC_COLORS = {
    "blocked": curses.COLOR_RED, "working": curses.COLOR_YELLOW, "done": curses.COLOR_CYAN,
    "idle": curses.COLOR_WHITE, "unknown": curses.COLOR_WHITE, "workspace": curses.COLOR_MAGENTA,
    "time": curses.COLOR_BLUE, "claude": curses.COLOR_YELLOW, "codex": curses.COLOR_WHITE,
    "dim": curses.COLOR_WHITE, "title": curses.COLOR_BLUE, "error": curses.COLOR_RED,
}


# ---------------------------------------------------------------- text width


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text):
    return sum(char_width(ch) for ch in text)


def fit(text, width):
    """Cut `text` to at most `width` cells, ending with … when shortened."""
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


# ---------------------------------------------------------------- data


def herdr_command(machine=None):
    cmd = [os.environ.get("HERDR_BIN_PATH") or "herdr"]
    if machine:
        cmd += ["--machine", machine]
    return cmd


def run_json(args, timeout=FETCH_TIMEOUT):
    proc = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(" ".join((proc.stderr or proc.stdout or "failed").split())[:160])
    return json.loads(proc.stdout)


def saved_machines():
    """Labels of the enabled saved SSH machines known to this host's Herdr."""
    try:
        machines = run_json(herdr_command() + ["machine", "list", "--json"], timeout=10)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return []
    if not isinstance(machines, list):
        return []
    return [m["label"] for m in machines if isinstance(m, dict) and m.get("label") and m.get("enabled", True)]


def home_label(remotes):
    return "Local" if remotes else socket.gethostname().split(".")[0]


def agent_rows(machine, machine_index, payload, config):
    """Flatten an `api snapshot` response into display rows."""
    snapshot = payload.get("result", {}).get("snapshot", {})
    workspaces = snapshot.get("workspaces") or []
    workspace_by_id = {w.get("workspace_id"): w for w in workspaces}
    workspace_order = {w.get("workspace_id"): i for i, w in enumerate(workspaces)}
    tabs = {t.get("tab_id"): t for t in snapshot.get("tabs") or []}
    rows = []
    for agent in snapshot.get("agents") or []:
        kind = agent.get("agent")
        if not kind or not agent.get("pane_id"):
            continue
        workspace = workspace_by_id.get(agent.get("workspace_id")) or {}
        tab = tabs.get(agent.get("tab_id")) or {}
        tab_label = (tab.get("label") or "").strip()
        show_tab = (workspace.get("tab_count") or 1) > 1 or (tab_label and not tab_label.isdigit())
        tokens = agent.get("tokens") or {}
        title = " ".join((agent.get("terminal_title_stripped") or "").split())
        rows.append({
            "machine": machine,
            "pane_id": agent["pane_id"],
            "status": agent.get("agent_status") if agent.get("agent_status") in STATUS_SYMBOLS else "unknown",
            "kind": kind,
            "logo": tokens.get("logo") or al.resolve_logo(kind, config) or kind,
            "workspace": workspace.get("label") or agent.get("workspace_id") or "?",
            "tab": tab_label if show_tab else "",
            "title": "" if title.lower() in GENERIC_TITLES else title,
            "since": tokens.get("since") or "",
            "order": (machine_index, workspace_order.get(agent.get("workspace_id"), 999), tab.get("number") or 0),
        })
    return rows


def build_lines(rows, errors, text, labels, fold_idle):
    """Lines to draw: ("group", status, count), ("agent", row), ("note", message), ("error", message)."""
    lines = [("error", text["failed"].format(machine=m, error=e)) for m, e in errors]
    for status in GROUP_ORDER:
        group = sorted((r for r in rows if r["status"] == status), key=lambda r: r["order"])
        if not group:
            continue
        lines.append(("group", status, len(group)))
        if status == "idle" and fold_idle:
            lines.append(("note", text["collapsed"]))
            continue
        lines.extend(("agent", row) for row in group)
    if not rows and not errors:
        lines.append(("note", text["empty"]))
    return lines


def agent_segments(row, width, machine_width, show_machine):
    """(text, style) pieces of one agent line, fitted to `width` cells."""
    left = [(" " + STATUS_SYMBOLS[row["status"]] + " ", row["status"]), (row["logo"] + " ", row["kind"])]
    if show_machine:
        name = row["machine"]
        left.append((name + " " * (machine_width - text_width(name) + 2), "dim"))
    since = row["since"]
    right = [(since + " ", "time")] if since else []
    used = sum(text_width(t) for t, _ in left) + sum(text_width(t) for t, _ in right)
    room = max(0, width - used - 1)
    middle = []
    name = fit(row["workspace"], room)
    middle.append((name, "workspace"))
    room -= text_width(name)
    if row["tab"] and room > 4:
        tab = fit(" · " + row["tab"], room)
        middle.append((tab, "plain"))
        room -= text_width(tab)
    if row["title"] and room > 6:
        title = fit("  " + row["title"], room)
        middle.append((title, "dim"))
        room -= text_width(title)
    middle.append((" " * max(room, 0) + " ", "plain"))
    return left + middle + right


# ---------------------------------------------------------------- board state


class Board:
    def __init__(self, config, environ):
        self.config = config
        self.locale = al.resolve_locale(config, environ)
        self.text = TEXT[self.locale]
        self.labels = al.resolve_labels(config, environ)
        self.remotes = saved_machines()
        self.home = home_label(self.remotes)
        self.machines = [self.home] + self.remotes
        self.lock = threading.Lock()
        self.rows = {}
        self.errors = {}
        self.updated = None
        self.dirty = True
        self.fetching = False

    def fetch_one(self, index, machine):
        remote = machine if machine != self.home else None
        try:
            payload = run_json(herdr_command(remote) + ["api", "snapshot"])
            rows = agent_rows(machine, index, payload, self.config)
            with self.lock:
                self.rows[machine] = rows
                self.errors.pop(machine, None)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as err:
            with self.lock:
                self.errors[machine] = str(err) or err.__class__.__name__
        with self.lock:
            self.dirty = True

    def refresh(self):
        with self.lock:
            if self.fetching:
                return
            self.fetching = True
        try:
            with ThreadPoolExecutor(max_workers=len(self.machines)) as pool:
                for index, machine in enumerate(self.machines):
                    pool.submit(self.fetch_one, index, machine)
        finally:
            with self.lock:
                self.fetching = False
                self.updated = time.strftime("%H:%M:%S")
                self.dirty = True

    def snapshot(self):
        with self.lock:
            rows = [row for machine in self.machines for row in self.rows.get(machine, [])]
            errors = [(m, self.errors[m]) for m in self.machines if m in self.errors]
            loaded = bool(self.rows) or bool(self.errors)
            return rows, errors, loaded, self.updated

    def header(self, rows):
        summary = self.text["summary"].format(machines=len(self.machines), agents=len(rows))
        return "{}  ·  {}".format(self.text["title"], summary)

    def group_title(self, status, count):
        return "{} {}".format(self.labels.get(status, status), count)


def focus_agent(board, row):
    """Jump to the agent. Returns a message to show, or None when the popup can close."""
    remote = row["machine"] if row["machine"] != board.home else None
    try:
        run_json(herdr_command(remote) + ["agent", "focus", row["pane_id"]], timeout=FETCH_TIMEOUT)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as err:
        return board.text["failed"].format(machine=row["machine"], error=err)
    if remote is None:
        return None
    return board.text["remote"].format(machine=row["machine"], workspace=row["workspace"])


# ---------------------------------------------------------------- plain text dump


def dump(board, width=100):
    board.refresh()
    rows, errors, _, updated = board.snapshot()
    machine_width = max((text_width(m) for m in board.machines), default=0)
    show_machine = len(board.machines) > 1
    print(board.header(rows) + "    " + board.text["updated"].format(time=updated))
    for line in build_lines(rows, errors, board.text, board.labels, fold_idle=False):
        if line[0] == "group":
            print("▍" + board.group_title(line[1], line[2]))
        elif line[0] == "agent":
            print("".join(t for t, _ in agent_segments(line[1], width, machine_width, show_machine)).rstrip())
        else:
            print("  " + line[1])
    if not board.remotes:
        print("  " + board.text["local_only"])


# ---------------------------------------------------------------- curses UI


class Screen:
    def __init__(self, stdscr, board):
        self.stdscr = stdscr
        self.board = board
        self.selected = 0
        self.top = 0
        self.fold_idle = False
        self.message = ""
        self.attrs = {}

    def setup_colors(self):
        self.attrs = {key: curses.A_NORMAL for key in COLORS}
        self.attrs["plain"] = curses.A_NORMAL
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        palette = COLORS if curses.COLORS >= 256 else BASIC_COLORS
        for number, (key, color) in enumerate(palette.items(), start=1):
            curses.init_pair(number, color, background)
            self.attrs[key] = curses.color_pair(number)
        for key in ("blocked", "working", "done", "workspace", "title"):
            self.attrs[key] |= curses.A_BOLD

    def put(self, y, x, text, attr=0):
        height, width = self.stdscr.getmaxyx()
        if y >= height or x >= width:
            return x
        text = fit(text, width - x - (1 if y == height - 1 else 0))
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass
        return x + text_width(text)

    def draw(self):
        board = self.board
        rows, errors, loaded, updated = board.snapshot()
        lines = build_lines(rows, errors, board.text, board.labels, self.fold_idle) if loaded else []
        selectable = [i for i, line in enumerate(lines) if line[0] == "agent"]
        self.selected = min(self.selected, max(len(selectable) - 1, 0))
        height, width = self.stdscr.getmaxyx()
        self.stdscr.erase()

        self.put(0, 1, board.header(rows), self.attrs["title"])
        stamp = board.text["updated"].format(time=updated) if updated else board.text["loading"]
        self.put(0, max(1, width - text_width(stamp) - 2), stamp, self.attrs["dim"])
        self.put(1, 0, "─" * width, self.attrs["dim"])

        body_top, body_height = 2, max(1, height - 4)
        if selectable:
            current = selectable[self.selected]
            if current < self.top:
                self.top = current
            elif current >= self.top + body_height:
                self.top = current - body_height + 1
        self.top = max(0, min(self.top, max(len(lines) - body_height, 0)))

        machine_width = max((text_width(m) for m in board.machines), default=0)
        show_machine = len(board.machines) > 1
        for offset, line in enumerate(lines[self.top:self.top + body_height]):
            y = body_top + offset
            index = self.top + offset
            if line[0] == "group":
                self.put(y, 1, "▍" + board.group_title(line[1], line[2]), self.attrs[line[1]])
            elif line[0] == "agent":
                highlight = curses.A_REVERSE if selectable and index == selectable[self.selected] else 0
                x = 1
                for piece, style in agent_segments(line[1], width - 2, machine_width, show_machine):
                    x = self.put(y, x, piece, self.attrs.get(style, 0) | highlight)
            elif line[0] == "error":
                self.put(y, 2, line[1], self.attrs["error"])
            else:
                self.put(y, 3, line[1], self.attrs["dim"])
        if not loaded:
            self.put(body_top, 3, board.text["loading"], self.attrs["dim"])

        self.put(height - 2, 0, "─" * width, self.attrs["dim"])
        footer = self.message or (board.text["local_only"] if not board.remotes else board.text["help"])
        self.put(height - 1, 1, footer, self.attrs["time"] if self.message else self.attrs["dim"])
        self.stdscr.refresh()
        return lines, selectable

    def run(self):
        curses.curs_set(0)
        self.setup_colors()
        self.stdscr.timeout(200)
        stop = threading.Event()

        def refresher():
            while not stop.is_set():
                self.board.refresh()
                stop.wait(REFRESH_SECONDS)

        thread = threading.Thread(target=refresher, daemon=True)
        thread.start()
        lines, selectable = self.draw()
        try:
            while True:
                key = self.stdscr.getch()
                if key in (ord("q"), 27):
                    return
                if key in (curses.KEY_DOWN, ord("j")):
                    self.selected = min(self.selected + 1, max(len(selectable) - 1, 0))
                elif key in (curses.KEY_UP, ord("k")):
                    self.selected = max(self.selected - 1, 0)
                elif key == curses.KEY_NPAGE:
                    self.selected = min(self.selected + 10, max(len(selectable) - 1, 0))
                elif key == curses.KEY_PPAGE:
                    self.selected = max(self.selected - 10, 0)
                elif key in (ord("i"),):
                    self.fold_idle = not self.fold_idle
                elif key in (ord("r"),):
                    threading.Thread(target=self.board.refresh, daemon=True).start()
                elif key in (curses.KEY_ENTER, 10, 13) and selectable:
                    row = lines[selectable[self.selected]][1]
                    self.message = focus_agent(self.board, row) or ""
                    if not self.message:
                        return
                elif key == curses.KEY_RESIZE:
                    pass
                elif key == -1:
                    with self.board.lock:
                        changed, self.board.dirty = self.board.dirty, False
                    if not changed:
                        continue
                lines, selectable = self.draw()
        finally:
            stop.set()


def ensure_utf8_locale():
    for name in ("", "C.UTF-8", "en_US.UTF-8", "zh_CN.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, name)
        except locale.Error:
            continue
        if "utf" in (locale.getpreferredencoding(False) or "").lower():
            return


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    config = al.load_config(os.environ.get("HERDR_PLUGIN_CONFIG_DIR"))
    board = Board(config, os.environ)
    if argv[:1] == ["--dump"]:
        dump(board)
        return 0
    ensure_utf8_locale()
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(lambda stdscr: Screen(stdscr, board).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
