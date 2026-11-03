import datetime
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_labels as al  # noqa: E402


def local(*args):
    return datetime.datetime(*args).timestamp()


NOW = local(2026, 11, 3, 13, 0)
CLAUDE = "\uec82"
CODEX = "\uec81"


class FakeHerdr:
    """Records herdr CLI calls and answers agent/pane listings."""

    def __init__(self, agents=(), panes=(), fail_on=None):
        self.calls = []
        self.agents = list(agents)
        self.panes = list(panes)
        self.fail_on = fail_on

    def __call__(self, cmd, **kwargs):
        args = cmd[1:]
        self.calls.append(args)
        if self.fail_on and self.fail_on in args:
            return SimpleNamespace(returncode=1, stdout="", stderr='{"error":"pane_not_found"}')
        if args[:2] == ["agent", "list"]:
            body = {"result": {"agents": self.agents, "type": "agent_list"}}
        elif args[:2] == ["agent", "get"]:
            match = [a for a in self.agents if a.get("pane_id") == args[2]]
            body = {"result": {"agent": match[0] if match else {}, "type": "agent_info"}}
        elif args[:2] == ["pane", "list"]:
            body = {"result": {"panes": self.panes, "type": "pane_list"}}
        else:
            body = {"result": {"type": "ok"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    def metadata_calls(self):
        return [c for c in self.calls if c[:2] == ["pane", "report-metadata"]]

    def tokens(self, name):
        """Latest value written for a token per pane (None when cleared)."""
        result = {}
        for call in self.metadata_calls():
            pane = call[2]
            if name in flag_values(call, "--clear-token"):
                result[pane] = None
            for item in flag_values(call, "--token"):
                key, _, value = item.partition("=")
                if key == name:
                    result[pane] = value
        return result


def flag_values(args, flag):
    return [args[i + 1] for i, arg in enumerate(args) if arg == flag]


def agent(pane, kind="claude", status="idle", session=None):
    info = {"pane_id": pane, "agent": kind, "agent_status": status}
    if session:
        info["agent_session"] = {"agent": kind, "kind": "id", "value": session}
    return info


class LocaleTest(unittest.TestCase):
    def test_explicit_locale_wins(self):
        self.assertEqual(al.resolve_locale({"locale": "en"}, {"LANG": "zh_CN.UTF-8"}), "en")

    def test_auto_follows_environment_priority(self):
        self.assertEqual(al.resolve_locale({}, {"LC_ALL": "C", "LANG": "zh_CN.UTF-8"}), "en")
        self.assertEqual(al.resolve_locale({}, {"LANG": "zh_CN.UTF-8"}), "zh-CN")
        self.assertEqual(al.resolve_locale({}, {"LC_MESSAGES": "zh_TW"}), "zh-CN")

    def test_unknown_locale_falls_back_to_auto(self):
        self.assertEqual(al.resolve_locale({"locale": "fr"}, {"LANG": "zh_CN"}), "zh-CN")
        self.assertEqual(al.resolve_locale({}, {}), "en")


class LabelsTest(unittest.TestCase):
    def test_every_locale_covers_every_state(self):
        for labels in al.LABELS.values():
            self.assertEqual(set(labels), set(al.STATES))
        self.assertEqual(set(al.SINCE_FORMATS), set(al.LABELS))

    def test_overrides_only_known_states(self):
        config = {"locale": "zh-CN", "labels": {"done": " 跑完了 ", "bogus": "x", "idle": ""}}
        labels = al.resolve_labels(config, {})
        self.assertEqual(labels["done"], "跑完了")
        self.assertEqual(labels["idle"], "空闲")
        self.assertNotIn("bogus", labels)


class LogoTest(unittest.TestCase):
    def test_builtin_logos(self):
        self.assertEqual(al.resolve_logo("claude", {}), CLAUDE)
        self.assertEqual(al.resolve_logo("codex", {}), CODEX)

    def test_unknown_agent_shows_its_name(self):
        self.assertEqual(al.resolve_logo("gemini", {}), "gemini")

    def test_overrides_and_hiding(self):
        config = {"logos": {"gemini": "✦", "codex": ""}}
        self.assertEqual(al.resolve_logo("gemini", config), "✦")
        self.assertIsNone(al.resolve_logo("codex", config))


class ConfigTest(unittest.TestCase):
    def test_missing_and_invalid_files_use_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(al.load_config(tmp), {})
            with open(os.path.join(tmp, "config.json"), "w") as fh:
                fh.write("{not json")
            self.assertEqual(al.load_config(tmp), {})
            with open(os.path.join(tmp, "config.json"), "w") as fh:
                fh.write("[1, 2]")
            self.assertEqual(al.load_config(tmp), {})
        self.assertEqual(al.load_config(None), {})

    def test_reads_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"locale": "zh-CN"}, fh)
            self.assertEqual(al.load_config(tmp), {"locale": "zh-CN"})


