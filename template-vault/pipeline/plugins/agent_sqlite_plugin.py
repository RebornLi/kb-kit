#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_sqlite_plugin.py — SQLite 记忆源适配器插件。

封装 memory_ingest_sqlite.py，支持 Codex 等 SQLite 格式记忆源。
"""
import sys
from pathlib import Path

from plugin_base import PluginBase, PluginContext, PluginMetadata


class AgentSQLitePlugin(PluginBase):
    """SQLite 记忆源适配器插件 — 封装 memory_ingest_sqlite.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_sqlite",
            version="1.0",
            plugin_type="agent",
            actions=["ingest"],
            description="SQLite 记忆源适配器（Codex）",
        )

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        pipeline_dir = str(Path(self._ctx.vault_root) / "pipeline")
        if pipeline_dir not in sys.path:
            sys.path.insert(0, pipeline_dir)
        from memory_ingest_sqlite import ingest

        root = params.get("root", self._ctx.vault_root)
        data_dir = params.get("data_dir")
        dry_run = params.get("dry_run", False)
        if not data_dir:
            print("❌ 缺少 --data-dir 参数", file=sys.stderr)
            return 1
        return ingest(root, Path(data_dir), dry_run)
