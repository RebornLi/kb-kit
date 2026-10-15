#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p2_items.py — P2 四项（FSRS / 睡眠整理 / claim 槽位 / 质量门禁）的守卫。

这些测试的重点是**性质**（不变量、单调性、边界）而不是固定数值 —— 公式类代码最容易
出"看起来对但差个符号"的错，用定义式当不变量能立刻抓到。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
TEMPLATE = REPO / "template-vault"
sys.path.insert(0, str(PIPELINE))

import consolidate  # noqa: E402
import fsrs_core as F  # noqa: E402
import gate  # noqa: E402
import kb_claim  # noqa: E402


def write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class FsrsDefinitionalInvariantTest(unittest.TestCase):
    """FSRS 的定义式当不变量：R(0,S)=1 · R(S,S)=0.9 · I(S,0.9)=S。

    这三条是模型的**定义**（S 就是"可提取性降到 90% 所需天数"），
    公式里任何一个系数写错都会破坏它们。比对着数字断言更可靠。
    """

    def test_r_at_zero_is_one(self):
        for s in (1, 10, 100):
            self.assertAlmostEqual(F.retrievability(0, s), 1.0, places=9)

    def test_r_at_stability_is_ninety_percent(self):
        for s in (1, 10, 100):
            self.assertAlmostEqual(F.retrievability(s, s), 0.9, places=9,
                                   msg="S 的定义是 R(t=S)=0.9")

    def test_interval_inverts_retrievability(self):
        for s in (1, 10, 100):
            self.assertAlmostEqual(F.interval(s, 0.9), s, places=6)

    def test_retrievability_decreases_monotonically(self):
        seq = [F.retrievability(t, 10) for t in (0, 1, 5, 10, 30, 100)]
        self.assertTrue(all(seq[i] > seq[i + 1] for i in range(len(seq) - 1)), seq)

    def test_higher_retention_target_shortens_interval(self):
        """目标保留率越高，间隔越短（0.95 的工作量约为 0.9 的两倍）。"""
        self.assertLess(F.interval(100, 0.95), F.interval(100, 0.90))

    def test_first_review_stability_ordering(self):
        """首次复习：easy > good > hard > again。"""
        ss = [F.review({"stability": 0, "difficulty": 0, "reps": 0}, g, 0)["stability"]
              for g in (1, 2, 3, 4)]
        self.assertEqual(ss, sorted(ss))
        self.assertLess(ss[0], ss[3])

    def test_difficulty_moves_in_the_right_direction(self):
        """again 应把难度推高，easy 应把难度压低（评分口径 1/2/3/4）。"""
        d0 = 5.0
        self.assertGreater(F.next_difficulty(d0, 1), d0, "again 应提高难度")
        self.assertLess(F.next_difficulty(d0, 4), d0, "easy 应降低难度")

    def test_difficulty_stays_in_bounds(self):
        d = 5.0
        for g in (1, 1, 1, 1, 1, 1, 1, 1):
            d = F.next_difficulty(d, g)
            self.assertGreaterEqual(d, 1.0)
            self.assertLessEqual(d, 10.0)

    def test_successive_success_extends_interval(self):
        st = {"stability": 0, "difficulty": 0, "reps": 0}
        out = []
        for _ in range(5):
            st = F.review(st, 3, st.get("due_in_days", 0))
            out.append(st["due_in_days"])
        self.assertEqual(out, sorted(out), f"间隔应单调增长：{out}")

    def test_sm2_migration_preserves_interval_as_stability(self):
        """迁移零成本的依据：SM-2 的 interval 可直接当 FSRS 的 S。"""
        for iv in (1, 18, 90):
            s, d = F.memory_state_from_sm2(iv, 2.5)
            self.assertAlmostEqual(s, float(iv), places=6)
            self.assertTrue(1.0 <= d <= 10.0)

    def test_grade_scale_is_fsrs_not_sm2(self):
        """FSRS 只认 1/2/3/4 —— recall_schedule 的口径必须一起改，否则 D 反向更新。"""
        import recall_schedule as rs
        self.assertEqual(rs.GRADE_Q, {"again": 1, "hard": 2, "good": 3, "easy": 4})
        self.assertIn("SM2_LEGACY", "\n".join(dir(rs)), "旧口径应留档以便对账")


