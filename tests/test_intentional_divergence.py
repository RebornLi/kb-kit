#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_intentional_divergence.py — 「有意差异」登记表 + 钉住守卫。

## 为什么有这个文件

分册精读（`.survey/`）列出了 16 组「重复实现」。其中一部分**看似相同实则不同**，
合并会改变行为。对这类，正确的做法不是"顺手统一"，而是：

  1. **登记**：把每一处差异连同「为什么必须不同」写下来；
  2. **钉住**：用探针固定当前行为 —— 谁把它们悄悄合并/改动，测试立刻红；
  3. **显式化**：让读者一眼看出这里有几套口径、各自用在哪。

已经**安全合并**的部分（实测等价，各有守卫测试）：
  · CJK 正则 9 份 → `kb_text.CJK`（第十一轮）
  · 原子写 4 处 → `kb_io.atomic_write_text`（第十一轮）
  · 状态文件 5 套路径 → `state_manager` 原语（第十六轮）
  · `kb_rsi.parse_f` → `kb_metautil.parse_importance`、`kb_claim._parse_d` → `parse_date`、
    `rag._cos` / `kb_rsi.cos`(dict 分支) → `kb_text.cos`、`curate_prompt.load_schema`
    → `curate.load_schema`（本轮，均经抽样逐例/逐位验证）
  · `kb_schema_migrate.iter_notes` → 委托 `kb_io.iter_notes`（本轮：它原会写生成物）
  · 同类项**改名**让语义显式：`kb_usage._norm_query`/`_cos_vec`、
    `embed_rerank._norm_vec`/`_cos_vec`、`kb_retriage._parse_tags_line`、
    `kb_calibrate._parse_day_epoch`、`memory_sync.DUP_SIM_FALLBACK`

