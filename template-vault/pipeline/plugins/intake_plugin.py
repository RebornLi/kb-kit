#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intake_plugin.py — 收件箱摄入 triage 插件。封装 intake_triage.py。

动作：
  review / apply  → 原样透传（apply 无 --move/--trash 时按脚本语义为 no-op）
  move / trash    → apply 子命令 + 对应布尔标志（路由 / 归档），便于 `kb ingest move|trash`
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class IntakePlugin(SubprocessPlugin):
    SCRIPT_NAME = "intake_triage.py"
    # move/trash 都落到原脚本的 apply 子命令，再由 execute 注入 --move/--trash
    ACTION_MAP = {"review": "review", "apply": "apply", "move": "apply", "trash": "apply"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="intake",
            version="1.0",
            plugin_type="kb_module",
            actions=["review", "apply", "move", "trash"],
            cli_aliases={"ingest": "review"},
            description="收件箱摄入 triage",
        )

    def execute(self, action: str, params: dict) -> int:
        # move/trash = apply + 布尔标志；注入后由基类统一拼 --root/--flag
        if action in ("move", "trash"):
            params = {**params, action: True}
        return super().execute(action, params)
