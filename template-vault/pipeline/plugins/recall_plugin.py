#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall_plugin.py — 兼容垫片（P2-1 第二期：retrieval 域已聚合）。

实现已收进 `retrieval_plugin.RetrievalPlugin`；本文件只保留**原插件名 `recall`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.recall_plugin import RecallPlugin` 不受影响。

新代码请直接用 `kb retrieval recall.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.retrieval_plugin import RetrievalPlugin


class RecallPlugin(RetrievalPlugin):
    """`recall` 的兼容门面 —— 保留原名与 action，实现在 RetrievalPlugin。"""

    SHIM_PLUGIN = "recall"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="recall",
            version="1.0",
            plugin_type="kb_module",
            actions=['deck', 'mark', 'status'],
            cli_aliases={'recall': 'deck'},
            description="回忆排期与间隔重复",
        )
