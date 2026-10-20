#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
sys.path.insert(0, str(REPO / "scripts"))   # precheck / setup_obsidian
sys.path.insert(0, str(REPO))               # create_vault

import link_engine  # noqa: E402



class AgentPluginTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-agent-"))
        (self.root / "kb-agent.json").write_text(
            json.dumps({"version": 1, "agents": {}}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_json_path_recovered_from_positional(self):
        import agent_detect_plugin as adp
        self.assertEqual(adp.AgentDetectPlugin._json_path(
            {"json": True, "_positional": ["/p/m.json"]}), "/p/m.json")
        self.assertEqual(adp.AgentDetectPlugin._json_path(
            {"json_path": "/p/explicit.json"}), "/p/explicit.json")
        self.assertIsNone(adp.AgentDetectPlugin._json_path({"json": True}),
                          "裸 --json 必须判为缺路径，不能写出 sources.json=true")

    def test_ingest_list_is_read_only(self):
        import agent_detect_plugin as adp
        from plugin_base import PluginContext
        before = (self.root / "kb-agent.json").read_text(encoding="utf-8")
        p = adp.AgentDetectPlugin()
        p.initialize(PluginContext(vault_root=str(self.root)))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = p.execute("ingest", {"root": str(self.root), "list": True})
        self.assertEqual(rc, 0)
        self.assertEqual((self.root / "kb-agent.json").read_text(encoding="utf-8"), before,
                         "--list 不应该写库")

    def test_adapter_exit_code_is_not_the_write_count(self):
        import agent_json_plugin as ajp
        from plugin_base import PluginContext
        p = ajp.AgentJSONPlugin()
        p.initialize(PluginContext(vault_root=str(self.root)))
        missing = self.root / "nope.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(p.execute("json", {"root": str(self.root),
                                                "json_path": str(missing)}), 1,
                             "源缺失必须返回非 0")
        src = self.root / "m.json"
        src.write_text(json.dumps({"tables": {"records": {
            "r1": {"content": "这是一条足够长的记忆内容用于测试。", "kind": "fact"}}}}),
            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = p.execute("json", {"root": str(self.root), "json_path": str(src)})
        self.assertEqual(rc, 0, "写成功必须返回 0（以前返回写入条数，破坏 set -e）")


# ── 48~53：第五轮（适配器去重丢数据 / LLM 预算 / fitness 地板 / T3 棘轮）──
class AdapterDedupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-adapter-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _turns(self):
        return [
            {"thread_id": "t1", "turn_id": "1", "user": "同一个标题\n旧内容 A",
             "reply": "答 A", "reasoning": "", "ts": 1700000000000},
            {"thread_id": "t1", "turn_id": "2", "user": "同一个标题\n新内容 B",
             "reply": "答 B", "reasoning": "", "ts": 1700000100000},
        ]

    def test_same_title_new_content_is_not_dropped(self):
        """去重键以前只有标题（`codex-{domain}-{title}`），同标题新内容被永久丢弃。"""
        import memory_ingest_sqlite as m
        oi, oe = m._iter_sqlite_dirs, m._extract_turns
        m._iter_sqlite_dirs = lambda d: ["/fake/codex.db"]
        m._extract_turns = lambda p: self._turns()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                n1 = m.ingest(self.root, self.root)
            with contextlib.redirect_stdout(io.StringIO()):
                n2 = m.ingest(self.root, self.root)
        finally:
            m._iter_sqlite_dirs, m._extract_turns = oi, oe
        self.assertEqual(n1, 2, "同标题不同内容的两轮必须都落库")
        self.assertEqual(n2, 0, "同一批再跑必须幂等（内容级去重）")
        text = "".join(f.read_text(encoding="utf-8") for f in self.root.rglob("*.md"))
        self.assertIn("旧内容 A", text)
        self.assertIn("新内容 B", text)

    def test_adapter_dedup_key_includes_content_hash(self):
        for name in ("memory_ingest_sqlite.py", "memory_ingest_opencode.py"):
            src = (PIPELINE / name).read_text(encoding="utf-8")
            self.assertIn("dedup_key", src, f"{name} 的去重键没有内容指纹")
            self.assertNotIn("summary in seen", src,
                             f"{name} 仍在用标题级去重（会丢同标题新内容）")


class LinkMultiSignalGateTest(unittest.TestCase):
    """建链的多信号闸：≥2 信号且至少一个结构性信号（标签/同源/共引）。

    依据（KG Refinement, doi:10.3233/SW-160218）：误链沿图传播放大，漏链近乎零成本。
    真实库实测：闸门滤掉 3107 条建议中的 943 条（30%），且**全部**是
    `lexical+neighbor`（同域+用词相近，无共享标签/同源/共引）—— 正是最弱的一类。
    """

    def test_single_lexical_signal_is_rejected(self):
        self.assertFalse(link_engine.passes_link_gate(["lexical"]))

    def test_domain_proximity_alone_is_not_enough(self):
        """同域只是"弱补充"，不能作为建链理由。"""
        self.assertFalse(link_engine.passes_link_gate(["lexical", "neighbor"]))

    def test_structural_signal_admits(self):
        for s in ("tag", "source", "co-ref"):
            self.assertTrue(link_engine.passes_link_gate(["lexical", s]), s)

    def test_signals_detects_tag_and_co_ref(self):
        notes = {
            "a.md": {"tags": {"开发"}, "out": {"x", "y"}, "domain": "开发", "source": "src/A"},
            "b.md": {"tags": {"开发"}, "out": {"y", "z"}, "domain": "开发", "source": "src/A"},
            "c.md": {"tags": set(), "out": set(), "domain": "开发", "source": ""},
        }
        sigs = link_engine.link_signals("a.md", "b.md", notes)
        self.assertIn("tag", sigs)
        self.assertIn("source", sigs)
        self.assertIn("co-ref", sigs)
        self.assertTrue(link_engine.passes_link_gate(sigs))
        # 无标签/无同源/无共引 → 只剩 lexical + neighbor → 不该建链
        self.assertFalse(link_engine.passes_link_gate(
            link_engine.link_signals("a.md", "c.md", notes)))

    def test_constants_are_documented_knobs(self):
        self.assertGreaterEqual(link_engine.LINK_MIN_SIGNALS, 2)
        self.assertEqual(link_engine.LINK_STRUCTURAL, {"tag", "source", "co-ref"})
