#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schema_plugin.py — 兼容垫片（P2-1 第二期：organization 域已聚合）。

实现已收进 `organization_plugin.OrganizationPlugin`；本文件只保留**原插件名 `schema`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.schema_plugin import SchemaPlugin` 不受影响。

新代码请直接用 `kb organization schema.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.organization_plugin import OrganizationPlugin


class SchemaPlugin(OrganizationPlugin):
    """`schema` 的兼容门面 —— 保留原名与 action，实现在 OrganizationPlugin。"""

    SHIM_PLUGIN = "schema"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="schema",
            version="1.0",
            plugin_type="kb_module",
            actions=['check', 'fields', 'bitemporal'],
            cli_aliases={'schema': 'check'},
            description="知识契约检查（reference/kb-schema.json）：只读对照，产出偏差清单",
        )
