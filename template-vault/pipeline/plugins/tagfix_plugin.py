#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tagfix_plugin.py — 标签越界治理（P6）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class TagFixPlugin(SubprocessPlugin):
    SCRIPT_NAME = "tag_fix.py"
    ACTION_MAP = {"scan": "scan", "fix": "fix"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="tagfix", version="1.0", plugin_type="kb_module",
            actions=["scan", "fix"],
            cli_aliases={"tagfix": "scan"},
            description="标签越界治理：删目录路径型 / 归一别名 / 扩受控词表（默认 dry-run）",
        )
