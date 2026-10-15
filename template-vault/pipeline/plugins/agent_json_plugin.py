#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_json_plugin.py — JSON 记忆源适配器插件。

封装 memory_ingest_json.py，支持 DSH 等 JSON 格式记忆源。
"""
import sys
from pathlib import Path

from plugin_base import PluginBase, PluginContext, PluginMetadata


class AgentJSONPlugin(PluginBase):
    """JSON 记忆源适配器插件 — 封装 memory_ingest_json.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_json",
            version="1.0",
            plugin_type="agent",
            actions=["ingest"],
            description="JSON 记忆源适配器（DSH）",
        )

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        pipeline_dir = str(Path(self._ctx.vault_root) / "pipeline")
        if pipeline_dir not in sys.path:
            sys.path.insert(0, pipeline_dir)
        from memory_ingest_json import ingest

        root = params.get("root", self._ctx.vault_root)
        json_path = params.get("json_path") or params.get("json")
        dry_run = params.get("dry_run", False)
        if not json_path or json_path is True:
            print("❌ 缺少 --json-path 参数（`--json` 是全局布尔旗标，本命令请用 --json-path）",
                  file=sys.stderr)
            return 1
        if not Path(json_path).exists():
            # ingest() 对缺失源只打印一行并返回 0 → 命令「静默成功」（rc=0）。
            print(f"❌ JSON 源不存在: {json_path}", file=sys.stderr)
            return 1
        n = ingest(root, Path(json_path), dry_run)
        # ingest 返回的是**写入条数**，不能直接当进程退出码（成功写 5 条 → rc=5，
        # 破坏 set -e / cron / `kb … && …`）。规范化为 0/1。
        return 0 if not isinstance(n, int) or n >= 0 else 1
