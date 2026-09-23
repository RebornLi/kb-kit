#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_plugin.py — 同步插件。封装 sync.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class SyncPlugin(SubprocessPlugin):
    SCRIPT_NAME = "sync.py"
    ACTION_MAP = {
        "dry-run": "dry-run",
        "apply": "apply",
        "rollback": "rollback",
        "history": "history",
        "ingest-any": "ingest-any",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="sync",
            version="1.0",
            plugin_type="kb_module",
            actions=["dry-run", "apply", "rollback", "history", "ingest-any"],
            cli_aliases={"sync": None, "ingest-any": "ingest-any"},
            description="知识同步管道",
        )