class ClaimSlotClassificationTest(unittest.TestCase):
    """「矛盾」与「时序更新」的分水岭 —— 分不开就会出现"去调和一个不存在的冲突"。"""

    def _c(self, s, r, v, vf="", vt=""):
        return {"subject": s, "relation": r, "value": v, "valid_from": vf, "valid_to": vt}

    def test_different_slot_is_not_conflict(self):
        self.assertEqual(kb_claim.classify_pair(
            self._c("vLLM", "端口", "8000"), self._c("索引", "层", "canon")), "unrelated")

    def test_overlapping_time_different_value_is_conflict(self):
        self.assertEqual(kb_claim.classify_pair(
            self._c("vLLM", "端口", "8000", "2026-01-01"),
            self._c("vLLM", "端口", "8080", "2026-02-01")), "conflict")

    def test_disjoint_time_different_value_is_temporal_update(self):
        """**关键**：时间不相交 = 事实变了，不是矛盾 —— 不该去"调和"。"""
        self.assertEqual(kb_claim.classify_pair(
            self._c("vLLM", "端口", "8000", "2026-01-01", "2026-02-01"),
            self._c("vLLM", "端口", "8080", "2026-03-01")), "update")

    def test_same_value_is_dup(self):
        self.assertEqual(kb_claim.classify_pair(
            self._c("vLLM", "端口", "8000"), self._c("vLLM", "端口", "8000")), "dup")

    def test_missing_slot_is_unrelated_not_conflict(self):
        """缺 subject/relation → 无法对齐，保守判 unrelated（**不能**默认当矛盾）。"""
        self.assertEqual(kb_claim.classify_pair({"value": "a"}, {"value": "b"}), "unrelated")

    def test_extraction_stores_slot_and_time_fields(self):
        src = (PIPELINE / "kb_claim.py").read_text(encoding="utf-8")
        for f in ("subject", "relation", "value", "temporal_state", "valid_from"):
            self.assertIn(f'"{f}"', src, f"claim schema 缺 {f}")


