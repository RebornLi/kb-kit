#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feedback_plugin.py — 反馈循环插件。封装 feedback_loop.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class FeedbackPlugin(SubprocessPlugin):
    SCRIPT_NAME = "feedback_loop.py"
    ACTION_MAP = {"ingest": "ingest", "hit": "hit", "apply": "apply"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="feedback",
            version="1.0",
            plugin_type="kb_module",
            actions=["ingest", "hit", "apply"],
            cli_aliases={"feedback": "ingest"},
            description="命中信号反馈循环",
        )
