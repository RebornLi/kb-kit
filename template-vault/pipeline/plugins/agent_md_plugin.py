#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_md_plugin.py — MD 记忆源适配器插件。

封装 memory_ingest.py，支持 OpenClaw / Hermes 等 MD 格式记忆源。
"""
import sys
from pathlib import Path

from plugin_base import PluginBase, PluginContext, PluginMetadata


class AgentMDPlugin(PluginBase):
    """MD 记忆源适配器插件 — 封装 memory_ingest.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_md",
            version="1.0",
            plugin_type="agent",
            actions=["mirror", "sync", "mirror-core", "extract"],
            description="MD 记忆源适配器（OpenClaw/Hermes）",
        )

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        # 延迟 import 原模块（确保 pipeline 目录在 sys.path）
        pipeline_dir = str(Path(self._ctx.vault_root) / "pipeline")
        if pipeline_dir not in sys.path:
            sys.path.insert(0, pipeline_dir)
        import memory_ingest as mi

        root = params.get("root", self._ctx.vault_root)
        agent_name = params.get("agent_name", "openclaw")

        if action == "mirror":
            return mi.mirror(root, params["agent_src"], agent_name)
        elif action == "sync":
            return mi.sync(root, params["agent_src"], agent_name)
        elif action == "mirror-core":
            return mi.mirror_core(root, params["agent_root"], agent_name)
        elif action == "extract":
            return mi.extract(
                root, params["agent_root"],
                float(params.get("density", 0.15)),
                int(params.get("min_chars", 80)),
                params.get("semantic", False),
                agent_name,
            )
        return 1
