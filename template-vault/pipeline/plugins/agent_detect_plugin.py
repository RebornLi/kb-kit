#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_detect_plugin.py — Agent 探测与注册表管理插件。

封装 agent_registry.py 的探测和注册表操作（detect/list/add/enable/setup/ingest）。
"""
import sys
from pathlib import Path

from plugin_base import PluginBase, PluginContext, PluginMetadata


class AgentDetectPlugin(PluginBase):
    """Agent 探测 + 注册表管理插件 — 封装 agent_registry.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_registry",
            version="1.0",
            plugin_type="agent",
            actions=["detect", "list", "add", "enable", "setup", "ingest"],
            cli_aliases={"agent": None, "ingest-agent": "ingest"},
            description="Agent 探测与注册表管理",
        )

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        pipeline_dir = str(Path(self._ctx.vault_root) / "pipeline")
        if pipeline_dir not in sys.path:
            sys.path.insert(0, pipeline_dir)
        import agent_registry as ar

        root = params.get("root", self._ctx.vault_root)
        out = []

        if action == "detect":
            r = ar.cmd_detect(root, out)
        elif action == "list":
            r = ar.cmd_list(root, out)
        elif action == "setup":
            r = ar.cmd_setup(root, out)
        elif action == "ingest":
            name = params.get("name")
            dry_run = params.get("dry_run", False)
            r = ar.cmd_ingest(root, name, dry_run, out)
        elif action == "add":
            name = params.get("name")
            agent_type = params.get("type")
            if not name or not agent_type:
                print("❌ add 需要 --name 和 --type 参数", file=sys.stderr)
                return 1
            fields = {}
            if params.get("json_path") or params.get("json"):
                fields["sources"] = {"json": params.get("json_path") or params.get("json")}
            if params.get("daily_src"):
                fields.setdefault("sources", {})["daily_src"] = params["daily_src"]
            if params.get("root_src"):
                fields.setdefault("sources", {})["root"] = params["root_src"]
            r = ar.cmd_add(root, name, agent_type, out, **fields)
        elif action == "enable":
            name = params.get("name")
            if not name:
                print("❌ enable 需要 --name 参数", file=sys.stderr)
                return 1
            enabled_str = params.get("enable", "true")
            enabled = enabled_str.lower() not in ("false", "0", "no")
            r = ar.cmd_enable(root, name, enabled, out)
        else:
            print(f"❓ 未知动作: {action}", file=sys.stderr)
            return 1

        if out:
            print("\n".join(out))
        return r
