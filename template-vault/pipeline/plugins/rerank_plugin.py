#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rerank_plugin.py — 重排序插件。封装 rerank.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class RerankPlugin(SubprocessPlugin):
    SCRIPT_NAME = "rerank.py"
    ACTION_MAP = {"rerank": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="rerank",
            version="1.0",
            plugin_type="kb_module",
            actions=["rerank"],
            description="检索结果重排序",
        )
