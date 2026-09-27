#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rag_incremental.py — rag.py 增量索引回归测试（P1-1）。

覆盖：
  1. 笔记标记 kb_action=retire 后，增量索引应把它从 tf/meta/doc_hashes 移除
  2. 物理删除的笔记同样被移除
  3. retire 后的增量结果与全量重建一致（行为对齐）

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

import rag  # noqa: E402  (需要 pipeline 在 sys.path)


def write_note(path: Path, title: str, body: str, extra_fm: str = "", mtime: float = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\ndomain: 运维\n{extra_fm}---\n{body}\n",
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def load_payload(root: Path) -> dict:
    return json.loads((root / "vector index" / "df_idf.json").read_text(encoding="utf-8"))


def quiet_index(root: Path) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return rag.cmd_index(root)


class RagIncrementalRetireTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-rag-"))
        self.a = self.root / "10-项目 Projects" / "a.md"
        self.b = self.root / "20-技术 Technology" / "b.md"
        write_note(self.a, "备份策略", "知识库备份 记忆摄取 间隔回忆 alpha", mtime=1_700_000_000)
        write_note(self.b, "检索方案", "本地检索 余弦相似度 beta", mtime=1_700_000_000)
        self.assertEqual(quiet_index(self.root), 0)  # 首次 → 全量
        p = load_payload(self.root)
        self.assertEqual(p["n_docs"], 2)
        self.assertIn("10-项目 Projects/a.md", p["tf"])
        self.assertIn("20-技术 Technology/b.md", p["tf"])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_retire_removed_by_incremental(self):
        # 把 a.md 标记 retire（改内容，mtime 前进）
        write_note(self.a, "备份策略", "知识库备份 记忆摄取 间隔回忆 alpha",
                   extra_fm="kb_action: retire\n", mtime=1_700_000_100)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = rag.cmd_index(self.root)  # 增量
        self.assertEqual(rc, 0)

        p = load_payload(self.root)
        rel_a = "10-项目 Projects/a.md"
        rel_b = "20-技术 Technology/b.md"
        self.assertEqual(p["n_docs"], 1)
        self.assertNotIn(rel_a, p["tf"], "retire 笔记应从 tf 移除")
        self.assertNotIn(rel_a, p["meta"], "retire 笔记应从 meta 移除")
        self.assertNotIn(rel_a, p.get("doc_hashes", {}), "retire 笔记应从 doc_hashes 移除")
        self.assertNotIn(rel_a, p.get("doc_mtimes", {}), "retire 笔记应从 doc_mtimes 移除")
        self.assertIn(rel_b, p["tf"], "未 retire 的笔记应保留")
        # 检索词表也不应再含仅属于 a 的词
        self.assertNotIn("alpha", p["vocab"])

    def test_deleted_file_removed_by_incremental(self):
        os.remove(self.b)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = rag.cmd_index(self.root)  # 增量
        self.assertEqual(rc, 0)
        p = load_payload(self.root)
        self.assertEqual(p["n_docs"], 1)
        self.assertNotIn("20-技术 Technology/b.md", p["tf"])
        self.assertNotIn("beta", p["vocab"])

    def test_retire_incremental_matches_full_rebuild(self):
        write_note(self.a, "备份策略", "知识库备份 记忆摄取 间隔回忆 alpha",
                   extra_fm="kb_action: retire\n", mtime=1_700_000_100)
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root)  # 增量
        inc = load_payload(self.root)

        # 删掉索引后全量重建，对比 tf/meta/n_docs
        shutil.rmtree(self.root / "vector index")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root)  # 索引不存在 → 全量
        full = load_payload(self.root)

        self.assertEqual(inc["n_docs"], full["n_docs"])
        self.assertEqual(set(inc["tf"]), set(full["tf"]))
        self.assertEqual(set(inc["meta"]), set(full["meta"]))


if __name__ == "__main__":
    unittest.main()
