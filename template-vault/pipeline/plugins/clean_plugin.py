#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clean_plugin.py — 清洗插件。封装 clean.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class CleanPlugin(SubprocessPlugin):
    SCRIPT_NAME = "clean.py"
    ACTION_MAP = {
        "dry-run": "dry-run",
        "apply": "apply",
        "report": "report",
        "chunk": "chunk",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="clean",
            version="1.0",
            plugin_type="kb_module",
            actions=["dry-run", "apply", "report", "chunk"],
            cli_aliases={"clean": "dry-run"},
            description="知识库清洗与分块",
        )
