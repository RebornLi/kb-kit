#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rsi_plugin.py — RSI 只读探针插件。封装 kb_rsi.py。

kb_rsi 只做改进提议（唯一写入：pipeline/.kb_rsi_proposals.jsonl 追加一条运行记录），
不改动任何笔记正文/元数据，故作为只读研究层入口暴露。
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class RSIPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_rsi.py"
    ACTION_MAP = {"probe": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="rsi",
            version="1.0",
            plugin_type="kb_module",
            actions=["probe"],
            cli_aliases={"rsi": "probe"},
            description="递归式自我改进（RSI）只读探针",
        )
