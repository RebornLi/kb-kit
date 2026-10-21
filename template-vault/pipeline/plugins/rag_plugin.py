#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rag_plugin.py — 兼容垫片（P2-1 第二期：retrieval 域已聚合）。

实现已收进 `retrieval_plugin.RetrievalPlugin`；本文件只保留**原插件名 `rag`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.rag_plugin import RAGPlugin` 不受影响。

新代码请直接用 `kb retrieval rag.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.retrieval_plugin import RetrievalPlugin


class RAGPlugin(RetrievalPlugin):
    """`rag` 的兼容门面 —— 保留原名与 action，实现在 RetrievalPlugin。"""

    SHIM_PLUGIN = "rag"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="rag",
            version="1.0",
            plugin_type="kb_module",
            actions=['index', 'query'],
            cli_aliases={'query': 'query', 'rag': None},
            description="本地 TF-IDF 语义检索",
        )
