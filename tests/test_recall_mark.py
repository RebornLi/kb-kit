#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_recall_mark.py — recall mark 稳定性种子回归测试（严重问题 3）。

覆盖：首次 mark 的稳定性应源自该笔记 importance 对应间隔，而非固定 0.5 档。

运行：
  python3 -m unittest discover -s tests
"""
import os
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

    def test_second_mark_grows_from_seed(self):
        rel = "20-技术 Technology/low.md"
        rs.mark(self.root, 10, rel)
        first = rs.load_state(self.root)[rel]["interval"]
        rs.mark(self.root, 10, rel)
        second = rs.load_state(self.root)[rel]["interval"]
        self.assertGreater(second, first, "第二次复习应在既有间隔上按 ease 增长")
        self.assertAlmostEqual(second, min(first * 2.5, 365), places=2)

    def test_lapse_shortens_interval(self):
        rel = "20-技术 Technology/high.md"
        rs.mark(self.root, 10, rel)          # good → 180
        rs.mark(self.root, 10, rel, "again")  # 遗忘 → 缩短
        st = rs.load_state(self.root)[rel]
        self.assertLess(st["interval"], 180, "遗忘应缩短间隔")
        self.assertEqual(st["reps"], 0)
        self.assertGreaterEqual(st.get("lapses", 0), 1)


if __name__ == "__main__":
    unittest.main()
