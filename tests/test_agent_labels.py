import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_labels as al  # noqa: E402


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
        elif args[:2] == ["pane", "list"]:
            body = {"result": {"panes": self.panes, "type": "pane_list"}}
        else:
            body = {"result": {"type": "ok"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    def metadata_calls(self):
        return [c for c in self.calls if c[:2] == ["pane", "report-metadata"]]


def flag_values(args, flag):
    return [args[i + 1] for i, arg in enumerate(args) if arg == flag]


class LocaleTest(unittest.TestCase):
    def test_explicit_locale_wins(self):
        self.assertEqual(al.resolve_locale({"locale": "en"}, {"LANG": "zh_CN.UTF-8"}), "en")

    def test_auto_follows_environment_priority(self):
        env = {"LC_ALL": "C", "LANG": "zh_CN.UTF-8"}
        self.assertEqual(al.resolve_locale({}, env), "en")
        self.assertEqual(al.resolve_locale({}, {"LANG": "zh_CN.UTF-8"}), "zh-CN")
        self.assertEqual(al.resolve_locale({}, {"LC_MESSAGES": "zh_TW"}), "zh-CN")

    def test_unknown_locale_falls_back_to_auto(self):
        self.assertEqual(al.resolve_locale({"locale": "fr"}, {"LANG": "zh_CN"}), "zh-CN")
        self.assertEqual(al.resolve_locale({}, {}), "en")


class LabelsTest(unittest.TestCase):
    def test_every_locale_covers_every_state(self):
        for labels in al.LABELS.values():
            self.assertEqual(set(labels), set(al.STATES))

    def test_overrides_only_known_states(self):
        config = {"locale": "zh-CN", "labels": {"done": " 跑完了 ", "bogus": "x", "idle": ""}}
        labels = al.resolve_labels(config, {})
        self.assertEqual(labels["done"], "跑完了")
        self.assertEqual(labels["idle"], "空闲")
        self.assertNotIn("bogus", labels)


class LogoTest(unittest.TestCase):
    def test_builtin_logos(self):
        self.assertEqual(al.resolve_logo("claude", {}), "\uec82")
        self.assertEqual(al.resolve_logo("codex", {}), "\uec81")

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


class ArgsTest(unittest.TestCase):
    def test_apply_args(self):
        labels = al.LABELS["zh-CN"]
        args = al.apply_args("w1:p1", "claude", "\uec82", labels)
        self.assertEqual(args[:3], ["pane", "report-metadata", "w1:p1"])
        self.assertEqual(flag_values(args, "--source"), [al.SOURCE])
        self.assertEqual(flag_values(args, "--agent"), ["claude"])
        self.assertEqual(flag_values(args, "--token"), ["logo=\uec82"])
        self.assertEqual(
            flag_values(args, "--state-label"),
            ["{}={}".format(s, labels[s]) for s in al.STATES],
        )

    def test_apply_without_logo_clears_token(self):
        args = al.apply_args("w1:p1", "codex", None, al.LABELS["en"])
        self.assertEqual(flag_values(args, "--clear-token"), ["logo"])
        self.assertEqual(flag_values(args, "--token"), [])

    def test_clear_args(self):
        args = al.clear_args("w1:p2")
        self.assertIn("--clear-state-labels", args)
        self.assertEqual(flag_values(args, "--clear-token"), ["logo"])
        self.assertEqual(flag_values(args, "--source"), [al.SOURCE])


class EventTest(unittest.TestCase):
    def test_parse_envelope(self):
        raw = json.dumps({
            "event": "pane_agent_detected",
            "data": {"type": "pane_agent_detected", "pane_id": "w1:p3", "workspace_id": "w1", "agent": "codex"},
        })
        self.assertEqual(al.parse_event(raw), ("w1:p3", "codex", False))

    def test_parse_released(self):
        raw = json.dumps({"data": {"pane_id": "w1:p3", "released": True, "final_status": "idle"}})
        self.assertEqual(al.parse_event(raw), ("w1:p3", None, True))

    def test_parse_garbage(self):
        for raw in (None, "", "not json", "[]", '{"data": 3}'):
            self.assertEqual(al.parse_event(raw), (None, None, False))


class CommandTest(unittest.TestCase):
    def env(self, **extra):
        env = {"HERDR_BIN_PATH": "/bin/herdr", "LANG": "zh_CN.UTF-8"}
        env.update(extra)
        return env

    def test_apply_labels_every_agent(self):
        fake = FakeHerdr(agents=[
            {"pane_id": "w1:p1", "agent": "claude"},
            {"pane_id": "w1:p2", "agent": "codex"},
            {"pane_id": "w1:p3"},
        ])
        self.assertEqual(al.main(["apply"], self.env(), run=fake), 0)
        calls = fake.metadata_calls()
        self.assertEqual([c[2] for c in calls], ["w1:p1", "w1:p2"])
        self.assertEqual(flag_values(calls[1], "--token"), ["logo=\uec81"])
        self.assertIn("working=运行中", flag_values(calls[0], "--state-label"))

    def test_apply_keeps_going_after_a_failure(self):
        fake = FakeHerdr(
            agents=[{"pane_id": "w1:p1", "agent": "claude"}, {"pane_id": "w1:p2", "agent": "claude"}],
            fail_on="w1:p1",
        )
        self.assertEqual(al.main(["apply"], self.env(), run=fake), 1)
        self.assertEqual([c[2] for c in fake.metadata_calls()], ["w1:p1", "w1:p2"])

    def test_clear_every_pane(self):
        fake = FakeHerdr(panes=[{"pane_id": "w1:p1"}, {"pane_id": "w2:p1"}])
        self.assertEqual(al.main(["clear"], self.env(), run=fake), 0)
        self.assertEqual([c[2] for c in fake.metadata_calls()], ["w1:p1", "w2:p1"])

    def test_event_detected_and_released(self):
        fake = FakeHerdr()
        detected = json.dumps({"data": {"pane_id": "w1:p4", "agent": "claude"}})
        self.assertEqual(al.main(["event"], self.env(HERDR_PLUGIN_EVENT_JSON=detected), run=fake), 0)
        released = json.dumps({"data": {"pane_id": "w1:p4", "released": True}})
        self.assertEqual(al.main(["event"], self.env(HERDR_PLUGIN_EVENT_JSON=released), run=fake), 0)
        first, second = fake.metadata_calls()
        self.assertEqual(flag_values(first, "--token"), ["logo=\uec82"])
        self.assertIn("--clear-state-labels", second)

    def test_event_without_pane_is_ignored(self):
        fake = FakeHerdr()
        self.assertEqual(al.main(["event"], self.env(HERDR_PLUGIN_EVENT_JSON="{}"), run=fake), 0)
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
