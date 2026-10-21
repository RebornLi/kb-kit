#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_plugin.py — 兼容垫片（P2-1 第二期：crystallization 域已聚合）。

实现已收进 `crystallization_plugin.CrystallizationPlugin`；本文件只保留**原插件名 `gate`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.gate_plugin import GatePlugin` 不受影响。

新代码请直接用 `kb crystallization gate.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.crystallization_plugin import CrystallizationPlugin


class GatePlugin(CrystallizationPlugin):
    """`gate` 的兼容门面 —— 保留原名与 action，实现在 CrystallizationPlugin。"""

    SHIM_PLUGIN = "gate"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="gate",
            version="1.0",
            plugin_type="kb_module",
            actions=['check'],
            cli_aliases={'gate': 'check'},
            description="写入前质量门禁：pass / quarantine / reject 三态 + 来源分级",
        )