class SinceFormatTest(unittest.TestCase):
    def test_zh(self):
        self.assertEqual(al.render_since(local(2026, 11, 3, 9, 5), NOW, "zh-CN"), "09:05 起")
        self.assertEqual(al.render_since(local(2026, 11, 2, 23, 10), NOW, "zh-CN"), "昨天 23:10 起")
        self.assertEqual(al.render_since(local(2026, 10, 30, 8, 0), NOW, "zh-CN"), "10-30 起")

    def test_en(self):
        self.assertEqual(al.render_since(local(2026, 11, 3, 9, 5), NOW, "en"), "since 09:05")
        self.assertEqual(al.render_since(local(2026, 11, 2, 23, 10), NOW, "en"), "since yesterday 23:10")
        self.assertEqual(al.render_since(local(2026, 10, 30, 8, 0), NOW, "en"), "since 10-30")

    def test_unknown_and_future(self):
        self.assertIsNone(al.render_since(None, NOW, "zh-CN"))
        self.assertEqual(al.render_since(NOW + 60, NOW, "zh-CN"), "13:01 起")


class KeepsSinceTest(unittest.TestCase):
    def test_rules(self):
        prev = {"status": "done", "session": "s1", "since": 100.0}
        self.assertTrue(al.keeps_since(prev, "done", "s1"))
        self.assertTrue(al.keeps_since(prev, "idle", "s1"))
        self.assertFalse(al.keeps_since(prev, "working", "s1"))
        self.assertFalse(al.keeps_since(prev, "done", "s2"))
        self.assertTrue(al.keeps_since(prev, "done", None))
        self.assertFalse(al.keeps_since(dict(prev, since=None), "done", "s1"))
        self.assertFalse(al.keeps_since(None, "idle", "s1"))
        self.assertFalse(al.keeps_since({"status": "idle", "since": 1.0}, "done", None))


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        project = os.path.join(self.root, "projects", "-home-me-repo")
        os.makedirs(project)
        self.path = os.path.join(project, "abc-123.jsonl")
        entries = [
            {"type": "user", "message": {"content": "first task"}, "timestamp": "2026-11-03T01:00:00.000Z"},
            {"type": "assistant", "message": {"content": []}, "timestamp": "2026-11-03T01:00:05.000Z"},
            {"type": "user", "message": {"content": [{"type": "text", "text": "second task"}]},
             "timestamp": "2026-11-03T02:30:00.123Z"},
            {"type": "user", "isMeta": True, "message": {"content": "meta"}, "timestamp": "2026-11-03T02:40:00Z"},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]},
             "timestamp": "2026-11-03T02:45:00Z"},
            {"type": "user", "isSidechain": True, "message": {"content": "sub"}, "timestamp": "2026-11-03T02:50:00Z"},
        ]
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
        os.utime(self.path, (local(2026, 11, 3, 11, 0), local(2026, 11, 3, 11, 0)))
        self.env = {"CLAUDE_CONFIG_DIR": self.root}

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_timestamp(self):
        expected = datetime.datetime(2026, 11, 3, 2, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertAlmostEqual(al.parse_timestamp("2026-11-03T02:30:00.123Z"), expected + 0.123, places=3)
        self.assertEqual(al.parse_timestamp("2026-11-03T02:30:00Z"), expected)
        self.assertIsNone(al.parse_timestamp("yesterday"))
        self.assertIsNone(al.parse_timestamp(None))

    def test_last_prompt_skips_tool_results_meta_and_sidechains(self):
        expected = datetime.datetime(2026, 11, 3, 2, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertAlmostEqual(al.last_prompt_time(self.path), expected + 0.123, places=3)

    def test_estimates(self):
        self.assertEqual(al.claude_transcript("abc-123", self.env), self.path)
        self.assertEqual(al.estimate_since("claude", "idle", "abc-123", self.env), local(2026, 11, 3, 11, 0))
        self.assertEqual(al.estimate_since("claude", "done", "abc-123", self.env), local(2026, 11, 3, 11, 0))
        self.assertAlmostEqual(al.estimate_since("claude", "working", "abc-123", self.env),
                               al.last_prompt_time(self.path))
        self.assertIsNone(al.estimate_since("codex", "idle", "abc-123", self.env))
        self.assertIsNone(al.estimate_since("claude", "idle", "missing", self.env))
        self.assertIsNone(al.estimate_since("claude", "idle", None, self.env))

    def test_prompt_far_behind_a_long_turn(self):
        expected = al.last_prompt_time(self.path)
        assistant = json.dumps({"type": "assistant", "message": {"content": "x" * 3000}})
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write((assistant + "\n") * 1500)  # about 4.5 MB of output after the prompt
        self.assertEqual(al.last_prompt_time(self.path), expected)

    def test_scan_limit(self):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(("x" * 1000 + "\n") * 300)
        old = al.TRANSCRIPT_SCAN_LIMIT
        al.TRANSCRIPT_SCAN_LIMIT = 100 * 1000
        try:
            self.assertIsNone(al.last_prompt_time(self.path))
        finally:
            al.TRANSCRIPT_SCAN_LIMIT = old

    def test_reversed_lines_across_chunks(self):
        old = al.TRANSCRIPT_CHUNK
        al.TRANSCRIPT_CHUNK = 7
        try:
            with open(self.path, "w") as fh:
                fh.write("alpha\nbravo charlie\n\ndelta\necho")
            self.assertEqual(list(al.reversed_lines(self.path, 1000)),
                             [b"echo", b"delta", b"bravo charlie", b"alpha"])
            self.assertEqual(list(al.reversed_lines(self.path, 12)), [b"echo", b"delta"])
        finally:
            al.TRANSCRIPT_CHUNK = old


class StateStoreTest(unittest.TestCase):
    def test_round_trip_and_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = al.StateStore(tmp)
            with store.locked() as state:
                state["w1:p1"] = {"status": "idle", "since": 1.5}
            with store.locked() as state:
                self.assertEqual(state, {"w1:p1": {"status": "idle", "since": 1.5}})
            store.remove()
            with store.locked() as state:
                self.assertEqual(state, {})

    def test_corrupt_file_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, al.state_file_name(None)), "w") as fh:
                fh.write("{oops")
            with al.StateStore(tmp).locked() as state:
                self.assertEqual(state, {})

    def test_each_server_has_its_own_file(self):
        default = al.state_file_name("/home/me/.config/herdr/herdr.sock")
        named = al.state_file_name("/home/me/.config/herdr/sessions/work/herdr.sock")
        self.assertNotEqual(default, named)
        self.assertTrue(default.startswith("since-") and default.endswith(".json"))
        self.assertEqual(al.state_file_name(None), "since.json")

    def test_without_state_dir(self):
        with al.StateStore(None).locked() as state:
            state["x"] = 1


