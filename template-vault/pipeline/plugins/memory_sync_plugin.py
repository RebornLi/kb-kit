#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_sync_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`memory_sync` 与 CLI 别名，保证 `kb memory-sync` / `plugin-config.json` /
外部 import 继续可用。新代码请直接用 `ops` 域聚合入口。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class MemorySyncPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="memory_sync",
            version="1.0",
            plugin_type="kb_module",
            actions=["review", "promote"],
            description="记忆晋升与五问门禁",
        )
