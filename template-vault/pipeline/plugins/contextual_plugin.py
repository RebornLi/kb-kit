#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contextual_plugin.py — 兼容垫片（P2-1 第二期：retrieval 域已聚合）。

实现已收进 `retrieval_plugin.RetrievalPlugin`；本文件只保留**原插件名 `contextual`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.contextual_plugin import ContextualPlugin` 不受影响。

新代码请直接用 `kb retrieval contextual.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.retrieval_plugin import RetrievalPlugin


class ContextualPlugin(RetrievalPlugin):
    """`contextual` 的兼容门面 —— 保留原名与 action，实现在 RetrievalPlugin。"""

    SHIM_PLUGIN = "contextual"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="contextual",
            version="1.0",
            plugin_type="kb_module",
            actions=['build', 'show'],
            cli_aliases={'contextual': 'build'},
            description="上下文强化：确定性 CCH（零成本）+ 可选 LLM 定位语（只进索引，不改正文）",
        )
