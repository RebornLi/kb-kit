#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_plugin.py — 写入前质量门禁（三态）插件。封装 gate.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class GatePlugin(SubprocessPlugin):
    SCRIPT_NAME = "gate.py"
    ACTION_MAP = {"check": "check"}
    IN_PROCESS_ACTIONS = {"check"}            # 只读

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="gate", version="1.0", plugin_type="kb_module",
            actions=["check"], cli_aliases={"gate": "check"},
            description="写入前质量门禁：pass / quarantine / reject 三态 + 来源分级",
        )
