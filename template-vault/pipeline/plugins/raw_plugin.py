#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""raw_plugin.py — 兼容垫片（P2-1 第二期：retrieval 域已聚合）。

实现已收进 `retrieval_plugin.RetrievalPlugin`；本文件只保留**原插件名 `raw`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.raw_plugin import RawPlugin` 不受影响。

新代码请直接用 `kb retrieval raw.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.retrieval_plugin import RetrievalPlugin


class RawPlugin(RetrievalPlugin):
    """`raw` 的兼容门面 —— 保留原名与 action，实现在 RetrievalPlugin。"""

    SHIM_PLUGIN = "raw"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="raw",
            version="1.0",
            plugin_type="kb_module",
            actions=['show', 'find', 'list', 'path'],
            cli_aliases={'raw': 'list'},
            description="原记忆（证据层）按需调用：show/find/list/path",
        )
