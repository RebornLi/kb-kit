#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_intake_move.py — `kb ingest move/trash` 与搬运安全回归测试（严重问题 1、2）。

覆盖：
  1. `kb ingest move` 真的把达标笔记路由到 kb_target 分区（修复静默 no-op）
  2. `kb ingest trash` 把低价值笔记归档到 _trash
  3. 目标区已有同名不同内容 → 改名避让，绝不覆盖
  4. 目标区已有同名相同内容 → 判重，不重复写入

运行：
  python3 -m unittest discover -s tests
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template-vault"
PIPELINE = TEMPLATE / "pipeline"
sys.path.insert(0, str(PIPELINE))
import intake_triage  # noqa: E402

GOOD_FM = """\
tags: ["管理"]
status: draft
domain: 管理
created: 2026-09-27
updated: 2026-09-27
importance: 0.5
kb_target: 20-技术 Technology
kb_action: new
kb_summary: 一条待路由的测试想法
"""
GOOD_BODY = "这是一条足够长的测试想法，包含根因/复盘/决定/经验/教训等价值信号，用于验证路由。"


def run_kb(vault: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(vault / "pipeline" / "kb_launcher.py"), *args, "--root", str(vault)],
        capture_output=True, text=True, cwd=str(vault), timeout=60,
    )


class IntakeMoveTest(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kb-intake-"))
        shutil.copytree(TEMPLATE, self.vault, dirs_exist_ok=True)
        self.inbox = self.vault / "00-收件箱 Inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _write_inbox(self, name: str, fm: str, body: str) -> Path:
        p = self.inbox / name
        p.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8")
        return p

    def test_ingest_move_routes_note(self):
        src = self._write_inbox("测试想法.md", GOOD_FM, GOOD_BODY)
        r = run_kb(self.vault, "ingest", "move")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(src.exists(), "源文件应被移走")
        self.assertTrue((self.vault / "20-技术 Technology" / "测试想法.md").exists(),
                        "应路由到 kb_target 分区")

    def test_ingest_trash_archives_low_value(self):
        # 内容过少（信号字符 <15，含 frontmatter）→ 强制 trash
        src = self.inbox / "垃圾.md"
        src.write_text("---\na: b\n---\n!\n", encoding="utf-8")
        r = run_kb(self.vault, "ingest", "trash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(src.exists(), "源文件应被归档移走")
        self.assertTrue((self.vault / "90-归档 Archive" / "_trash" / "垃圾.md").exists())

    def test_move_never_overwrites_conflicting_name(self):
        dest_dir = self.vault / "20-技术 Technology"
        dest_dir.mkdir(parents=True, exist_ok=True)
        existing = dest_dir / "测试想法.md"
        existing.write_text("— 已有的不同内容，不能被覆盖 —", encoding="utf-8")
        src = self._write_inbox("测试想法.md", GOOD_FM, GOOD_BODY)

        r = run_kb(self.vault, "ingest", "move")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(src.exists())
        self.assertEqual(existing.read_text(encoding="utf-8"),
                         "— 已有的不同内容，不能被覆盖 —", "原同名文件内容必须保留")
        self.assertTrue((dest_dir / "测试想法-1.md").exists(), "冲突内容应改名避让")

    def test_safe_place_dedupes_and_renames(self):
        d = self.vault / "20-技术 Technology"
        d.mkdir(parents=True, exist_ok=True)
        # new
        p, kind = intake_triage._safe_place(d, "a.md", "X")
        self.assertEqual(kind, "new")
        self.assertEqual(p.read_text(encoding="utf-8"), "X")
        # dup（内容相同 → 不新增文件）
        p2, kind2 = intake_triage._safe_place(d, "a.md", "X")
        self.assertEqual(kind2, "dup")
        self.assertEqual(p2, p)
        self.assertEqual(len(list(d.glob("a*.md"))), 1)
        # renamed（同名不同内容 → 改名避让，绝不覆盖）
        p3, kind3 = intake_triage._safe_place(d, "a.md", "Y")
        self.assertEqual(kind3, "renamed")
        self.assertEqual(p.read_text(encoding="utf-8"), "X", "原文件不得被覆盖")
        self.assertEqual(p3.read_text(encoding="utf-8"), "Y")


    def test_ingest_move_commits(self):
        """move 后应产生一次 git 提交，且目标文件被跟踪（防提交 pathspec 回归）。"""
        if not shutil.which("git"):
            self.skipTest("git not available")
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"],
                    ["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", "init"]):
            subprocess.run(cmd, cwd=str(self.vault), capture_output=True)
        self._write_inbox("提交测试.md", GOOD_FM, GOOD_BODY)
        r = run_kb(self.vault, "ingest", "move")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = subprocess.run(["git", "-C", str(self.vault), "log", "--oneline"],
                             capture_output=True, text=True).stdout
        self.assertIn("摄入 triage 处理", log, "move 应产生 triage 提交")
        tracked = subprocess.run(
            ["git", "-C", str(self.vault), "ls-files", "--error-unmatch", "--",
             "20-技术 Technology/提交测试.md"], capture_output=True)
        self.assertEqual(tracked.returncode, 0, "路由后的笔记应被 git 跟踪")


if __name__ == "__main__":
    unittest.main()
