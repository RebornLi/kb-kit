#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_create_vault.py — create_vault 回归测试（P2-3 / P2-4）。

覆盖：
  1. STATE_IGNORE 排除运行时向量索引目录（避免 --no-demo 带入陈旧索引）
  2. 兜底生成的知识库首页与快捷卡自洽、幂等（不覆盖已有内容）

运行：
  python3 -m unittest discover -s tests
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import create_vault  # noqa: E402


class CopyTemplateIgnoreTest(unittest.TestCase):
    def test_state_ignore_excludes_vector_index(self):
        base = Path(tempfile.mkdtemp(prefix="kb-cvtest-"))
        try:
            src = base / "src"
            (src / "vector index").mkdir(parents=True)
            (src / "vector index" / "df_idf.json").write_text("{}", encoding="utf-8")
            (src / "keep.md").write_text("x", encoding="utf-8")

            dst = base / "vault"
            shutil.copytree(src, dst, ignore=create_vault.STATE_IGNORE)

            self.assertTrue((dst / "keep.md").exists(), "普通文件应被复制")
            self.assertFalse((dst / "vector index").exists(),
                             "运行时向量索引不应随模板复制")
        finally:
            shutil.rmtree(base, ignore_errors=True)


class OnboardingFilesTest(unittest.TestCase):
    def test_homepage_and_card_generated_consistent(self):
        base = Path(tempfile.mkdtemp(prefix="kb-cvtest-"))
        try:
            vault = base / "vault"
            vault.mkdir()
            create_vault.generate_homepage(vault)
            create_vault.generate_quickstart_card(vault)

            home = vault / "🏠-知识库首页.md"
            card = vault / "如何使用.md"
            self.assertTrue(home.exists(), "应兜底生成知识库首页")
            self.assertTrue(card.exists(), "应兜底生成快捷卡")
            # 卡片若引用首页，则首页必须存在（引用自洽）
            if "🏠-知识库首页.md" in card.read_text(encoding="utf-8"):
                self.assertTrue(home.exists(), "卡片引用的首页应存在")

            # 幂等：不覆盖已有内容
            home.write_text("custom-home", encoding="utf-8")
            card.write_text("custom-card", encoding="utf-8")
            create_vault.generate_homepage(vault)
            create_vault.generate_quickstart_card(vault)
            self.assertEqual(home.read_text(encoding="utf-8"), "custom-home")
            self.assertEqual(card.read_text(encoding="utf-8"), "custom-card")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_fallback_content_matches_template_docs(self):
        """兜底生成内容必须与模板自带文档逐字一致（单一内容源守卫）。"""
        base = Path(tempfile.mkdtemp(prefix="kb-cvtest-"))
        try:
            vault = base / "vault"
            vault.mkdir()
            create_vault.generate_homepage(vault)
            create_vault.generate_quickstart_card(vault)
            tmpl = REPO / "template-vault"
            for name in ("🏠-知识库首页.md", "如何使用.md"):
                got = (vault / name).read_text(encoding="utf-8")
                want = (tmpl / name).read_text(encoding="utf-8")
                self.assertEqual(got, want, f"{name} 兜底内容应与模板一致")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_install_manifest_written(self):
        """安装清单应记录模板基线文件与生成文件，且不含运行时索引。"""
        base = Path(tempfile.mkdtemp(prefix="kb-cvtest-"))
        try:
            vault = base / "vault"
            create_vault.copy_template(vault)
            create_vault.generate_homepage(vault)
            create_vault.generate_quickstart_card(vault)
            create_vault.write_install_manifest(vault)

            mf = vault / ".kb" / "install-manifest.json"
            self.assertTrue(mf.exists(), "应写安装清单")
            files = json.loads(mf.read_text(encoding="utf-8"))["files"]
            self.assertIn("kb", files)
            self.assertIn("pipeline/rag.py", files)
            self.assertFalse(any(f.startswith("vector index") for f in files),
                             "运行时向量索引不应在清单内")
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class TemplateCleanlinessTest(unittest.TestCase):
    """模板**绝不能**夹带运行态 —— 它会随 create_vault 复制进每个新用户的知识库。

    实测踩到：`template-vault/.kb/state/feedback_state.json`、`query_cache.json`、
    `pipeline/.kb_rsi_proposals.jsonl`（在模板目录里跑测试留下的）会被原样分发，
    新库开局就带着指向不存在笔记的反馈命中与查询缓存。
    """

    def test_no_state_dir_in_template(self):
        self.assertFalse((REPO / "template-vault" / ".kb").exists(),
                         "模板里不应有 .kb/（运行态；create_vault.STATE_IGNORE 现已排除）")

    def test_pipeline_and_scripts_contain_only_code(self):
        stray = []
        for d in ("pipeline", "scripts"):
            base = REPO / "template-vault" / d
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                if p.name.startswith(".") or p.suffix in (".json", ".jsonl"):
                    stray.append(f"{d}/{p.relative_to(base)}")
        self.assertEqual(stray, [], f"模板运行目录里混入运行态文件：{stray}")

    def test_state_ignore_covers_runtime_dirs(self):
        """create_vault 的忽略表必须覆盖运行态目录（否则上面两条会被绕过）。"""
        import create_vault
        # ignore_patterns 不接受直接查询，改为断言源码里登记了这些名字
        src = (REPO / "create_vault.py").read_text(encoding="utf-8")
        for name in (".kb", ".agents", "vector index"):
            self.assertIn(f'"{name}"', src, f"STATE_IGNORE 未登记 {name}")


if __name__ == "__main__":
    unittest.main()
