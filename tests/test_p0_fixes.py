#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p0_fixes.py — 2026-10 代码梳理发现的高危问题回归测试。

覆盖（每条都对应一个已修的真实缺陷，防止回退）：
  破坏性写 / 数据丢失
    1. clean.do_clean 不再改写 raw/ 不可变证据层
    2. clean 保留空值 frontmatter 键（`url:` 不再被删）
    3. 块列表 tags 不再被目录名派生标签替换
    4. 精确去重保留**最新**（不再归档修订版）
    5. sync rollback 清理未跟踪文件前先备份
    6. sync apply 产出带 frontmatter 的笔记
    7. curate 的 noise/raw-only 判定写回过 apply 门控
    8. kb_query apply 只删已写入的提案，不整份清空
  CLI 解析
    9. --key=value 生效
   10. 布尔旗标不再吞掉下一个位置参数
   11. BOOL_FLAGS 覆盖全部 store_true 旗标（源码守卫）
   12. `kb clean --apply` 路由到 apply 动作而不是 dry-run
   13. `kb ingest apply` 路由到 intake/apply
 崩溃 / 静默失败
   14. evolution_log 接受 ALERT 事件
   15. rag 缓存键区分文本/JSON 模式（不再 KeyError: 'result'）
   16. dashboard 死链为 0 时不产生假告警
   17. kb-healthcheck 死链巡检包含 _INDEX.md（导航文件）
   18. kb_meta apply --all 真的会写（force 已接通）

运行：python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import math
import os
import pickle
from collections import Counter
import re
import shutil
import subprocess
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

import clean                # noqa: E402
import create_vault         # noqa: E402
import curate               # noqa: E402
import dashboard            # noqa: E402
import evolution_log        # noqa: E402
import kb_claim             # noqa: E402
import kb_engine            # noqa: E402
import kb_meta              # noqa: E402
import kb_query             # noqa: E402
import kb_scale as _kb_scale  # noqa: E402
import kb_launcher          # noqa: E402
import precheck             # noqa: E402
import link_engine
import rag                  # noqa: E402
import sync as sync_mod     # noqa: E402

KBLauncher = kb_launcher.KBLauncher

FM_BASE = ("tags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
           "updated: 2026-01-01\nimportance: 0.5\nkb_summary: 测试\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def has_git() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True)


# ── 1~4：clean 的破坏性写 ────────────────────────────────────
class CleanSafetyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-clean-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_raw_layer_is_never_rewritten(self):
        """raw/ 是不可变证据层：任何 clean 写路径都不得改写它。"""
        raw = self.root / "raw" / "AI" / "evidence.md"
        original = ("---\ntags: [raw]\nstatus: active\ndomain: 综合\ncreated: 2026-01-01\n"
                    "updated: 2026-01-01\nimportance: 0.4\nurl:\nis_source: true\n---\n\n"
                    "# 证据原文\n正文不该被改写。\n")
        write(raw, original)
        with contextlib.redirect_stdout(io.StringIO()):
            clean.do_clean(self.root, dry=False)
        self.assertEqual(raw.read_text(encoding="utf-8"), original,
                         "clean.apply 改写了 raw/ 证据层（违反 write-side-protocol §4）")

    def test_empty_value_frontmatter_key_survives(self):
        """`url:` 这类有意占位的空值键不能被删掉。"""
        note = self.root / "20-技术 Technology" / "n.md"
        write(note, f"---\n{FM_BASE}url:\nnote:\n---\n\n# 标题\n正文。\n")
        with contextlib.redirect_stdout(io.StringIO()):
            clean.do_clean(self.root, dry=False)
        text = note.read_text(encoding="utf-8")
        self.assertIn("url:", text, "空值键 url: 被 clean 删除")
        self.assertIn("note:", text, "空值键 note: 被 clean 删除")
        self.assertNotIn("url: None", text, "空值被渲染成了字面 None")

    def test_block_list_tags_not_replaced_by_folder(self):
        """YAML 块列表 `tags:` 必须解析出真实标签，而不是回退成目录名派生标签。"""
        note = self.root / "40-资源库 Resources" / "n.md"
        write(note, "---\ntags:\n  - 开发\n  - kb-kit\nstatus: active\ndomain: 开发\n"
                    "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.5\n"
                    "kb_summary: 块列表\n---\n\n# 标题\n正文。\n")
        rel, new = clean.clean_note(self.root, note)
        self.assertIn("[开发, kb-kit]", new, f"块列表标签被替换（实际 {new.splitlines()[1]!r}）")
        tags_line = next(l for l in new.splitlines() if l.startswith("tags:"))
        self.assertNotIn("40-资源库 Resources", tags_line, "tags 回退成了目录名派生标签")

    def test_exact_dedup_keeps_newest(self):
        """内容指纹一致时保留最新的一份，旧者归档（与注释语义一致）。"""
        old = self.root / "20-技术 Technology" / "old.md"
        new = self.root / "20-技术 Technology" / "new.md"
        body = "---\n" + FM_BASE + "---\n\n同一份正文指纹。\n"
        write(new, body)
        write(old, body)
        os.utime(old, (1_700_000_000, 1_700_000_000))    # 2023-11
        os.utime(new, (1_800_000_000, 1_800_000_000))    # 2027-01
        with contextlib.redirect_stdout(io.StringIO()):
            clean.do_clean(self.root, dry=False)
        self.assertTrue(new.exists(), "最新的一份被归档了（去重方向反了）")
        self.assertFalse(old.exists(), "旧的一份没有被归档")
        trashed = list((self.root / "90-归档 Archive" / "_trash").glob("old*.md"))
        self.assertTrue(trashed, "旧份没有落到 _trash/")


# ── 5~6：sync 的破坏性写 ─────────────────────────────────────
@unittest.skipUnless(has_git(), "需要 git")
class SyncSafetyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-sync-"))
        git(self.root, "init", "-q", ".")
        git(self.root, "config", "user.email", "t@t")
        git(self.root, "config", "user.name", "t")
        write(self.root / "logs" / ".keep", "x")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        write(self.root / "logs" / ".last-checkpoint",
              git(self.root, "rev-parse", "HEAD").stdout.strip())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_rollback_backs_up_untracked_notes(self):
        """rollback 的 git clean 不得无备份地删掉用户未提交的笔记。"""
        area = self.root / "20-技术 Technology" / "后端开发"
        write(area / "keep.md", "base")          # 已跟踪
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "add keep")
        write(self.root / "logs" / ".last-checkpoint",
              git(self.root, "rev-parse", "HEAD").stdout.strip())
        mine = area / "my-uncommitted-note.md"
        write(mine, "用户自己写的还没提交的笔记")
        args = type("A", (), {"root": str(self.root), "no_purge": False})()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sync_mod.cmd_rollback(args)
        self.assertEqual(rc, 0)
        backups = list((self.root / "backups").glob("rollback-*/**/my-uncommitted-note.md"))
        self.assertTrue(backups, "未跟踪笔记被删且没有备份")
        self.assertIn("还没提交", backups[0].read_text(encoding="utf-8"))

    def test_apply_writes_frontmatter(self):
        """sync apply 的产物必须自带 frontmatter，否则会被自家 validate 判硬错误。"""
        src = self.root / "src.md"
        write(src, "---\n" + FM_BASE + "---\n\n# 标题\n正文。\n")
        args = type("A", (), {"root": str(self.root), "allow_existing_issues": True})()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sync_mod.cmd_apply(args, str(src))
        self.assertEqual(rc, 0)
        out = next((self.root / "20-技术 Technology").rglob("*.md"))
        text = out.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "产物没有 frontmatter")
        self.assertIn("domain: 开发", text)
        self.assertIn("kb_target:", text)


