#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""organization_plugin.py — 整理域聚合插件（P2-1 第二期）。

合并 8 个插件：clean / schema / link / tagfix / classify / graph / semantic_chunk / validate

与第一期 `OpsPlugin` 的差别：该域内**有 action 撞名**（如 `query` 同时属于
`rag` 与 `compile`），故路由键用 `(plugin, action)` 复合键而非裸 action。
这样**原插件名与原 action 名一个字都不用改**，`COMPAT_MAP` 一条不动，
CLI 契约 100% 不变。旧插件文件改为本类的子类垫片，保留原 meta.name/actions。
"""
from typing import Any, Dict

from plugins._domain_plugin import DomainPlugin


class OrganizationPlugin(DomainPlugin):
    """清洗/契约校验/补链/标签治理/分类/图谱/语义分块/frontmatter 校验。"""

    DOMAIN = "organization"
    _PLUGIN_NAME = "organization"

    # (原插件名, 原 action) → (脚本, 原子子命令, 是否进程内快路径)
    _ROUTES: Dict[Any, Any] = {
        ("clean", "dry-run"): ('clean.py', "dry-run", False),
        ("clean", "apply"): ('clean.py', "apply", False),
        ("clean", "report"): ('clean.py', "report", False),
        ("clean", "chunk"): ('clean.py', "chunk", False),
        ("clean", "refine"): ('clean.py', "refine", False),
        ("clean", "taxonomy"): ('clean.py', "taxonomy", False),
        ("clean", "repair"): ('clean.py', "repair", False),
        ("clean", "repair-headings"): ('clean.py', "repair-headings", False),
        ("schema", "check"): ('kb_schema.py', "check", True),
        ("schema", "fields"): ('kb_schema.py', "fields", True),
        ("schema", "bitemporal"): ('kb_schema.py', "bitemporal", False),
        ("link", "suggestions"): ('link_engine.py', "suggestions", False),
        ("link", "apply"): ('link_engine.py', "apply", False),
        ("link", "moc"): ('link_engine.py', "moc", False),
        ("tagfix", "scan"): ('tag_fix.py', "scan", False),
        ("tagfix", "fix"): ('tag_fix.py', "fix", False),
        ("classify", "classify"): ('classify.py', None, False),
        ("graph", "build"): ('graph.py', None, False),
        ("semantic_chunk", "chunk"): ('semantic_chunk.py', None, False),
        ("validate", "validate"): ('validate.py', None, True),
    }

    # CLI 别名：{命令: (原插件, 原 action)}，值来自各原插件的 cli_aliases
    _ALIASES: Dict[str, Any] = {
        "clean": ("clean", "dry-run"),
        "schema": ("schema", "check"),
        "link": ("link", "suggestions"),
        "tagfix": ("tagfix", "scan"),
        "classify": ("classify", "classify"),
        "graph": ("graph", "build"),
        "validate": ("validate", "validate"),
    }

    # 别名值为 None 时（如 `ladder: None`）回落到该插件的默认首个 action
    _DEFAULT_ACTION: Dict[str, str] = {
        "clean": "dry-run",
        "schema": "check",
        "link": "suggestions",
        "tagfix": "scan",
        "classify": "classify",
        "graph": "build",
        "semantic_chunk": "chunk",
        "validate": "validate",
    }

    _DESCRIPTIONS = {
        "clean": "知识库清洗、分块与历史残留修复",
        "schema": "知识契约检查",
        "link": "孤岛补链引擎",
        "tagfix": "标签越界治理",
        "classify": "笔记自动分类",
        "graph": "知识图谱构建",
        "semantic_chunk": "语义分块",
        "validate": "frontmatter 校验",
    }

