#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic_chunk_plugin.py — 语义分块插件。封装 semantic_chunk.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class SemanticChunkPlugin(SubprocessPlugin):
    SCRIPT_NAME = "semantic_chunk.py"
    ACTION_MAP = {"chunk": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="semantic_chunk",
            version="1.0",
            plugin_type="kb_module",
            actions=["chunk"],
            description="语义分块",
        )
