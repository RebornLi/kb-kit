#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contextual_plugin.py — 上下文强化（CCH；LLM 上下文为可选增强）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class ContextualPlugin(SubprocessPlugin):
    SCRIPT_NAME = "contextual.py"
    ACTION_MAP = {"build": "build", "show": "show"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="contextual", version="1.0", plugin_type="kb_module",
            actions=["build", "show"],
            cli_aliases={"contextual": "build"},
            description="上下文强化：确定性 CCH（零成本）+ 可选 LLM 定位语（只进索引，不改正文）",
        )
