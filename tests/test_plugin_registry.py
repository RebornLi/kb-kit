#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_plugin_registry.py — 插件发现回归测试（P3-2）。

覆盖：
  1. 同一模块内多个具体 PluginBase 子类都被注册（不再只取首个）
  2. 抽象子类不被注册
  3. 下划线开头的内部辅助模块被跳过（如 _subprocess_plugin.py）
  4. import 进来的具体子类不被重复注册
  5. 发现阶段不产生 WARNING 噪声

运行：
  python3 -m unittest discover -s tests
"""
import logging
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

from plugin_registry import PluginRegistry  # noqa: E402

MULTI = '''\
from plugin_base import PluginBase, PluginMetadata


class AbstractOne(PluginBase):          # 缺 metadata → 抽象，不应注册
    def initialize(self, ctx): pass
    def execute(self, action, params): return 0


class PluginA(PluginBase):
    def metadata(self):
        return PluginMetadata(name="zzz_a", version="1", plugin_type="kb_module", actions=["x"])
    def initialize(self, ctx): pass
    def execute(self, action, params): return 0


class PluginB(PluginBase):
    def metadata(self):
        return PluginMetadata(name="zzz_b", version="1", plugin_type="kb_module", actions=["y"])
    def initialize(self, ctx): pass
    def execute(self, action, params): return 0
'''

IMPORTED = '''\
from zzz_multi import PluginA  # noqa: F401  仅重导出，不应被注册
'''

HELPER = '''\
from plugin_base import PluginBase, PluginMetadata


class PluginC(PluginBase):              # 下划线文件 → 发现阶段跳过
    def metadata(self):
        return PluginMetadata(name="zzz_c", version="1", plugin_type="kb_module", actions=["z"])
    def initialize(self, ctx): pass
    def execute(self, action, params): return 0
'''


class PluginDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="kb-plugreg-"))
        self.plugins = self.base / "plugins"
        self.plugins.mkdir()
        (self.base / "reference").mkdir()
        (self.plugins / "zzz_multi.py").write_text(MULTI, encoding="utf-8")
        (self.plugins / "zzz_imported.py").write_text(IMPORTED, encoding="utf-8")
        (self.plugins / "_zzz_helper.py").write_text(HELPER, encoding="utf-8")

    def tearDown(self):
        for name in ("zzz_multi", "zzz_imported", "_zzz_helper"):
            sys.modules.pop(name, None)
        shutil.rmtree(self.base, ignore_errors=True)

    def test_all_concrete_plugins_registered_and_no_noise(self):
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        logger = logging.getLogger("kb.registry")
        logger.addHandler(handler)
        try:
            reg = PluginRegistry(str(self.plugins), str(self.base),
                                 config_path=str(self.base / "reference" / "plugin-config.json"))
            reg.discover()
        finally:
            logger.removeHandler(handler)

        names = {meta.name for meta, _status, _err in reg.list_plugins()}
        self.assertIn("zzz_a", names, "模块内第一个具体插件应注册")
        self.assertIn("zzz_b", names, "模块内第二个具体插件也应注册（不只取首个）")
        self.assertNotIn("zzz_c", names, "下划线开头的辅助模块不应被加载")

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        self.assertEqual(warnings, [], f"发现阶段不应有 WARNING 噪声: {warnings}")


if __name__ == "__main__":
    unittest.main()
