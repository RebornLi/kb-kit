#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dashboard_plugin.py — 仪表盘插件。封装 dashboard.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class DashboardPlugin(SubprocessPlugin):
    SCRIPT_NAME = "dashboard.py"
    ACTION_MAP = {"generate": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="dashboard",
            version="1.0",
            plugin_type="kb_module",
            actions=["generate"],
            cli_aliases={"dashboard": "generate"},
            description="治理仪表盘生成",
        )
