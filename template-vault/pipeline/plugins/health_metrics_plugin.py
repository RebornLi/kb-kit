#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""health_metrics_plugin.py — 健康度量插件。封装 kb_health.py。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class HealthMetricsPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_health.py"
    ACTION_MAP = {"metrics": None}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="health_metrics",
            version="1.0",
            plugin_type="kb_module",
            actions=["metrics"],
            cli_aliases={"health-metrics": "metrics"},
            description="健康度量指标",
        )
