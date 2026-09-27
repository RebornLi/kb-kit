#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_growth_fixes2.py — 测试中发现问题的回归（E7/I2/I5/F3/I3）。

覆盖：
  1. memory_ingest.mirror_core 写出的核心镜像含必填 frontmatter（kb validate 无硬错误）
  2. classify.py 接受插件注入的 --root（不再报用法错误）
  3. feedback apply 跳过生成产物（_INDEX.md 不被提升）
  4. clean.do_chunk 幂等（语义父文档不再重复分块）且不写入 raw/

运行：python3 -m unittest discover -s tests
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

import memory_ingest  # noqa: E402
import feedback_loop  # noqa: E402
import clean  # noqa: E402
from validate import validate_file  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Fixes2Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-fix2-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # 1. mirror_core 必填 frontmatter
    def test_mirror_core_frontmatter_valid(self):
        agent_root = self.root / "agent"
        write(agent_root / "MEMORY.md", "# 长期记忆\n\n这是一条核心记忆内容。\n")
        memory_ingest.mirror_core(self.root, agent_root, "openclaw")
        out = self.root / "reference" / "openclaw-MEMORY.md"
        self.assertTrue(out.exists())
        hard, _warn, _ = validate_file(out)
        self.assertEqual(hard, [], "核心镜像不得有 frontmatter 硬错误")

    # 2. classify --root 兼容
    def test_classify_accepts_root(self):
        r = subprocess.run([sys.executable, str(PIPELINE / "classify.py"),
                            "--root", str(self.root), "备份 检索 经验"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        self.assertIn("content_type", r.stdout)

    # 3. feedback 跳过生成产物
    def test_feedback_skips_generated_report(self):
        idx = self.root / "70-知识治理 Governance" / "_INDEX.md"
        write(idx, "# 仪表盘报告\n\n生成产物，不应被提升。\n")
        before = idx.read_text(encoding="utf-8")
        feedback_loop.save_state(self.root, {"hits": {"70-知识治理 Governance/_INDEX.md": 10}})
        feedback_loop.apply_bumps(self.root)
        self.assertEqual(idx.read_text(encoding="utf-8"), before,
                         "_INDEX.md 是生成产物，不应被 feedback 提升")

    # 4. clean 幂等 + 不写 raw
    def test_clean_chunk_idempotent_and_skips_raw(self):
        long = "\n\n".join("段落内容示例" * 40 for _ in range(6))  # 多段落、无 ## 标题 → 语义分块
        write(self.root / "20-技术 Technology/long.md",
              f"---\ntags: [\"管理\"]\nstatus: active\ndomain: 管理\nimportance: 0.6\nkb_summary: s\n---\n{long}\n")
        write(self.root / "raw/paper.md", f"---\ntags: []\nstatus: active\ndomain: 开发\nimportance: 0.5\nkb_summary: s\n---\n{long}\n")
        c1, _ = clean.do_chunk(self.root, 200)
        self.assertGreater(c1, 0)
        c2, _ = clean.do_chunk(self.root, 200)
        self.assertEqual(c2, 0, "二次分块应为 0（幂等）")
        raw_extra = [p.name for p in (self.root / "raw").iterdir() if p.name != "paper.md"]
        self.assertEqual(raw_extra, [], "raw/ 不应产出任何分块文件")


if __name__ == "__main__":
    unittest.main()
