#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`sync` 与 CLI 别名，保证 `kb sync` / `plugin-config.json` / 外部 import
继续可用。新代码请直接用 `ops` 域聚合入口。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class SyncPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="sync",
            version="1.0",
            plugin_type="kb_module",
            actions=["dry-run", "apply", "rollback", "history", "ingest-any"],
            cli_aliases={"sync": None, "ingest-any": "ingest-any"},
            description="知识同步管道",
        )