class ArgsTest(unittest.TestCase):
    def test_apply_args(self):
        labels = al.LABELS["zh-CN"]
        args = al.apply_args("w1:p1", "claude", CLAUDE, labels, "09:05 起")
        self.assertEqual(args[:3], ["pane", "report-metadata", "w1:p1"])
        self.assertEqual(flag_values(args, "--source"), [al.SOURCE])
        self.assertEqual(flag_values(args, "--agent"), ["claude"])
        self.assertEqual(flag_values(args, "--token"), ["logo=" + CLAUDE, "since=09:05 起"])
        self.assertEqual(
            flag_values(args, "--state-label"),
            ["{}={}".format(s, labels[s]) for s in al.STATES],
        )

    def test_missing_values_clear_tokens(self):
        args = al.apply_args("w1:p1", "codex", None, al.LABELS["en"])
        self.assertEqual(flag_values(args, "--clear-token"), ["logo", "since"])
        self.assertEqual(flag_values(args, "--token"), [])

    def test_clear_args(self):
        args = al.clear_args("w1:p2")
        self.assertIn("--clear-state-labels", args)
        self.assertEqual(flag_values(args, "--clear-token"), ["logo", "since"])
        self.assertEqual(flag_values(args, "--source"), [al.SOURCE])

    def test_since_args_only_touch_the_token(self):
        args = al.since_args("w1:p3", "昨天 23:10 起")
        self.assertEqual(flag_values(args, "--token"), ["since=昨天 23:10 起"])
        self.assertNotIn("--state-label", args)
        self.assertNotIn("--agent", args)


