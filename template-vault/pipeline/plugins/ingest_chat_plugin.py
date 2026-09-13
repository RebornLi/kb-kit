#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_chat_plugin.py — 对话摄入插件。封装 ingest_chat.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class IngestChatPlugin(SubprocessPlugin):
    SCRIPT_NAME = "ingest_chat.py"
    ACTION_MAP = {"parse": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ingest_chat",
            version="1.0",
            plugin_type="kb_module",
            actions=["parse"],
            description="对话记录摄入",
        )
