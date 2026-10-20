#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import re
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

import clean  # noqa: E402
import curate  # noqa: E402
import kb_query  # noqa: E402
import sync as sync_mod  # noqa: E402

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


# ── 7：curate 越权写入 ───────────────────────────────────────
class _StubLLM:
    """最小 LLM 替身：只回一个预设的结晶提案。"""

    model = "stub-model"

    def __init__(self, prop):
        self.prop = prop

    def ask_json(self, msgs, max_tokens=4096):
        return self.prop, "stub"



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