class QualityGateTest(unittest.TestCase):
    """三态门禁：**reject 只用于不变量**，噪声一律 quarantine。"""

    def test_reject_only_for_invariants(self):
        # 空正文 → reject
        v = gate.classify({"status": "active"}, "")
        self.assertEqual(v["verdict"], "reject")
        # 缺 status → reject（契约必填）
        v = gate.classify({}, "这是一段足够长的正常正文内容。")
        self.assertEqual(v["verdict"], "reject")
        # 占位符 → reject
        v = gate.classify({"status": "active"}, "TODO")
        self.assertEqual(v["verdict"], "reject")

    def test_derived_content_without_source_is_rejected(self):
        """LLM 回灌防护：派生内容必须挂非 LLM 来源，否则拒收。"""
        v = gate.classify({"status": "active", "derived": "true"}, "一段足够长的 LLM 生成正文。")
        self.assertEqual(v["verdict"], "reject")
        self.assertTrue(any("source_ref" in r for r in v["reasons"]))
        # 挂了来源就放行
        v2 = gate.classify({"status": "active", "derived": "true",
                            "source_ref": "openclaw/MEMORY.md::x"},
                           "一段足够长的 LLM 生成正文，但有来源可追溯。")
        self.assertNotEqual(v2["verdict"], "reject")

    def test_noise_goes_to_quarantine_not_reject(self):
        """噪声嫌疑只标 quarantine —— 个人库里误拒不可恢复。"""
        body = "！@#￥%……" * 40
        v = gate.classify({"status": "active"}, body)
        self.assertEqual(v["verdict"], "quarantine")

    def test_clean_note_passes(self):
        v = gate.classify({"status": "active"},
                          "这是一篇内容正常、长度合适、标点占比合理的中文知识笔记。")
        self.assertEqual(v["verdict"], "pass")

    def test_placeholder_regex_is_linear(self):
        """灾难性回溯回归：`----x` 这类分隔线正文必须在微秒级完成，不能卡死。

        背景：初版 _PLACEHOLDER 写成 `^(?:...|-+|\s)*$`，`-+` 嵌套在 `(?:)*` 里，
        遇到「很多 - 跟个非 -」就指数级回溯（每多 2 字符 ×4，n=26 就 2.5 秒）。
        实测把整个 consolidate 从秒级拖到 6 分 40 秒（faulthandler 定位到 gate.classify）。
        """
        import time
        s = "----x" * 1000          # 4000 字符的分隔线 + 干扰
        t0 = time.time()
        gate._is_placeholder(s)
        self.assertLess(time.time() - t0, 0.5, "占位符判定发生了灾难性回溯")

    def test_source_tier_mapping(self):
        self.assertEqual(gate.classify({"status": "active", "source_tier": "1"}, "正文" * 20)["source_tier"], 1)
        self.assertEqual(gate.classify({"status": "active", "source_tier": "llm"}, "正文" * 20)["source_tier"], 5)
        self.assertEqual(gate.classify({"status": "active"}, "正文" * 20)["source_tier"], 4)  # 缺省=个人笔记


class ConsolidateTest(unittest.TestCase):
    """睡眠时整理：增量、幂等、**只产提议不改笔记**。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-consol-"))
        for i in range(4):
            write(self.root / "20-技术 Technology" / f"n{i}.md",
                  f"---\ntags: [开发]\nstatus: active\ndomain: 开发\nimportance: 0.5\n"
                  f"created: 2026-01-01\nupdated: 2020-01-01\n---\n\n"
                  + f"这是第 {i} 篇笔记的正文内容，主题各不相同以免被判重复。" * 5)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_run_is_incremental_and_idempotent(self):
        consolidate.run(self.root, max_seconds=60)
        st1 = json.loads((self.root / ".kb" / "state" / "kb_consolidate_state.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(st1["n_changed"], 4)
        # 第二次：没有新变更 → 增量为 0（幂等）
        consolidate.run(self.root, max_seconds=60)
        st2 = json.loads((self.root / ".kb" / "state" / "kb_consolidate_state.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(st2["n_changed"], 0, "增量模式重复跑不应重算全库")

    def test_only_proposals_never_touches_notes(self):
        """只产提议、绝不改笔记。

        注意要排除 `.kb/`：那是整理**自己的**状态与报告目录（里面也有 .md）。
        我第一版用 `rglob("*.md")` 把 consolidate_report.md 也算成笔记，于是误报失败。
        """
        def notes():
            return {p: p.read_bytes() for p in self.root.rglob("*.md")
                    if ".kb" not in p.parts}
        before = notes()
        consolidate.run(self.root, max_seconds=60)
        self.assertEqual(before, notes(), "整理**只能产提议**，绝不改笔记")
        self.assertTrue((self.root / ".kb" / "state" / "kb_consolidate_proposals.json").exists())

    def test_missing_claim_data_is_reported_as_na_not_zero(self):
        """没有 claim 数据时必须报 N/A —— 报 0 会让人以为"查过了没有矛盾"。"""
        consolidate.run(self.root, max_seconds=60)
        st = json.loads((self.root / ".kb" / "state" / "kb_consolidate_state.json")
                        .read_text(encoding="utf-8"))
        self.assertEqual(st["counts"]["claim_state"], "no-data")


if __name__ == "__main__":
    unittest.main()
