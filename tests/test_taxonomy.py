#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_taxonomy.py — P1 Stage5 受控词表/层级 tag 回归。

覆盖：
  1. 别名归一 / 层级前缀 / group 保持 / 未知原样
  2. normalize_tags 去重保序
  3. clean.do_taxonomy dry-run 与 apply（幂等）

运行：python3 -m unittest discover -s tests
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import taxonomy  # noqa: E402
import clean  # noqa: E402
from kb_common import load_meta  # noqa: E402

class TaxonomyTest(unittest.TestCase):
    def setUp(self):
        self.tax = taxonomy.load_taxonomy()
        self.root = Path(tempfile.mkdtemp(prefix="kb-tax-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_normalize_tag(self):
        self.assertEqual(taxonomy.normalize_tag("ai", self.tax), "tech/ai")
        self.assertEqual(taxonomy.normalize_tag("会议", self.tax), "work/meeting")
        self.assertEqual(taxonomy.normalize_tag("meeting", self.tax), "work/meeting")
        self.assertEqual(taxonomy.normalize_tag("tech", self.tax), "tech")
        self.assertEqual(taxonomy.normalize_tag("未知tag", self.tax), "未知tag")

    def test_normalize_tags_dedup(self):
        out = taxonomy.normalize_tags(["ai", "会议", "会议", "复盘"], self.tax)
        self.assertEqual(out, ["tech/ai", "work/meeting", "work/review"])

    def test_do_taxonomy_dry_and_apply(self):
        note = self.root / "20-技术 Technology/a.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text('---\ntags: ["ai", "会议", "会议"]\nstatus: active\ndomain: 开发\n'
                        'importance: 0.6\nkb_summary: s\n---\n正文\n', encoding="utf-8")
        changed, touched = clean.do_taxonomy(self.root, dry=True)
        self.assertEqual(len(changed), 1)
        self.assertEqual(touched, [], "dry-run 不写")
        changed2, touched2 = clean.do_taxonomy(self.root, dry=False)
        self.assertEqual(len(changed2), 1)
        tags = taxonomy.parse_tags(load_meta(note)[0].get("tags"))
        self.assertEqual(tags, ["tech/ai", "work/meeting"])
        # 幂等
        changed3, _ = clean.do_taxonomy(self.root, dry=True)
        self.assertEqual(changed3, [])


if __name__ == "__main__":
    unittest.main()