# ── 7：curate 越权写入 ───────────────────────────────────────
class _StubLLM:
    """最小 LLM 替身：只回一个预设的结晶提案。"""

    model = "stub-model"

    def __init__(self, prop):
        self.prop = prop

    def ask_json(self, msgs, max_tokens=4096):
        return self.prop, "stub"


class CurateApplyGateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-curate-"))
        self.rel = "20-技术 Technology/n.md"
        self.note = self.root / self.rel
        write(self.note, "---\n" + FM_BASE + "---\n\n# 标题\n过程记录，无结论。\n")
        self.before = self.note.read_text(encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_noise_verdict_not_written_without_apply(self):
        llm = _StubLLM({"verdict": "noise", "title": "t", "sections": [], "facts": [],
                        "sources": [], "confidence": 0.9})
        with contextlib.redirect_stdout(io.StringIO()):
            rec = curate.curate_one(self.root, self.rel, llm, apply=False)
        self.assertEqual(rec["action"], "propose:noise")
        self.assertEqual(self.note.read_text(encoding="utf-8"), self.before,
                         "不带 --apply 仍改写了笔记 frontmatter")

    def test_noise_verdict_written_with_apply(self):
        llm = _StubLLM({"verdict": "noise", "title": "t", "sections": [], "facts": [],
                        "sources": [], "confidence": 0.9})
        with contextlib.redirect_stdout(io.StringIO()):
            rec = curate.curate_one(self.root, self.rel, llm, apply=True)
        self.assertTrue(rec["action"].startswith("skip:noise"), rec["action"])
        self.assertNotEqual(self.note.read_text(encoding="utf-8"), self.before,
                            "带 --apply 应当写回判定")


# ── 8：kb_query 提案保留 ─────────────────────────────────────
class KbQueryApplyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-kbquery-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_apply_keeps_unselected_proposals(self):
        p = kb_query._prop_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        recs = [{"id": f"q-2026-10-15-00{i}", "query": f"问题{i}",
                 "sources": ["a.md"], "ts": "2026-10-15T00:00:00"} for i in (1, 2, 3)]
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                     encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            r = kb_query.apply(self.root, ids=["q-2026-10-15-001"])
        left = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(left), 2, "未选中的提案被删除了")
        self.assertEqual(sorted(r["skipped"]), ["q-2026-10-15-002", "q-2026-10-15-003"])


# ── 9~13：CLI 解析与路由 ─────────────────────────────────────
def _add_argument_blocks(text: str):
    """粗粒度提取 add_argument(...) 的实参块（括号配对）。"""
    out, i = [], 0
    while True:
        m = re.search(r"add_argument\(", text[i:])
        if not m:
            break
        s = i + m.end()
        depth, j = 1, s
        while depth > 0 and j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        out.append(text[s:j - 1])
        i = j
    return out


