#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcheck_plugin.py — 健康巡检插件。封装 kb-healthcheck.py。

注意：原模块名含连字符（kb-healthcheck.py），不能直接 import，
通过 subprocess 调用。
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class HealthcheckPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb-healthcheck.py"
    ACTION_MAP = {"check": None}  # kb-healthcheck.py 无子命令

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="healthcheck",
            version="1.0",
            plugin_type="kb_module",
            actions=["check"],
            cli_aliases={"healthcheck": "check", "kb-healthcheck": "check"},
            description="健康巡检（死链/缺字段/空目录/超长）",
        )
