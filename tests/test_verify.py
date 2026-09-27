#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify.py — 安装自检回归测试（P2-1 / P2-3）。

覆盖：
  1. 只有机制文件、无示例内容文件 → 结构校验通过（内容降级为提示）
  2. 缺机制文件（kb）→ 校验失败
  3. 缺内容文件不影响通过
  4. 向量索引显示真实文档数（n_docs）而非 payload 键数

运行：
  python3 -m unittest discover -s tests
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import verify  # noqa: E402


class VerifyStructureTest(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kb-verify-"))
        for d in verify.REQUIRED_DIRS:
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        # 只造机制文件，不造任何示例内容文件
        for f in verify.REQUIRED_FILES:
            (self.vault / f).write_text("x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_passes_without_optional_content(self):
        # 前提：示例内容确实不存在
        for f in verify.CONTENT_FILES:
            self.assertFalse((self.vault / f).exists())
        item = verify.check_vault_structure(self.vault)
        self.assertTrue(item.passed, item.detail)
        self.assertIn("可选内容", item.detail)

    def test_fails_when_machinery_missing(self):
        (self.vault / "kb").unlink()
        item = verify.check_vault_structure(self.vault)
        self.assertFalse(item.passed)
        self.assertIn("kb", item.detail)

    def test_missing_content_does_not_fail(self):
        (self.vault / "如何使用.md").write_text("x", encoding="utf-8")
        item = verify.check_vault_structure(self.vault)
        self.assertTrue(item.passed, item.detail)

    def test_index_reports_doc_count(self):
        idx = self.vault / "vector index"
        idx.mkdir(parents=True, exist_ok=True)
        (idx / "df_idf.json").write_text(json.dumps({
            "version": 2, "n_docs": 5, "vocab": [], "idf": {}, "tf": {}, "meta": {},
        }), encoding="utf-8")
        item = verify.check_vector_index(self.vault)
        self.assertTrue(item.passed, item.detail)
        self.assertIn("5 篇", item.detail)
        self.assertNotIn("9 条", item.detail)

    def test_install_manifest_check(self):
        # 无清单（旧版/精简安装）→ 跳过通过
        item = verify.check_install_manifest(self.vault)
        self.assertTrue(item.passed, item.detail)
        self.assertIn("无清单", item.detail)

        mf = self.vault / ".kb" / "install-manifest.json"
        mf.parent.mkdir(parents=True, exist_ok=True)
        # 清单文件都在 → 通过
        mf.write_text(json.dumps({"files": ["kb", "kb.cmd"]}), encoding="utf-8")
        self.assertTrue(verify.check_install_manifest(self.vault).passed)
        # 清单里有缺失文件 → 失败
        mf.write_text(json.dumps({"files": ["kb", "nope.md"]}), encoding="utf-8")
        item = verify.check_install_manifest(self.vault)
        self.assertFalse(item.passed)
        self.assertIn("nope.md", item.detail)


if __name__ == "__main__":
    unittest.main()
