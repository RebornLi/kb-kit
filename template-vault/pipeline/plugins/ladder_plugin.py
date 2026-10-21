#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ladder_plugin.py — 兼容垫片（P2-1 第二期：crystallization 域已聚合）。

实现已收进 `crystallization_plugin.CrystallizationPlugin`；本文件只保留**原插件名 `ladder`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.ladder_plugin import LadderPlugin` 不受影响。

新代码请直接用 `kb crystallization ladder.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.crystallization_plugin import CrystallizationPlugin


class LadderPlugin(CrystallizationPlugin):
    """`ladder` 的兼容门面 —— 保留原名与 action，实现在 CrystallizationPlugin。"""

    SHIM_PLUGIN = "ladder"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ladder",
            version="1.0",
            plugin_type="kb_module",
            actions=['status', 'propose', 'run', 'apply', 'adjudicate', 'verdicts'],
            cli_aliases={'ladder': 'status'},
            description="L0–L5 反馈阶梯：状态 / 产出提案 / 本地模型自主裁决 / 人工落地",
        )
