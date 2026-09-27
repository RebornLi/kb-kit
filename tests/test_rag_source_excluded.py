#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rag_source_excluded.py — 来源/退出笔记不入检索回归测试（C 方案）。

覆盖：
  1. is_source_note / index_excluded 判定
  2. 全量索引：`is_source: true`、`kind: source`、`kb_index: false` 的笔记不入 tf/meta
  3. 增量索引：后加入的来源笔记被排除、且不会被记入 doc_hashes

运行：
  python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import rag  # noqa: E402
from kb_common import index_excluded, is_source_note  # noqa: E402


def write_note(path: Path, body: str, extra_fm: str = "", mtime: float = 1_700_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: t\ndomain: 运维\n{extra_fm}---\n{body}\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def load_payload(root: Path) -> dict:
    return json.loads((root / "vector index" / "df_idf.json").read_text(encoding="utf-8"))


def quiet_index(root: Path) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return rag.cmd_index(root)


class SourceExcludedHelpersTest(unittest.TestCase):
    def test_predicates(self):
        self.assertTrue(is_source_note({"is_source": True}))
        self.assertTrue(is_source_note({"is_source": "true"}))
        self.assertTrue(is_source_note({"kind": "source"}))
        self.assertFalse(is_source_note({"kind": "note"}))
        self.assertTrue(index_excluded({"kb_action": "retire"}))
        self.assertTrue(index_excluded({"kb_index": "false"}))
        self.assertTrue(index_excluded({"is_source": True}))
        self.assertFalse(index_excluded({"status": "active"}))


class RagSourceExcludedTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-rag-src-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_full_rebuild_excludes_source_notes(self):
        write_note(self.root / "20-技术 Technology/keep.md", "普通知识 备份 检索 keep")
        write_note(self.root / "raw/a.md", "来源原文 is_source alpha",
                   extra_fm="is_source: true\nkind: source\n")
        write_note(self.root / "raw/b.md", "来源原文 kind alpha", extra_fm="kind: source\n")
        write_note(self.root / "raw/c.md", "显式退出 alpha", extra_fm="kb_index: false\n")

        self.assertEqual(quiet_index(self.root), 0)
        p = load_payload(self.root)
        self.assertEqual(p["n_docs"], 1, "只有普通笔记入索引")
        self.assertIn("20-技术 Technology/keep.md", p["tf"])
        for rel in ("raw/a.md", "raw/b.md", "raw/c.md"):
            self.assertNotIn(rel, p["tf"])
            self.assertNotIn(rel, p["meta"])
        self.assertNotIn("alpha", p["vocab"], "来源独有词不应进词表")

    def test_incremental_excludes_newly_added_source(self):
        write_note(self.root / "20-技术 Technology/keep.md", "普通知识 keep")
        self.assertEqual(quiet_index(self.root), 0)
        self.assertEqual(load_payload(self.root)["n_docs"], 1)

        write_note(self.root / "raw/new.md", "新增来源 alpha",
                   extra_fm="is_source: true\n", mtime=1_700_000_500)
        self.assertEqual(quiet_index(self.root), 0)  # 增量
        p = load_payload(self.root)
        self.assertNotIn("raw/new.md", p["tf"])
        self.assertNotIn("raw/new.md", p.get("doc_hashes", {}))


if __name__ == "__main__":
    unittest.main()
