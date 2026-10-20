#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dashboard_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`dashboard` 与 CLI 别名，保证 `kb dashboard` / `plugin-config.json` /
外部 import 继续可用。新代码请直接用 `ops` 域聚合入口。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class DashboardPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="dashboard",
            version="1.0",
            plugin_type="kb_module",
            actions=["generate"],
            cli_aliases={"dashboard": "generate"},
            description="治理仪表盘生成",
        )
