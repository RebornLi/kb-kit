#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_testenv.py — 测试环境脚本自身的守卫。

环境脚本是「验证基础设施」，它出错会让**后续所有验证结论失效**。最危险的一种错是
隔离失效：本机 `KB_ROOT` 全局指向**现役库**，一旦构建/检查落在那里，就会直接改生产数据。
所以这里钉住三件事：
  1. 只允许在 `.testenv/` 内构建（越界/保护路径一律 SystemExit）；
  2. 所有子进程的 `KB_ROOT` 被强制指向沙箱目标，绝不泄漏现役库路径；
  3. 环境检查项覆盖「单测 / 安装验收 / 合成库链路」三类，且失败会真的返回非 0。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

import testenv  # noqa: E402


class SandboxGuardTest(unittest.TestCase):
    def test_allows_paths_inside_testenv(self):
        p = testenv.assert_sandbox(testenv.TESTENV / "fresh")
        self.assertTrue(str(p).startswith(str(testenv.TESTENV.resolve())))

    def test_refuses_protected_live_vault(self):
        live = Path("/home/mushan/kb-kit-pure")
        with self.assertRaises(SystemExit):
            testenv.assert_sandbox(live)

    def test_refuses_arbitrary_outside_path(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                testenv.assert_sandbox(Path(d) / "vault")

    def test_refuses_kb_root_from_env(self):
        """$KB_ROOT 指向哪里，哪里就是保护路径（本机全局指向现役库）。"""
        old = os.environ.get("KB_ROOT")
        with tempfile.TemporaryDirectory() as d:
            os.environ["KB_ROOT"] = d
            try:
                with self.assertRaises(SystemExit):
                    testenv.assert_sandbox(Path(d))
            finally:
                if old is None:
                    os.environ.pop("KB_ROOT", None)
                else:
                    os.environ["KB_ROOT"] = old

    def test_run_forces_kb_root_into_sandbox(self):
        """子进程看到的 KB_ROOT 必须是沙箱路径，不能是现役库。"""
        root = testenv.TESTENV / "_guard"      # 专用临时目录，别污染 fresh/synth/clone 状态
        try:
            root.mkdir(parents=True, exist_ok=True)
            r = testenv.run([sys.executable, "-c", "import os;print(os.environ.get('KB_ROOT'))"],
                            cwd=REPO, root=root, quiet=True)
            self.assertEqual(r["code"], 0)
            self.assertEqual(r["out"].strip(), str(root.resolve()))
            self.assertNotIn("/home/mushan/kb-kit-pure\n", r["out"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_run_without_root_clears_kb_root(self):
        r = testenv.run([sys.executable, "-c", "import os;print(os.environ.get('KB_ROOT'))"],
                        cwd=REPO, root=None, quiet=True)
        self.assertEqual(r["out"].strip(), "None", "不传 root 时也必须清掉 KB_ROOT")


class EnvShapeTest(unittest.TestCase):
    def test_check_covers_the_three_core_verifications(self):
        src = (REPO / "tests" / "testenv.py").read_text(encoding="utf-8")
        for needle in ("check_unit", "check_fresh", "check_synth"):
            self.assertIn(needle, src)
        self.assertIn("check_clone", src)
        self.assertIn("check_perf", src)

    def test_check_returns_nonzero_on_failure(self):
        src = (REPO / "tests" / "testenv.py").read_text(encoding="utf-8")
        self.assertIn("return 1 if n_fail else 0", src)

    def test_env_is_gitignored(self):
        gi = (REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".testenv/", gi, "测试环境目录必须 gitignore（可能含真实库副本）")


if __name__ == "__main__":
    unittest.main()
