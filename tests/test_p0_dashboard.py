#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import importlib
import io
import json
import pickle
import re
import shutil
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

import dashboard  # noqa: E402
import kb_meta  # noqa: E402

def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")



# ── 16~17：巡检与仪表盘 ──────────────────────────────────────
class HealthcheckTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-hc-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_dashboard_has_no_false_deadlink_alert(self):
        """死链为 0 时不得产生告警（真实输出形如 `0/2146 死链`，子串判定会误报）。"""
        write(self.root / "20-技术 Technology" / "ok.md",
              "---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\n---\n\n# 干净笔记\n短内容。\n")
        alerts = dashboard.check_healthcheck(str(self.root))
        self.assertFalse([a for a in alerts if "死链" in a],
                         f"死链为 0 却报了告警: {alerts}")

    def test_index_md_participates_in_deadlink_scan(self):
        """_MOC/_INDEX 是导航文件，最容易积累悬空链接，必须纳入死链巡检。"""
        sys.path.insert(0, str(PIPELINE))
        import importlib
        hc = importlib.import_module("kb-healthcheck")
        write(self.root / "70-知识治理 Governance" / "_INDEX.md",
              "---\ntags: [moc]\nstatus: active\ndomain: 管理\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\n---\n\n# 索引\n- [[悬空目标ZZZ]]\n")
        default = {p.name for p in hc.collect_md(self.root)}
        with_reports = {p.name for p in hc.collect_md(self.root, include_reports=True)}
        self.assertNotIn("_INDEX.md", default)
        self.assertIn("_INDEX.md", with_reports)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hc.check_deadlinks(self.root)
        self.assertIn("_INDEX.md", buf.getvalue(), "_INDEX.md 的出链没有被检查")


# ── 18：kb_meta apply --all ──────────────────────────────────


# ── 85：死链检测不得把代码示例当链接 ────────────────────────
class DeadlinkSkipsCodeTest(unittest.TestCase):
    """写文档讲双链语法时必然出现 `[[名]]` 这类示例。

    检查器原先靠往 NOISE_LINKS 里逐个补词（"双向链接"/"wikilink"/"..."）打补丁，
    于是模板库 SOP 里的 `` `[[名]]` `` 被判成死链（1/25 > 2% 阈值），
    `kb verify` 在一个刚建好的库上就报失败。
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "kbhc_test", str(PIPELINE / "kb-healthcheck.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _links(self, text):
        return re.findall(r"\[\[[^\]]+\]\]", self.mod._strip_code_spans(text))

    def test_inline_code_example_is_not_a_link(self):
        self.assertEqual(self._links("写 `[[名]]` 表示重定向。"), [])

    def test_fenced_block_example_is_not_a_link(self):
        self.assertEqual(self._links("```\n[[不存在]]\n```\n"), [])

    def test_real_link_survives(self):
        self.assertEqual(self._links("见 [[如何使用]]。"), ["[[如何使用]]"])

    def test_real_link_after_code_span_survives(self):
        self.assertEqual(self._links("`[[x]]` 之后 [[如何使用]]"), ["[[如何使用]]"])



# pickle 只能引用**模块级**可导入对象，所以恶意载荷必须放在模块级（不能是局部函数）。


class HealthKpiTest(unittest.TestCase):
    """抗 Goodhart 的健康 KPI：只算难刷的量，易刷量必须显式标注为"仅诊断"。

    依据 Manheim & Garrabrant《Goodhart's Law》(arXiv:1803.04585)：
    凡"加动作就能变好"的量（笔记数/链接数）都不能当 KPI，只能诊断。
    """

    def test_kpi_shape_and_honest_na(self):
        import kb_health
        tmp = Path(tempfile.mkdtemp(prefix="kb-kpi-"))
        try:
            write(tmp / "00-入口" / "🏠-首页.md",
                  "---\ntags: [管理]\nstatus: active\ndomain: 管理\nimportance: 0.5\n"
                  "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n[[笔记A]]\n")
            write(tmp / "20-技术 Technology" / "笔记A.md",
                  "---\ntags: [开发]\nstatus: active\ndomain: 开发\nimportance: 0.8\n"
                  "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n[[笔记B]]\n")
            write(tmp / "20-技术 Technology" / "笔记B.md",
                  "---\ntags: [开发]\nstatus: active\ndomain: 开发\nimportance: 0.5\n"
                  "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n内容\n")
            k = kb_health.kpi(tmp)
            for key in ("two_hop_reach", "first_hit_rate", "calibration_brier",
                        "staleness", "reuse"):
                self.assertIn(key, k)
            # 拿不到数据的必须**诚实报 None**，不能假装 0
            self.assertIsNone(k["first_hit_rate"])
            self.assertIsNone(k["calibration_brier"])
            # 两跳：首页 → 笔记A → 笔记B 全部可达
            self.assertGreaterEqual(k["two_hop_reach"], 0.99)
            # 易刷量必须被标注为"仅诊断"
            self.assertIn("_diagnostics_only", k)
            self.assertIn("KPI", k["_diagnostics_only"]["_warn"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_first_hit_and_calibration_are_not_faked(self):
        """没有黄金集/复习数据时必须是 N/A —— 严禁用 0 冒充"测过了"。"""
        import kb_health
        tmp = Path(tempfile.mkdtemp(prefix="kb-kpi2-"))
        try:
            k = kb_health.kpi(tmp)
            self.assertIsNone(k["first_hit_rate"])
            self.assertIsNone(k["calibration_brier"])
            self.assertIsNone(k["staleness"])       # 没有高 importance 笔记 → N/A
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BitemporalSchemaTest(unittest.TestCase):
    """双时态字段：契约里必须声明，且迁移默认 dry-run。"""

    def test_schema_declares_bitemporal_fields(self):
        import json
        sch = json.loads((PIPELINE.parent / "reference" / "kb-schema.json").read_text(encoding="utf-8"))
        for f in ("valid_from", "valid_to", "recorded_at", "superseded_by", "conflict_with"):
            self.assertIn(f, sch["fields"], f"契约缺少双时态字段 {f}")
        self.assertIn("bitemporal", sch)

    def test_migration_defaults_to_dry_run(self):
        """--apply 才写；默认预演（要改几百篇 frontmatter，必须先给清单）。"""
        src = (PIPELINE / "kb_schema.py").read_text(encoding="utf-8")
        self.assertIn("预演模式（默认）：未写入任何文件", src)
        self.assertIn("bitemporal", src)
