#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_plugin.py — 兼容垫片（P2-1 第二期：organization 域已聚合）。

实现已收进 `organization_plugin.OrganizationPlugin`；本文件只保留**原插件名 `clean`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.clean_plugin import CleanPlugin` 不受影响。

新代码请直接用 `kb organization clean.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.organization_plugin import OrganizationPlugin


class CleanPlugin(OrganizationPlugin):
    """`clean` 的兼容门面 —— 保留原名与 action，实现在 OrganizationPlugin。"""

    SHIM_PLUGIN = "clean"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="clean",
            version="1.0",
            plugin_type="kb_module",
            actions=['dry-run', 'apply', 'report', 'chunk', 'refine', 'taxonomy', 'repair', 'repair-headings'],
            cli_aliases={'clean': 'dry-run'},
            description="知识库清洗、分块与历史残留修复",
        )
