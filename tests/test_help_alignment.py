#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_help_alignment.py — `kb help` 与路由表对齐守卫（P3-3）。

架构约束：`kb help` 宣传的每个命令都必须能被 `kb_launcher` 解析（COMPAT_MAP 或同名
插件），否则用户按帮助敲命令会得到“未知命令”。本测试防止 help 与实现再次漂移
（历史问题：help 宣传 `kb backup` 但 COMPAT_MAP 无此键）。

运行：
  python3 -m unittest discover -s tests
"""
import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

from kb_launcher import KBLauncher  # noqa: E402

SPECIAL = {"list", "help"}  # 由 run() 直接处理，不走 COMPAT_MAP


class HelpAlignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="kb-help-")
        cls.launcher = KBLauncher(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _help_tools(self) -> set:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.launcher._print_help()
        tools = set()
        for line in buf.getvalue().splitlines():
            m = re.match(r"\s*kb\s+([a-z][a-z0-9-]*)", line)
            if m:
                tools.add(m.group(1))
        return tools

    def test_help_lists_commands(self):
        self.assertTrue(self._help_tools(), "help 应至少列出若干命令")

    def test_advertised_commands_resolve(self):
        self.launcher._ensure_initialized()
        bad = []
        for tool in sorted(self._help_tools()):
            if tool in SPECIAL:
                continue
            resolved = self.launcher._resolve(tool, None)
            plugin = self.launcher.registry.get_plugin(tool)
            if resolved is None and plugin is None:
                bad.append(tool)
        self.assertEqual(bad, [], f"help 宣传但无法解析的命令: {bad}")


if __name__ == "__main__":
    unittest.main()
