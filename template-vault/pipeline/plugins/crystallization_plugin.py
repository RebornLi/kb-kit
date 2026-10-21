#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crystallization_plugin.py — 结晶域聚合插件（P2-1 第二期）。

合并 6 个插件：curate / ladder / confidence / contradict / gate / consolidate

与第一期 `OpsPlugin` 的差别：该域内**有 action 撞名**（如 `query` 同时属于
`rag` 与 `compile`），故路由键用 `(plugin, action)` 复合键而非裸 action。
这样**原插件名与原 action 名一个字都不用改**，`COMPAT_MAP` 一条不动，
CLI 契约 100% 不变。旧插件文件改为本类的子类垫片，保留原 meta.name/actions。
"""
from typing import Any, Dict

from plugins._domain_plugin import DomainPlugin


class CrystallizationPlugin(DomainPlugin):
    """知识结晶/反馈阶梯/置信分/矛盾在环/写入门禁/睡眠整理。"""

    DOMAIN = "crystallization"
    _PLUGIN_NAME = "crystallization"

    # (原插件名, 原 action) → (脚本, 原子子命令, 是否进程内快路径)
    _ROUTES: Dict[Any, Any] = {
        ("curate", "plan"): ('curate.py', "plan", False),
        ("curate", "run"): ('curate.py', "run", False),
        ("curate", "verify"): ('curate.py', "verify", False),
        ("curate", "report"): ('curate.py', "report", False),
        ("curate", "review"): ('curate.py', "review", False),
        ("ladder", "status"): ('ladder.py', "status", False),
        ("ladder", "propose"): ('ladder.py', "propose", False),
        ("ladder", "run"): ('ladder.py', "run", False),
        ("ladder", "apply"): ('ladder.py', "apply", False),
        ("ladder", "adjudicate"): ('ladder.py', "adjudicate", False),
        ("ladder", "verdicts"): ('ladder.py', "verdicts", False),
        ("confidence", "show"): ('kb_confidence.py', "show", True),
        ("confidence", "audit"): ('kb_confidence.py', "audit", True),
        ("confidence", "apply"): ('kb_confidence.py', "apply", False),
        ("confidence", "decay"): ('kb_confidence.py', "decay", False),
        ("contradict", "scan"): ('kb_contradiction2.py', "scan", False),
        ("contradict", "apply"): ('kb_contradiction2.py', "apply", False),
        ("gate", "check"): ('gate.py', "check", True),
        ("consolidate", "run"): ('consolidate.py', "run", False),
        ("consolidate", "status"): ('consolidate.py', "status", False),
    }

    # CLI 别名：{命令: (原插件, 原 action)}，值来自各原插件的 cli_aliases
    _ALIASES: Dict[str, Any] = {
        "curate": ("curate", "plan"),
        "ladder": ("ladder", "status"),
        "confidence": ("confidence", "audit"),
        "contradict": ("contradict", "scan"),
        "gate": ("gate", "check"),
        "consolidate": ("consolidate", "run"),
    }

    # 别名值为 None 时（如 `ladder: None`）回落到该插件的默认首个 action
    _DEFAULT_ACTION: Dict[str, str] = {
        "curate": "plan",
        "ladder": "status",
        "confidence": "show",
        "contradict": "scan",
        "gate": "check",
        "consolidate": "run",
    }

    _DESCRIPTIONS = {
        "curate": "知识结晶：日志炼成正典",
        "ladder": "L0–L5 反馈阶梯统一入口",
        "confidence": "复合置信分 + 原则性遗忘审计",
        "contradict": "矛盾在环：冲突标注入人工队列",
        "gate": "写入前质量门禁三态",
        "consolidate": "睡眠时整理：只产提议不改笔记",
    }

