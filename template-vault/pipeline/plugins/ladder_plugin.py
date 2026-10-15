#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ladder_plugin.py — L0–L5 反馈阶梯入口插件。封装 ladder.py。

为什么这个插件必须存在：ladder.py 的 docstring 记录了那 26% 代码曾经的处境 ——
L0–L5 六个模块合计 6743 行（占 pipeline 25.9%），却**没有任何插件**，
于是 `kb l4`/`kb adaptretrieve`/`kb retriage` 全是「❓ 未知命令」，
用户敲不出来、调度器也无从调用，生产库里这条阶梯的全部状态文件都不存在。
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class LadderPlugin(SubprocessPlugin):
    SCRIPT_NAME = "ladder.py"
#   注：adjudicate / verdicts 实际由 adjudicate.py 实现，ladder.action() 会转发。
    ACTION_MAP = {"status": "status", "propose": "propose", "run": "run",
                  "apply": "apply", "adjudicate": "adjudicate", "verdicts": "verdicts"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ladder",
            version="1.0",
            plugin_type="kb_module",
            actions=["status", "propose", "run", "apply", "adjudicate", "verdicts"],
            cli_aliases={"ladder": "status"},
            description="L0–L5 反馈阶梯：状态 / 产出提案 / 本地模型自主裁决 / 人工落地",
        )
