#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""link_plugin.py — 补链引擎插件。封装 link_engine.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class LinkPlugin(SubprocessPlugin):
    SCRIPT_NAME = "link_engine.py"
    ACTION_MAP = {"suggestions": "suggestions", "apply": "apply"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="link",
            version="1.0",
            plugin_type="kb_module",
            actions=["suggestions", "apply"],
            cli_aliases={"link": "suggestions"},
            description="孤岛补链引擎",
        )
