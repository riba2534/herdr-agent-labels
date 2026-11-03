import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import board as bd  # noqa: E402

CLAUDE = "\uec82"
TEXT = bd.TEXT["zh-CN"]
LABELS = {"blocked": "待你确认", "working": "运行中", "done": "已完成·未查看", "idle": "空闲", "unknown": "状态未知"}


def snapshot():
    return {"result": {"snapshot": {
        "workspaces": [
            {"workspace_id": "w1", "label": "copa", "tab_count": 2},
            {"workspace_id": "w2", "label": "herdrx", "tab_count": 1},
        ],
        "tabs": [
            {"tab_id": "w1:t1", "label": "微信转发", "number": 1},
            {"tab_id": "w1:t2", "label": "2", "number": 2},
            {"tab_id": "w2:t1", "label": "1", "number": 1},
        ],
        "agents": [
            {"pane_id": "w2:p1", "agent": "claude", "agent_status": "working", "workspace_id": "w2",
             "tab_id": "w2:t1", "terminal_title_stripped": "Claude Code",
             "tokens": {"logo": CLAUDE, "since": "13:05"}},
            {"pane_id": "w1:p2", "agent": "codex", "agent_status": "idle", "workspace_id": "w1",
             "tab_id": "w1:t2", "terminal_title_stripped": "修 登录 超时"},
            {"pane_id": "w1:p1", "agent": "claude", "agent_status": "done", "workspace_id": "w1",
             "tab_id": "w1:t1", "tokens": {"since": "11-02 23:10"}},
            {"pane_id": "w9:p9", "agent_status": "idle"},
        ],
    }}}


class WidthTest(unittest.TestCase):
    def test_widths(self):
        self.assertEqual(bd.text_width("copa"), 4)
        self.assertEqual(bd.text_width("微信转发"), 8)
        self.assertEqual(bd.text_width(CLAUDE + "×◐✓○"), 5)

    def test_fit(self):
        self.assertEqual(bd.fit("copa", 10), "copa")
        self.assertEqual(bd.fit("微信转发", 5), "微信…")
        self.assertEqual(bd.fit("微信转发", 0), "")
        self.assertLessEqual(bd.text_width(bd.fit("DeepSeekSuperAgent", 8)), 8)


class RowsTest(unittest.TestCase):
    def test_agent_rows(self):
        rows = {r["pane_id"]: r for r in bd.agent_rows("devcn", 1, snapshot(), {})}
        self.assertEqual(set(rows), {"w2:p1", "w1:p2", "w1:p1"})
        self.assertEqual(rows["w2:p1"]["tab"], "")          # single default tab is hidden
        self.assertEqual(rows["w2:p1"]["title"], "")        # generic title is hidden
        self.assertEqual(rows["w2:p1"]["since"], "13:05")
        self.assertEqual(rows["w1:p1"]["tab"], "微信转发")
        self.assertEqual(rows["w1:p2"]["tab"], "2")         # shown because the workspace has two tabs
        self.assertEqual(rows["w1:p2"]["title"], "修 登录 超时")
        self.assertEqual(rows["w1:p2"]["logo"], "\uec81")   # falls back to the built-in logo
        self.assertEqual(rows["w1:p1"]["order"][0], 1)
        self.assertLess(rows["w1:p1"]["order"], rows["w2:p1"]["order"])

    def test_bad_payload(self):
        self.assertEqual(bd.agent_rows("x", 0, {}, {}), [])


class LinesTest(unittest.TestCase):
    def rows(self):
        return bd.agent_rows("Local", 0, snapshot(), {})

    def test_groups_in_order_with_counts(self):
        lines = bd.build_lines(self.rows(), [], TEXT, LABELS, fold_idle=False)
        groups = [(l[1], l[2]) for l in lines if l[0] == "group"]
        self.assertEqual(groups, [("working", 1), ("done", 1), ("idle", 1)])
        self.assertEqual([l[0] for l in lines], ["group", "agent", "group", "agent", "group", "agent"])

    def test_fold_idle_and_errors(self):
        lines = bd.build_lines(self.rows(), [("homebig", "timeout")], TEXT, LABELS, fold_idle=True)
        self.assertEqual(lines[0], ("error", "homebig 读取失败：timeout"))
        self.assertEqual(lines[-1], ("note", TEXT["collapsed"]))
        self.assertEqual(lines[-2], ("group", "idle", 1))

    def test_empty(self):
        self.assertEqual(bd.build_lines([], [], TEXT, LABELS, False), [("note", TEXT["empty"])])

    def test_blocked_comes_first(self):
        rows = self.rows()
        rows[1]["status"] = "blocked"
        lines = bd.build_lines(rows, [], TEXT, LABELS, False)
        self.assertEqual(lines[0], ("group", "blocked", 1))


class SegmentsTest(unittest.TestCase):
    def test_line_fits_and_keeps_time_on_the_right(self):
        row = {r["pane_id"]: r for r in bd.agent_rows("macminim6", 0, snapshot(), {})}["w1:p1"]
        for width in (30, 50, 90):
            pieces = bd.agent_segments(row, width, machine_width=9, show_machine=True)
            text = "".join(t for t, _ in pieces)
            self.assertLessEqual(bd.text_width(text), width)
            self.assertTrue(text.rstrip().endswith("11-02 23:10"))
        styles = [s for _, s in bd.agent_segments(row, 90, 9, True)]
        self.assertEqual(styles[:4], ["done", "claude", "dim", "workspace"])

    def test_single_machine_hides_machine_column(self):
        row = bd.agent_rows("Local", 0, snapshot(), {})[0]
        text = "".join(t for t, _ in bd.agent_segments(row, 60, 5, False))
        self.assertNotIn("Local", text)


if __name__ == "__main__":
    unittest.main()
