#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schema_plugin.py — 知识契约插件。封装 kb_schema.py。

把 reference/kb-schema.json 变成可执行的契约检查（只读）：
  kb schema check    对照契约查偏差（缺字段/越界/溯源断裂/noise 未退检索…）
  kb schema fields   统计 frontmatter 字段使用率，看契约实际覆盖
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class SchemaPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_schema.py"
    ACTION_MAP = {"check": "check", "fields": "fields"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="schema",
            version="1.0",
            plugin_type="kb_module",
            actions=["check", "fields"],
            cli_aliases={"schema": "check"},
            description="知识契约检查（reference/kb-schema.json）：只读对照，产出偏差清单",
        )
