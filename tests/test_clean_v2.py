#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_clean_v2.py — 清洗优化（L2 去重 / L3 分块）回归。

覆盖：
  1. 递归兜底：单个超长段落也能被切分，且每块 <= chunk_size
  2. L3 父文档统一改写为索引页（不再产生 -index.md / 父+块重复）
  3. L3 contextual header：子块含《父标题》
  4. L2 正文指纹去重：正文相同、frontmatter 不同 → 归档到 _trash/
  5. MinHash+LSH 近重复：近重复成对、无关不配

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

import clean  # noqa: E402
import semantic_chunk  # noqa: E402
import dedup  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


FM = "tags: [\"管理\"]\nstatus: active\ndomain: 管理\nimportance: 0.6\nkb_summary: s\ntitle: 长文\n"


class CleanV2Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-cleanv2-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # 1. 递归兜底（单段超长）
    def test_recursive_split_single_paragraph(self):
        body = "字" * 5000  # 单个超长段落，无标点/换行
        notes = semantic_chunk.chunk(body, "x.md", chunk_size=500, overlap=100)
        children = [n for n in notes if not n.is_parent_index]
        self.assertGreater(len(children), 1, "单段超长应被切分")
        self.assertTrue(all(len(n.body) <= 500 for n in children), "每块应 <= chunk_size")

    # 2/3. 父文档改写为索引页 + contextual header
    def test_semantic_chunk_parent_index_and_header(self):
        para = "\n\n".join("段落内容" * 60 for _ in range(6))  # 多段无 ## → 语义路径
        write(self.root / "20-技术 Technology/long.md", f"---\n{FM}---\n{para}\n")
        c1, touched = clean.do_chunk(self.root, 300)
        self.assertGreater(c1, 0)
        # 无独立 -index.md
        self.assertEqual(list((self.root / "20-技术 Technology").glob("*-index.md")), [],
                         "不应再产生独立 -index.md")
        parent = (self.root / "20-技术 Technology/long.md").read_text(encoding="utf-8")
        self.assertIn("is_chunk_index", parent, "父文档应改写为索引页")
        children = list((self.root / "20-技术 Technology").glob("long-c*.md"))
        self.assertTrue(children, "应产生子块")
        self.assertIn("《长文》", children[0].read_text(encoding="utf-8"), "子块应含 contextual header")
        # 幂等
        c2, _ = clean.do_chunk(self.root, 300)
        self.assertEqual(c2, 0, "二次分块应为 0")

    # 4. 正文指纹去重
    def test_dedup_by_body_fingerprint(self):
        body = "这是完全相同的正文内容，用于验证按正文指纹去重。" * 3
        write(self.root / "20-技术 Technology/a.md",
              f"---\ntags: [\"管理\"]\nstatus: active\ndomain: 管理\nimportance: 0.5\nkb_summary: x\n---\n{body}\n")
        write(self.root / "20-技术 Technology/b.md",
              f"---\ntags: [\"开发\"]\nstatus: draft\ndomain: 开发\nimportance: 0.7\nkb_summary: y\n---\n{body}\n")
        _changed, dup, _stats, _touched = clean.do_clean(self.root, dry=False)
        self.assertEqual(dup, 1, "正文相同应去重 1 条")
        self.assertTrue((self.root / "90-归档 Archive" / "_trash").exists())
        self.assertEqual(len(list((self.root / "20-技术 Technology").glob("*.md"))), 1)

    # 5. MinHash 近重复
    def test_near_duplicate_pairs(self):
        base = "本地知识库检索使用 TF-IDF 余弦相似度并支持中文 unigram 与 bigram。" * 5
        docs = {"a.md": base, "b.md": base + "额外一句。", "c.md": "完全无关的天气与股票行情。" * 5}
        pairs = dedup.near_duplicate_pairs(docs, threshold=0.6)
        keys = {(x, y) for _s, x, y in pairs}
        self.assertIn(("a.md", "b.md"), keys)
        self.assertNotIn(("a.md", "c.md"), keys)


if __name__ == "__main__":
    unittest.main()
