#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contradict_plugin.py — 矛盾在环（P3）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class ContradictPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_contradiction2.py"
    ACTION_MAP = {"scan": "scan", "apply": "apply"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="contradict", version="1.0", plugin_type="kb_module",
            actions=["scan", "apply"],
            cli_aliases={"contradict": "scan"},
            description="矛盾在环：主题重合+变更信号 → 冲突标注入人工队列（区分真冲突与近似重复）",
        )
