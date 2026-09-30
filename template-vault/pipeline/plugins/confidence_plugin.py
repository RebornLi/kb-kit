#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""confidence_plugin.py — 复合置信分与原则性遗忘（P3）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class ConfidencePlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_confidence.py"
    ACTION_MAP = {"show": "show", "audit": "audit", "apply": "apply", "decay": "decay"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="confidence", version="1.0", plugin_type="kb_module",
            actions=["show", "audit", "apply", "decay"],
            cli_aliases={"confidence": "audit"},
            description="复合置信分（四因子可审计）+ 原则性遗忘审计（只降层不删文件）",
        )
