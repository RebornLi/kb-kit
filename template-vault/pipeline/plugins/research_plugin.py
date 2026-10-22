#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""research_plugin.py — 研究层只读入口（P2-3）。

## 为什么需要这个插件

`pipeline/_research/` 下有 18 个模块、约 6900 行，但在此之前**只有
`kb_rsi` 与 `kb_compile` 两个模块经 ladder/adjudicate 动态导入后可达**
（见 README 第 250-252 行的自述）。其余模块的 `main()` 写得完整、
能独立运行，却没有任何 CLI 入口 —— 用户不知道它们存在，也就从没用过。

`tests/test_reachability.py` 允许这种情况（`_research/` 目录本身就是
"内部实验代码"的结构化声明），但**允许存在 ≠ 应该不可见**。

## 边界：只暴露**只读**动作

这是本插件唯一的硬约束。研究层里会写盘的模块（`kb_calibrate` 6 处、
`kb_contradiction` 4 处、`kb_ingest` 1 处、`kb_consistency` 1 处、
`kb_usage mark` 走 `record_hit`）**一律不放进这里**：
它们要么需要人工确认语义，要么会改笔记/状态，不该被当作"看看情况"的命令。

已暴露的四个模块经审计确认零写操作：
  · kb_usage analyze|report|stats —— 注意：**不含 mark**（mark 会提升 importance）
  · kb_fitness                     外部锚定适应度
  · kb_drift                       晋升漂移度量
  · kb_scale                       规模档位与索引层覆盖

## 用法

    kb research                      列出可用子命令（默认）
    kb research usage [analyze|report|stats] [--window 3600]
    kb research fitness [--json]
    kb research drift [--window day|week|month] [--decay]
    kb research scale [bracket --n N | fabric | scale]

新代码请直接用复合键入口 `kb research <module>.<action>`。
"""
from typing import Any, Dict, List

from plugin_base import PluginMetadata
from plugins._domain_plugin import DomainPlugin


class ResearchPlugin(DomainPlugin):
    """研究层只读能力的统一入口。"""

    DOMAIN = "research"
    _PLUGIN_NAME = "research"

    # `DomainPlugin.execute()` 每次调用都会用路由里的脚本名遮蔽这两个类属性，
    # 但 `SubprocessPlugin.execute()` 在**遮蔽之前**就读了 `self.SCRIPT_NAME`
    # 去拼 `script_path`。类属性若为空，`pipeline_dir / ""` 会解析成 `pipeline/`
    # 目录本身：`script_path.exists()` 为真（目录存在），于是走到 `import_module("")`
    # 拿不到 main、回退 subprocess —— 但 `cmd` 里的脚本路径是空目录，
    # 表现为 `unrecognized arguments: report` 这类莫名其妙的 argparse 报错。
    # 给一个真实存在的默认值，让"未遮蔽"这个窗口期也有合理行为。
    SCRIPT_NAME = "_research/kb_usage.py"

    # (原插件名, 原 action) → (脚本, 原子子命令, 是否进程内)
    #   in_proc：只读且轻量 → True（省一次解释器启动）
    _ROUTES: Dict[Any, Any] = {
        ("kb_usage", "analyze"): ('_research/kb_usage.py', "analyze", True),
        ("kb_usage", "report"): ('_research/kb_usage.py', "report", True),
        ("kb_usage", "stats"): ('_research/kb_usage.py', "stats", True),
        ("kb_fitness", "fitness"): ('_research/kb_fitness.py', None, True),
        ("kb_drift", "drift"): ('_research/kb_drift.py', None, True),
        ("kb_scale", "bracket"): ('_research/kb_scale.py', "bracket", True),
        ("kb_scale", "fabric"): ('_research/kb_scale.py', "fabric", True),
        ("kb_scale", "scale"): ('_research/kb_scale.py', "scale", True),
    }

    _ALIASES: Dict[str, Any] = {
        "research": ("kb_usage", "report"),
        "usage": ("kb_usage", "report"),
        "fitness": ("kb_fitness", "fitness"),
        "research-drift": ("kb_drift", "drift"),
        "scale": ("kb_scale", "scale"),
    }

    _DEFAULT_ACTION: Dict[str, str] = {
        "kb_usage": "report",
        "kb_fitness": "fitness",
        "kb_drift": "drift",
        "kb_scale": "scale",
    }

    _DESCRIPTIONS = {
        "kb_usage": "真实使用结果捕获（查询成功率 / 再问率，只读）",
        "kb_fitness": "外部锚定适应度（来源多样性/外部反馈/矛盾/查询成功率）",
        "kb_drift": "晋升漂移度量（内容漂移率 / 出处保留率）",
        "kb_scale": "规模档位与索引层覆盖感知",
    }

    # ── 裸 action 名的消歧（P2-3 配套）────────────────────────────
    # 聚合入口 `kb research <action>` 里的 `<action>` 不带插件名前缀，
    # 而 `DomainPlugin.execute()` 只认复合键或 `SHIM_PLUGIN` 拼出来的键。
    # 本插件不是任何原插件的垫片（`SHIM_PLUGIN is None`），所以裸 action 会
    # 直接落到"不支持动作"。
    #
    # 这里给出**显式**的裸名 → 复合键映射，而不是按 `_ROUTES` 顺序瞎猜：
    #   - 名字唯一的直接用（fitness / drift / bracket / fabric / scale）
    #   - 名字有歧义的按"最常用的那个"固定下来（report/stats/analyze → kb_usage）
    # 未列出的裸名仍然报错，避免静默路由到意料之外的模块。
    _BARE_ACTIONS: Dict[str, Any] = {
        "report": ("kb_usage", "report"),
        "stats": ("kb_usage", "stats"),
        "analyze": ("kb_usage", "analyze"),
        "usage": ("kb_usage", "report"),
        "fitness": ("kb_fitness", "fitness"),
        "drift": ("kb_drift", "drift"),
        "bracket": ("kb_scale", "bracket"),
        "fabric": ("kb_scale", "fabric"),
        "scale": ("kb_scale", "scale"),
    }

    def execute(self, action: str, params: dict) -> int:
        """裸 action 名先映射到复合键，再交给基类按复合键分派。"""
        if "." not in action and action in self._BARE_ACTIONS:
            plug, act = self._BARE_ACTIONS[action]
            action = f"{plug}.{act}"
        return super().execute(action, params)

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="research",
            version="1.0",
            plugin_type="kb_module",
            actions=sorted({a for (_p, a) in self._ROUTES}
                           | {f"{p}.{a}" for (p, a) in self._ROUTES}),
            cli_aliases={"research": "kb_usage.report",
                         "usage": "kb_usage.report",
                         "fitness": "kb_fitness.fitness",
                         "research-drift": "kb_drift.drift",
                         "scale": "kb_scale.scale"},
            description="研究层只读入口：使用信号 / 适应度 / 漂移 / 规模（**不含任何写操作**）",
        )
