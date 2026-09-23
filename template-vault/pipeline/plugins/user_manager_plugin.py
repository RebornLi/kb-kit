#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""user_manager_plugin.py — 用户管理插件。封装 user_manager.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class UserManagerPlugin(SubprocessPlugin):
    SCRIPT_NAME = "user_manager.py"
    ACTION_MAP = {
        "add": "add",
        "list": "list",
        "remove": "remove",
        "check-perm": "check-perm",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="user_manager",
            version="1.0",
            plugin_type="kb_module",
            actions=["add", "list", "remove", "check-perm"],
            cli_aliases={"user": None},
            description="用户与权限管理",
        )
