#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_growth_fixes.py — 成长引擎中等问题回归测试。

覆盖：
  1. refine 条目离开收件箱（移入 _review/），不再每轮重复
  2. merge 真合并进最相似目标笔记并移除源（不再当 trash）
  3. feedback ingest 命中计数按 HIT_DECAY 衰减（不再单调膨胀）
  4. link_engine 跳过运行期报告产物（不污染/不自引用）
  5. clean.do_chunk 幂等（重复运行不产生 -p1-p1 级联）
  6. rag 索引排除运行期报告产物

运行：python3 -m unittest discover -s tests
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import intake_triage  # noqa: E402
import feedback_loop  # noqa: E402
import link_engine  # noqa: E402
import clean  # noqa: E402
import rag  # noqa: E402
import recall_schedule  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


NOTE_FM = "tags: [\"管理\"]\nstatus: active\ndomain: 管理\nimportance: 0.6\nkb_summary: 测试\n"


class GrowthFixesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-growthfix-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # 1. refine 消解
    def test_refine_moves_out_of_inbox(self):
        src = self.root / "00-收件箱 Inbox" / "need.md"
        write(src, f"---\n{NOTE_FM}---\n待补写内容。\n")
        rc = intake_triage.apply_moves(
            self.root, [{"action": "refine", "rel": "00-收件箱 Inbox/need.md"}],
            move=True, trash=False)
        self.assertEqual(rc, 0)
        self.assertFalse(src.exists(), "refine 原件应离开收件箱")
        self.assertTrue((self.root / "70-知识治理 Governance" / "_review" / "need.md").exists())

    # 2. 真合并
    def test_merge_into_target(self):
        src = self.root / "00-收件箱 Inbox" / "dup.md"
        tgt = self.root / "20-技术 Technology" / "orig.md"
        write(src, f"---\n{NOTE_FM}---\n重复正文要点。\n")
        write(tgt, f"---\n{NOTE_FM}---\n原始笔记正文。\n")
        rc = intake_triage.apply_moves(
            self.root,
            [{"action": "merge", "rel": "00-收件箱 Inbox/dup.md", "sim_to": "20-技术 Technology/orig.md"}],
            move=True, trash=False)
        self.assertEqual(rc, 0)
        self.assertFalse(src.exists(), "合并后源应被移除")
        t = tgt.read_text(encoding="utf-8")
        self.assertIn("## 合并自 00-收件箱 Inbox/dup.md", t)
        self.assertIn("重复正文要点", t)

    # 3. 命中衰减
    def test_hits_decay_on_ingest(self):
        feedback_loop.save_state(self.root, {"hits": {"a.md": 10}})
        feedback_loop.ingest(self.root)  # 无索引 → 只衰减不新增
        hits = feedback_loop.load_state(self.root).get("hits", {})
        self.assertAlmostEqual(hits.get("a.md", 0), 6.0, places=3)  # 10 * HIT_DECAY(0.6)

    # 4. link 跳过报告产物
    def test_link_skips_generated_reports(self):
        write(self.root / "intake_triage.md", "# triage 报告\n\n普通知识 备份\n")
        write(self.root / "20-技术 Technology/a.md", f"---\n{NOTE_FM}---\n本地检索 余弦\n")
        write(self.root / "20-技术 Technology/b.md", f"---\n{NOTE_FM}---\n本地检索 余弦 方案\n")
        rels, _notes, _recs, _cross = link_engine.suggestions(self.root, 0.1)
        self.assertIn("20-技术 Technology/a.md", rels)
        self.assertNotIn("intake_triage.md", rels, "报告产物不应参与补链")

    # 5. 分块幂等
    def test_clean_chunk_is_idempotent(self):
        body = "".join(f"## 章节{i}\n\n" + "内容" * 120 + "\n\n" for i in range(1, 4))
        write(self.root / "20-技术 Technology/long.md", f"---\n{NOTE_FM}---\n{body}")
        created1, _ = clean.do_chunk(self.root, 200)
        self.assertGreater(created1, 0, "首次应分块")
        created2, _ = clean.do_chunk(self.root, 200)
        self.assertEqual(created2, 0, "二次运行不应再分块（幂等）")

    # 6. 报告产物不入索引
    def test_rag_excludes_generated_reports(self):
        write(self.root / "20-技术 Technology/keep.md", f"---\n{NOTE_FM}---\n可检索知识\n")
        write(self.root / "intake_triage.md", "# triage\n\n噪声报告\n")
        rag.cmd_index(self.root)
        import json as _json
        p = _json.loads((self.root / "vector index" / "df_idf.json").read_text(encoding="utf-8"))
        self.assertIn("20-技术 Technology/keep.md", p["tf"])
        self.assertNotIn("intake_triage.md", p["tf"], "报告产物不应入索引")

    # 7. 补链去重：已有出链不再重复建议
    def test_link_apply_dedupes_existing_links(self):
        a = self.root / "20-技术 Technology/a.md"
        b = self.root / "20-技术 Technology/b.md"
        c = self.root / "20-技术 Technology/c.md"
        write(a, f"---\n{NOTE_FM}---\n本地检索 余弦 方案 [[b]]\n")
        write(b, f"---\n{NOTE_FM}---\n本地检索 余弦 方案\n")
        write(c, f"---\n{NOTE_FM}---\n本地检索 余弦 方案\n")
        link_engine.apply_links(self.root, 0.3, 50)
        ta = a.read_text(encoding="utf-8")
        self.assertIn("## 🔗 智能建议链接", ta)
        self.assertIn("[[20-技术 Technology/c.md]]", ta, "未链的 c 应被建议")
        self.assertNotIn("b.md", ta.split("## 🔗 智能建议链接")[1],
                         "已链的 b 不应再出现在建议区块")

    # 8. recall 状态改用 StateStore（原子写，.kb/state/）
    def test_recall_state_uses_statestore(self):
        write(self.root / "20-技术 Technology/n.md",
              f"---\ntags: [\"管理\"]\nstatus: active\ndomain: 管理\nimportance: 0.6\nkb_summary: s\n---\n正文。\n")
        recall_schedule.mark(self.root, 10, "20-技术 Technology/n.md")
        self.assertTrue((self.root / ".kb" / "state" / "recall_state.json").exists(),
                        "回忆状态应写入 .kb/state/（StateStore 原子写）")


if __name__ == "__main__":
    unittest.main()
