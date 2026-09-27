#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_opencode_ingest.py — OpenCode SQLite 记忆适配回归测试。

覆盖：
  1. 从 session/message/part schema 正确抽取 user/assistant 交互对并落盘
  2. dry-run 只计数、不落盘、不写状态
  3. agent_registry.detect_opencode 依据 KB_OPENCODE_DB 探测

运行：
  python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template-vault"
PIPELINE = TEMPLATE / "pipeline"
sys.path.insert(0, str(PIPELINE))
import memory_ingest_opencode as mio  # noqa: E402
import agent_registry  # noqa: E402

MARKER = "同步自 OpenCode 会话"


def make_db(path: Path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE session (id text PRIMARY KEY, title text, "
                "directory text, time_created integer)")
    con.execute("CREATE TABLE message (id text PRIMARY KEY, session_id text, "
                "time_created integer, data text)")
    con.execute("CREATE TABLE part (id text PRIMARY KEY, message_id text, "
                "session_id text, time_created integer, data text)")
    con.execute("INSERT INTO session VALUES (?,?,?,?)",
                ("ses_1", "如何做 X", "/tmp/proj", 1000))
    con.execute("INSERT INTO message VALUES (?,?,?,?)",
                ("m1", "ses_1", 1000, json.dumps({"role": "user"})))
    con.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                ("p1", "m1", "ses_1", 1000,
                 json.dumps({"type": "text", "text": "问题：如何做 X？"})))
    con.execute("INSERT INTO message VALUES (?,?,?,?)",
                ("m2", "ses_1", 1100, json.dumps({"role": "assistant"})))
    con.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                ("p2", "m2", "ses_1", 1100,
                 json.dumps({"type": "text", "text": "答案：按步骤做 X。"})))
    con.commit()
    con.close()


class OpenCodeIngestTest(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="kb-oc-"))
        shutil.copytree(TEMPLATE, self.vault, dirs_exist_ok=True)
        self.db = self.vault / "opencode.db"
        make_db(self.db)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _haystack(self):
        return "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in self.vault.rglob("*.md"))

    def test_extract_and_ingest(self):
        turns = mio.extract_sessions(self.db)
        self.assertEqual(len(turns), 1)
        self.assertIn("如何做 X", turns[0]["user"])
        n = mio.ingest(self.vault, self.db, dry_run=False)
        self.assertEqual(n, 1)
        self.assertIn(MARKER, self._haystack())
        self.assertTrue((self.vault / ".memory_ingest_state.json").exists())

    def test_dry_run_does_not_write(self):
        n = mio.ingest(self.vault, self.db, dry_run=True)
        self.assertEqual(n, 1)
        self.assertNotIn(MARKER, self._haystack())
        self.assertFalse((self.vault / ".memory_ingest_state.json").exists())

    def test_detect_opencode(self):
        os.environ["KB_OPENCODE_DB"] = str(self.db)
        try:
            res = agent_registry.detect_opencode()
        finally:
            os.environ.pop("KB_OPENCODE_DB", None)
        self.assertTrue(res["found"])
        self.assertEqual(res["type"], "opencode")
        self.assertEqual(res["sources"]["db"], str(self.db))


if __name__ == "__main__":
    unittest.main()
