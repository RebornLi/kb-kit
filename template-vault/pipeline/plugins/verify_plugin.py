#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_plugin.py — 一行验收：五项全绿检查。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class VerifyPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_verify.py"
    # 映射为 None：kb_verify.py 没有子命令，不能把 action 当位置参数传
    #   （传了会报 'unrecognized arguments: all'）
    ACTION_MAP = {"all": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="verify", version="1.0", plugin_type="kb_module",
            actions=["all"],
            cli_aliases={"verify": "all"},
            description="一行验收：契约/校验/巡检/探针/单测 五项全绿（退出码 0=全绿）",
        )