class EventParsingTest(unittest.TestCase):
    def test_parse_envelope(self):
        raw = json.dumps({
            "event": "pane_agent_detected",
            "data": {"type": "pane_agent_detected", "pane_id": "w1:p3", "agent": "codex"},
        })
        self.assertEqual(al.parse_event(raw)["agent"], "codex")
        self.assertEqual(al.event_name({}, al.parse_event(raw)), "pane.agent_detected")
        self.assertEqual(al.event_name({"HERDR_PLUGIN_EVENT": "pane.agent_status_changed"}, {}),
                         "pane.agent_status_changed")

    def test_parse_garbage(self):
        for raw in (None, "", "not json", "[]", '{"data": 3}'):
            self.assertEqual(al.parse_event(raw), {})


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = os.path.join(self.tmp.name, "state")
        self.claude_dir = os.path.join(self.tmp.name, "claude")
        os.makedirs(os.path.join(self.claude_dir, "projects", "p"))

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **extra):
        env = {
            "HERDR_BIN_PATH": "/bin/herdr",
            "LANG": "zh_CN.UTF-8",
            "HERDR_PLUGIN_STATE_DIR": self.state_dir,
            "CLAUDE_CONFIG_DIR": self.claude_dir,
            "HERDR_SOCKET_PATH": "/tmp/herdr-test.sock",
        }
        env.update(extra)
        return env

    def transcript(self, session, mtime):
        path = os.path.join(self.claude_dir, "projects", "p", session + ".jsonl")
        with open(path, "w") as fh:
            fh.write("{}\n")
        os.utime(path, (mtime, mtime))

    def state(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            return {k: dict(v) for k, v in state.items()}

    def event(self, fake, name, now, **data):
        env = self.env(HERDR_PLUGIN_EVENT=name, HERDR_PLUGIN_EVENT_JSON=json.dumps({"data": data}))
        return al.main(["event"], env, run=fake, now=now)

    def test_apply_labels_every_agent_and_estimates_times(self):
        self.transcript("s1", local(2026, 11, 2, 23, 10))
        fake = FakeHerdr(agents=[
            agent("w1:p1", "claude", "idle", "s1"),
            agent("w1:p2", "codex", "working"),
            {"pane_id": "w1:p3"},
        ])
        self.assertEqual(al.main(["apply"], self.env(), run=fake, now=NOW), 0)
        calls = fake.metadata_calls()
        self.assertEqual([c[2] for c in calls], ["w1:p1", "w1:p2"])
        self.assertIn("working=运行中", flag_values(calls[0], "--state-label"))
        self.assertEqual(fake.tokens("logo"), {"w1:p1": CLAUDE, "w1:p2": CODEX})
        self.assertEqual(fake.tokens("since"), {"w1:p1": "昨天 23:10 起", "w1:p2": None})

    def test_apply_keeps_known_times_and_prunes_closed_panes(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p1"] = {"status": "done", "session": "s1", "since": local(2026, 11, 3, 9, 5)}
            state["w9:p9"] = {"status": "idle", "session": "s9", "since": 1.0}
        fake = FakeHerdr(agents=[agent("w1:p1", "claude", "idle", "s1")])
        self.assertEqual(al.main(["apply"], self.env(), run=fake, now=NOW), 0)
        self.assertEqual(fake.tokens("since"), {"w1:p1": "09:05 起"})
        self.assertEqual(set(self.state()), {"w1:p1"})
        self.assertEqual(self.state()["w1:p1"]["status"], "idle")

    def test_apply_keeps_going_after_a_failure(self):
        fake = FakeHerdr(agents=[agent("w1:p1"), agent("w1:p2")], fail_on="w1:p1")
        self.assertEqual(al.main(["apply"], self.env(), run=fake, now=NOW), 1)
        self.assertEqual([c[2] for c in fake.metadata_calls()], ["w1:p1", "w1:p2"])

    def test_detected_then_status_changes(self):
        fake = FakeHerdr(agents=[agent("w1:p4", "claude", "working", "s4")])
        t0 = local(2026, 11, 3, 10, 0)
        self.assertEqual(self.event(fake, "pane.agent_detected", t0, pane_id="w1:p4", agent="claude"), 0)
        self.assertEqual(fake.tokens("since"), {"w1:p4": "10:00 起"})
        self.assertEqual(fake.tokens("logo"), {"w1:p4": CLAUDE})

        # Writing labels makes Herdr resend the same status; the time must not move.
        self.event(fake, "pane.agent_status_changed", local(2026, 11, 3, 10, 1),
                   pane_id="w1:p4", agent_status="working", agent="claude")
        self.assertEqual(self.state()["w1:p4"]["since"], t0)

        fake.agents[0]["agent_status"] = "done"
        self.event(fake, "pane.agent_status_changed", local(2026, 11, 3, 10, 30),
                   pane_id="w1:p4", agent_status="done", agent="claude")
        self.assertEqual(fake.tokens("since"), {"w1:p4": "10:30 起"})
        status_calls = fake.metadata_calls()[-1]
        self.assertNotIn("--state-label", status_calls)

        # Viewing it later turns done into idle but keeps the finish time.
        fake.agents[0]["agent_status"] = "idle"
        self.event(fake, "pane.agent_status_changed", local(2026, 11, 3, 12, 0),
                   pane_id="w1:p4", agent_status="idle", agent="claude")
        self.assertEqual(self.state()["w1:p4"]["since"], local(2026, 11, 3, 10, 30))
        self.assertEqual(self.state()["w1:p4"]["status"], "idle")

    def test_status_change_refreshes_labels_that_crossed_midnight(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p1"] = {"status": "idle", "since": local(2026, 11, 2, 22, 0), "shown": "22:00 起"}
            state["w1:p2"] = {"status": "idle", "since": local(2026, 11, 3, 8, 0), "shown": "08:00 起"}
            state["w1:p3"] = {"status": "idle", "since": local(2026, 11, 3, 8, 0), "shown": "08:00 起"}
        fake = FakeHerdr(agents=[agent("w1:p3", "claude", "working")])
        self.event(fake, "pane.agent_status_changed", NOW, pane_id="w1:p3", agent_status="working", agent="claude")
        self.assertEqual(fake.tokens("since"), {"w1:p1": "昨天 22:00 起", "w1:p3": "13:00 起"})

    def test_status_change_after_release_does_not_bring_the_time_back(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p4"] = {"status": "idle", "since": 1.0, "shown": "x"}
        fake = FakeHerdr()  # the agent is already gone, though the event still names it
        self.assertEqual(self.event(fake, "pane.agent_status_changed", NOW,
                                    pane_id="w1:p4", agent_status="unknown", agent="claude"), 0)
        (call,) = fake.metadata_calls()
        self.assertEqual(flag_values(call, "--clear-token"), ["since"])
        self.assertNotIn("--clear-state-labels", call)
        self.assertEqual(self.state(), {})

    def test_untracked_panes_are_left_alone(self):
        # Clearing labels makes Herdr resend the status; that must not bring the time back.
        fake = FakeHerdr(agents=[agent("w1:p6", "claude", "idle")])
        self.event(fake, "pane.agent_status_changed", NOW, pane_id="w1:p6", agent_status="idle", agent="claude")
        self.assertEqual(fake.metadata_calls(), [])
        self.assertEqual(self.state(), {})

    def test_new_session_in_the_same_pane_restarts_the_time(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p7"] = {"status": "idle", "session": "old", "since": 1.0}
        fake = FakeHerdr(agents=[agent("w1:p7", "claude", "idle", "new")])
        self.event(fake, "pane.agent_status_changed", NOW, pane_id="w1:p7", agent_status="idle", agent="claude")
        self.assertEqual(self.state()["w1:p7"]["since"], NOW)
        self.assertEqual(self.state()["w1:p7"]["session"], "new")

    def test_status_change_uses_the_live_status(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p5"] = {"status": "working", "since": 1.0}
        fake = FakeHerdr(agents=[agent("w1:p5", "claude", "done")])
        self.event(fake, "pane.agent_status_changed", NOW, pane_id="w1:p5", agent_status="working", agent="claude")
        self.assertEqual(self.state()["w1:p5"]["status"], "done")

    def test_released_clears_everything(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p4"] = {"status": "idle", "since": 1.0}
        fake = FakeHerdr()
        self.assertEqual(self.event(fake, "pane.agent_detected", NOW, pane_id="w1:p4", released=True), 0)
        (call,) = fake.metadata_calls()
        self.assertIn("--clear-state-labels", call)
        self.assertEqual(flag_values(call, "--clear-token"), ["logo", "since"])
        self.assertEqual(self.state(), {})

    def test_clear_every_pane_and_forget_times(self):
        with al.StateStore(self.state_dir, "/tmp/herdr-test.sock").locked() as state:
            state["w1:p1"] = {"status": "idle", "since": 1.0}
        fake = FakeHerdr(panes=[{"pane_id": "w1:p1"}, {"pane_id": "w2:p1"}])
        self.assertEqual(al.main(["clear"], self.env(), run=fake, now=NOW), 0)
        self.assertEqual([c[2] for c in fake.metadata_calls()], ["w1:p1", "w2:p1"])
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, al.state_file_name("/tmp/herdr-test.sock"))))

    def test_events_without_pane_or_unknown_events_are_ignored(self):
        fake = FakeHerdr()
        self.assertEqual(al.main(["event"], self.env(HERDR_PLUGIN_EVENT_JSON="{}"), run=fake), 0)
        self.assertEqual(self.event(fake, "pane.focused", NOW, pane_id="w1:p1"), 0)
        self.assertEqual(fake.calls, [])

    def test_usage(self):
        self.assertEqual(al.main([], self.env(), run=FakeHerdr()), 2)
        self.assertEqual(al.main(["bogus"], self.env(), run=FakeHerdr()), 2)

    def test_missing_binary(self):
        def broken(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])
        self.assertEqual(al.main(["apply"], self.env(), run=broken), 1)


if __name__ == "__main__":
    unittest.main()
