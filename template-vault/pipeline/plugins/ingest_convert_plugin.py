#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_convert_plugin.py — 格式转换插件。封装 ingest_convert.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class IngestConvertPlugin(SubprocessPlugin):
    SCRIPT_NAME = "ingest_convert.py"
    ACTION_MAP = {"convert": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ingest_convert",
            version="1.0",
            plugin_type="kb_module",
            actions=["convert"],
            description="笔记格式转换",
        )
