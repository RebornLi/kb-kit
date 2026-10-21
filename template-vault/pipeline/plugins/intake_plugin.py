#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intake_plugin.py — 兼容垫片（P2-1 第二期：ingestion 域已聚合）。

实现已收进 `ingestion_plugin.IngestionPlugin`；本文件只保留**原插件名 `intake`** 与
原 action 列表，使既有调用方零改动：
  · `COMPAT_MAP` 仍指向原名（87 条路由一条未改）；
  · `plugin-config.json` / `kb list` 仍显示原名；
  · 测试里的 `from plugins.intake_plugin import IntakePlugin` 不受影响。

新代码请直接用 `kb ingestion intake.<action>`（复合键入口）。
"""
from plugin_base import PluginMetadata
from plugins.ingestion_plugin import IngestionPlugin


class IntakePlugin(IngestionPlugin):
    """`intake` 的兼容门面 —— 保留原名与 action，实现在 IngestionPlugin。"""

    SHIM_PLUGIN = "intake"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="intake",
            version="1.0",
            plugin_type="kb_module",
            actions=['review', 'apply', 'move', 'trash'],
            cli_aliases={'ingest': 'review'},
            description="收件箱摄入 triage",
        )
