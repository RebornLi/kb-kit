#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_plugin.py — 图谱插件。封装 graph.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class GraphPlugin(SubprocessPlugin):
    SCRIPT_NAME = "graph.py"
    ACTION_MAP = {"build": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="graph",
            version="1.0",
            plugin_type="kb_module",
            actions=["build"],
            cli_aliases={"graph": "build"},
            description="知识图谱构建",
        )
