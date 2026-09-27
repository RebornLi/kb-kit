#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backup_plugin.py — 全量快照插件。封装 backup.py（跨平台，替代 backup_now.sh）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class BackupPlugin(SubprocessPlugin):
    SCRIPT_NAME = "backup.py"
    # action "backup" 无子命令，类型由位置参数/--kind 传入
    ACTION_MAP = {"backup": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="backup",
            version="1.0",
            plugin_type="kb_module",
            actions=["backup"],
            cli_aliases={"backup": "backup"},
            description="全量快照（zip + sha256，跨平台）",
        )
