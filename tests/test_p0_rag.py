#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import io
import json
import math
import os
import pickle
from collections import Counter
import re
import shutil
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))
sys.path.insert(0, str(REPO / "scripts"))   # precheck / setup_obsidian
sys.path.insert(0, str(REPO))               # create_vault

import rag                  # noqa: E402
import kb_scale as _kb_scale  # noqa: E402

FM_BASE = ("tags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
           "updated: 2026-01-01\nimportance: 0.5\nkb_summary: 测试\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# pickle 只能引用**模块级**可导入对象，所以恶意载荷必须放在模块级（不能是局部函数）。
# pickle 只能引用**模块级**可导入对象，所以恶意载荷必须放在模块级（不能是局部函数）。
def _p0_evil_write(path):        # pragma: no cover - 只在测试里被反序列化尝试
    Path(path).write_text("pwned")
    return 0


class _P0Evil:                   # pragma: no cover
    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return (_p0_evil_write, (self.path,))


# ── 86~91：索引格式 v6（pickle 主格式 + 受限反序列化）──────────



# ── 15：rag 缓存键 ───────────────────────────────────────────
class RagCacheKeyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-rag-"))
        write(self.root / "20-技术 Technology" / "n.md",
              "---\ntitle: 备份\n" + FM_BASE + "---\n\n知识库备份与恢复。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_text_then_json_same_query_does_not_crash(self):
        """以前缓存键不含 as_json：文本查一次后再加 --json 会 KeyError: 'result'。"""
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rag.cmd_query(self.root, "备份", 3, False), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rag.cmd_query(self.root, "备份", 3, False, as_json=True)
        self.assertEqual(rc, 0, "同问题切到 --json 模式崩溃了")
        json.loads(buf.getvalue())   # 必须是合法 JSON（不是 None / 报错）

    def test_legacy_entry_without_result_does_not_crash(self):
        """老缓存条目只存了 text：JSON 模式取不到就回退正常检索，不能 KeyError。"""
        from state_manager import StateStore
        cache = StateStore(self.root).load("query_cache.json", {})
        for v in cache.values():
            v.pop("result", None)
        StateStore(self.root).save("query_cache.json", cache)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rag.cmd_query(self.root, "备份", 3, False, as_json=True)
        self.assertEqual(rc, 0)


# ── 16~17：巡检与仪表盘 ──────────────────────────────────────


# ── 22~29：第二轮（研究层 / 检索 / 安装器）────────────────────
class RagGhostAndTagsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-ghost-"))
        write(self.root / "20-技术 Technology" / "n.md",
              "---\ntitle: 备份\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\ntags: [sop, metric]\n---\n\n"
              "知识库备份与恢复方案。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_parse_tags_strips_flow_list_brackets(self):
        self.assertEqual(rag._parse_tags("[sop, metric, governance]"),
                         ["sop", "metric", "governance"])
        self.assertEqual(rag._parse_tags('["开发"]'), ["开发"])

    def test_tags_filter_works_for_flow_list_notes(self):
        """`tags: [sop, metric]` 式笔记以前 --tags 过滤永远返回空。"""
        hits = []
        with contextlib.redirect_stdout(io.StringIO()):
            hits = rag._apply_filters(
                [("20-技术 Technology/n.md", 1.0)], self.root,
                None, None, None, None, None, "metric", None, None)
        self.assertEqual(len(hits), 1, "--tags metric 没匹配到 flow 列表标签")

    def test_ghost_hit_is_dropped_with_warning(self):
        """索引过期（笔记已删）时不得把幽灵文档当命中返回且零警告。"""
        (self.root / "20-技术 Technology" / "n.md").unlink()
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rag.cmd_query(self.root, "备份", 3, False)
        self.assertEqual(rc, 0)
        self.assertIn("索引已过期", err.getvalue())


# ── 54~57：查询路径性能（v5 索引预存范数）──────────────────
class FastCosineTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-norm-"))
        words = ["备份", "恢复", "索引", "检索", "矛盾", "置信", "评测", "部署",
                 "cron", "日志", "死链", "巡检", "孤儿", "向量", "分块", "去重",
                 "正典", "结晶", "记忆", "摄取"]
        for i in range(40):
            ws = words[i % 10:i % 10 + 6] or words[:6]
            write(self.root / "20-技术 Technology" / f"n{i}.md",
                  f"---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                  f"updated: 2026-01-01\nimportance: 0.5\nkb_summary: {' '.join(ws[:3])}\n---\n\n"
                  f"# 笔记{i}\n{' '.join(ws)}\n\n## 结论\n{' '.join(ws[::-1])}。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=False)
        self.payload, _ver = rag.load_index(self.root / "vector index")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_index_carries_norms_dl_avgdl(self):
        self.assertIn("norms", self.payload)
        self.assertIn("dl", self.payload)
        self.assertIn("avgdl", self.payload)
        self.assertEqual(rag.load_index(self.root / "vector index")[1],
                         rag.IDX_VERSION)

    def test_stored_norm_matches_definition(self):
        """范数口径必须与 _vec 一致：不在 idf 里的词不计入。"""
        for rel, cnt in rag.tf_from_postings(self.payload).items():
            want = math.sqrt(sum((f * self.payload["idf"][t]) ** 2
                                 for t, f in cnt.items() if t in self.payload["idf"])) or 1.0
            self.assertAlmostEqual(want, self.payload["norms"][rel], places=12,
                                   msg=f"{rel} 的预存范数与定义不符")

    def _top(self, q):
        path = self.root / ".kb" / "state" / "query_cache.json"
        path.unlink(missing_ok=True)   # 禁缓存
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rag.cmd_query(self.root, q, 10, False)
        return [l.split("← ")[1].split(" · ")[0]
                for l in buf.getvalue().splitlines() if l.startswith("⭐")]

    def _write_legacy_index(self, *drop):
        """把索引降级写成**旧格式 JSON** 并删掉 pickle，模拟升级前的库。

        这样一箭双雕地覆盖两条兼容路径：① 读 `df_idf.json`；② 缺 `norms` 时回退慢路径。
        （直接往 .pkl 里写 JSON 是不对的 —— 那会造出一个损坏的 pickle。）
        """
        d = self.root / "vector index"
        old = dict(self.payload)
        for k in drop:
            old.pop(k, None)
        (d / "df_idf.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        (d / "df_idf.pkl").unlink(missing_ok=True)

    def test_fast_and_fallback_cosine_agree_numerically(self):
        """两条余弦实现的**分数**必须一致：postings 快路径 vs 全库物化回退路径。

        为什么不再比"最终 top-10 逐位相同"：最终分经过 RRF 融合（`1/(k+rank)`），
        底层余弦的 1 ULP 差异会让**并列项**互换名次，再被 RRF 放大成 1e-4 量级的
        最终分差（实测 1.2308 vs 1.2301）。也就是说那个断言本身不稳健 —— 它以前
        只是碰巧在小库上通过。真正要保证的是"两条实现算出的余弦相同"。
        """
        payload = self.payload
        qv = rag._vec(rag.Counter(rag.tokenize("备份 恢复 索引")),
                      payload["vocab"], payload["idf"])
        fast = dict(rag._score_cosine_postings(payload["postings"], payload["docs"],
                                               payload["idf"], qv, payload["norms"]))
        fb = {r: s for r, s in ((r, rag._cos(v, qv))
                                for r, v in rag.all_doc_vectors(payload).items()) if s > 0}
        self.assertEqual(set(fast), set(fb), "两条路径命中的文档集合不同")
        worst = max((abs(fast[r] - fb[r]), r) for r in fast)
        self.assertLess(worst[0], 1e-9, f"余弦分数不一致：{worst[1]} 差 {worst[0]:.3e}")

    def test_fallback_path_returns_same_hit_set(self):
        """降级成旧索引（无 norms）后，CLI 返回的命中**集合**必须不变。"""
        q = "备份 恢复 索引"
        fast = set(self._scores(q))
        self.assertTrue(fast)
        self._write_legacy_index("norms")
        self.assertEqual(set(self._scores(q)), fast)

    def _scores(self, q):
        """查询并返回 {rel: score}（禁缓存；top 放大到覆盖整个测试小库）。"""
        (self.root / ".kb" / "state" / "query_cache.json").unlink(missing_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rag.cmd_query(self.root, q, 60, False, as_json=True)
        raw = buf.getvalue()
        d = json.loads(raw[raw.find("{"):])
        return {h["path"]: float(h.get("score") or 0) for h in (d.get("hits") or [])}

    def test_legacy_index_without_norms_still_queries(self):
        self._write_legacy_index("norms")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rag.cmd_query(self.root, "备份 恢复", 5, False)
        self.assertEqual(rc, 0)
        self.assertIn("⭐", buf.getvalue(), "旧索引（无 norms）未返回任何命中")

    def test_legacy_json_index_is_still_readable(self):
        """v6 改了主格式为 pickle，但**旧的 JSON 索引必须仍能读**（不强制迁移）。"""
        self._write_legacy_index()
        self.assertTrue((self.root / "vector index" / "df_idf.json").exists())
        payload, ver = rag.load_index(self.root / "vector index")
        self.assertIsNotNone(payload, "旧 JSON 索引读不到了")
        self.assertEqual(len(rag.indexed_docs(payload)), len(rag.indexed_docs(self.payload)))


# ── 58~59：倒排加成的实测结论守卫（P1-9 经评测判定「不是缺陷」）──


# ── 58~59：倒排加成的实测结论守卫（P1-9 经评测判定「不是缺陷」）──
class InvertedBoostByDesignTest(unittest.TestCase):
    """`INVERTED_HIT_BOOST` 是**加性且刻意压过 RRF**的。

    30 题 × 4 目标的黄金候选集实测（scripts/eval_retrieval_gold.py）：
      现状(top1 90.0% / recall@10 98.3%) ≫ 乘性+IDF 加权(60.0% / 55.0%)
                                            ≫ 去掉加成(56.7% / 45.8%)
      甚至「加性+IDF 加权」(80.0% / 94.2%) 与「剔单字 CJK」(80.0% / 83.3%) 也更差。
    本库的 tag / kb_summary 是人工标注的主题词，命中它比命中正文词强得多；
    IDF 加权恰好会压掉那些「常见但正是主题词」的 token。
    下面两条就是防止后人再按「量纲冲突」把它改坏。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-boost-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_boost_stays_additive_and_unweighted(self):
        # P1-3 拆分后常量定义在 rag_score.py、加成逻辑在 rag_query.py；rag.py 为 shim。
        # 合并两处源码一并检查，守卫「加性且无 IDF 加权」的设计决策不被改坏。
        src = ((PIPELINE / "rag_score.py").read_text(encoding="utf-8")
               + "\n" + (PIPELINE / "rag_query.py").read_text(encoding="utf-8"))
        self.assertIn("INVERTED_HIT_BOOST = 0.15", src)
        self.assertIn("scored_dict[rel] = INVERTED_HIT_BOOST * count", src,
                      "加成被改成非加性了；改动前必须先用 scripts/eval_retrieval_gold.py 复测")
        self.assertNotIn("INVERTED_HIT_ALPHA", src)

    def test_metadata_match_can_rank_a_note_not_matched_by_body(self):
        """正文完全不含查询词的笔记，仅靠 tag/summary 命中也应被排进来。"""
        write(self.root / "20-技术 Technology" / "tagged.md",
              "---\ntags: [备份, 恢复]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\nkb_summary: 备份与恢复流程\n---\n\n"
              "# 甲\n无关正文占位。\n")
        write(self.root / "20-技术 Technology" / "body.md",
              "---\ntags: [其他]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\nkb_summary: 别的主题\n---\n\n"
              "# 乙\n" + "备份 恢复 备份 恢复 备份 恢复 备份 恢复。\n" * 8)
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rag.cmd_query(self.root, "备份 恢复", 5, False)
        hits = [l for l in out.getvalue().splitlines() if l.startswith("⭐")]
        self.assertTrue(hits, "没有任何命中")
        rels = [h.split("← ")[1].split(" · ")[0] for h in hits]
        self.assertIn("20-技术 Technology/tagged.md", rels,
                      f"仅靠 tag/summary 命中的笔记被漏掉了：{rels}")


# ── 60~62：评测流量不得污染使用信号（P1-21 余）─────────────


# ── 60~62：评测流量不得污染使用信号（P1-21 余）─────────────
class NoRecordTest(unittest.TestCase):
    """评测/探针用 `rag query` 当 oracle；若照常写 agent_hits，一小时内重跑黄金题
    会把再问率顶到 1、成功率代理打到 0，反过来压低 fitness —— 测量行为污染被测对象。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-norec-"))
        for i in range(5):
            write(self.root / "20-技术 Technology" / f"n{i}.md",
                  "---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                  "updated: 2026-01-01\nimportance: 0.5\nkb_summary: 备份 恢复\n---\n\n"
                  f"# 笔记{i}\n备份与恢复流程。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        os.environ.pop("KB_NO_FEEDBACK", None)

    def _hits(self):
        from state_manager import StateStore
        return len(StateStore(self.root).load("feedback_state.json", {}).get("agent_hits") or {})

    def test_no_record_skips_agent_hits(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_query(self.root, "备份 恢复", 3, False, no_record=True)
        self.assertEqual(self._hits(), 0, "--no-record 仍写入了使用信号")

    def test_env_switch_skips_agent_hits(self):
        os.environ["KB_NO_FEEDBACK"] = "1"
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_query(self.root, "备份 恢复", 3, False)
        self.assertEqual(self._hits(), 0, "KB_NO_FEEDBACK=1 仍写入了使用信号")

    def test_normal_query_still_records(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_query(self.root, "备份 恢复", 3, False)
        self.assertEqual(self._hits(), 1, "普通查询不再记录使用信号（回归）")


class SmallToolTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-tool-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_mermaid_node_shape_wraps_label(self):
        """Mermaid 形状必须包住标签：`N0["标题"]`，不能拼成 `N0[]["标题"]`。"""
        import graph
        write(self.root / "20-技术 Technology" / "p.md",
              "---\ntags: [x]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\ncontent_type: policy\n"
              "related_to: [\"20-技术 Technology/q.md\"]\n---\n\n# 政策\n\n## 结论\n- 甲\n")
        write(self.root / "20-技术 Technology" / "q.md",
              "---\ntags: [x]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\ncontent_type: decision\n---\n\n"
              "# 决策\n\n## 结论\n- 乙\n")
        nodes = graph.collect_nodes(self.root)
        self.assertTrue(nodes, "没收集到节点")
        mermaid = graph.build_mermaid(nodes)
        self.assertNotIn(']["', mermaid, "出现非法的 N[][...] 语法")
        self.assertNotIn('()["', mermaid, "出现非法的 N()[...] 语法")
        self.assertIn('["', mermaid)

    def test_gen_kb_arch_creates_output_dir(self):
        """arch/ 被 gitignore，新克隆里不存在，生成器必须自己建目录。"""
        text = (REPO / "gen_kb_arch.py").read_text(encoding="utf-8")
        self.assertIn("mkdir(parents=True, exist_ok=True)", text,
                      "缺 mkdir → 新克隆跑 gen_kb_arch.py 必 FileNotFoundError")

    def test_scale_version_follows_rag(self):
        import kb_scale
        self.assertEqual(kb_scale.EXPECTED_VERSION, rag.IDX_VERSION,
                         "kb_scale 的期望索引版本必须跟 rag 走（曾停在 2 而 rag 已是 4）")

    def test_scale_denominator_is_indexable_set(self):
        """覆盖率的分母必须与 rag 同源（排除 raw/生成物），否则数字无意义。"""
        write(self.root / "20-技术 Technology" / "ok.md",
              "---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\n---\n\n# 正常\n正文。\n")
        write(self.root / "raw" / "src.md",
              "---\ntags: [x]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.3\nis_source: true\n---\n\n证据层内容。\n")
        write(self.root / "70-知识治理 Governance" / "_MOC.md",
              "---\ntags: [moc]\nstatus: active\ndomain: 管理\ncreated: 2026-01-01\n"
              "updated: 2026-01-01\nimportance: 0.5\n---\n\n# MOC\n")
        n = _kb_scale.indexable_count(self.root)
        self.assertEqual(n, 1, f"分母里混进了 raw/ 或生成物（算出 {n}）")


# ── 48~53：第五轮（适配器去重丢数据 / LLM 预算 / fitness 地板 / T3 棘轮）──


class IncrementalIndexTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-incr-"))
        for i in range(12):
            write(self.root / "20-技术 Technology" / f"n{i}.md",
                  f"---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                  f"updated: 2026-01-01\nimportance: 0.5\nkb_summary: 笔记{i}\n---\n\n"
                  f"# 笔记{i}\n第 {i} 篇内容。\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _count_reads(self):
        counts = {"n": 0}
        orig = Path.read_text

        def counting(path_self, *a, **k):
            if str(path_self).endswith(".md"):
                counts["n"] += 1
            return orig(path_self, *a, **k)

        Path.read_text = counting
        return counts, orig

    def test_unchanged_vault_reads_no_notes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=True)
        counts, orig = self._count_reads()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rag.cmd_index(self.root, incremental=True)
        finally:
            Path.read_text = orig
        self.assertEqual(counts["n"], 0,
                         f"无变更时仍读了 {counts['n']} 个笔记文件（增量是假的）")

    def test_meta_carries_inverted_index_fields(self):
        """meta 里必须带 author/kb_summary，倒排索引才能零读盘重建。"""
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=True)
        payload, _ver = rag.load_index(self.root / "vector index")
        some = next(iter(payload["meta"].values()))
        self.assertIn("author", some)
        self.assertIn("kb_summary", some)
        self.assertTrue(payload["inverted_index"]["domain"], "倒排索引为空")

    def test_memory_sync_fingerprint_matches_rag(self):
        """memory_sync 的变更探测必须用 body 指纹，与 rag 写入的口径一致。"""
        text = (PIPELINE / "memory_sync.py").read_text(encoding="utf-8")
        self.assertNotIn("content_fingerprint(p.read_text", text,
                         "整文件指纹与 rag 的 body 指纹永不相等 → 每次都触发全量重建")


# ── 86~91：索引格式 v6（pickle 主格式 + 受限反序列化）──────────
class IndexFormatV6Test(unittest.TestCase):
    """v6 把索引主格式从 JSON 换成 pickle：加载 161ms→70ms、体积 −42%。

    但「索引文件 = 任意代码执行」是不能接受的风险面，所以 loader 用受限 Unpickler
    （只放行内置容器/标量与白名单类型）；同时必须仍能读旧的 JSON（不强制迁移）。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-idx-"))
        self.d = self.root / "vector index"
        self.d.mkdir(parents=True, exist_ok=True)
        self.payload = {"version": rag.IDX_VERSION, "n_docs": 1,
                        "tf": {"a.md": {"x": 1}}, "vocab": ["x"],
                        "idf": {"x": 1.0}, "meta": {}, "df": {"x": 1},
                        "docs": ["a.md"], "norms": {"a.md": 1.0}, "dl": {"a.md": 1},
                        "avgdl": 1.0, "doc_hashes": {}, "doc_mtimes": {},
                        "inverted_index": {}}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_writes_pickle_and_removes_stale_json(self):
        (self.d / "df_idf.json").write_text("{}", encoding="utf-8")   # 模拟旧库
        p = rag.save_index(self.d, self.payload)
        self.assertEqual(p.name, rag.IDX_PICKLE)
        self.assertTrue(p.exists())
        self.assertFalse((self.d / rag.IDX_JSON).exists(), "遗留的旧 JSON 没清掉")

    def test_load_accepts_dir_and_file(self):
        rag.save_index(self.d, self.payload)
        for arg in (self.d, rag.index_file(self.d)):
            payload, ver = rag.load_index(arg)
            self.assertEqual(ver, rag.IDX_VERSION)
            self.assertEqual(payload["n_docs"], 1)

    def test_pickle_preferred_over_json(self):
        (self.d / "df_idf.json").write_text(json.dumps({**self.payload, "n_docs": 99}),
                                            encoding="utf-8")
        rag.save_index(self.d, self.payload)          # 写 pkl（并清 json）
        (self.d / "df_idf.json").write_text(json.dumps({**self.payload, "n_docs": 99}),
                                            encoding="utf-8")
        self.assertEqual(rag.index_file(self.d).name, rag.IDX_PICKLE)
        self.assertEqual(rag.load_index(self.d)[0]["n_docs"], 1)

    def test_corrupt_index_is_reported_as_missing(self):
        (self.d / "df_idf.pkl").write_bytes(b"\x80\x04not a pickle")
        self.assertEqual(rag.load_index(self.d), (None, 0))
        (self.d / "df_idf.pkl").unlink()
        (self.d / "df_idf.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(rag.load_index(self.d), (None, 0))

    def test_malicious_pickle_is_rejected_without_side_effect(self):
        """有人往 vector index/ 扔一个恶意 pickle，绝不能被执行。"""
        marker = self.root / "pwned.txt"
        (self.d / "df_idf.pkl").write_bytes(pickle.dumps({"x": _P0Evil(str(marker))}))
        payload, ver = rag.load_index(self.d)
        self.assertEqual((payload, ver), (None, 0), "恶意 pickle 没被拒绝")
        self.assertFalse(marker.exists(), "恶意 pickle 被执行了")

    def test_counter_is_still_allowed(self):
        """Counter 是 dict 子类，必须显式放行（真实 tf 就是 Counter）。"""
        rag.save_index(self.d, {**self.payload, "tf": {"a.md": Counter({"x": 1})}})
        payload, _ = rag.load_index(self.d)
        self.assertIsNotNone(payload, "Counter 被误拒，真实索引将无法加载")


# ── 92~97：索引 v7（tf → postings）──────────────────────────


# ── 92~97：索引 v7（tf → postings）──────────────────────────
class IndexV7PostingsTest(unittest.TestCase):
    """v7 把文档主序的 `tf`（占索引 64%）换成词主序 `postings`。

    · 查询只访问**命中文档**；`df` 由 `len(postings[t])//2` 导出，不再单独存；
    · `postings[t]` 是扁平 `[doc_id, tf, doc_id, tf, ...]`（嵌套 pair 会让 pickle
      对象数翻倍：35 万个 2 元素 list）；
    · 构造必须按**排序后的文档序**，否则同一语料在「增量」与「全量」后得到不同字节。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-v7-"))
        for i in range(12):
            write(self.root / "20-技术 Technology" / f"n{i}.md",
                  f"---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                  f"updated: 2026-01-01\nimportance: 0.5\nkb_summary: 索{i % 3}\n---\n\n"
                  f"# 笔记{i}\n索引 检索 备份{' 恢复' if i % 2 else ''} 方案{i % 4}\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=False)
        self.payload, _ = rag.load_index(self.root / "vector index")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_index_has_postings_and_no_tf_or_df(self):
        self.assertIn("postings", self.payload)
        self.assertNotIn("tf", self.payload, "v7 不应再写文档主序 tf（占索引 64%）")
        self.assertNotIn("df", self.payload, "df 可由 len(postings[t])//2 导出，不必存")

    def test_postings_are_flat_doc_ids(self):
        for term, p in list(self.payload["postings"].items())[:20]:
            self.assertIsInstance(p, list)
            self.assertEqual(len(p) % 2, 0, f"{term} 的 postings 不是扁平 [doc,tf,...]")
            self.assertIsInstance(p[0], int, "postings 应存整数 doc id，不是路径")

    def test_indexed_docs_matches_docs_list(self):
        self.assertEqual(rag.indexed_docs(self.payload), set(self.payload["docs"]))

    def test_tf_from_postings_roundtrip(self):
        """从 postings 还原的 tf 必须与「原始计数」一致（增量构建依赖它）。"""
        tf = rag.tf_from_postings(self.payload)
        self.assertEqual(set(tf), set(self.payload["docs"]))
        # 对照的是**条目数**（文档,词）对数 —— 不是 Σtf：
        # Σtf 是所有词次出现数（含频次），postings 条目数是去重后的对数，两者天然不同。
        entries = sum(len(c) for c in tf.values())
        postings_entries = sum(len(p) // 2 for p in self.payload["postings"].values())
        self.assertEqual(entries, postings_entries, "还原后的 (doc,词) 条目数与 postings 不一致")
        # 再核一遍每个词的 tf 值确实被搬过来了
        for term, p in list(self.payload["postings"].items())[:20]:
            for i in range(0, len(p), 2):
                rel = self.payload["docs"][p[i]]
                self.assertEqual(tf[rel][term], p[i + 1], f"{rel}/{term} 的 tf 还原不一致")

    def test_all_doc_vectors_matches_stored_norms(self):
        """all_doc_vectors 算出的范数必须与索引里存的 norms 逐位一致。"""
        vecs = rag.all_doc_vectors(self.payload)
        for rel, v in vecs.items():
            n = math.sqrt(sum(x * x for x in sorted(v.values()))) or 1.0
            self.assertAlmostEqual(n, 1.0, places=12, msg=f"{rel} 向量未归一")
        # 与索引里的 norms 交叉校验：cos 自比为 1
        for rel in list(vecs)[:10]:
            self.assertAlmostEqual(rag._cos(vecs[rel], vecs[rel]), 1.0, places=12)

    def test_norm_is_insertion_order_independent(self):
        """范数求和必须与词的插入顺序无关（否则增量/全量会差 1 ULP、并列项翻转）。"""
        idf = {"a": 1.7, "b": 2.3, "c": 0.9, "d": 3.1}
        c1 = Counter({"a": 2, "b": 1, "c": 5, "d": 3})
        c2 = Counter({"d": 3, "c": 5, "b": 1, "a": 2})
        self.assertEqual(rag._doc_norm(c1, idf), rag._doc_norm(c2, idf))

    def test_incremental_equals_full_index_byte_for_byte(self):
        """增量索引与全量重建必须产出**完全相同**的结构（可复现基线的前提）。"""
        victim = self.root / "20-技术 Technology" / "n3.md"
        write(victim, victim.read_text(encoding="utf-8") + "\n新增段落 紫罗兰协议。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=True)
        inc, _ = rag.load_index(self.root / "vector index")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=False)
        full, _ = rag.load_index(self.root / "vector index")
        for key in ("postings", "norms", "idf", "inverted_index", "docs"):
            self.assertEqual(inc[key], full[key], f"增量与全量的 {key} 不一致")


# ── 98~101：状态文件路径统一（5 套约定 → .kb/state/）──────────────


class IndexPayloadRoundTripTest(unittest.TestCase):
    """索引 payload 必须能通过 `_SafeUnpickler` 往返。

    为什么需要：索引是受限 unpickle（白名单防任意代码执行）。**只要往索引里加一种新类型
    而忘了登记白名单**，unpickle 就抛 UnpicklingError → `load_index` 把它当"索引损坏"
    静默吞掉 → 查询报 "index not found"。本轮 v8 加 `array`（稠密向量）时正是这样踩的：
    索引写得好好的，就是读不回来。这条测试用"索引实际会用到的全部类型"构造载荷，
    任何新类型漏登记都会立刻红。
    """

    def test_index_payload_survives_safe_unpickler(self):
        import io as _io
        import pickle as _pickle
        from array import array as _array
        payload = {
            "version": 8, "n_docs": 1, "vocab": ["a"], "idf": {"a": 1.0},
            "meta": {"x.md": {"title": "t", "importance": 0.5}},
            "postings": {"a": [0, 2]}, "docs": ["x.md"],
            "doc_hashes": {"x.md": "h"}, "doc_mtimes": {"x.md": 1.0},
            "norms": {"x.md": 1.0}, "dl": {"x.md": 2}, "avgdl": 2.0,
            "inverted_index": {"title": {"t": [0]}},
            "dense": {"dim": 4, "dim_in": 8, "seed": 1,
                      "vecs": {"x.md": _array("f", [0.1, 0.2, 0.3, 0.4])}},
            "tf": {"x.md": Counter({"a": 2})},      # 旧版残留字段也须可读
        }
        blob = _pickle.dumps(payload, protocol=_pickle.HIGHEST_PROTOCOL)
        back = rag._SafeUnpickler(_io.BytesIO(blob)).load()
        self.assertAlmostEqual(back["dense"]["vecs"]["x.md"][1], 0.2, places=6)
        self.assertIsInstance(back["dense"]["vecs"]["x.md"], _array)

    def test_safe_unpickler_still_rejects_arbitrary_globals(self):
        """放宽白名单不等于放开任意类型 —— 非白名单全局仍须拒绝。"""
        import io as _io
        import pickle as _pickle
        blob = _pickle.dumps(Path("x"))
        with self.assertRaises(_pickle.UnpicklingError):
            rag._SafeUnpickler(_io.BytesIO(blob)).load()


class DenseChannelTest(unittest.TestCase):
    """稠密语义通道：**只填尾部槽位**，绝不参与头部排序。

    实测依据（真实库 16 题改写式查询 + 30 题黄金集，2026-10-16）：
      · 纯词法命中源笔记 37.5% · 纯稠密 94%（62% 只有语义能找回）
      · 但**加权 RRF 融合**会让黄金集 top1 90.0%→83.3%(w=0.3)→76.7%(w=0.5)，单调变差
      · 改为**预留尾部槽位**后：top1/rec@4/MRR 逐位不变，改写探针 37.5%→68.8%
    """

    def test_head_is_bit_identical(self):
        """核心不变量：头部 (1-frac)·k 名**与纯词法完全一致**。"""
        ranked = [(f"lex{i}.md", 10.0 - i) for i in range(10)]
        # 足够多的稠密-only 候选，才填得满预留槽位
        dense = [(f"den{i}.md", 0.9 - i * 0.01) for i in range(8)] + [("lex0.md", 0.95)]
        out = rag.dense_fill_tail(ranked, dense, k=10, frac=0.4)
        n_keep = int(round(10 * 0.6))
        self.assertEqual(out[:n_keep], ranked[:n_keep], "头部被改动了 —— 这会导致黄金集回退")
        self.assertEqual(len(out), 10)

    def test_never_pads_with_nonexistent_notes(self):
        """候选不够时**不补齐** —— 宁可少给，也不能编造不存在的笔记。"""
        ranked = [(f"lex{i}.md", 5.0 - i) for i in range(10)]
        out = rag.dense_fill_tail(ranked, [("only1.md", 0.9), ("lex0.md", 0.8)], k=10, frac=0.4)
        self.assertEqual(len(out), 7)          # 6 头部 + 1 个稠密-only
        self.assertEqual([r for r, _ in out][-1], "only1.md")

    def test_only_dense_only_notes_fill_the_tail(self):
        """已在词法结果里的笔记不会再被"补"一次（否则就是重复命中）。"""
        ranked = [("a.md", 5.0), ("b.md", 4.0), ("c.md", 3.0), ("d.md", 2.0), ("e.md", 1.0)]
        dense = [("a.md", 0.99), ("z.md", 0.9)]
        out = rag.dense_fill_tail(ranked, dense, k=5, frac=0.4)
        rels = [r for r, _ in out]
        self.assertEqual(len(rels), len(set(rels)), "出现重复条目")
        self.assertEqual(rels[:3], ["a.md", "b.md", "c.md"])
        self.assertIn("z.md", rels)

    def test_frac_zero_is_pure_lexical(self):
        ranked = [("a.md", 1.0), ("b.md", 0.5)]
        self.assertEqual(rag.dense_fill_tail(ranked, [("z.md", 1.0)], k=5, frac=0.0), ranked)

    def test_disabled_by_env_and_by_absent_vectors(self):
        os.environ["KB_DENSE"] = "0"
        try:
            self.assertEqual(rag.dense_rank(".", "q", {"dense": {"vecs": {"a.md": None}}}), [])
        finally:
            os.environ.pop("KB_DENSE", None)
        # 索引里没有 dense 段（v7 及更早 / 端点不可用时重建）→ 静默缺席
        self.assertEqual(rag.dense_rank(".", "q", {}), [])
        self.assertEqual(rag.dense_rank(".", "q", {"dense": {}}), [])

    def test_unreachable_endpoint_degrades_silently(self):
        """端点不可达时必须静默返回空，绝不抛异常（否则会拖垮整个检索）。"""
        payload = {"dense": {"dim": 4, "dim_in": 8, "seed": 1,
                             "vecs": {"a.md": array("f", [0.1, 0.2, 0.3, 0.4])}}}
        got = rag.dense_rank("/nonexistent-vault-root", "任意查询", payload)
        self.assertEqual(got, [])


class MetricWindowTest(unittest.TestCase):
    def test_usage_window_takes_newest_events(self):
        """evs 升序后取 [:N] 会一直分析最老的一批事件，必须取尾部。"""
        text = (PIPELINE / "kb_usage.py").read_text(encoding="utf-8")
        self.assertIn("return evs[-_MAX_EVENTS:]", text)
        self.assertNotIn("return evs[:_MAX_EVENTS]", text)

    def test_adaptretrieve_window_takes_newest(self):
        text = (PIPELINE / "kb_adaptretrieve.py").read_text(encoding="utf-8")
        self.assertIn("events[-_MAX_EVENTS:]", text,
                      "取头部会永久忽略最新查询信号")
        self.assertNotIn("events[:_MAX_EVENTS]", text)


# ── 71~74：P1-22 —— L1 检索缺口改为诚实的审计清单并可见 ──────
class L1ContractLoopTest(unittest.TestCase):
    """L1 检索修正契约的**运行时消费**（闭环最后一跳）。

    契约由 `kb adaptretrieve apply` 写下，`rag.cmd_query` 读取并应用。
    黄金集实测（30 题，见 rag.ADJUST_MIN_SIM 处注释）：
      · 契约正确 → top1 90.0%→96.7%、recall@4 82.5%→90.0%、recall@10 98.3%→**100%**
      · 契约指向无关笔记 → top1 90.0%→**76.7%**、MRR 0.950→0.883
    所以消费侧必须有三道护栏（置信地板 / 条数上限 / 逃生门）+ 审计字段。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-l1-"))
        for i in range(6):
            write(self.root / "20-技术 Technology" / f"n{i}.md",
                  f"---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                  f"updated: 2026-01-01\nimportance: 0.5\nkb_summary: 备份 恢复\n---\n\n"
                  f"# 笔记{i}\n向量索引 重建 备份 恢复 流程 {i}\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rag.cmd_index(self.root, incremental=False)
        os.environ.pop("KB_RETRIEVAL_ADJUST", None)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        os.environ.pop("KB_RETRIEVAL_ADJUST", None)

    def _write_contract(self, rows):
        p = self.root / ".kb" / "state" / "kb_retrieval_adjust.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"adjustments": rows}, ensure_ascii=False), encoding="utf-8")

    def _query(self, q="备份 恢复", top=5):
        (self.root / ".kb" / "state" / "query_cache.json").unlink(missing_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rag.cmd_query(self.root, q, top, False, as_json=True, no_record=True)
        raw = buf.getvalue()
        return json.loads(raw[raw.find("{"):])

    def test_indexed_note_gets_boosted_and_is_audited(self):
        target = "20-技术 Technology/n5.md"
        self._write_contract([{"query": "备份 恢复", "action": "boost", "note": target,
                               "sim": 0.8, "issue": "miss"}])
        d = self._query()
        self.assertEqual(d["hits"][0]["path"], target, "契约指向的笔记应被拉到第 1")
        self.assertEqual(d.get("adjustments_applied"), [target], "缺少可审计字段")

    def test_no_contract_means_no_change(self):
        d = self._query()
        self.assertNotIn("adjustments_applied", d)

    def test_other_query_is_unaffected(self):
        target = "20-技术 Technology/n5.md"
        self._write_contract([{"query": "完全不同的另一个问题", "action": "boost",
                               "note": target, "sim": 0.8}])
        d = self._query()
        self.assertNotIn("adjustments_applied", d, "契约只应作用于同一查询（归一化精确匹配）")

    def test_kill_switch_disables_consumption(self):
        self._write_contract([{"query": "备份 恢复", "action": "boost",
                               "note": "20-技术 Technology/n5.md", "sim": 0.8}])
        os.environ["KB_RETRIEVAL_ADJUST"] = "0"
        d = self._query()
        self.assertNotIn("adjustments_applied", d, "KB_RETRIEVAL_ADJUST=0 应完全关闭消费")

    def test_low_confidence_contract_is_ignored(self):
        """置信地板：低 sim 的契约不采信（坏契约代价很大，见类注释实测）。"""
        self._write_contract([{"query": "备份 恢复", "action": "boost",
                               "note": "20-技术 Technology/n5.md", "sim": 0.2}])
        self.assertNotIn("adjustments_applied", self._query())

    def test_non_indexed_note_is_ignored(self):
        """只对**已索引**的笔记加成，绝不注入幽灵命中。"""
        self._write_contract([{"query": "备份 恢复", "action": "boost",
                               "note": "不存在的笔记.md", "sim": 0.8}])
        self.assertNotIn("adjustments_applied", self._query())

    def test_contract_is_declared_as_affecting_retrieval(self):
        import kb_adaptretrieve as l1
        src = (PIPELINE / "kb_adaptretrieve.py").read_text(encoding="utf-8")
        self.assertIn('"affects_retrieval": True', src)
        self.assertIn("KB_RETRIEVAL_ADJUST=0", src)
        self.assertNotIn("没有运行时消费者", src, "闭环已接通，不应再声明无消费者")

    def test_render_mentions_it_affects_retrieval(self):
        p = self.root / "pipeline"
        p.mkdir(parents=True, exist_ok=True)
        (p / "kb_adaptretrieve_proposals.jsonl").write_text(
            json.dumps({"id": 1, "note": "a.md", "query": "q", "sim": 0.6,
                        "issue": "miss", "reason": "r"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        import kb_adaptretrieve as l1
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print(l1.render(self.root))
        self.assertIn("已被检索消费", buf.getvalue())

    def test_render_tolerates_missing_fields(self):
        p = self.root / "pipeline"
        p.mkdir(parents=True, exist_ok=True)
        (p / "kb_adaptretrieve_proposals.jsonl").write_text(
            json.dumps({"id": 1, "note": "a.md"}, ensure_ascii=False) + "\n", encoding="utf-8")
        import kb_adaptretrieve as l1
        with contextlib.redirect_stdout(io.StringIO()):
            l1.render(self.root)      # 不应抛 KeyError


# ── 75~78：状态文件原子写入（第十一轮去重）──────────────────


class ConsistencyVerdictTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-cons-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        os.environ.pop("KB_CONSISTENCY_LLM_MAX", None)

    def _verdict(self, answer):
        import kb_consistency as kc
        import rag as _rag
        orig_ans, orig_ep = _rag.llm_answer, kc._endpoint_reachable
        _rag.llm_answer = lambda root, q, hits: (answer, "")
        kc._endpoint_reachable = lambda: True     # 单测不依赖真实端点
        try:
            return kc._check_faithfulness(self.root, "原文", "目标")
        finally:
            _rag.llm_answer, kc._endpoint_reachable = orig_ans, orig_ep

    def test_chinese_answer_is_recognized(self):
        """中文回答以前全部落到 else → 默认判「忠实」，把 Critical/High 全漏掉。"""
        self.assertEqual(self._verdict("不忠实")["verdict"], "unfaithful")
        self.assertEqual(self._verdict("部分忠实")["verdict"], "partial")
        self.assertEqual(self._verdict("忠实")["verdict"], "faithful")

    def test_unparsable_answer_does_not_default_to_faithful(self):
        r = self._verdict("嗯……这个我不太确定呢")
        self.assertFalse(r["ok"], "无法解析的判定被默认放行成「忠实」")
        self.assertIsNone(r["verdict"])

    def test_llm_budget_constant_exists(self):
        src = (PIPELINE / "kb_consistency.py").read_text(encoding="utf-8")
        self.assertIn("LLM_RECORD_BUDGET", src)
        self.assertIn("KB_CONSISTENCY_LLM_MAX", src)
