#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p2.py — P2 生命周期优化回归（Stage4 链接类型/MOC + Stage9 墓碑/lineage）。

覆盖：
  1. link 建议带 kind（cross/concept）；apply 分组写入
  2. link moc 生成 _MOC.md（且列为生成产物）
  3. intake merge：目标加别名 + 墓碑(redirect_to) + lineage + 移除源
  4. clean 去重：保留笔记加别名 + lineage

运行：python3 -m unittest discover -s tests
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import link_engine  # noqa: E402
import intake_triage  # noqa: E402
import clean  # noqa: E402
from kb_common import is_generated_report, add_alias_text  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


FM = "tags: [\"管理\"]\nstatus: active\ndomain: {d}\nimportance: 0.6\nkb_summary: s\n"


class P2Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p2-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_link_kind_and_moc(self):
        write(self.root / "20-技术 Technology/a.md",
              "---\n" + FM.format(d="开发") + "---\n本地检索 余弦 相似度\n")
        write(self.root / "10-项目 Projects/b.md",
              "---\n" + FM.format(d="产品") + "---\n本地检索 余弦 相似度方案\n")
        _rels, _notes, recs, cross = link_engine.suggestions(self.root, 0.1)
        self.assertTrue(recs)
        self.assertTrue(all("kind" in r for r in recs))
        self.assertTrue(all(r["kind"] == "cross" for r in cross))
        link_engine.moc(self.root)
        moc = self.root / "70-知识治理 Governance" / "_MOC.md"
        self.assertTrue(moc.exists())
        self.assertIn("MOC", moc.read_text(encoding="utf-8"))
        self.assertTrue(is_generated_report("70-知识治理 Governance/_MOC.md"))

    def test_merge_tombstone_alias_lineage(self):
        src = self.root / "00-收件箱 Inbox" / "dup.md"
        tgt = self.root / "20-技术 Technology" / "orig.md"
        write(src, "---\n" + FM.format(d="开发") + "---\n重复正文要点。\n")
        write(tgt, "---\n" + FM.format(d="开发") + "---\n原始正文。\n")
        rc = intake_triage.apply_moves(
            self.root,
            [{"action": "merge", "rel": "00-收件箱 Inbox/dup.md",
              "sim_to": "20-技术 Technology/orig.md"}],
            move=True, trash=False)
        self.assertEqual(rc, 0)
        self.assertFalse(src.exists())
        tt = tgt.read_text(encoding="utf-8")
        self.assertIn("## 合并自", tt)
        self.assertIn('aliases: [dup]', tt, "目标应声明 source 别名（旧链接重定向）")
        tombs = list((self.root / "90-归档 Archive" / "_trash").glob("dup-merged-*.md"))
        self.assertTrue(tombs, "应写合并墓碑")
        tb = tombs[0].read_text(encoding="utf-8")
        self.assertIn("redirect_to: 20-技术 Technology/orig.md", tb)
        self.assertIn("kb_action: retire", tb)
        lin = self.root / ".kb" / "state" / "lineage.jsonl"
        self.assertTrue(lin.exists())
        self.assertIn("merge", lin.read_text(encoding="utf-8"))

    def test_dedup_adds_alias_and_lineage(self):
        body = "完全相同的正文用于去重与别名重定向验证。" * 3
        write(self.root / "20-技术 Technology/k.md", "---\n" + FM.format(d="开发") + f"---\n{body}\n")
        write(self.root / "20-技术 Technology/d.md", "---\n" + FM.format(d="开发") + f"---\n{body}\n")
        _ch, dup, _st, _touched = clean.do_clean(self.root, dry=False)
        self.assertEqual(dup, 1)
        kept = [p for p in (self.root / "20-技术 Technology").glob("*.md")]
        self.assertEqual(len(kept), 1)
        kt = kept[0].read_text(encoding="utf-8")
        self.assertIn("aliases: [", kt)
        self.assertTrue((self.root / ".kb" / "state" / "lineage.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