本文件覆盖**不能合并**的那些。
"""
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
TEMPLATE = REPO / "template-vault"
sys.path.insert(0, str(PIPELINE))

import embed_rerank      # noqa: E402
import kb_calibrate      # noqa: E402
import kb_io            # noqa: E402
import kb_text          # noqa: E402
import kb_metautil      # noqa: E402
import kb_config        # noqa: E402
import kb_domain        # noqa: E402
import kb_infer         # noqa: E402
import kb_engine         # noqa: E402
import kb_retriage       # noqa: E402
import kb_rsi            # noqa: E402
import kb_schema_migrate  # noqa: E402
import kb_usage          # noqa: E402
import rag               # noqa: E402
import sync              # noqa: E402
import taxonomy          # noqa: E402

TOKEN_PROBE = "中文 abc 测试-doc 123"
BLOCK_FM = "---\ntags:\n  - 开发\n  - 管理\nstatus: active\n---\n正文\n"
QUOTED_FM = '---\ntags: "开发"\nimportance: "0.7"\n---\n正文\n'


class TokenizeDivergenceTest(unittest.TestCase):
    """至少 4 种切分口径，各有明确用途 —— 合并会改变检索/去重/领域推断。"""

    def test_unigram_plus_bigram(self):
        """rag / kb_text.tokenize：CJK unigram + bigram（检索索引口径）。"""
        self.assertEqual(rag.tokenize(TOKEN_PROBE), kb_text.tokenize(TOKEN_PROBE))
        self.assertIn("中文", rag.tokenize(TOKEN_PROBE))    # bigram
        self.assertIn("中", rag.tokenize(TOKEN_PROBE))      # unigram

    def test_unigram_only_for_domain_inference(self):
        """kb_infer._tokens：CJK **仅 unigram**（infer_domain 用，避免 bigram 噪声）。"""
        got = kb_infer._tokens(TOKEN_PROBE)
        self.assertIn("中", got)
        self.assertNotIn("中文", got, "infer_domain 口径不应有 bigram")

    def test_rsi_uses_two_char_segments(self):
        """kb_rsi.tokenize：CJK 取 **2 字以上连续段**（整段中文不切，相似度口径不同）。"""
        got = kb_rsi.tokenize(TOKEN_PROBE)
        self.assertNotIn("中", got, "kb_rsi 不切单字")
        self.assertIn("中文", got)

    def test_all_four_are_actually_different(self):
        """若哪天有人把它们统一了，这条会立刻失败（差异必须是**有意**的）。"""
        variants = {tuple(kb_text.tokenize(TOKEN_PROBE)), tuple(kb_infer._tokens(TOKEN_PROBE)),
                    tuple(kb_rsi.tokenize(TOKEN_PROBE))}
        self.assertEqual(len(variants), 3, "三套分词口径被合并了 —— 请先确认是否有意")


class FrontmatterReaderDivergenceTest(unittest.TestCase):
    """4 个 frontmatter 读取器，在「块列表 / 引号值」上分成两组。

    它们是**有意不同**的：kb_io/sync 走"值即字符串"的朴素口径，
    kb_rsi/kb_engine 保留原样（引号不剥、块列表给空串）。
    调用方（如 kb_query）已知这一点并做了补偿，所以不能单方面统一。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-div-fm-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _readers(self, text):
        p = self.root / "x.md"
        p.write_text(text, encoding="utf-8")
        return {
            "kb_io": kb_io.parse_frontmatter(text)[0],
            "sync": sync.read_note(p)[0],
            "kb_rsi": kb_rsi.load_note(p)[0],
            "kb_engine": kb_engine.read_note(p)[0],
        }

    def test_block_list_handling_differs(self):
        got = self._readers(BLOCK_FM)
        self.assertIsNone(got["kb_io"]["tags"], "kb_io 不解析块列表（得 None）")
        self.assertIsNone(got["sync"]["tags"])
        self.assertEqual(got["kb_rsi"]["tags"], "", "kb_rsi 把块列表读成空串")
        self.assertEqual(got["kb_engine"]["tags"], "")

    def test_quote_stripping_differs(self):
        got = self._readers(QUOTED_FM)
        self.assertEqual(got["kb_io"]["tags"], "开发", "kb_io 去引号")
        self.assertEqual(got["sync"]["tags"], "开发")
        self.assertEqual(got["kb_rsi"]["tags"], '"开发"', "kb_rsi 保留引号（调用方已知）")
        self.assertEqual(got["kb_engine"]["tags"], '"开发"')

    def test_plain_frontmatter_agrees(self):
        """朴素输入上四者一致 —— 这正是它们"看起来像重复"的原因。"""
        plain = "---\ntags: [开发]\nstatus: active\n---\n正文\n"
        got = self._readers(plain)
        self.assertEqual({str(v) for v in got.values()}, {str({"tags": "[开发]", "status": "active"})})


