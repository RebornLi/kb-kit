#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`audit` 与 CLI 别名，保证 `kb audit` / `plugin-config.json` / 外部 import
继续可用。新代码请直接用 `ops` 域聚合入口。

`fix` 是写动作，仍走 subprocess；`scan`/`log` 只读，保留进程内快路径
（由 OpsPlugin._ROUTES 的 in_process 字段承载）。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class AuditPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="audit", version="1.0", plugin_type="kb_module",
            actions=["scan", "fix", "log"],
            cli_aliases={"audit": "scan"},
            description="结构审计（溯源漂移/标签簇内聚/索引一致性/孤儿/死链）+ 确认后修复",
        )
