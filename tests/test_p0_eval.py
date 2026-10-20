#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))
sys.path.insert(0, str(REPO / "scripts"))   # precheck / setup_obsidian
sys.path.insert(0, str(REPO))               # create_vault

import evolution_log  # noqa: E402
import rag  # noqa: E402

def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")



class EvalCallersUseNoRecordTest(unittest.TestCase):
    def test_all_eval_paths_pass_no_record(self):
        targets = {
            "pipeline/kb_eval.py": "--no-record",
            "pipeline/kb_eval_judge.py": "--no-record",
            "pipeline/llm_rerank.py": "--no-record",
            "scripts/eval_retrieval.py": "--no-record",
            "scripts/eval_retrieval_gold.py": "no_record=True",
        }
        for rel, needle in targets.items():
            src = (REPO / "template-vault" / rel).read_text(encoding="utf-8")
            self.assertIn(needle, src, f"{rel} 的评测调用没有关闭使用信号记录")


# ── 63~70：第七轮（研究层小缺陷）─────────────────────────────


class FitnessFloorTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-fit-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_vault_has_no_fitness(self):
        """空库以前白送 0.44（0 矛盾→1.0 × 0.24 + 无查询→1.0 × 0.20）。"""
        import kb_fitness
        with contextlib.redirect_stdout(io.StringIO()):
            f = kb_fitness.fitness(self.root)
        self.assertFalse(f.get("valid"), f"空库不该给出 fitness: {f}")
        self.assertIsNone(f.get("fitness"))

    def test_unmeasured_components_are_excluded_and_renormalized(self):
        import kb_fitness
        write(self.root / "20-技术 Technology" / "c.md",
              "---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.6\nkb_layer: canon\n"
              "kb_summary: 正典\n---\n\n# 结论\n知识库备份策略。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            f = kb_fitness.fitness(self.root)
        if not f.get("valid"):
            self.skipTest("该 vault 未构成有效 fitness 场景")
        self.assertIn("w_effective_sum", f)
        self.assertIn("components_excluded", f)
        self.assertLess(f["w_effective_sum"], 1.0,
                        "无数据的分量没有被排除，权重和仍是 1.0")
        self.assertTrue(f["components_excluded"])


class DriftModuleTest(unittest.TestCase):
    def test_subprocess_imported_at_module_level(self):
        """以前 subprocess 只在 __main__ 里 import，被 import 调用时 NameError 被 except 吞掉。"""
        import kb_drift
        self.assertTrue(hasattr(kb_drift, "subprocess"))
        src = (PIPELINE / "kb_drift.py").read_text(encoding="utf-8")
        self.assertIn("import argparse, json, hashlib, datetime, os, math, subprocess", src)

    def test_length_ratio_uses_body(self):
        src = (PIPELINE / "kb_drift.py").read_text(encoding="utf-8")
        self.assertIn("len(body_now) - len(orig)", src,
                      "长度变化仍在用含 frontmatter 的整文件长度（与 kb_consistency 口径不一致）")


# ── 71~74：P1-22 —— L1 检索缺口改为诚实的审计清单并可见 ──────


class ContradictionBudgetTest(unittest.TestCase):
    def test_llm_calls_are_budgeted(self):
        """只读 lint 路径不得对每一对无上限调 LLM。"""
        import kb_contradiction as kc
        calls = []
        orig = kc._llm_polarity
        kc._llm_polarity = lambda root, a, b: (calls.append(1), False)[1]
        try:
            notes = [{"vec": {"x": 1.0}, "neg": 0.0, "body": "甲", "rel": "a.md"},
                     {"vec": {"x": 1.0}, "neg": 0.0, "body": "乙", "rel": "b.md"}]
            # 预算为 0 → 一次都不该调
            kc._maybe(notes, 0, 1, [], root=Path("/tmp"), budget={"left": 0})
            self.assertEqual(calls, [], "预算为 0 时仍调了 LLM")
            # 预算为 1 → 只调一次，且预算被扣减
            b = {"left": 1}
            kc._maybe(notes, 0, 1, [], root=Path("/tmp"), budget=b)
            self.assertEqual(len(calls), 1)
            self.assertEqual(b["left"], 0)
        finally:
            kc._llm_polarity = orig

    def test_budget_constant_exists(self):
        src = (PIPELINE / "kb_contradiction.py").read_text(encoding="utf-8")
        self.assertIn("LLM_PAIR_BUDGET", src)
        self.assertIn("KB_CONTRA_LLM_MAX", src)


class ContradictionMergeTest(unittest.TestCase):
    def test_conflicts_with_is_merged_not_overwritten(self):
        """一篇与多方冲突时，以前 f'["{other}"]' 覆盖写，只剩最后一条。"""
        import kb_contradiction2 as kc2
        self.assertEqual(kc2._merge_conflicts("---\na: b\n---\n正文", "x.md"), '["x.md"]')
        self.assertEqual(
            kc2._merge_conflicts('---\nconflicts_with: ["a.md"]\n---\n正文', "b.md"),
            '["a.md", "b.md"]')
        self.assertEqual(
            kc2._merge_conflicts('---\nconflicts_with: ["a.md"]\n---\n正文', "a.md"),
            '["a.md"]', "重复项不应被加两次")
        # frontmatter 的 --- 分隔符不能被块列表正则误匹配
        self.assertEqual(
            kc2._merge_conflicts("---\nconflicts_with:\n  - a.md\n  - b.md\n---\n正文", "c.md"),
            '["a.md", "b.md", "c.md"]')

    def test_threshold_is_not_silently_raised(self):
        src = (PIPELINE / "kb_contradiction2.py").read_text(encoding="utf-8")
        self.assertNotIn("thr = max(threshold", src,
                         "CLI 传入的阈值被静默抬高，用户调整无效且无从察觉")


# ── 14：evolution_log 事件白名单 ─────────────────────────────
class EvolutionLogEventTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-evo-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_alert_event_accepted(self):
        """kb_rsi 的集中度护栏会写 ALERT；不在 EVENTS 里会抛 ValueError 崩 CLI。"""
        evolution_log.append(self.root, "ALERT", "集中度护栏触发", detail="P3-3")
        self.assertIn("ALERT", evolution_log.EVENTS)
        self.assertIn("ALERT", evolution_log._EVENT_LABEL)


# ── 15：rag 缓存键 ───────────────────────────────────────────
