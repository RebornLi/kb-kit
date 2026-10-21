#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_plugin.py — 兼容垫片（P2-1 第二期：organization 域已聚合）。

实现已收进 `organization_plugin.OrganizationPlugin`；本文件只保留**原插件名 `validate`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.validate_plugin import ValidatePlugin` 不受影响。

新代码请直接用 `kb organization validate.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.organization_plugin import OrganizationPlugin


class ValidatePlugin(OrganizationPlugin):
    """`validate` 的兼容门面 —— 保留原名与 action，实现在 OrganizationPlugin。"""

    SHIM_PLUGIN = "validate"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="validate",
            version="1.0",
            plugin_type="kb_module",
            actions=['validate'],
            cli_aliases={'validate': 'validate'},
            description="frontmatter 校验",
        )
