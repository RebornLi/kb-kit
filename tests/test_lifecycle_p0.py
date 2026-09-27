#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_lifecycle_p0.py — P0 生命周期优化回归（Stage8 来源权威/新鲜度 + Stage3 混合检索）。

覆盖：
  1. freshness / is_stale / retrievable_status 判定
  2. rag 索引排除非可检索状态（draft/archived）
  3. rag query --exclude-stale 过滤陈旧笔记
  4. BM25 混合检索：命中相关文档
  5. kb-healthcheck freshness 子命令可运行

运行：python3 -m unittest discover -s tests
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import rag  # noqa: E402
from kb_common import freshness, is_stale, retrievable_status, index_excluded  # noqa: E402


def write(path: Path, fm: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8")


class LifecycleP0Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_predicates(self):
        self.assertTrue(is_stale({"updated": "2000-01-01"}))
        self.assertFalse(is_stale({"updated": "2999-01-01"}))
        self.assertTrue(is_stale({"updated": "2999-01-01", "review_after": "2000-01-01"}))
        self.assertTrue(retrievable_status({"status": "active"}))
        self.assertFalse(retrievable_status({"status": "draft"}))
        self.assertTrue(retrievable_status({}))  # 无 status 宽松放行
        self.assertTrue(index_excluded({"status": "archived"}))
        self.assertFalse(index_excluded({"status": "active"}))

    def test_index_excludes_non_retrievable_status(self):
        write(self.root / "20-技术 Technology/active.md",
              "status: active\ndomain: 开发\nimportance: 0.6\n", "备份 检索 知识")
        write(self.root / "20-技术 Technology/draft.md",
              "status: draft\ndomain: 开发\nimportance: 0.6\n", "备份 检索 知识")
        write(self.root / "20-技术 Technology/arch.md",
              "status: archived\ndomain: 开发\nimportance: 0.6\n", "备份 检索 知识")
        rag.cmd_index(self.root)
        tf = json.loads((self.root / "vector index" / "df_idf.json").read_text(encoding="utf-8"))["tf"]
        self.assertIn("20-技术 Technology/active.md", tf)
        self.assertNotIn("20-技术 Technology/draft.md", tf)
        self.assertNotIn("20-技术 Technology/arch.md", tf)

    def test_exclude_stale_filter(self):
        write(self.root / "20-技术 Technology/fresh.md",
              "status: active\ndomain: 开发\nimportance: 0.6\nupdated: 2999-01-01\n", "备份 检索 知识")
        write(self.root / "20-技术 Technology/old.md",
              "status: active\ndomain: 开发\nimportance: 0.6\nupdated: 2000-01-01\n", "备份 检索 知识")
        rag.cmd_index(self.root)

        def hits(extra):
            out = subprocess.run(
                [sys.executable, str(PIPELINE / "rag.py"), "query", "备份 检索",
                 "--top", "10", "--json", "--root", str(self.root), *extra],
                capture_output=True, text=True).stdout
            return {h["path"] for h in json.loads(out).get("hits", [])}

        all_hits = hits([])
        self.assertIn("20-技术 Technology/old.md", all_hits)
        fresh_hits = hits(["--exclude-stale"])
        self.assertIn("20-技术 Technology/fresh.md", fresh_hits)
        self.assertNotIn("20-技术 Technology/old.md", fresh_hits, "陈旧笔记应被 freshness 过滤")

    def test_bm25_hybrid_scores(self):
        tf_all = {"a.md": __import__("collections").Counter(["备份", "检索", "知识"]),
                  "b.md": __import__("collections").Counter(["天气", "股票"])}
        sc = rag._bm25_scores(tf_all, ["备份", "检索"])
        self.assertIn("a.md", sc)
        self.assertNotIn("b.md", sc)

    def test_healthcheck_freshness_runs(self):
        write(self.root / "20-技术 Technology/old.md",
              "status: active\ndomain: 开发\nimportance: 0.6\nupdated: 2000-01-01\n", "内容")
        r = subprocess.run([sys.executable, str(PIPELINE / "kb-healthcheck.py"),
                            "freshness", "--root", str(self.root)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, "有陈旧项应返回 1")
        self.assertIn("freshness", r.stdout)


if __name__ == "__main__":
    unittest.main()
