#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_plugin.py — 知识库审计与确认后修复（P5）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class AuditPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_audit.py"
    ACTION_MAP = {"scan": "scan", "fix": "fix", "log": "log"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="audit", version="1.0", plugin_type="kb_module",
            actions=["scan", "fix", "log"],
            cli_aliases={"audit": "scan"},
            description="结构审计（溯源漂移/标签簇内聚/索引一致性/孤儿/死链）+ 确认后修复",
        )
