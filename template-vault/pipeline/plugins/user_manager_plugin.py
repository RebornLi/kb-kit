#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""user_manager_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`user_manager` 与 CLI 别名，保证 `kb user` / `plugin-config.json` /
外部 import 继续可用。新代码请直接用 `ops` 域聚合入口。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class UserManagerPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="user_manager",
            version="1.0",
            plugin_type="kb_module",
            actions=["add", "list", "remove", "check-perm"],
            cli_aliases={"user": None},
            description="用户与权限管理",
        )
