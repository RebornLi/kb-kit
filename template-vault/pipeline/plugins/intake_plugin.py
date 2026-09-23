#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intake_plugin.py — 收件箱摄入 triage 插件。封装 intake_triage.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class IntakePlugin(SubprocessPlugin):
    SCRIPT_NAME = "intake_triage.py"
    ACTION_MAP = {"review": "review", "apply": "apply"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="intake",
            version="1.0",
            plugin_type="kb_module",
            actions=["review", "apply"],
            cli_aliases={"ingest": "review"},
            description="收件箱摄入 triage",
        )
