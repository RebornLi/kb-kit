#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""curate_plugin.py — 知识结晶插件。封装 curate.py。

把「日志/碎片」交给空闲的本地 Agent 炼成「可调用的正典」，代码负责接地校验与溯源。
  kb curate plan   只读：选批 + 预估
  kb curate run    跑结晶（默认提案；--apply 写回）
  kb curate verify 离线重校验提案
  kb curate report 进度与质量指标
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class CuratePlugin(SubprocessPlugin):
    SCRIPT_NAME = "curate.py"
    ACTION_MAP = {
        "plan": "plan",
        "run": "run",
        "verify": "verify",
        "report": "report",
        "review": "review",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="curate",
            version="1.1",
            plugin_type="kb_module",
            actions=["plan", "run", "verify", "report", "review"],
            cli_aliases={"curate": "plan"},
            description="知识结晶：空闲本地 Agent 把日志炼成可调用正典（接地校验 + 可溯源）",
        )
