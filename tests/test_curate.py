#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_curate.py — 知识结晶层安全网（P2-3 拆分前置）。

为什么先有这份测试：`curate.py` 是现役最大单文件（1289 行），
ARCHITECTURE-REVIEW.md §九 ③ 计划把 `cmd_run` / `cmd_verify` / `cmd_report` /
`cmd_recheck` / `cmd_review` 拆出去。但当时**没有独立 test_curate.py** ——
拿掉子命令后没有任何测试能证明行为没变。这份文件就是那次拆分的安全网。

覆盖策略（按"搬动后最容易坏"的顺序）：

  A. 纯函数不变量：render_canon / _jaccard / _body_of / verify_note 的判据
  B. 单篇结晶 curate_one：noise/raw-only 写回门控、低置信转人工队列、
     重复正典闸门、错误路径
  C. 写回 apply_canon：原文保号归档、frontmatter 溯源字段、lineage
  D. 五个子命令 cmd_plan/run/verify/report/recheck/review 的可观测行为
  E. 跨子命令契约：账本精简 / 提案旁路完整 / review 与 recheck 的联动

所有测试都用 `_StubLLM` 替身，不触网、不调真实模型。

运行：python3 -m unittest tests.test_curate
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))

import curate  # noqa: E402

FM_BASE = ("tags: [开发]\nstatus: active\ndomain: 开发\n"
           "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.6\n"
           "kb_summary: 示例摘要\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _StubLLM:
    """最小 LLM 替身：回预设提案，可模拟"模型不可用"。"""

    model = "stub-model"

    def __init__(self, prop, note="stub", available=True):
        self.prop = prop
        self.note = note
        self._available = available
        self.calls = 0

    def available(self):
        return (True, "ok") if self._available else (False, "stub 不可用")

    def ask_json(self, msgs, max_tokens=4096):
        self.calls += 1
        return self.prop, self.note


def _good_prop(**over):
    """一份能过校验的提案：结论/事实都取自原文，长度落在区间内。"""
    prop = {
        "title": "本地检索选 TF-IDF",
        "summary": "选 TF-IDF 因为零依赖可一键部署",
        "verdict": "canon",
        "confidence": 0.9,
        "sections": {
            "结论": ["选 TF-IDF，因为不需要 GPU 和 API，一键部署"],
            "依据": ["需求是人人都能一键部署，不买 GPU/API", "实测 300 篇库检索 12ms"],
        },
        "facts": [
            {"value": "TF-IDF", "kind": "verbatim"},
            {"value": "12ms", "kind": "verbatim"},
        ],
        "sources": ["vault:20-技术 Technology/n.md"],
    }
    prop.update(over)
    return prop


BODY = ("# 选检索方案\n\n"
        "## 背景\n"
        "需求：一个人人都能一键部署、不用买 GPU/API 的本地检索。\n"
        "对比过 embedding 方案，需要跑模型服务，部署门槛高。\n"
        "最后选 TF-IDF，零依赖，纯标准库实现。\n\n"
        "## 实测\n"
        "300 篇的库，TF-IDF 检索耗时 12ms，完全够用。\n"
        "embedding 方案要额外起 8081 端口服务，cron 环境读不到凭证。\n\n"
        "## 结论\n"
        "选 TF-IDF。零依赖、可一键部署、12ms 够快。\n"
        "以后规模上去了再考虑 embedding 作为可选增强。\n")


class _CurateCase(unittest.TestCase):
    """公共脚手架：临时 vault + 一篇笔记。"""

    REL = "20-技术 Technology/n.md"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-curate-"))
        self.note = self.root / self.REL
        write(self.note, "---\n" + FM_BASE + "---\n\n" + BODY)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_quiet(self, fn, *a, **kw):
        """跑一个会 print 的函数，吞掉输出（返回 (rc, stdout)）。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
        return rc, buf.getvalue()


# ── A. 纯函数不变量 ─────────────────────────────────────────
class RenderCanonTest(unittest.TestCase):
    def test_section_order_is_fixed(self):
        """结论/依据/操作/坑与边界 顺序固定，其余节按出现顺序追加。"""
        prop = {"sections": {"坑与边界": ["x"], "结论": ["y"], "额外": ["z"], "依据": ["w"]}}
        out = curate.render_canon(prop)
        self.assertLess(out.index("## 结论"), out.index("## 依据"))
        self.assertLess(out.index("## 依据"), out.index("## 坑与边界"))
        self.assertLess(out.index("## 坑与边界"), out.index("## 额外"))

    def test_skips_empty_sections(self):
        out = curate.render_canon({"sections": {"结论": ["只有结论"], "依据": []}})
        self.assertIn("## 结论", out)
        self.assertNotIn("## 依据", out)

    def test_list_and_str_both_render(self):
        self.assertIn("- 单条", curate.render_canon({"sections": {"结论": "单条"}}))
        self.assertIn("- 列表项", curate.render_canon({"sections": {"结论": ["列表项"]}}))

    def test_preserves_existing_bullet_prefix(self):
        """已是 `- x` / `1. x` 的行不重复加 `- `。"""
        out = curate.render_canon({"sections": {"结论": ["- 已带横线", "1. 已编号"]}})
        self.assertIn("- 已带横线", out)
        self.assertIn("1. 已编号", out)
        self.assertNotIn("- - 已带横线", out)

    def test_empty_proposal_returns_just_newline(self):
        self.assertEqual(curate.render_canon({}), "\n")


class BodyOfTest(unittest.TestCase):
    def test_strips_frontmatter(self):
        self.assertTrue(curate._body_of("---\na: 1\n---\n正文").startswith("正文"))

    def test_no_frontmatter_returns_asis(self):
        self.assertEqual(curate._body_of("纯正文"), "纯正文")

    def test_unterminated_frontmatter_returns_asis(self):
        """只有开头 --- 没有结尾 —— 不当 frontmatter 处理（避免吞掉正文）。"""
        self.assertEqual(curate._body_of("---\na: 1\n没有结束"), "---\na: 1\n没有结束")


class JaccardTest(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertEqual(curate._jaccard("abc def", "abc def"), 1.0)

    def test_disjoint_is_zero(self):
        self.assertEqual(curate._jaccard("abc", "xyz"), 0.0)

    def test_empty_is_zero(self):
        self.assertEqual(curate._jaccard("", "abc"), 0.0)
        self.assertEqual(curate._jaccard("abc", ""), 0.0)


# ── B. verify_note 三关判据 ─────────────────────────────────
class VerifyNoteTest(_CurateCase):
    def test_good_proposal_passes(self):
        v = curate.verify_note(BODY, _good_prop(), self.root, "vault:x")
        self.assertTrue(v["ok"], v["issues"])
        self.assertEqual(v["checks"]["facts_grounded"], 2)
        self.assertEqual(v["dropped_facts"], [])

    def test_fabricated_fact_is_dropped_not_grounded(self):
        """接地关：原文没有的事实必须被丢弃（drift control 的核心）。"""
        prop = _good_prop(facts=[{"value": "TF-IDF", "kind": "verbatim"},
                                 {"value": "999ms", "kind": "verbatim"}])
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        self.assertIn("999ms", v["dropped_facts"])
        self.assertEqual(v["checks"]["facts_grounded"], 1)

    def test_all_facts_fabricated_is_hard_fail(self):
        prop = _good_prop(facts=[{"value": f"编造{i}", "kind": "verbatim"} for i in range(3)])
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        self.assertFalse(v["ok"])
        self.assertTrue(any("全部 facts" in i for i in v["issues"]))

    def test_over_compression_is_hard_fail(self):
        prop = _good_prop(sections={"结论": ["太短"]})
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        self.assertFalse(v["ok"])
        self.assertTrue(any("过度压缩" in i for i in v["issues"]))

    def test_missing_sources_is_hard_fail(self):
        v = curate.verify_note(BODY, _good_prop(sources=[]), self.root, "vault:x")
        self.assertFalse(v["ok"])
        self.assertTrue(any("sources" in i for i in v["issues"]))

    def test_inferred_facts_not_counted_in_denominator(self):
        """kind=inferred 只登记，不计入接地分母（否则推断会被当编造）。"""
        prop = _good_prop(facts=[{"value": "TF-IDF", "kind": "verbatim"},
                                 {"value": "推断出来的东西", "kind": "inferred"}])
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        self.assertEqual(v["checks"]["facts_total"], 1)
        self.assertEqual(v["checks"]["facts_inferred"], ["推断出来的东西"])

    def test_wikilink_wrapper_is_stripped_before_grounding(self):
        """`[[TF-IDF]]` 应去掉方括号再核对，避免三连括号死链。"""
        prop = _good_prop(facts=[{"value": "[[TF-IDF]]", "kind": "verbatim"}])
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        self.assertIn("TF-IDF", v["grounded_facts"])
        self.assertNotIn("[[TF-IDF]]", v["grounded_facts"])

    def test_source_ref_in_facts_is_not_penalized(self):
        """模型偶尔把 source_ref 混进 facts，不该按未接地计罚。"""
        prop = _good_prop(facts=[{"value": "vault:memory/x::abc", "kind": "verbatim"},
                                 {"value": "TF-IDF", "kind": "verbatim"}])
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        self.assertEqual(v["dropped_facts"], [])


# ── C. curate_one 的判定链 ──────────────────────────────────
class CurateOneTest(_CurateCase):
    def test_canon_proposal_returns_propose_without_apply(self):
        before = self.note.read_text(encoding="utf-8")
        llm = _StubLLM(_good_prop())
        rec = curate.curate_one(self.root, self.REL, llm, apply=False)
        self.assertTrue(rec["ok"], rec.get("verify", {}).get("issues"))
        self.assertEqual(rec["action"], "propose")
        self.assertEqual(self.note.read_text(encoding="utf-8"), before,
                         "不带 apply 不应改笔记")

    def test_noise_verdict_proposes_without_apply(self):
        llm = _StubLLM({"verdict": "noise", "title": "t", "sections": [], "facts": [],
                        "sources": [], "confidence": 0.9})
        before = self.note.read_text(encoding="utf-8")
        rec = curate.curate_one(self.root, self.REL, llm, apply=False)
        self.assertEqual(rec["action"], "propose:noise")
        self.assertEqual(self.note.read_text(encoding="utf-8"), before,
                         "noise 判定在非 apply 下不得写 frontmatter")

    def test_noise_verdict_writes_with_apply(self):
        llm = _StubLLM({"verdict": "noise", "title": "t", "sections": [], "facts": [],
                        "sources": [], "confidence": 0.9})
        rec = curate.curate_one(self.root, self.REL, llm, apply=True)
        self.assertEqual(rec["action"], "skip:noise")
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("kb_layer: noise", text)
        self.assertIn("kb_index: false", text)
        self.assertIn("curate_verdict: noise", text)

    def test_raw_only_verdict_marks_raw_layer(self):
        llm = _StubLLM({"verdict": "raw-only", "title": "t", "sections": [], "facts": [],
                        "sources": [], "confidence": 0.9})
        rec = curate.curate_one(self.root, self.REL, llm, apply=True)
        self.assertEqual(rec["action"], "skip:raw-only")
        self.assertIn("kb_layer: raw", self.note.read_text(encoding="utf-8"))

    def test_low_confidence_goes_to_review_queue(self):
        """置信度 < CONF_MIN → 不自动写回，进人工队列，且 action 标记清楚。"""
        llm = _StubLLM(_good_prop(confidence=0.2))
        before = self.note.read_text(encoding="utf-8")
        rec = curate.curate_one(self.root, self.REL, llm, apply=True)
        self.assertEqual(rec["action"], "review:low-confidence")
        self.assertTrue(rec["ok"], "低置信不算失败，只是需人工拍板")
        self.assertEqual(self.note.read_text(encoding="utf-8"), before, "低置信不得写回")
        rv = self.root / curate.REVIEW
        self.assertTrue(rv.exists(), "应写入人工裁决队列")
        entry = json.loads(rv.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(entry["rel"], self.REL)
        self.assertEqual(entry["confidence"], 0.2)

    def test_llm_error_returns_error_record(self):
        llm = _StubLLM(None, note="连接失败")
        rec = curate.curate_one(self.root, self.REL, llm, apply=False)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"], "连接失败")
        self.assertNotIn("proposal", rec)

    def test_truncation_triggers_one_compact_retry(self):
        """被截断 → 就地重试一次（更紧凑）；第二次成功则采用。"""
        class _RetryLLM(_StubLLM):
            def ask_json(self, msgs, max_tokens=4096):
                self.calls += 1
                if self.calls == 1:
                    return None, "输出被截断"
                return _good_prop(), "ok"

        llm = _RetryLLM(None)
        rec = curate.curate_one(self.root, self.REL, llm, apply=False)
        self.assertEqual(llm.calls, 2, "截断应重试一次")
        self.assertTrue(rec["ok"])

    def test_duplicate_canon_is_skipped_on_apply(self):
        """已有近乎相同正典 → skip，不再新建（防"正典双胞胎"）。"""
        canon_text = curate.render_canon(_good_prop())
        write(self.root / "20-技术 Technology/已存在.md",
              "---\n" + FM_BASE + "kb_layer: canon\n---\n\n" + canon_text)
        llm = _StubLLM(_good_prop())
        rec = curate.curate_one(self.root, self.REL, llm, apply=True)
        self.assertTrue(str(rec["action"]).startswith("skip:duplicate-of"),
                        rec["action"])


# ── D. apply_canon 写回语义 ─────────────────────────────────
class ApplyCanonTest(_CurateCase):
    def _apply(self, prop=None):
        prop = prop or _good_prop()
        fm, _t, _b, body = curate.load(self.note)
        v = curate.verify_note(body, prop, self.root, "vault:x")
        v["checks"]["orig_chars"] = len(body)
        return curate.apply_canon(self.root, self.REL, fm, prop, "vault:x", v, "stub-model")

    def test_original_is_archived_preserving_path(self):
        """原文必须保号归档到 raw/_curated/<原路径>，内容逐字不变。"""
        original = self.note.read_text(encoding="utf-8")
        self.assertEqual(self._apply(), "canon-written")
        archived = self.root / "raw" / "_curated" / self.REL
        self.assertTrue(archived.exists(), "原文未归档")
        self.assertEqual(archived.read_text(encoding="utf-8"), original,
                         "归档内容必须与原文字节一致")

    def test_archive_collision_adds_suffix(self):
        archived = self.root / "raw" / "_curated" / self.REL
        write(archived, "占位")
        self._apply()
        self.assertEqual(archived.read_text(encoding="utf-8"), "占位", "已存在的归档不能被覆盖")
        self.assertTrue(archived.with_name("n-1.md").exists(), "冲突时应加 -N 后缀")

    def test_note_becomes_canon_with_provenance(self):
        self._apply()
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("kb_layer: canon", text)
        self.assertIn("curated_by: stub-model", text)
        self.assertIn("archived_original:", text)
        self.assertIn("source_ref:", text)
        self.assertIn("kb raw show", text, "尾注应给出回原记忆的方式")

    def test_chunk_fields_are_dropped(self):
        """正典不应继承 chunk_of 等分块字段（否则会继续被当碎片）。"""
        fm, _t, _b, _body = curate.load(self.note)
        fm["chunk_of"] = "20-技术 Technology/p.md"
        prop = _good_prop()
        v = curate.verify_note(BODY, prop, self.root, "vault:x")
        v["checks"]["orig_chars"] = len(BODY)
        curate.apply_canon(self.root, self.REL, fm, prop, "vault:x", v, "m")
        self.assertNotIn("chunk_of", self.note.read_text(encoding="utf-8"))

    def test_lineage_is_recorded(self):
        self._apply()
        lin = self.root / ".kb" / "state" / "lineage.jsonl"
        self.assertTrue(lin.exists(), "未记录血缘")
        self.assertIn("curate-archive-original", lin.read_text(encoding="utf-8"))


# ── E. 子命令可观测行为 ─────────────────────────────────────
class CmdPlanTest(_CurateCase):
    def test_plan_lists_candidate_in_json(self):
        rc, out = self.run_quiet(curate.cmd_plan, self.root, None, 10, True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        rels = [c["rel"] for c in data["candidates"]]
        self.assertIn(self.REL, rels)

    def test_plan_skips_raw_layer(self):
        write(self.root / "raw" / "AI" / "e.md", "---\n" + FM_BASE + "---\n\n" + BODY)
        rc, out = self.run_quiet(curate.cmd_plan, self.root, None, 50, True)
        data = json.loads(out)
        rels = [c["rel"] for c in data["candidates"]]
        self.assertFalse(any(r.startswith("raw/") for r in rels), "证据层不应参与结晶")

    def test_plan_skips_noise_verdict_notes(self):
        write(self.note, "---\n" + FM_BASE + "curate_verdict: noise\n---\n\n" + BODY)
        rc, out = self.run_quiet(curate.cmd_plan, self.root, None, 50, True)
        data = json.loads(out)
        rels = [c["rel"] for c in data["candidates"]]
        self.assertNotIn(self.REL, rels, "已判 noise 的笔记不应再送模型")


class CmdRunTest(_CurateCase):
    def test_run_without_model_degrades_to_plan(self):
        """模型不可用 → 降级只出 plan，绝不阻塞流水线。"""
        orig = curate.LLM
        curate.LLM = lambda model=None: _StubLLM(None, available=False)
        try:
            rc, out = self.run_quiet(curate.cmd_run, self.root, None, 5, False, None, False)
        finally:
            curate.LLM = orig
        self.assertEqual(rc, 0, "模型不可用不应返回非 0")
        self.assertIn("不可用", out)

    def test_run_writes_ledger_and_proposals(self):
        orig = curate.LLM
        curate.LLM = lambda model=None: _StubLLM(_good_prop())
        try:
            rc, out = self.run_quiet(curate.cmd_run, self.root, None, 5, False, None, False)
        finally:
            curate.LLM = orig
        self.assertEqual(rc, 0)
        ledger = self.root / curate.LEDGER
        props = self.root / curate.PROPS
        self.assertTrue(ledger.exists(), "未写账本")
        self.assertTrue(props.exists(), "未写提案旁路")
        # 契约：账本精简（剥掉 proposal），提案旁路完整
        led = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn("proposal", led, "账本必须精简（proposal 剥离）")
        pr = json.loads(props.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("proposal", pr, "提案旁路必须含完整 proposal")

    def test_run_respects_l4_freeze(self):
        """L4 冻结 lc → 即使 --apply 也只出提案。"""
        write(self.root / ".kb/state/kb_l4_config.json",
              json.dumps({"frozen": {"lc": True}}))
        orig = curate.LLM
        curate.LLM = lambda model=None: _StubLLM(_good_prop())
        try:
            rc, out = self.run_quiet(curate.cmd_run, self.root, None, 5, True, None, False)
        finally:
            curate.LLM = orig
        self.assertIn("冻结", out)
        self.assertNotIn("kb_layer: canon", self.note.read_text(encoding="utf-8"),
                         "L4 冻结时不得自动写回")


class CmdVerifyTest(_CurateCase):
    def test_verify_reads_proposals_not_ledger(self):
        """verify 必须读提案旁路（账本已剥 proposal），否则永远输出"无提案记录"。"""
        write(self.root / curate.PROPS,
              json.dumps({"rel": self.REL, "sid": "vault:x", "ts": curate._now(),
                          "proposal": _good_prop()}, ensure_ascii=False) + "\n")
        rc, out = self.run_quiet(curate.cmd_verify, self.root, None, False)
        self.assertEqual(rc, 0)
        self.assertIn(self.REL, out)
        self.assertNotIn("无提案记录", out)

    def test_verify_empty_when_no_proposals(self):
        rc, out = self.run_quiet(curate.cmd_verify, self.root, None, False)
        self.assertEqual(rc, 0)
        self.assertIn("无提案记录", out)


class CmdReportTest(_CurateCase):
    def test_report_json_shape(self):
        rc, out = self.run_quiet(curate.cmd_report, self.root, True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        for k in ("notes", "canon", "raw", "index_stubs", "canon_ratio",
                  "curate_runs", "curate_ok", "facts_dropped_total"):
            self.assertIn(k, data, f"report 缺字段 {k}")

    def test_report_counts_canon(self):
        write(self.root / "20-技术 Technology/c.md",
              "---\n" + FM_BASE + "kb_layer: canon\n---\n\n# t\n\n## 结论\n- x\n")
        rc, out = self.run_quiet(curate.cmd_report, self.root, True)
        data = json.loads(out)
        self.assertGreaterEqual(data["canon"], 1)


class CmdReviewTest(_CurateCase):
    def test_review_empty_queue(self):
        rc, out = self.run_quiet(curate.cmd_review, self.root, 10, False)
        self.assertEqual(rc, 0)
        self.assertIn("无待裁决", out)

    def test_review_lists_low_confidence(self):
        write(self.root / curate.REVIEW, json.dumps(
            {"rel": self.REL, "confidence": 0.3, "verify_ok": True,
             "title": "标题", "issues": [], "sid": "vault:x"},
            ensure_ascii=False) + "\n")
        rc, out = self.run_quiet(curate.cmd_review, self.root, 10, False)
        self.assertEqual(rc, 0)
        self.assertIn(self.REL, out)
        self.assertIn("0.3", out)


class CmdRecheckTest(_CurateCase):
    def test_recheck_without_proposals_returns_3(self):
        rc, out = self.run_quiet(curate.cmd_recheck, self.root, False, False)
        self.assertEqual(rc, 3, "无提案旁路时应返回 3（提示历史提案看 _curate/*.md）")

    def test_recheck_does_not_override_low_confidence(self):
        """低置信原样保留人工裁决 —— 改校验规则不得绕过置信闸门。"""
        write(self.root / curate.PROPS, json.dumps(
            {"rel": self.REL, "sid": "vault:x", "ts": curate._now(),
             "action": "review:low-confidence", "proposal": _good_prop()},
            ensure_ascii=False) + "\n")
        before = self.note.read_text(encoding="utf-8")
        rc, out = self.run_quiet(curate.cmd_recheck, self.root, False, False)
        self.assertEqual(rc, 0)
        # 低置信必须计入"仍未达标"（不写回是实质），而不是计入"达标"
        self.assertIn("达标 0 篇", out)
        self.assertIn("仍未达标 1 篇", out)
        self.assertEqual(self.note.read_text(encoding="utf-8"), before,
                         "recheck 不得绕过低置信闸门写回")

    def test_recheck_export_lists_low_confidence_reason(self):
        """--export 时，低置信要出现在人工清单里并注明原因（否则人工不知道该看什么）。"""
        write(self.root / curate.PROPS, json.dumps(
            {"rel": self.REL, "sid": "vault:x", "ts": curate._now(),
             "action": "review:low-confidence", "proposal": _good_prop()},
            ensure_ascii=False) + "\n")
        rc, out = self.run_quiet(curate.cmd_recheck, self.root, False, True)
        self.assertEqual(rc, 0)
        md = self.root / curate.PROPOSAL_DIR / "REVIEW-NEEDED.md"
        self.assertTrue(md.exists(), "未导出人工清单")
        text = md.read_text(encoding="utf-8")
        self.assertIn("低置信", text)
        self.assertIn(self.REL, text)

    def test_recheck_apply_pass_writes_back(self):
        write(self.root / curate.PROPS, json.dumps(
            {"rel": self.REL, "sid": "vault:x", "ts": curate._now(),
             "action": "propose", "proposal": _good_prop()},
            ensure_ascii=False) + "\n")
        rc, out = self.run_quiet(curate.cmd_recheck, self.root, True, False)
        self.assertEqual(rc, 0)
        self.assertIn("kb_layer: canon", self.note.read_text(encoding="utf-8"))


class L4FrozenTest(_CurateCase):
    def test_missing_config_means_not_frozen(self):
        self.assertFalse(curate.l4_frozen(self.root))

    def test_frozen_lc(self):
        write(self.root / ".kb/state/kb_l4_config.json", json.dumps({"frozen": {"lc": True}}))
        self.assertTrue(curate.l4_frozen(self.root))

    def test_frozen_other_rung_does_not_freeze_lc(self):
        write(self.root / ".kb/state/kb_l4_config.json", json.dumps({"frozen": {"l0": True}}))
        self.assertFalse(curate.l4_frozen(self.root))

    def test_corrupt_config_is_best_effort_not_frozen(self):
        write(self.root / ".kb/state/kb_l4_config.json", "{坏 json")
        self.assertFalse(curate.l4_frozen(self.root), "配置损坏不应卡住流水线")


class FrontmatterUpsertTest(_CurateCase):
    def test_inserts_new_key(self):
        out = curate._upsert_frontmatter("---\na: 1\n---\n正文", {"b": 2})
        self.assertIn("b: 2", out)
        self.assertIn("正文", out)

    def test_replaces_existing_key(self):
        out = curate._upsert_frontmatter("---\na: 1\n---\n正文", {"a": 9})
        self.assertIn("a: 9", out)
        self.assertNotIn("a: 1", out)

    def test_no_frontmatter_returns_none(self):
        """无 frontmatter → 返回 None，绝不改写文件（避免破坏原文）。"""
        self.assertIsNone(curate._upsert_frontmatter("纯正文", {"a": 1}))


if __name__ == "__main__":
    unittest.main()
