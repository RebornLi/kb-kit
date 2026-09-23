#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_plugin.py — frontmatter 校验插件。封装 validate.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class ValidatePlugin(SubprocessPlugin):
    SCRIPT_NAME = "validate.py"
    ACTION_MAP = {"validate": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="validate",
            version="1.0",
            plugin_type="kb_module",
            actions=["validate"],
            cli_aliases={"validate": "validate"},
            description="frontmatter 校验",
        )
