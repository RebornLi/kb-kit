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
        "refine": "refine",
        "taxonomy": "taxonomy",
        "repair": "repair",
        "repair-headings": "repair-headings",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="clean",
            version="1.2",
            plugin_type="kb_module",
            actions=["dry-run", "apply", "report", "chunk", "refine", "taxonomy", "repair", "repair-headings"],
            cli_aliases={"clean": "dry-run"},
            description="知识库清洗、分块与历史残留修复",
        )