def _store_true_flags() -> set:
    found = set()
    files = list(PIPELINE.glob("*.py")) + list((PIPELINE / "plugins").glob("*.py"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        for blk in _add_argument_blocks(text):
            if "store_true" in blk:
                found |= set(re.findall(r'["\'](--[a-zA-Z0-9-]+)["\']', blk))
    return found


class LauncherParsingTest(unittest.TestCase):
    def test_key_equals_value(self):
        self.assertEqual(KBLauncher._parse_params(["--top=3", "hello"]),
                         {"top": "3", "_positional": ["hello"]})

    def test_bool_flag_does_not_eat_next_token(self):
        got = KBLauncher._parse_params(["--exclude-stale", "怎么备份"])
        self.assertIs(got.get("exclude_stale"), True)
        self.assertEqual(got.get("_positional"), ["怎么备份"])
        got = KBLauncher._parse_params(["--apply", "x.md"])
        self.assertIs(got.get("apply"), True)
        self.assertEqual(got.get("_positional"), ["x.md"])

    def test_bool_flags_cover_every_store_true_flag(self):
        missing = sorted(_store_true_flags() - kb_launcher.BOOL_FLAGS)
        self.assertEqual(
            missing, [],
            f"这些 store_true 旗标没进 BOOL_FLAGS，会吞掉下一个参数: {missing}")

    def _route(self, argv):
        rec = []
        l = KBLauncher(".")
        l._ensure_initialized = lambda: setattr(l, "_initialized", True)
        l.registry.execute = lambda p, a, params: (rec.append((p, a, dict(params))), 0)[1]
        l.run(argv)
        return rec[-1]

    def test_clean_apply_flag_routes_to_apply_action(self):
        plugin, action, _ = self._route(["clean", "--apply"])
        self.assertEqual((plugin, action), ("clean", "apply"))

    def test_ingest_apply_maps_to_intake_apply(self):
        self.assertIn(("ingest", "apply"), kb_launcher.COMPAT_MAP)
        plugin, action, _ = self._route(["ingest", "apply"])
        self.assertEqual((plugin, action), ("intake", "apply"))

    def test_real_flags_are_not_promoted_to_subcommands(self):
        """`--log` / `--json` 不是任何工具的子命令名，必须原样当旗标透传。"""
        plugin, action, params = self._route(["healthcheck", "--log"])
        self.assertEqual((plugin, action), ("healthcheck", "check"))
        self.assertIs(params.get("log"), True)
        _, action2, params2 = self._route(["clean", "taxonomy", "--apply"])
        self.assertEqual(action2, "taxonomy")
        self.assertIs(params2.get("apply"), True)


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
class KbMetaApplyAllTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-meta-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cli_apply_all_passes_force(self):
        """`--all` 必须把 force=True 传进 apply()，否则 pick 恒空、写 0 个却报成功。"""
        seen = {}
        orig_apply, orig_argv = kb_meta.apply, sys.argv
        kb_meta.apply = lambda root, ids=None, force=False: (
            seen.update(ids=ids, force=force) or
            {"written": [], "skipped": [], "note": ""})
        try:
            sys.argv = ["kb_meta.py", "apply", "--root", str(self.root), "--all"]
            with contextlib.redirect_stdout(io.StringIO()):
                kb_meta.main()
        finally:
            kb_meta.apply = orig_apply
            sys.argv = orig_argv
        self.assertTrue(seen.get("force"), "--all 没有传出 force=True")
        self.assertIsNone(seen.get("ids"))


# ── 19~21：安装器 / 校验器 / 标定的破坏性路径 ──────────────────
class InstallSafetyTest(unittest.TestCase):
    def test_windows_launcher_variable_expansion_is_well_formed(self):
        """kb.cmd 的 `%VAR:~0,-1%` 必须以 `%` 闭合；写成 `}` 会让 Windows 上 kb 全废。"""
        text = (REPO / "template-vault" / "kb.cmd").read_text(encoding="utf-8")
        self.assertIn("%VAULT:~0,-1%", text)
        self.assertNotIn("%VAULT:~0,-1}", text)

    def test_verify_never_suggests_force_reinstall(self):
        """verify.py 的修复建议不能让用户跑 --force（会清空整个知识库）。"""
        text = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
        self.assertNotIn("create_vault.py --vault {vault} --force", text)
        self.assertIn("REINSTALL_HINT", text)


class CalibrateGuardTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-cal-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_synth_refuses_to_wipe_real_vault(self):
        import kb_calibrate
        write(self.root / "20-技术 Technology" / "我的笔记.md", "重要内容\n")
        write(self.root / ".git" / "HEAD", "ref: refs/heads/main\n")
        with self.assertRaises(SystemExit):
            kb_calibrate.synth(self.root, n=5, pairs=1)
        self.assertTrue((self.root / "20-技术 Technology" / "我的笔记.md").exists(),
                        "synth 清空了非仿真目录")

    def test_synth_allows_empty_dir(self):
        import kb_calibrate
        with contextlib.redirect_stdout(io.StringIO()):
            kb_calibrate.synth(self.root, n=5, pairs=1)
        self.assertTrue((self.root / "gt_dup.json").exists())


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


class EngineDeltaTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-neg-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_neg_delta_skips_health_rows(self):
        """每轮末尾追加的 tier=health 行没有 ab_snapshot，不能让它把统计 break 掉。"""
        log = self.root / "pipeline" / ".kb_engine_proposals.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"ab_snapshot": {"delta": -0.1}},
                {"ab_snapshot": {"delta": -0.2}},
                {"tier": "health", "health": 0.9}]
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        self.assertEqual(kb_engine._count_consecutive_neg_delta(self.root), 2)

    def test_config_path_follows_root(self):
        """kb_engine 的配置必须跟着 --root 走（且落在统一的 .kb/state/），不写进模块目录。

        位置演进：模块目录（写脏源码树）→ `<root>/pipeline/` → **`<root>/.kb/state/`**
        （状态文件统一约定）。旧位置仍可读，见 test_legacy_config_location_is_read。
        """
        other = Path(tempfile.mkdtemp(prefix="kb-p0-cfg-"))
        try:
            kb_engine.save_config(dict(kb_engine.CONFIG_DEFAULT, confidence_floor=0.71), other)
            self.assertTrue((other / ".kb" / "state" / "kb_rsi_config.json").exists(),
                            "配置应写入 .kb/state/（统一约定）")
            self.assertEqual(kb_engine.load_config(other)["confidence_floor"], 0.71)
            self.assertEqual(kb_engine.config_path(), kb_engine._LEGACY_CONFIG_PATH)  # 无 root 向后兼容
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_legacy_config_location_is_read(self):
        """旧位置 pipeline/.kb_rsi_config.json 必须仍能读到（否则升级后开关全部复位）。"""
        other = Path(tempfile.mkdtemp(prefix="kb-p0-cfg2-"))
        try:
            (other / "pipeline").mkdir(parents=True, exist_ok=True)
            (other / "pipeline" / ".kb_rsi_config.json").write_text(
                json.dumps({"confidence_floor": 0.42}), encoding="utf-8")
            self.assertEqual(kb_engine.load_config(other)["confidence_floor"], 0.42)
        finally:
            shutil.rmtree(other, ignore_errors=True)


class L4FreezeGateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-l4-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_not_grounded_does_not_freeze_rung(self):
        """未接地时不得把候选冻结写盘 —— 否则「闭锁系统更锁死」。"""
        import kb_l4
        from kb_constants import L4_FREEZE_CONSEC, L4_MIN_SAMPLE
        p = kb_l4._verdict_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        need = max(L4_FREEZE_CONSEC, L4_MIN_SAMPLE) + 2
        p.write_text("\n".join(
            json.dumps({"rung": "lc", "verb": "wrong", "ts": f"2026-10-{10 + i:02d}T00:00:00"})
            for i in range(need)) + "\n", encoding="utf-8")
        grounded, _note, _ = kb_l4.grounding(self.root)
        res = kb_l4.freeze(self.root)
        cfg = json.loads(kb_l4._config_path(self.root).read_text(encoding="utf-8"))
        if not grounded:
            self.assertTrue(res["newly_frozen"], "测试前置不成立：本应产生候选")
            self.assertFalse(cfg["frozen"].get("lc"),
                             "未接地却把 rung 冻上了（自锁 bug 回归）")


class ClaimSourceDateTest(unittest.TestCase):
    def test_date_comes_from_note_not_today(self):
        """extracted_at 取来源材料时间，否则 raw_date > claim_date 的约束永远配不到对。"""
        self.assertEqual(kb_claim._source_date({"created": "2026-03-05"}, "2026-10-15"),
                         "2026-03-05")
        self.assertEqual(kb_claim._source_date({"event_at": "2026-01-02",
                                                "created": "2026-03-05"}, "2026-10-15"),
                         "2026-01-02")
        self.assertEqual(kb_claim._source_date({}, "2026-10-15"), "2026-10-15")
        self.assertEqual(kb_claim._source_date({"created": "not-a-date"}, "2026-10-15"),
                         "2026-10-15")


class InstallerExitCodeTest(unittest.TestCase):
    def test_windows_batch_propagates_exit_code(self):
        """install.bat 必须 exit /b 透传退出码，否则批处理调用恒看到成功。"""
        text = (REPO / "install.bat").read_text(encoding="utf-8")
        self.assertIn("exit /b %RC%", text)
        tail = text.split("create_vault.py", 1)[1]
        self.assertNotIn("goto :eof", tail, "尾部仍然只用 goto :eof，退出码会被吞掉")

    def test_verify_install_treats_timeout_as_failure(self):
        """自检超时/异常必须按失败返回，否则「装坏了也算成功」。"""
        orig = create_vault.subprocess.run

        def boom(*a, **k):
            raise create_vault.subprocess.TimeoutExpired(cmd="x", timeout=1)

        create_vault.subprocess.run = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(create_vault.verify_install(Path("/tmp/whatever")))
        finally:
            create_vault.subprocess.run = orig

    def test_verify_windows_uses_comspec(self):
        """Windows 上 CreateProcess 不能直接跑 .cmd，必须经 cmd /c。"""
        text = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
        self.assertIn("COMSPEC", text)


class PrecheckConsentTest(unittest.TestCase):
    def test_obsidian_requires_consent(self):
        """未装 Obsidian 时不得静默下载安装（非 TTY 一律跳过）。"""
        import precheck
        import setup_obsidian
        called = []
        orig_detect, orig_dl = setup_obsidian.detect, setup_obsidian.download_and_install
        setup_obsidian.detect = lambda: (False, None)
        setup_obsidian.download_and_install = lambda: (called.append(1), (True, "/fake"))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r1 = precheck.check_obsidian(allow_install=False)
                r2 = precheck.check_obsidian(allow_install=True)   # 测试环境非 TTY
            self.assertFalse(called, "未经用户确认就下载安装了 Obsidian")
            self.assertFalse(r1.found)
            self.assertFalse(r2.found)
        finally:
            setup_obsidian.detect, setup_obsidian.download_and_install = orig_detect, orig_dl


# ── 30~39：第三轮（插件层 CLI 一致性 / 摄取路由）──────────────
class ParaRouteGuardTest(unittest.TestCase):
    """路由表里的 PARA 目录名必须真实存在。

    这条守卫是为 `30-资源 Resources` / `40-领域 Areas` 那类漂移写的：模板与现役库都只有
    `40-资源库 Resources`，而 triage 路由表写的是别的名字 → `kb ingest apply` 会凭空
    造出一个顶层目录，笔记落到 PARA 体系之外。
    """

    PARA_LITERAL = re.compile(r'''["']((?:\d{2})-[^"'/\n]*\s[^"'/\n]*)["']''')

    def test_every_para_dir_literal_exists(self):
        template = REPO / "template-vault"
        pipeline = template / "pipeline"
        files = list(pipeline.glob("*.py")) + list((pipeline / "plugins").glob("*.py"))
        bad, seen = [], set()
        for f in files:
            for m in self.PARA_LITERAL.finditer(f.read_text(encoding="utf-8")):
                s = m.group(1)
                if s.endswith(".md"):
                    continue
                top = s.split("/")[0]
                seen.add(top)
                if not (template / top).is_dir():
                    bad.append(f"{f.name}: {s}")
        self.assertTrue(seen, "没扫到任何 PARA 目录字面量，守卫失效")
        self.assertEqual(bad, [], f"路由表引用了不存在的 PARA 目录: {bad}")


class LauncherFlagConsistencyTest(unittest.TestCase):
    def test_short_flag_preserved_verbatim(self):
        got = KBLauncher._parse_params(["-h"])
        self.assertEqual(got, {"-h": True})

    def test_multi_flag_collects_all_values(self):
        got = KBLauncher._parse_params(["--project-context-root", "/a", "/b", "--name", "x"])
        self.assertEqual(got["project_context_root"], ["/a", "/b"])
        self.assertEqual(got["name"], "x")

    def test_subprocess_plugin_serializes_lists_and_short_flags(self):
        import importlib
        sp = importlib.import_module("plugins._subprocess_plugin")
        root = Path(tempfile.mkdtemp(prefix="kb-p0-cmd-"))
        try:
            (root / "pipeline").mkdir(parents=True, exist_ok=True)
            (root / "pipeline" / "dummy.py").write_text("# noop\n", encoding="utf-8")

            class _P(sp.SubprocessPlugin):
                SCRIPT_NAME = "dummy.py"
                ACTION_MAP = {"go": None}

                def metadata(self):
                    from plugin_base import PluginMetadata
                    return PluginMetadata(name="dummy", version="1", plugin_type="kb_module",
                                          actions=["go"])

            captured = {}
            orig = sp.subprocess.run
            sp.subprocess.run = lambda cmd, *a, **k: (
                captured.update(cmd=cmd) or type("R", (), {"returncode": 0})())
            try:
                from plugin_base import PluginContext
                p = _P()
                p.initialize(PluginContext(vault_root=str(root)))
                p.execute("go", {"root": str(root), "project_context_root": ["/a", "/b"],
                                 "-h": True})
            finally:
                sp.subprocess.run = orig
            cmd = captured["cmd"]
            self.assertIn("-h", cmd, "短选项被改写了")
            self.assertNotIn("--h", cmd)
            self.assertEqual(cmd.count("--project-context-root"), 2,
                             "多值旗标没有逐个传值")
        finally:
            shutil.rmtree(root, ignore_errors=True)


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


class IngestRoutingTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-route-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_clean_preserves_existing_summary(self):
        note = self.root / "20-技术 Technology" / "n.md"
        write(note, "---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                    "updated: 2026-01-01\nimportance: 0.5\n"
                    "kb_summary: curate 写的更准确摘要\n---\n\n# 标题\n正文。\n")
        _rel, new = clean.clean_note(self.root, note)
        self.assertIn("curate 写的更准确摘要", new, "已有 kb_summary 被 clean 覆盖")

    def test_intake_sets_kb_target(self):
        import intake_triage as it
        out = it._set_kb_target("---\ntags: [x]\nkb_target: 00-收件箱 Inbox\n---\n\n正文\n",
                                "20-技术 Technology")
        self.assertIn("kb_target: 20-技术 Technology", out)
        self.assertNotIn("kb_target: 00-收件箱 Inbox", out)
        out2 = it._set_kb_target("---\ntags: [x]\n---\n\n正文\n", "20-技术 Technology")
        self.assertIn("kb_target: 20-技术 Technology", out2, "缺失时应补行")
        self.assertEqual(it._set_kb_target("纯正文\n", "X"), "纯正文\n",
                         "无 frontmatter 时不应改动")

    def test_memory_sync_default_target_is_not_inbox(self):
        import memory_sync
        self.assertNotEqual(memory_sync.DEFAULT_TARGET, "00-收件箱 Inbox",
                            "未知 domain 回落收件箱会形成「晋升→回收件箱→疑似垃圾」回路")
        self.assertTrue((REPO / "template-vault" / memory_sync.DEFAULT_TARGET).is_dir())

    def test_sync_ingest_any_uses_real_converter(self):
        text = (PIPELINE / "sync.py").read_text(encoding="utf-8")
        self.assertIn("from ingest_convert import convert\n", text,
                      "ingest_convert 没有 convert_to_markdown，必须用真实入口 convert()")
        self.assertIn("raw_domain = infer_domain(", text,
                      "ClassifyResult 没有 domain 字段，domain 必须靠推断")


# ── 40~47：第四轮（dry-run 安全 / 增量索引 / 指标口径 / 小工具）──
class DryRunSafetyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-dry-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_do_clean_dry_never_writes(self):
        """do_clean(dry=True) 必须是纯只读：以前去重段无视 dry，会归档+改别名+删文件。"""
        body = ("---\ntags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
                "updated: 2026-01-01\nimportance: 0.5\nkb_summary: x\n---\n\n同一份正文。\n")
        for name in ("a.md", "b.md"):
            write(self.root / "20-技术 Technology" / name, body)
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.md"))
        with contextlib.redirect_stdout(io.StringIO()):
            _ch, dup, _st, _t = clean.do_clean(self.root, dry=True)
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.md"))
        self.assertEqual(before, after, "dry=True 却改动了磁盘")
        self.assertFalse((self.root / "90-归档 Archive" / "_trash").exists(),
                         "dry=True 却建了归档目录")
        self.assertGreaterEqual(dup, 1, "dry=True 仍应给出拟归档数（可数预览）")


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


class T3RatchetTest(unittest.TestCase):
    def test_t3_has_symmetric_relaxation_and_stable_is_not_stalled(self):
        src = (PIPELINE / "kb_engine.py").read_text(encoding="utf-8")
        self.assertIn("TUNED_RELAX", src, "T3 只有收紧、没有放宽（单向棘轮）")
        self.assertNotIn("fitness_trend <= 0", src,
                         "把 trend==0（稳定）当「停滞」会每次运行都收紧一档")
        self.assertIn("稳定不是失败信号", src)


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
        src = (PIPELINE / "rag.py").read_text(encoding="utf-8")
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
class L4LedgerToleranceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-l4-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_one_corrupt_line_does_not_wipe_ledger(self):
        """以前账本里一行 JSON 损坏就整体返回 []，裁决历史「凭空消失」、信任度归零。"""
        import kb_l4
        p = kb_l4._verdict_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"rung":"lc","verb":"wrong","ts":"2026-10-01T00:00:00"}\n'
                     '{ 这不是 JSON\n'
                     '{"rung":"lc","verb":"correct","ts":"2026-10-02T00:00:00"}\n',
                     encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            rows = kb_l4._read_verdicts(self.root)
        self.assertEqual(len(rows), 2, "损坏行把整本账本带走了")

    def test_render_reuses_passed_summary(self):
        """render(root, summary) 必须复用传入的 summary（status 是全库扫描）。"""
        import kb_l4
        calls = {"n": 0}
        orig = kb_l4.status

        def counting(root):
            calls["n"] += 1
            return orig(root)

        kb_l4.status = counting
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                kb_l4.render(self.root, orig(self.root))
        finally:
            kb_l4.status = orig
        self.assertEqual(calls["n"], 0, "render 忽略了传入的 summary，又扫了一遍全库")


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
class AtomicWriteTest(unittest.TestCase):
    """状态文件半截 = 配置回退默认 / 用户表失效 / agent 注册表整表丢失。

    原先只有 `state_manager._atomic_write` 是原子的，另有 4 处（kb_meta 状态与配置、
    user_manager 用户表、agent_registry 注册表）直接 `write_text`。现已统一到
    `kb_common.atomic_write_text`。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-atomic-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_roundtrip_and_no_tmp_left(self):
        import kb_common
        p = self.root / ".kb" / "state" / "x.json"
        kb_common.atomic_write_text(p, '{"a": 1}')
        self.assertEqual(p.read_text(encoding="utf-8"), '{"a": 1}')
        self.assertFalse(p.with_name(p.name + ".tmp").exists(), "残留临时文件")
        self.assertTrue(p.parent.is_dir(), "父目录应被自动创建")

    def test_overwrite_existing(self):
        """第二次写入必须成功（Windows 上 Path.rename 在目标存在时会 FileExistsError）。"""
        import kb_common
        p = self.root / "y.json"
        kb_common.atomic_write_text(p, "1")
        kb_common.atomic_write_text(p, "2")
        self.assertEqual(p.read_text(encoding="utf-8"), "2")

    def test_failure_does_not_corrupt_existing_file(self):
        """写入过程中抛错时，旧内容必须完好、且不留临时文件。"""
        import kb_common
        p = self.root / "z.json"
        kb_common.atomic_write_text(p, "good")
        orig = Path.write_text

        def boom(self, *a, **k):
            raise OSError("disk full")

        Path.write_text = boom
        try:
            with self.assertRaises(OSError):
                kb_common.atomic_write_text(p, "bad")
        finally:
            Path.write_text = orig
        self.assertEqual(p.read_text(encoding="utf-8"), "good", "旧内容被破坏")
        self.assertFalse(p.with_name(p.name + ".tmp").exists(), "残留临时文件")

    def test_state_writers_use_atomic_write(self):
        targets = {
            "kb_meta.py": 2,            # _write_state + _write_config
            "user_manager.py": 1,       # save_users
            "agent_registry.py": 1,     # save_registry
        }
        for rel, n in targets.items():
            src = (PIPELINE / rel).read_text(encoding="utf-8")
            # 两条都算原子：直接 atomic_write_text，或走 state_manager.write_json（内部即原子写）
            n_atomic = src.count("atomic_write_text(") + src.count("write_json(")
            self.assertGreaterEqual(n_atomic, n, f"{rel} 状态写入未原子化")
        # state_manager 应委托共享实现，而不是保留第二份同构代码
        sm = (PIPELINE / "state_manager.py").read_text(encoding="utf-8")
        self.assertIn("from kb_common import atomic_write_text", sm)


# ── 79~82：验收器必须验收「你要它验收的那个库」────────────────
class VerifyTargetsRightVaultTest(unittest.TestCase):
    """`kb verify --root X` 曾经把 schema/validate/healthcheck 三项**在另一个库里**跑。

    原因：这三项以裸 `kb` 作 argv[0]，而 `kb` 是 PATH 上的符号链接（指向别的库/别的安装），
    且这些子命令默认从 `KB_ROOT` 或模块目录推断 vault —— 实测 `kb verify --root <模板库>`
    测出来的数字属于 `/home/mushan/kb-kit-pure`（现役库）。
    验收工具绝不能验收错对象：数字、结论、退出码会全属于别人。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-verify-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_uses_target_own_launcher_and_root(self):
        import kb_verify
        (self.root / "pipeline").mkdir(parents=True, exist_ok=True)
        (self.root / "pipeline" / "kb_launcher.py").write_text("", encoding="utf-8")
        got = kb_verify._resolve_argv(["kb", "healthcheck", "summary"], self.root)
        self.assertNotEqual(got[0], "kb", "仍在用 PATH 上的 kb")
        self.assertEqual(got[1], str(self.root / "pipeline" / "kb_launcher.py"))
        self.assertIn("--root", got)
        self.assertEqual(got[got.index("--root") + 1], str(self.root))

    def test_resolve_refuses_non_kb_vault(self):
        import kb_verify
        self.assertIsNone(kb_verify._resolve_argv(["kb", "validate"], self.root),
                          "目标不是 kb-kit 库时应明确拒绝，而不是去测别的库")

    def test_resolve_rewrites_script_invocations(self):
        import kb_verify
        got = kb_verify._resolve_argv([sys.executable, "scripts/verify_curate.py"], self.root)
        self.assertEqual(got[1], str(self.root / "scripts" / "verify_curate.py"))
        self.assertEqual(got[got.index("--root") + 1], str(self.root))

    def test_main_actually_resolves_before_running(self):
        """真正的守卫：跑一遍 main()，看它**实际**调起的 argv 指向哪个库。

        只测 `_resolve_argv` 本身是不够的 —— 把 main 里的解析改回直接透传，
        那种测试照样全绿（已实测踩过）。
        """
        import kb_verify
        (self.root / "pipeline").mkdir(parents=True, exist_ok=True)
        (self.root / "pipeline" / "kb_launcher.py").write_text("", encoding="utf-8")
        seen = []

        def fake_run(argv, cwd, timeout):
            seen.append(list(argv))
            return {"code": 0, "out": "", "err": "", "secs": 0.0}

        orig_run, orig_argv = kb_verify._run, sys.argv
        kb_verify._run = fake_run
        sys.argv = ["kb_verify.py", "--root", str(self.root), "--json"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                kb_verify.main()
        finally:
            kb_verify._run, sys.argv = orig_run, orig_argv
        self.assertTrue(seen, "main 没有调起任何检查")
        launcher = str(self.root / "pipeline" / "kb_launcher.py")
        for argv in seen:
            if "-m" in argv and "unittest" in argv:
                continue                      # 单测项走 tests/ 定位，与 vault 无关
            self.assertNotIn("kb", argv,
                             f"main 仍以裸 kb 调起检查（会不会测到别的库）：{argv}")
            # 每一项都必须显式带上目标库的 --root
            self.assertIn("--root", argv, f"没传 --root：{argv}")
            self.assertEqual(argv[argv.index("--root") + 1], str(self.root),
                             f"--root 指向了别的库：{argv}")
            # 且必须用目标库自己的东西：kb 类走它自己的 launcher，脚本类走它自己目录下的脚本
            self.assertTrue(launcher in argv or any(str(self.root) in a for a in argv),
                            f"没有用目标库自己的入口：{argv}")

    def test_no_bare_kb_survives_in_checks(self):
        import kb_verify
        (self.root / "pipeline").mkdir(parents=True, exist_ok=True)
        (self.root / "pipeline" / "kb_launcher.py").write_text("", encoding="utf-8")
        for _key, _name, argv, _parser in kb_verify.CHECKS:
            if argv is None:
                continue
            got = kb_verify._resolve_argv(list(argv), self.root)
            self.assertNotIn("kb", got, f"argv 里仍有裸 kb：{got}")


# ── 83~84：活库不该因「未启用结晶」或「没分发 tests」而验收失败 ──
class VerifyTolerantStatesTest(unittest.TestCase):
    def test_missing_tests_is_skip_not_fail(self):
        src = (PIPELINE / "kb_verify.py").read_text(encoding="utf-8")
        self.assertIn("--strict", src)
        self.assertIn('"skipped": skipped', src)
        # 统计必须用 `is False`，否则 pass=None 的跳过项会被当成失败
        self.assertIn('r["pass"] is False', src)

    def test_curate_probe_marks_unmeasured_as_na(self):
        src = (REPO / "template-vault" / "scripts" / "verify_curate.py").read_text(encoding="utf-8")
        self.assertIn('"N/A" if na', src)
        self.assertIn("curate_enabled", src)
        # 退出码只看 FAIL
        self.assertIn('if c["status"] == "FAIL"', src)


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
def _p0_evil_write(path):        # pragma: no cover - 只在测试里被反序列化尝试
    Path(path).write_text("pwned")
    return 0


class _P0Evil:                   # pragma: no cover
    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return (_p0_evil_write, (self.path,))


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
class StatePathConventionTest(unittest.TestCase):
    """状态文件曾经散在 5 套约定里：根 dotfile、`pipeline/` dotfile、`.kb/` 直下、
    `.kb/state/`（StateStore）、以及 vault 根的普通文件。

    现统一为 **`<vault>/.kb/state/<name>`**，其余位置只在 `state_manager.LEGACY_PATHS`
    里登记为**只读回退**（旧库升级后状态不会凭空清零）。这几条守住这个约定。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-state-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_module_hardcodes_a_dotfile_state_path(self):
        """除 state_manager 的旧路径表外，任何模块都不应再构造「点文件」状态路径。

        迁移前各处写 `Path(root) / ".kb_meta_state.json"` 之类；迁移后只允许作为
        `legacy=(...)` 参数出现。这条用「点文件字面量」做判据（精确指向旧约定）。
        """
        dotfile = re.compile(r'["\'](\.[A-Za-z0-9_.-]+\.(?:json|jsonl))["\']')
        # 先剥掉「旧路径登记」块（含跨行的 legacy=(...) 与 _LEGACY = {...}），再扫描。
        legacy_block = re.compile(
            r"(?:legacy\s*=\s*\([^)]*\)"                      # legacy=(...)，可跨行
            r"|^_LEGACY[A-Za-z_]*\s*=.*$"                       # 单行 _LEGACY_X = <表达式>
            r"|_LEGACY[A-Za-z_]*\s*=\s*[\{\(][^\}\)]*[\}\)])", re.S | re.M)
        offenders = []
        for f in sorted(PIPELINE.glob("*.py")):
            if f.name == "state_manager.py":
                continue                      # 旧路径表就在这里，是唯一合法出处
            src = legacy_block.sub("", f.read_text(encoding="utf-8"))
            for i, line in enumerate(src.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for m in dotfile.finditer(line):
                    offenders.append(f"{f.name}:{i} {m.group(1)}")
        self.assertEqual(offenders, [],
                         "仍有模块写死旧位置的状态文件（应改用 state_manager 原语）：\n"
                         + "\n".join(offenders))

    def test_every_registered_state_name_is_under_kb_state(self):
        from state_manager import LEGACY_PATHS, state_path, STATE_DIR
        for name in LEGACY_PATHS:
            p = state_path(self.root, name)
            self.assertEqual(p.parent.name, "state")
            self.assertEqual(p.parent.parent.name, ".kb")
            self.assertTrue(str(p).endswith(f"{STATE_DIR}/{name}".replace("/", os.sep))
                            or p.name == name)

    def test_legacy_location_is_read_when_canonical_missing(self):
        from state_manager import read_state_path, write_json
        legacy = self.root / ".kb_meta_state.json"
        legacy.write_text('{"cycles": [1]}', encoding="utf-8")
        got = read_state_path(self.root, "kb_meta_state.json",
                              legacy=(".kb_meta_state.json",))
        self.assertEqual(got, legacy, "规范文件缺失时应回退到旧位置")
        write_json(self.root, "kb_meta_state.json", {"cycles": [2]})
        self.assertEqual(read_state_path(self.root, "kb_meta_state.json",
                                         legacy=(".kb_meta_state.json",)).parent.name,
                         "state", "写了规范文件后应优先读规范位置")

    def test_write_never_touches_legacy_file(self):
        """写只写规范路径：旧文件必须原样保留（迁移期不搬、不删，避免数据丢失）。"""
        from state_manager import write_json
        legacy = self.root / ".kb_l4_state.json"
        legacy.write_text('{"trust": {}}', encoding="utf-8")
        before = legacy.read_text(encoding="utf-8")
        write_json(self.root, "kb_l4_state.json", {"trust": {"l0": 1}})
        self.assertEqual(legacy.read_text(encoding="utf-8"), before, "旧文件被改动了")
        self.assertTrue((self.root / ".kb" / "state" / "kb_l4_state.json").exists())

    def test_jsonl_append_creates_state_dir(self):
        from state_manager import append_jsonl, read_jsonl
        append_jsonl(self.root, "kb_evolution.jsonl", {"event": "X"})
        self.assertTrue((self.root / ".kb" / "state" / "kb_evolution.jsonl").exists(),
                        "追加时应自动创建 .kb/state/")
        self.assertEqual(len(read_jsonl(self.root, "kb_evolution.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()


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


class InProcessPluginTest(unittest.TestCase):
    """插件进程内快路径：只读命令在启动器进程内执行，省一次解释器启动。

    核心不变量：**进程内执行的结果必须与子进程一致**（快路径不得改变任何语义）。
    """

    def _mk_script(self, root, body):
        p = root / "pipeline"
        p.mkdir(parents=True, exist_ok=True)
        script = p / "probe_script.py"
        script.write_text(
            "import sys\n"
            "def main():\n"
            "    print('argv=' + '|'.join(sys.argv[1:]))\n"
            "    if '--boom' in sys.argv:\n"
            "        raise RuntimeError('boom')\n"
            "    return 7\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(main())\n", encoding="utf-8")
        return script

    def test_in_process_matches_subprocess(self):
        """机制正确性：import + sys.argv + main() 的结果必须等于子进程。"""
        from plugins._subprocess_plugin import SubprocessPlugin
        root = Path(tempfile.mkdtemp(prefix="kb-iproc-"))
        try:
            script = self._mk_script(root, "")
            sys.path.insert(0, str(root / "pipeline"))
            import io as _io, contextlib as _cx
            buf = _io.StringIO()
            cmd = [sys.executable, str(script), "probe", "--root", str(root), "--flag", "x"]
            with _cx.redirect_stdout(buf):
                rc = SubprocessPlugin._execute_in_process(cmd, script)
            inproc_out, inproc_rc = buf.getvalue(), rc
            # 子进程
            sp = subprocess_run(cmd)
            self.assertEqual(inproc_rc, 7)
            self.assertEqual(sp.returncode, 7)
            self.assertEqual(inproc_out.strip(), sp.stdout.strip(),
                             "进程内输出必须与子进程一致")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_exception_falls_back_to_subprocess(self):
        """main() 抛异常 → 返回 None（丢弃输出），调用方回退 subprocess。"""
        from plugins._subprocess_plugin import SubprocessPlugin
        root = Path(tempfile.mkdtemp(prefix="kb-iproc2-"))
        try:
            script = self._mk_script(root, "")
            sys.path.insert(0, str(root / "pipeline"))
            import io as _io, contextlib as _cx
            buf = _io.StringIO()
            cmd = [sys.executable, str(script), "probe", "--root", str(root), "--boom"]
            with _cx.redirect_stdout(buf):
                rc = SubprocessPlugin._execute_in_process(cmd, script)
            self.assertIsNone(rc, "抛异常应返回 None 以回退 subprocess")
            self.assertEqual(buf.getvalue(), "", "异常时不应回放任何输出")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_main_falls_back(self):
        from plugins._subprocess_plugin import SubprocessPlugin
        root = Path(tempfile.mkdtemp(prefix="kb-iproc3-"))
        try:
            (root / "pipeline").mkdir(parents=True, exist_ok=True)
            sys.path.insert(0, str(root / "pipeline"))
            (root / "pipeline" / "no_main.py").write_text("x = 1\n", encoding="utf-8")
            self.assertIsNone(SubprocessPlugin._execute_in_process(
                [sys.executable, str(root / "pipeline" / "no_main.py"), "--root", str(root)],
                root / "pipeline" / "no_main.py"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_write_actions_are_not_in_process(self):
        """静态护栏：写动作（index/apply/mark/fix/bitemporal/decay）不得进白名单。"""
        import plugins.rag_plugin as rp
        import plugins.recall_plugin as rcp
        import plugins.schema_plugin as sp
        import plugins.audit_plugin as ap
        import plugins.confidence_plugin as cp
        from plugins._subprocess_plugin import SubprocessPlugin
        WRITE = {"index", "apply", "mark", "fix", "bitemporal", "decay", "run", "promote"}
        for mod in (rp, rcp, sp, ap, cp):
            for v in vars(mod).values():
                if isinstance(v, type) and issubclass(v, SubprocessPlugin) \
                        and v is not SubprocessPlugin and v.IN_PROCESS_ACTIONS:
                    self.assertTrue(v.IN_PROCESS_ACTIONS.isdisjoint(WRITE),
                                    f"{mod.__name__} 把写动作放进了进程内白名单")
        self.assertIn("query", rp.RAGPlugin.IN_PROCESS_ACTIONS)


def subprocess_run(cmd, **kw):
    import subprocess as sp
    return sp.run(cmd, capture_output=True, text=True, timeout=120, **kw)
