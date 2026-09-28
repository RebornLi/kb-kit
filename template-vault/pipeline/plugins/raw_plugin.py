#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""raw_plugin.py — 原记忆可达性插件。封装 kb_raw.py。

知识层只放结晶后的干净内容，本插件负责「必要时调用原记忆文件」：
  kb raw show <ID>   取原文真实内容
  kb raw find "词"   在证据层原文里全文搜索
  kb raw list        列出证据层清单
  kb raw path <ID>   只解析路径（管道/Agent 消费）
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class RawPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_raw.py"
    ACTION_MAP = {
        "show": "show",
        "find": "find",
        "list": "list",
        "path": "path",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="raw",
            version="1.0",
            plugin_type="kb_module",
            actions=["show", "find", "list", "path"],
            cli_aliases={"raw": "list"},
            description="原记忆（证据层）按需调用：show/find/list/path",
        )
