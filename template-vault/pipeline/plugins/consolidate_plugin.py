#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consolidate_plugin.py — 睡眠时整理插件。封装 consolidate.py。

空闲期做重活（近重复/槽位对齐/陈旧/闸门），产出**提议**而非直接改笔记。
由来：睡眠时计算研究（arXiv:2504.13171）—— 记忆管理搬到空闲路径，
同等精度省约 5× test-time 算力；也是本库"只读主流程 + 有写权限的整理流程"的落点。
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class ConsolidatePlugin(SubprocessPlugin):
    SCRIPT_NAME = "consolidate.py"
    ACTION_MAP = {"run": "run", "status": "status"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="consolidate", version="1.0", plugin_type="kb_module",
            actions=["run", "status"], cli_aliases={"consolidate": "run"},
            description="睡眠时整理：空闲期算近重复/冲突/陈旧，只产提议不改笔记",
        )