class IterNotesNowUnifiedTest(unittest.TestCase):
    """`kb_schema_migrate.iter_notes` 曾自有一套更松的排除集（会遍历 `pipeline/` 与 `logs/`），
    导致 `kb schema-migrate --apply` 给生成物 `pipeline/EVOLVED.md` 写 frontmatter。

    现已**委托 kb_io.iter_notes**（唯一遍历权威）。保留这条测试是为了钉住"它们必须一致"，
    避免有人再引入第二套排除集。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-div-it-"))
        (self.root / "20-技术 Technology").mkdir(parents=True)
        (self.root / "20-技术 Technology" / "a.md").write_text("# a\n", encoding="utf-8")
        (self.root / "pipeline").mkdir()
        (self.root / "pipeline" / "EVOLVED.md").write_text("# 生成物\n", encoding="utf-8")
        (self.root / "logs").mkdir()
        (self.root / "logs" / "x.md").write_text("# 日志\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_both_iterators_agree(self):
        a = {str(p.relative_to(self.root)) for p in kb_io.iter_notes(self.root)}
        b = {str(p.relative_to(self.root)) for p in kb_schema_migrate.iter_notes(self.root)}
        self.assertEqual(a, b, "又出现了第二套遍历口径")

    def test_generated_artifact_is_not_migrated(self):
        """写工具绝不能碰生成物 —— 这正是当年 schema-migrate 的问题。"""
        got = {str(p.relative_to(self.root)) for p in kb_schema_migrate.iter_notes(self.root)}
        self.assertEqual(got, {"20-技术 Technology/a.md"})
        self.assertNotIn("pipeline/EVOLVED.md", got)
        self.assertNotIn("logs/x.md", got)


class DateTagNormDivergenceTest(unittest.TestCase):
    def test_date_contracts_differ(self):
        """kb_metautil.parse_date → datetime|None；kb_calibrate → date（失败回退 1970）。"""
        import datetime
        self.assertIsInstance(kb_metautil.parse_date("2026-01-02"), datetime.datetime)
        self.assertIsNone(kb_metautil.parse_date("abc"))
        self.assertIsInstance(kb_calibrate._parse_day_epoch("2026-01-02"), datetime.date)
        self.assertNotIsInstance(kb_calibrate._parse_day_epoch("2026-01-02"), datetime.datetime)
        self.assertEqual(kb_calibrate._parse_day_epoch("abc"), datetime.date(1970, 1, 1))

    def test_tags_parsers_differ_on_spaces(self):
        """taxonomy/kb_retriage 保留值内空格；rag 按空白切分。"""
        v = '["带空格 的"]'
        self.assertEqual(taxonomy.parse_tags(v), ["带空格 的"])
        self.assertEqual(kb_retriage._parse_tags_line("tags: " + v), ["带空格 的"])
        self.assertEqual(rag._parse_tags(v), ["带空格", "的"], "rag 口径按空白切")

    def test_norm_cos_have_three_shapes(self):
        """dict→dict（稀疏向量）/ str→str（查询串）/ list→list（embedding）。"""
        self.assertEqual(kb_text.norm({"a": 3.0}), {"a": 1.0})
        self.assertEqual(kb_usage._norm_query("A, b!"), "ab")
        self.assertEqual(embed_rerank._norm_vec([3.0, 4.0]), [0.6, 0.8])

    def test_cos_dict_branch_is_delegated_but_list_branch_is_local(self):
        """kb_rsi.cos 只保留 embedding list 分支；dict 分支已委托 kb_text.cos。"""
        self.assertEqual(kb_rsi.cos({"a": 1.0}, {"a": 1.0}), 1.0)
        self.assertAlmostEqual(kb_rsi.cos([1.0, 0.0], [1.0, 0.0]), 1.0)


class ConfigDriftTest(unittest.TestCase):
    """配置/常量漂移守卫：同一事实只应有**一个**权威定义。"""

    def test_no_module_redefines_a_kb_constants_name(self):
        import kb_constants
        import re
        offenders = []
        for f in sorted(PIPELINE.glob("*.py")):
            if f.name == "kb_constants.py":
                continue
            src = f.read_text(encoding="utf-8")
            for name in dir(kb_constants):
                if name.startswith("_") or not name.isupper():
                    continue
                if re.search(rf"^{name}\s*=", src, re.M):
                    offenders.append(f"{f.name}:{name}")
        self.assertEqual(offenders, [],
                         "这些常量在 kb_constants 之外又被定义了一份（改一处漏一处）：\n"
                         + "\n".join(offenders))

    def test_schema_status_matches_code(self):
        import json
        sch = json.loads((TEMPLATE / "reference" / "kb-schema.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(sch["fields"]["status"]["values"]),
                         sorted(kb_domain.STATUS_SET))

    def test_schema_layers_are_all_known_to_code(self):
        import json
        sch = json.loads((TEMPLATE / "reference" / "kb-schema.json").read_text(encoding="utf-8"))
        for layer in sch["fields"]["kb_layer"]["values"]:
            self.assertIn(layer, kb_metautil.LAYER_MULTIPLIER,
                          f"契约声明的 kb_layer `{layer}` 在代码里没有对应权重")

    def test_domain_whitelist_is_consistent_everywhere(self):
        import json
        t = json.loads((TEMPLATE / "reference" / "taxonomy.json").read_text(encoding="utf-8"))
        dt = json.loads((TEMPLATE / "reference" / "domain-taxonomy.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(kb_config.DOMAIN_WHITELIST), sorted(t["domains"]))
        self.assertEqual(sorted(kb_config.DOMAIN_WHITELIST),
                         sorted(k for k in dt if not k.startswith("_")))


if __name__ == "__main__":
    unittest.main()
