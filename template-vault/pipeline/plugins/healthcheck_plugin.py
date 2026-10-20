#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcheck_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`healthcheck` 与 CLI 别名，保证 `kb healthcheck` / `kb kb-healthcheck` /
`plugin-config.json` / 外部 import 继续可用。新代码请直接用 `ops` 域聚合入口。

注意：原模块名含连字符（kb-healthcheck.py），不能直接 import，
`check` 只读走进程内快路径（由 OpsPlugin._ROUTES 的 in_process 字段承载）。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class HealthcheckPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="healthcheck",
            version="1.0",
            plugin_type="kb_module",
            actions=["check"],
            cli_aliases={"healthcheck": "check", "kb-healthcheck": "check"},
            description="健康巡检（死链/缺字段/空目录/超长）",
        )
