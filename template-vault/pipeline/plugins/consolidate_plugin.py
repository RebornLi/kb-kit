#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consolidate_plugin.py — 兼容垫片（P2-1 第二期：crystallization 域已聚合）。

实现已收进 `crystallization_plugin.CrystallizationPlugin`；本文件只保留**原插件名 `consolidate`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.consolidate_plugin import ConsolidatePlugin` 不受影响。

新代码请直接用 `kb crystallization consolidate.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.crystallization_plugin import CrystallizationPlugin


class ConsolidatePlugin(CrystallizationPlugin):
    """`consolidate` 的兼容门面 —— 保留原名与 action，实现在 CrystallizationPlugin。"""

    SHIM_PLUGIN = "consolidate"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="consolidate",
            version="1.0",
            plugin_type="kb_module",
            actions=['run', 'status'],
            cli_aliases={'consolidate': 'run'},
            description="睡眠时整理：空闲期算近重复/冲突/陈旧，只产提议不改笔记",
        )
