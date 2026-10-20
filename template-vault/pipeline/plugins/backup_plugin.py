#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backup_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`backup` 与 CLI 别名，保证 `kb backup` / `plugin-config.json` / 外部 import
继续可用。新代码请直接用 `ops` 域聚合入口。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class BackupPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="backup",
            version="1.0",
            plugin_type="kb_module",
            actions=["backup"],
            cli_aliases={"backup": "backup"},
            description="全量快照（zip + sha256，跨平台）",
        )
