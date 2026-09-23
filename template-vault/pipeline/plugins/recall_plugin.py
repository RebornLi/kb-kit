#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall_plugin.py — 回忆排期插件。封装 recall_schedule.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class RecallPlugin(SubprocessPlugin):
    SCRIPT_NAME = "recall_schedule.py"
    ACTION_MAP = {"deck": "deck", "mark": "mark", "status": "status"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="recall",
            version="1.0",
            plugin_type="kb_module",
            actions=["deck", "mark", "status"],
            cli_aliases={"recall": "deck"},
            description="回忆排期与间隔重复",
        )
