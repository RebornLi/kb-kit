#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_sync_plugin.py — 记忆同步插件。封装 memory_sync.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class MemorySyncPlugin(SubprocessPlugin):
    SCRIPT_NAME = "memory_sync.py"
    ACTION_MAP = {"review": "review", "promote": "promote"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="memory_sync",
            version="1.0",
            plugin_type="kb_module",
            actions=["review", "promote"],
            description="记忆晋升与五问门禁",
        )
