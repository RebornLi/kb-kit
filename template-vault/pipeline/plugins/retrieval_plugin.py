#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retrieval_plugin.py — 检索域聚合插件（P2-1 第二期）。

合并 7 个插件：rag / recall / raw / rerank / contextual / compile / rsi

与第一期 `OpsPlugin` 的差别：该域内**有 action 撞名**（如 `query` 同时属于
`rag` 与 `compile`），故路由键用 `(plugin, action)` 复合键而非裸 action。
这样**原插件名与原 action 名一个字都不用改**，`COMPAT_MAP` 一条不动，
CLI 契约 100% 不变。旧插件文件改为本类的子类垫片，保留原 meta.name/actions。
"""
from typing import Any, Dict

from plugins._domain_plugin import DomainPlugin


class RetrievalPlugin(DomainPlugin):
    """检索/召回/证据层/重排/上下文/查询编译/RSI 探针。"""

    DOMAIN = "retrieval"
    _PLUGIN_NAME = "retrieval"

    # (原插件名, 原 action) → (脚本, 原子子命令, 是否进程内快路径)
    _ROUTES: Dict[Any, Any] = {
        ("rag", "index"): ('rag.py', "index", False),
        ("rag", "query"): ('rag.py', "query", True),
        ("recall", "deck"): ('recall_schedule.py', "deck", True),
        ("recall", "mark"): ('recall_schedule.py', "mark", False),
        ("recall", "status"): ('recall_schedule.py', "status", True),
        ("raw", "show"): ('kb_raw.py', "show", False),
        ("raw", "find"): ('kb_raw.py', "find", False),
        ("raw", "list"): ('kb_raw.py', "list", False),
        ("raw", "path"): ('kb_raw.py', "path", False),
        ("rerank", "rerank"): ('rerank.py', None, False),
        ("contextual", "build"): ('contextual.py', "build", False),
        ("contextual", "show"): ('contextual.py', "show", False),
        ("compile", "query"): ('kb_query.py', "query", False),
        ("compile", "apply"): ('kb_query.py', "apply", False),
        ("compile", "status"): ('kb_query.py', "status", False),
        ("rsi", "probe"): ('kb_rsi.py', None, False),
    }

    # CLI 别名：{命令: (原插件, 原 action)}，值来自各原插件的 cli_aliases
    _ALIASES: Dict[str, Any] = {
        "query": ("rag", "query"),
        "rag": ("rag", None),
        "recall": ("recall", "deck"),
        "raw": ("raw", "list"),
        "contextual": ("contextual", "build"),
        "compile": ("compile", None),
        "rsi": ("rsi", "probe"),
    }

    # 别名值为 None 时（如 `ladder: None`）回落到该插件的默认首个 action
    _DEFAULT_ACTION: Dict[str, str] = {
        "rag": "index",
        "recall": "deck",
        "raw": "show",
        "rerank": "rerank",
        "contextual": "build",
        "compile": "query",
        "rsi": "probe",
    }

    _DESCRIPTIONS = {
        "rag": "本地 TF-IDF 语义检索",
        "recall": "回忆排期与间隔重复",
        "raw": "原记忆（证据层）按需调用",
        "rerank": "检索结果重排序",
        "contextual": "上下文强化",
        "compile": "查询→知识 正回路",
        "rsi": "递归式自我改进只读探针",
    }

