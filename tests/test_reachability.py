#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reachability.py — pipeline 模块可达性守卫（P3-1）。

架构约束：pipeline/*.py 要么被接线（插件封装 / COMPAT_MAP / 脚本调用 / 被可达模块
import），要么在 INTERNAL_MODULES 里被显式声明为“内部研究层/库”。本测试防止再出现
“写了却没入口、也没人声明”的静默死代码。

运行：
  python3 -m unittest discover -s tests
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
PLUGINS = PIPELINE / "plugins"
SCRIPTS = REPO / "template-vault" / "scripts"

# 内部研究层/库：不提供 CLI 入口，或被其它模块作为库 import。
# 新增条目必须写明理由。
INTERNAL_MODULES: dict[str, str] = {
    "kb_adaptretrieve.py": "检索自适应研究层（被 kb_retriage 引用）",
    "kb_calibrate.py": "RSI 校准（合成/拟合），无稳定 CLI",
    "kb_claim.py": "知识主张校验库（被 kb_eval/kb_usage 引用）",
    "kb_consistency.py": "一致性审计，研究层",
    "kb_contradiction.py": "矛盾检测（被 kb_fitness 引用）",
    "kb_drift.py": "漂移度量，研究层",
    "kb_engine.py": "RSI 执行层（被 kb_contradiction/kb_scale 引用）",
    "kb_eval.py": "RSI 评测，研究层",
    "kb_fitness.py": "适应度函数库（被 RSI 层引用）",
    "kb_ingest.py": "外部资料抓取（--url/--file），无稳定 CLI 契约",
    "kb_l4.py": "反馈阶梯 L4（人工裁判层），研究层",
    "kb_l5.py": "反馈阶梯 L5（训练层），研究层",
    "kb_meta.py": "反馈阶梯 L3 元调参，研究层",
    "kb_retriage.py": "再分诊研究层（与 kb_adaptretrieve 关联）",
    "kb_scale.py": "规模压测，开发工具",
    "kb_schema_migrate.py": "schema 迁移一次性脚本",
    "kb_selfweight.py": "反馈阶梯 L2 自我加权，研究层",
    "kb_types.py": "类型定义库",
    "kb_usage.py": "使用度量库（被 RSI 层引用）",
}

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def pipeline_modules() -> set:
    return {p.name for p in PIPELINE.glob("*.py") if not p.name.startswith("__")}


def plugin_scripts() -> set:
    out = set()
    for p in PLUGINS.glob("*.py"):
        m = re.search(r'SCRIPT_NAME\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8"))
        if m:
            out.add(m.group(1))
    return out


def script_refs() -> set:
    out = set()
    # cron/辅助脚本
    for p in SCRIPTS.glob("*.sh"):
        for m in re.finditer(r"([A-Za-z0-9_-]+\.py)", p.read_text(encoding="utf-8")):
            out.add(m.group(1))
    # 启动器（kb / kb.cmd）本身也是一条接线根
    for name in ("kb", "kb.cmd"):
        f = REPO / "template-vault" / name
        if f.exists():
            for m in re.finditer(r"([A-Za-z0-9_-]+\.py)", f.read_text(encoding="utf-8")):
                out.add(m.group(1))
    return out


def imports_of(module: str) -> set:
    try:
        text = (PIPELINE / module).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {m.group(1) + ".py" for m in _IMPORT_RE.finditer(text)}


def reachable() -> set:
    """从接线根（插件脚本 + 脚本调用 + 传递 import）做 BFS。"""
    roots = plugin_scripts() | script_refs()
    seen, stack = set(), list(roots)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        stack.extend(d for d in imports_of(m) if d not in seen)
    return seen


class ReachabilityTest(unittest.TestCase):
    def test_every_module_wired_or_internal(self):
        unknown = pipeline_modules() - reachable() - set(INTERNAL_MODULES)
        self.assertEqual(
            unknown, set(),
            f"未接线且未声明 internal 的模块（应接线或加入 INTERNAL_MODULES）: {sorted(unknown)}")

    def test_internal_list_not_stale(self):
        mods, reach = pipeline_modules(), reachable()
        stale = [m for m in INTERNAL_MODULES if m in reach or m not in mods]
        self.assertEqual(stale, [], f"INTERNAL 中已可达/不存在的条目应移除: {stale}")


if __name__ == "__main__":
    unittest.main()
