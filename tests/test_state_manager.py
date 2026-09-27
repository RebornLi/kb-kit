#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_state_manager.py — StateStore 原子写入回归测试（P1-3）。

覆盖：
  1. Windows 语义下（rename 目标已存在即抛 FileExistsError）save 连续写仍成功
  2. 同上，update 连续写仍成功
  3. 覆盖写后无残留 .tmp 文件

说明：通过 monkeypatch pathlib.Path.rename 模拟 Windows 行为。修复前
save/update 用 tmp.rename(p)，第二次写会抛 FileExistsError（调用方多吞异常
→ 状态静默不更新）；修复后走 os.replace，不受影响。

运行：
  python3 -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

from state_manager import StateStore  # noqa: E402


def windows_rename(self_path, target):
    """模拟 Windows：目标已存在时 rename 抛 FileExistsError。"""
    t = Path(target)
    if t.exists():
        raise FileExistsError(f"[WinError 183] 目标已存在: {t}")
    return os.rename(self_path, t)


class StateStoreAtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-state-"))
        self.store = StateStore(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_overwrites_under_windows_rename(self):
        self.store.save("s.json", {"n": 1})
        with mock.patch.object(Path, "rename", windows_rename):
            self.store.save("s.json", {"n": 2})  # 旧实现：tmp.rename(p) 会抛
            self.store.save("s.json", {"n": 3})
        self.assertEqual(self.store.load("s.json"), {"n": 3})
        self.assertFalse((self.store.dir / "s.json.tmp").exists(), "不应残留临时文件")

    def test_update_overwrites_under_windows_rename(self):
        self.store.save("c.json", {"n": 1})
        with mock.patch.object(Path, "rename", windows_rename):
            self.store.update("c.json", lambda d: {**d, "n": d.get("n", 0) + 1})
            self.store.update("c.json", lambda d: {**d, "n": d.get("n", 0) + 1})
        self.assertEqual(self.store.load("c.json"), {"n": 3})
        self.assertFalse((self.store.dir / "c.json.tmp").exists(), "不应残留临时文件")

    def test_update_creates_when_missing(self):
        self.store.update("new.json", lambda d: {**d, "created": True})
        self.assertEqual(self.store.load("new.json"), {"created": True})

    def test_load_default_when_missing(self):
        self.assertEqual(self.store.load("nope.json", {"d": 1}), {"d": 1})


if __name__ == "__main__":
    unittest.main()
