#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_launcher_root.py — `--root` 优先级回归测试（P3-4）。

架构约束：目标库优先级应为 命令行 --root > 环境变量 KB_ROOT > 脚本所在目录。
历史缺陷：kb/kb.cmd 总在末尾追加 `--root "$VAULT"`，_extract_root 取最后一个，
导致用户显式 --root（及 KB_ROOT）被静默忽略。

运行：
  python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

from kb_launcher import _extract_root  # noqa: E402


def _env_without_kb_root():
    return {k: v for k, v in os.environ.items() if k != "KB_ROOT"}


class ExtractRootTest(unittest.TestCase):
    def test_env_kb_root_used_when_no_flag(self):
        with mock.patch.dict(os.environ, {"KB_ROOT": "/env/vault"}, clear=False):
            root, args = _extract_root(["query", "x"])
        self.assertEqual(root, "/env/vault")
        self.assertEqual(args, ["query", "x"], "--root 以外的参数应原样保留")

    def test_explicit_root_overrides_env(self):
        with mock.patch.dict(os.environ, {"KB_ROOT": "/env/vault"}, clear=False):
            root, args = _extract_root(["query", "x", "--root", "/cli/vault"])
        self.assertEqual(root, "/cli/vault", "命令行 --root 应覆盖 KB_ROOT")
        self.assertEqual(args, ["query", "x"], "--root 应被剥离")

    def test_root_equals_form(self):
        with mock.patch.dict(os.environ, {"KB_ROOT": "/env/vault"}, clear=False):
            root, args = _extract_root(["--root=/cli2", "rag", "index"])
        self.assertEqual(root, "/cli2")
        self.assertEqual(args, ["rag", "index"])

    def test_falls_back_to_pipeline_parent(self):
        with mock.patch.dict(os.environ, _env_without_kb_root(), clear=True):
            root, _ = _extract_root(["query"])
        self.assertEqual(Path(root).name, "template-vault",
                         "无 env / 无 --root 时回落到 pipeline 的上一级")


if __name__ == "__main__":
    unittest.main()
