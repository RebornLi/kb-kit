#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingestion_plugin.py — 摄入域聚合插件（P2-1 第二期）。

合并 8 个插件：intake / feedback / ingest_chat / ingest_convert / agent_registry / agent_json / agent_md / agent_sqlite

与第一期 `OpsPlugin` 的差别：该域内**有 action 撞名**（如 `query` 同时属于
`rag` 与 `compile`），故路由键用 `(plugin, action)` 复合键而非裸 action。
这样**原插件名与原 action 名一个字都不用改**，`COMPAT_MAP` 一条不动，
CLI 契约 100% 不变。旧插件文件改为本类的子类垫片，保留原 meta.name/actions。
"""
from typing import Any, Dict

from plugins._domain_plugin import DomainPluginWithDelegates


class IngestionPlugin(DomainPluginWithDelegates):
    """收件箱分诊/反馈循环/对话摄入/格式转换/Agent 注册表与记忆源适配器。"""

    DOMAIN = "ingestion"
    _PLUGIN_NAME = "ingestion"

    # (原插件名, 原 action) → (脚本, 原子子命令, 是否进程内快路径)
    _ROUTES: Dict[Any, Any] = {
        ("intake", "review"): ('intake_triage.py', "review", False),
        ("intake", "apply"): ('intake_triage.py', "apply", False),
        ("intake", "move"): ('intake_triage.py', "apply", False),
        ("intake", "trash"): ('intake_triage.py', "apply", False),
        ("feedback", "ingest"): ('feedback_loop.py', "ingest", False),
        ("feedback", "hit"): ('feedback_loop.py', "hit", False),
        ("feedback", "apply"): ('feedback_loop.py', "apply", False),
        ("ingest_chat", "parse"): ('ingest_chat.py', None, False),
        ("ingest_convert", "convert"): ('ingest_convert.py', None, False),
        # agent_registry: 自定义 execute()（非 SubprocessPlugin），见下方 _in_process_override
        # agent_json: 自定义 execute()（非 SubprocessPlugin），见下方 _in_process_override
        # agent_md: 自定义 execute()（非 SubprocessPlugin），见下方 _in_process_override
        # agent_sqlite: 自定义 execute()（非 SubprocessPlugin），见下方 _in_process_override
    }

    # 这 4 个原插件用自定义 execute() 直接调引擎函数（无 SCRIPT_NAME）。
    # 聚合类里保留"交给原插件类处理"的分派，避免把它们的逻辑搬进来。
    CUSTOM_DELEGATES: Dict[Any, str] = {
        ("agent_registry", "detect"): "agent_registry",
        ("agent_registry", "list"): "agent_registry",
        ("agent_registry", "add"): "agent_registry",
        ("agent_registry", "enable"): "agent_registry",
        ("agent_registry", "setup"): "agent_registry",
        ("agent_registry", "ingest"): "agent_registry",
        ("agent_json", "ingest"): "agent_json",
        # 兼容：agent_json 的 `metadata().actions` 声明是 "ingest"，但原实现
        # 的 execute() 从不检查 action 名（直接跑 JSON 摄入），而测试与历史调用方
        # 写的是 `execute("json", …)`。两个名字都路由到同一实现，避免这次合并
        # 把"能用"变成"不支持动作"。
        ("agent_json", "json"): "agent_json",
        ("agent_md", "mirror"): "agent_md",
        ("agent_md", "sync"): "agent_md",
        ("agent_md", "mirror-core"): "agent_md",
        ("agent_md", "extract"): "agent_md",
        ("agent_sqlite", "ingest"): "agent_sqlite",
    }

    # 原 `IntakePlugin.execute` 的额外行为：move/trash 都走 `apply` 子命令，
    # 但必须额外注入 --move / --trash 旗标才能区分语义。照抄 ACTION_MAP 会丢这段，
    # 表现为 `kb ingest move` 静默不生效（返回 0 但什么都没挪）。
    _ACTION_HOOKS = {
        ("intake", "move"): lambda p: {**p, "move": True},
        ("intake", "trash"): lambda p: {**p, "trash": True},
    }

    # CLI 别名：{命令: (原插件, 原 action)}，值来自各原插件的 cli_aliases
    _ALIASES: Dict[str, Any] = {
        "ingest": ("intake", "review"),
        "feedback": ("feedback", "ingest"),
        "agent": ("agent_registry", None),
        "ingest-agent": ("agent_registry", "ingest"),
    }

    # 别名值为 None 时（如 `ladder: None`）回落到该插件的默认首个 action
    _DEFAULT_ACTION: Dict[str, str] = {
        "intake": "review",
        "feedback": "ingest",
        "ingest_chat": "parse",
        "ingest_convert": "convert",
        "agent_registry": "detect",
        "agent_json": "ingest",
        "agent_md": "mirror",
        "agent_sqlite": "ingest",
    }

    _DESCRIPTIONS = {
        "intake": "收件箱摄入 triage",
        "feedback": "命中信号反馈循环",
        "ingest_chat": "对话记录摄入",
        "ingest_convert": "笔记格式转换",
        "agent_registry": "Agent 探测与注册表管理",
        "agent_json": "JSON 记忆源适配器（DSH）",
        "agent_md": "MD 记忆源适配器（OpenClaw/Hermes）",
        "agent_sqlite": "SQLite 记忆源适配器（Codex）",
    }

