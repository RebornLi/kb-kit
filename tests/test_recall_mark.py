#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_recall_mark.py — recall mark 稳定性种子回归测试（严重问题 3）。

覆盖：首次 mark 的稳定性应源自该笔记 importance 对应间隔，而非固定 0.5 档。

运行：
  python3 -m unittest discover -s tests
"""
import os
import datetime
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import recall_schedule as rs  # noqa: E402


def write_note(path: Path, imp: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: t\ndomain: 运维\nimportance: {imp}\nkb_summary: 测试\n---\n正文内容。\n",
        encoding="utf-8")


class RecallMarkSeedTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-recall-"))
        self.high = self.root / "20-技术 Technology" / "high.md"
        self.low = self.root / "20-技术 Technology" / "low.md"
        write_note(self.high, 0.95)   # L4 → base 180
        write_note(self.low, 0.1)     # <0.3 → base 7

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_first_mark_seeds_from_importance(self):
        rs.mark(self.root, 10, "20-技术 Technology/high.md")
        rs.mark(self.root, 10, "20-技术 Technology/low.md")
        st = rs.load_state(self.root)
        # 首次回忆保持 importance 对应的种子间隔（L4=180 / <0.3=7）
        self.assertAlmostEqual(st["20-技术 Technology/high.md"]["interval"], 180.0, places=2)
        self.assertAlmostEqual(st["20-技术 Technology/low.md"]["interval"], 7.0, places=2)
        self.assertEqual(st["20-技术 Technology/low.md"]["reps"], 1)

    def test_second_mark_grows_when_days_elapsed(self):
        """跨天复习应增长（FSRS：S 随成功次数增长）。

        注意**不要**用"同日连点两次"来测这件事：FSRS 有意让同日重复几乎不改变稳定性
        （同一天里你不可能真的多记住多少），而旧 SM-2 的 `interval *= ease` 每次复习
        都放大间隔 —— 那正是 SM-2 在"一天点十次"时会给出荒谬间隔的原因。
        """
        rel = "20-技术 Technology/low.md"
        rs.mark(self.root, 10, rel)
        first = rs.load_state(self.root)[rel]["interval"]
        # 把水位拨回到 7 天前，模拟真实间隔
        st = rs.load_state(self.root)
        st[rel]["last_seen"] = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        rs.save_state(self.root, st)
        rs.mark(self.root, 10, rel)
        second = rs.load_state(self.root)[rel]["interval"]
        self.assertGreater(second, first, "跨天复习后间隔应增长")

    def test_same_day_repeat_does_not_inflate(self):
        """同日重复**不应**放大间隔 —— 这是 FSRS 相对 SM-2 的关键修正。"""
        rel = "20-技术 Technology/low.md"
        rs.mark(self.root, 10, rel)
        a = rs.load_state(self.root)[rel]["interval"]
        for _ in range(5):
            rs.mark(self.root, 10, rel)
        b = rs.load_state(self.root)[rel]["interval"]
        self.assertLess(b, a * 2, "同日重复把间隔放大了 —— FSRS 不该这样")

    def test_lapse_shortens_interval(self):
        rel = "20-技术 Technology/high.md"
        rs.mark(self.root, 10, rel)          # good → 180
        rs.mark(self.root, 10, rel, "again")  # 遗忘 → 缩短
        st = rs.load_state(self.root)[rel]
        self.assertLess(st["interval"], 180, "遗忘应缩短间隔")
        self.assertGreaterEqual(st.get("lapses", 0), 1)
        # FSRS 的难度会因遗忘显著上升（D 越大越难）
        self.assertGreater(float(st.get("difficulty") or 0), 5.0)


if __name__ == "__main__":
    unittest.main()
