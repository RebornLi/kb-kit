#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile_plugin.py — 查询→知识 正回路插件。封装 kb_query.py（提案制，人工在环）。

kb_query.query 只写提案到 pipeline/.kb_query_proposals.jsonl，绝不自动写库；
apply 才写入 KB（写操作）。故 CLI 层保留 query/apply/status 三动作。
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class CompilePlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_query.py"
    ACTION_MAP = {"query": "query", "apply": "apply", "status": "status"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="compile",
            version="1.0",
            plugin_type="kb_module",
            actions=["query", "apply", "status"],
            cli_aliases={"compile": None},
            description="查询→知识 正回路（写提案，人工在环 apply）",
        )
