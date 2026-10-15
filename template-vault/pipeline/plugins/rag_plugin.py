#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rag_plugin.py — RAG 检索插件。封装 rag.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class RAGPlugin(SubprocessPlugin):
    SCRIPT_NAME = "rag.py"
    ACTION_MAP = {"index": "index", "query": "query"}
    # `query` 是最高频、只读、延迟敏感的命令：允许进程内执行，省一次解释器启动+import
    # （实测 ~40ms，占 query 端到端的 21%）。`index` 是长任务，省 40ms 无意义，仍走 subprocess。
    IN_PROCESS_ACTIONS = {"query"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="rag",
            version="1.0",
            plugin_type="kb_module",
            actions=["index", "query"],
            cli_aliases={"query": "query", "rag": None},
            description="本地 TF-IDF 语义检索",
        )
