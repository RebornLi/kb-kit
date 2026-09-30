#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_plugin.py — RAGAS 式四指标评测（P4）。"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class EvalPlugin(SubprocessPlugin):
    SCRIPT_NAME = "kb_eval_judge.py"
    ACTION_MAP = {"run": "run", "gold": "gold", "compare": "compare"}

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="eval", version="1.0", plugin_type="kb_module",
            actions=["run", "gold", "compare"],
            cli_aliases={"eval": "run"},
            description="RAGAS 式四指标（faithfulness/relevancy/precision/recall，本地裁判）",
        )
