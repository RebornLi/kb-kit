#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_readme_plugin_list.py — README 与实现的一致性守卫。

为什么需要它：README 的「插件清单」曾写「KB 模块（24）」并只列 22 个，而实测是 32 个；
「命令参考」漏了 13 个已实现的工具。这类漂移不会让任何测试变红，只会让使用者找不到功能。
本测试把 README 绑到**实现**上：

  1. 插件清单必须覆盖注册中心里真实发现的每个插件（且分类正确）；
  2. 声明的数量必须与实际一致；
  3. 命令参考必须提到 `COMPAT_MAP` 里的每个工具；
  4. 清单里不得出现不存在的插件名（防手写臆造）。

运行：python3 -m unittest discover -s tests
"""
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template-vault"
PIPELINE = TEMPLATE / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))

from kb_launcher import COMPAT_MAP          # noqa: E402
from plugin_base import PluginContext       # noqa: E402
from plugin_registry import PluginRegistry  # noqa: E402

# 插件清单段里允许出现的非插件记号（分类小标题、路径、「实测 N 个」等）
NON_PLUGIN_TOKENS = {
    "kb", "list", "pipeline", "plugins", "tests", "test_readme_plugin_list",
    "py", "json", "md", "example", "readme",
}


def _readme() -> str:
    return (REPO / "README.md").read_text(encoding="utf-8")


def real_plugins():
    """从注册中心实测插件（返回 (agent 名单, kb_module 名单)）。"""
    reg = PluginRegistry(str(PIPELINE / "plugins"), str(TEMPLATE),
                         str(TEMPLATE / "reference" / "plugin-config.json"))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        reg.discover()
        reg.initialize_all(PluginContext(vault_root=str(TEMPLATE)))
        agent, kb = [], []
        for name, e in sorted(reg._registry.items()):
            (agent if e.metadata.type == "agent" else kb).append(name)
    return agent, kb


def plugin_section(text: str) -> str:
    m = re.search(r"### 插件清单.*?(?=\n---)", text, re.S)
    return m.group(0) if m else ""


class ReadmePluginListTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent, cls.kb = real_plugins()
        cls.text = _readme()
        cls.section = plugin_section(cls.text)

    def test_section_exists(self):
        self.assertTrue(self.section, "README 里找不到「插件清单」小节")

    def test_every_real_plugin_is_listed(self):
        missing = [n for n in (self.agent + self.kb) if f"`{n}`" not in self.section]
        self.assertEqual(missing, [],
                         f"README 插件清单漏掉了实际存在的插件：{missing}")

    def test_no_phantom_plugin_is_listed(self):
        """清单里不得出现不存在的插件名（历史上手写清单会臆造/留旧名）。"""
        tokens = set(re.findall(r"`([a-z][a-z0-9_]*)`", self.section))
        unknown = sorted(tokens - set(self.agent) - set(self.kb) - NON_PLUGIN_TOKENS)
        self.assertEqual(unknown, [], f"README 插件清单里出现了不存在的插件：{unknown}")

    def test_declared_counts_match_reality(self):
        self.assertIn(f"（{len(self.agent)}）", self.section,
                      f"README 未标出 Agent 适配器实际数量 {len(self.agent)}")
        self.assertIn(f"（{len(self.kb)}）", self.section,
                      f"README 未标出 KB 模块实际数量 {len(self.kb)}")

    def test_section_covers_all_kb_modules(self):
        """KB 模块那一段（不只是整个小节）要逐个列出。"""
        m = re.search(r"KB 模块（\d+）(.*)", self.section, re.S)
        self.assertTrue(m, "README 里找不到「KB 模块（N）」小节")
        body = m.group(1)
        missing = [n for n in self.kb if f"`{n}`" not in body]
        self.assertEqual(missing, [], f"KB 模块清单漏列：{missing}")


class ReadmeCommandCoverageTest(unittest.TestCase):
    def test_every_tool_is_documented(self):
        text = _readme()
        tools = sorted({t for t, _ in COMPAT_MAP})
        missing = [t for t in tools if not re.search(r"`kb " + re.escape(t) + r"\b", text)]
        self.assertEqual(missing, [],
                         f"README 命令参考没提到这些已实现的工具：{missing}")

    def test_documented_counts_are_not_hardcoded_stale(self):
        """README 里不应再出现当年那种写死的错误数量。"""
        text = _readme()
        self.assertNotIn("KB 模块（24）", text)
        self.assertNotIn("（66 条", text)


if __name__ == "__main__":
    unittest.main()
