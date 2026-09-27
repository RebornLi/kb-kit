#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rag_cache.py — rag.py 查询缓存回归测试（P1-2）。

覆盖：
  1. 命中缓存时不短路反馈记录（agent_hits 计数随每次查询递增）
  2. 缓存键包含 context（片段长度）——不同 context 不串用缓存
  3. 缓存键包含 answer（是否调 LLM 作答）——不同 answer 不串用缓存
  4. 缓存命中返回的命中路径与首次一致

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
from state_manager import StateStore  # noqa: E402


def write_note(path: Path, title: str, body: str, mtime: float = 1_700_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {title}\ndomain: 运维\n---\n{body}\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def quiet_index(root: Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        assert rag.cmd_index(root) == 0


def run_query(root: Path, q: str = "知识库备份", *, as_json=True, context=240, answer=False,
              top=3) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = rag.cmd_query(root, q, top, answer, as_json=as_json, context=context)
    assert rc == 0
    return buf.getvalue()


def agent_hits(root: Path) -> dict:
    return StateStore(root).load("feedback_state.json", {}).get("agent_hits", {})


def cache_entries(root: Path) -> dict:
    return StateStore(root).load("query_cache.json", {})


class RagQueryCacheTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-rag-cache-"))
        write_note(self.root / "20-技术 Technology" / "备份.md", "备份策略",
                   "知识库备份 记忆摄取 间隔回忆 的说明与操作步骤")
        quiet_index(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cache_hit_still_records_feedback(self):
        # 首次：填充缓存 + 记录 1 条
        out1 = run_query(self.root, as_json=True)
        hits1 = agent_hits(self.root)
        self.assertEqual(len(hits1), 1, "首次查询应记录 1 条命中事件")
        self.assertEqual(len(cache_entries(self.root)), 1, "首次查询应写入 1 条缓存")

        # 第二次相同查询：命中缓存，但仍应新增 1 条命中事件
        out2 = run_query(self.root, as_json=True)
        hits2 = agent_hits(self.root)
        self.assertEqual(len(hits2), 2, "缓存命中仍须记录反馈（不得短路）")
        self.assertEqual(len(cache_entries(self.root)), 1, "相同查询不应新增缓存条目")

        # 两次返回的命中路径应一致
        paths1 = list(hits1.values())[0]["hits"]
        paths2 = list(hits2.values())[1]["hits"]
        self.assertEqual(paths1, paths2, "缓存命中记录的命中路径应与首次一致")
        # 缓存返回体应可解析且含 hits
        self.assertIn("hits", json.loads(out2))
        self.assertEqual(out1, out2, "相同查询缓存前后输出一致")

    def test_context_changes_cache_key(self):
        run_query(self.root, as_json=True, context=240)
        run_query(self.root, as_json=True, context=10)  # 片段长度不同 → 另一把键
        self.assertEqual(len(cache_entries(self.root)), 2, "不同 context 应生成不同缓存条目")

    def test_answer_changes_cache_key(self):
        run_query(self.root, as_json=False, answer=False)
        run_query(self.root, as_json=False, answer=True)  # 未配 LLM → 仅提示，不联网
        self.assertEqual(len(cache_entries(self.root)), 2, "不同 answer 应生成不同缓存条目")


if __name__ == "__main__":
    unittest.main()
