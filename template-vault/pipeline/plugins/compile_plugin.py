#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile_plugin.py — 兼容垫片（P2-1 第二期：retrieval 域已聚合）。

实现已收进 `retrieval_plugin.RetrievalPlugin`；本文件只保留**原插件名 `compile`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.compile_plugin import CompilePlugin` 不受影响。

新代码请直接用 `kb retrieval compile.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.retrieval_plugin import RetrievalPlugin


class CompilePlugin(RetrievalPlugin):
    """`compile` 的兼容门面 —— 保留原名与 action，实现在 RetrievalPlugin。"""

    SHIM_PLUGIN = "compile"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="compile",
            version="1.0",
            plugin_type="kb_module",
            actions=['query', 'apply', 'status'],
            cli_aliases={'compile': None},
            description="查询→知识 正回路（写提案，人工在环 apply）",
        )
