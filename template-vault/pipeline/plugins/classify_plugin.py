#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""classify_plugin.py — 分类插件。封装 classify.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class ClassifyPlugin(SubprocessPlugin):
    SCRIPT_NAME = "classify.py"
    ACTION_MAP = {"classify": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="classify",
            version="1.0",
            plugin_type="kb_module",
            actions=["classify"],
            cli_aliases={"classify": "classify"},
            description="笔记自动分类",
        )
