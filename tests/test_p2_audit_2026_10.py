#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p2_audit_2026_10.py — 2026-10-22 审计发现的六项 P2 架构债回归测试。

## 这六条的共同点

都不是"算错了"，而是**同一件事被实现了两遍以上，且两份不一致**。
单测测不出，因为它们各自在自己的语境里都是"对的"。

P2-1 `raw/` 判定 4 处实现 ↔ 2 种语义
     修复：`kb_metautil.is_raw_path()` 为唯一实现，**任何层级**的 raw 段都算原始层。
P2-2 系统目录判定靠**子串**匹配 → `mydocs/a.md`、`a.venvo/b.md` 被误判为系统文件
     修复：`kb_io.is_system_path()`，**按路径段**匹配。
P2-3 研究层 8 个只读视图没有 CLI 入口（只能直接跑脚本）
     修复：`ResearchPlugin` + `kb research <子命令>`，且**只收录只读模块**。
P2-4 `clean.py` 自称 SUPERSEDED、`sync.py` 自称 DEPRECATED 并让用户改用
     **根本不存在的** `kb engine` —— 注释在骗人
     修复：两处头部改为陈述事实（动作经 `*_plugin._ROUTES` 路由）。
P2-5 同一个字段在 schema 里叫 `conflict_with`，代码写的是 `conflicts_with`
     修复：schema/code/README 统一 `conflicts_with`，旧名标 `deprecated` + `_alias_of`。
P2-6 `deploy.py` 只部署代码不部署 `reference/` → 新代码配旧契约，静默退化
     修复：plan/apply 检测并报告契约漂移（不自动覆盖，留人工决定）。

运行：python3 -m unittest discover -s tests
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
TEMPLATE = REPO / "template-vault"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))
sys.path.insert(0, str(REPO / "tests"))

import kb_io               # noqa: E402
import kb_metautil        # noqa: E402


class P21RawPathTest(unittest.TestCase):
    """P2-1：`raw/` 判定必须只有一种语义。"""

    def test_top_level_raw(self):
        self.assertTrue(kb_metautil.is_raw_path("raw/a.md"))
        self.assertTrue(kb_metautil.is_raw_path("raw/安全/cwe.md"))
        # is_raw_root 只看顶层段，嵌套层级不影响它
        self.assertTrue(kb_metautil.is_raw_root("raw/安全/cwe.md"))
        self.assertFalse(kb_metautil.is_raw_root("产品/raw/cwe.md"))
        self.assertFalse(kb_metautil.is_raw_root("cwe.md"))

    def test_nested_raw_any_depth(self):
        """任何层级的 raw 段都算原始层 —— 这是 P2-1 的核心决定。

        理由是不对称：漏判一个嵌套 raw/ 会让引擎覆写证据层原件；
        误判只是少处理一篇笔记。代价不对等，所以往宽判。
        """
        for rel in ("产品/raw/a.md", "web/2026/raw/x.md", "a/b/raw/c/d.md"):
            with self.subTest(rel=rel):
                self.assertTrue(kb_metautil.is_raw_path(rel))

    def test_raw_as_filename_not_segment(self):
        """`rawa.md` / `x/raw.md` / `rawish/` 不是 raw 段。"""
        for rel in ("x/raw.md", "rawish/a.md", "产品/rawa.md", "raw.md"):
            with self.subTest(rel=rel):
                self.assertFalse(kb_metautil.is_raw_path(rel))

    def test_windows_separators(self):
        self.assertTrue(kb_metautil.is_raw_path("产品\\raw\\a.md"))

    def test_non_string_input(self):
        self.assertFalse(kb_metautil.is_raw_path(None))
        self.assertFalse(kb_metautil.is_raw_path(""))

    def test_all_callers_share_one_implementation(self):
        """源码守卫：不允许再出现独立实现的 is_raw。

        历史上有 4 份：`kb_rsi.is_raw` / `feedback_loop.is_raw_rel` /
        `_research/kb_engine.is_raw` / `kb_metautil.is_raw_path`，语义还有 2 种。
        现在前三个必须委托给 `kb_metautil.is_raw_path`。
        """
        targets = [
            PIPELINE / "kb_rsi.py",
            PIPELINE / "feedback_loop.py",
            PIPELINE / "_research" / "kb_engine.py",
        ]
        for py in targets:
            src = py.read_text(encoding="utf-8")
            with self.subTest(module=py.name):
                self.assertIn("is_raw_path", src,
                              f"{py.name} 没有委托给 kb_metautil.is_raw_path")
                # 不允许自己再切路径判断 raw
                self.assertIsNone(
                    re.search(r'startswith\(\s*["\']raw[/\\]', src),
                    f"{py.name} 里还有独立的 startswith('raw/') 判定")

    def test_is_raw_path_has_single_definition(self):
        files = (sorted(PIPELINE.glob("*.py"))
                 + sorted((PIPELINE / "_research").glob("*.py")))
        defs = []
        for py in files:
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if re.match(r"\s*def is_raw(_path|_rel)?\s*\(", line):
                    defs.append(f"{py.name}:{i}")
        # 允许 is_raw_path 本身 + 各模块的薄委托包装
        self.assertIn("kb_metautil.py", " ".join(defs))


class P22SystemPathTest(unittest.TestCase):
    """P2-2：系统目录判定要按段匹配，不能靠子串。"""

    def test_exact_segments(self):
        for rel in ("memory/a.md", "reference/kb-schema.json", "raw/x.md",
                    "main/README.md", ".git/config", ".obsidian/app.json"):
            with self.subTest(rel=rel):
                self.assertTrue(kb_io.is_system_path(rel))

    def test_substring_false_positives_are_gone(self):
        """修复前这些全被误判（healthcheck 用子串匹配）。"""
        for rel in ("mydocs/a.md", "产品/memory-notes.md", "a.venvo/b.md",
                    "notes/rawdata.md", "参考资料/a.md", "my-main/x.md"):
            with self.subTest(rel=rel):
                self.assertFalse(kb_io.is_system_path(rel),
                                 f"{rel} 被误判为系统路径")

    def test_nested_system_segment(self):
        """嵌套的系统目录也算（与 raw 同向：宁可少算一篇笔记）。"""
        self.assertTrue(kb_io.is_system_path("产品/attachments/memory/a.md"))

    def test_windows_and_none(self):
        self.assertTrue(kb_io.is_system_path("memory\\a.md"))
        self.assertFalse(kb_io.is_system_path(None))

    def test_healthcheck_has_no_substring_usage(self):
        """源码守卫：healthcheck 不许再对 SYSTEM_HINTS 做 `in rel`。"""
        src = (PIPELINE / "kb-healthcheck.py").read_text(encoding="utf-8")
        self.assertIn("is_system_path", src)
        self.assertIsNone(
            re.search(r"any\(\s*\w+\s+in\s+rel", src),
            "kb-healthcheck.py 仍在用子串匹配判系统路径")

    def test_audit_uses_shared_helper(self):
        src = (PIPELINE / "kb_audit.py").read_text(encoding="utf-8")
        self.assertIn("is_system_path", src)
        self.assertNotIn("_SYS = (", src)

    def test_curated_not_in_system_segments(self):
        """`_curated` 是审计专用的额外排除，不能混进系统目录集合。"""
        self.assertNotIn("_curated", kb_io.SYSTEM_SEGMENTS)


class P23ResearchPluginTest(unittest.TestCase):
    """P2-3：研究层只读视图要有 CLI 入口，且只收录只读模块。

    注意 `_ROUTES` 的真实形状是
        {(原插件名, 原 action): (脚本, 原子子命令, in_proc)}
    不是扁平字符串键 —— 本测试按真实形状断言。
    """

    @classmethod
    def setUpClass(cls):
        import research_plugin
        cls.mod = research_plugin
        cls.routes = dict(research_plugin.ResearchPlugin._ROUTES)

    def test_routes_present(self):
        self.assertGreaterEqual(len(self.routes), 8)
        pairs = {f"{p}.{a}" for (p, a) in self.routes}
        for key in ("kb_usage.report", "kb_usage.stats", "kb_usage.analyze",
                    "kb_fitness.fitness", "kb_drift.drift", "kb_scale.scale",
                    "kb_scale.bracket", "kb_scale.fabric"):
            with self.subTest(key=key):
                self.assertIn(key, pairs)

    def test_route_shape(self):
        for key, spec in self.routes.items():
            with self.subTest(key=key):
                self.assertIsInstance(key, tuple)
                self.assertEqual(len(key), 2, f"{key} 不是 (插件名, action) 二元组")
                self.assertEqual(len(spec), 3, f"{key} 的取值不是 (脚本, 子命令, in_proc)")

    def test_all_routes_in_proc(self):
        """只读视图没写副作用，进程内跑即可（省掉子进程解释器启动）。"""
        for key, spec in self.routes.items():
            with self.subTest(key=key):
                self.assertIs(spec[2], True, f"{key} 不是 in_proc")

    def test_no_write_capable_module_exposed(self):
        """审计结论：这些模块有写点，**不能**进只读入口。"""
        scripts = " ".join(spec[0] for spec in self.routes.values())
        for banned in ("kb_calibrate", "kb_contradiction", "kb_ingest",
                       "kb_consistency"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, scripts)

    def test_usage_mark_not_exposed(self):
        """`kb_usage mark` 会调 record_hit 改 importance，属写操作。"""
        for key, spec in self.routes.items():
            self.assertNotEqual(spec[1], "mark", f"{key} 暴露了 kb_usage mark")
        self.assertNotIn("mark", self.mod.ResearchPlugin._BARE_ACTIONS)

    def test_bare_actions_all_resolve(self):
        """裸 action 映射必须都指回真实路由，不能有悬空键。"""
        for bare, target in self.mod.ResearchPlugin._BARE_ACTIONS.items():
            with self.subTest(bare=bare):
                self.assertIn(target, self.routes,
                              f"裸名 {bare} → {target}，但该路由不存在")

    def test_compat_map_entries(self):
        src = (PIPELINE / "kb_launcher.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count('("research",'), 8)

    def test_cli_entry_works(self):
        """真跑一次 `kb research report`，不是只测常量。"""
        tmp = Path(tempfile.mkdtemp(prefix="kb-p23-"))
        try:
            vault = tmp / "vault"
            vault.mkdir()
            shutil.copytree(PIPELINE, vault / "pipeline",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(TEMPLATE / "reference", vault / "reference")
            (vault / "20-技术 Technology").mkdir()
            (vault / "20-技术 Technology" / "a.md").write_text(
                "---\ntags: [开发]\nstatus: active\ndomain: 开发\n"
                "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.5\n"
                "kb_summary: 测试笔记\n---\n\n正文内容足够长到能通过检查。\n",
                encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(vault / "pipeline" / "kb_launcher.py"),
                 "research", "report", "--root", str(vault)],
                capture_output=True, text=True, timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertEqual(r.returncode, 0, out[-2000:])
            self.assertNotIn("Traceback", out)
            self.assertNotIn("未知", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class P24StaleCommentsTest(unittest.TestCase):
    """P2-4：注释不能撒谎。

    判据不是"关键词出现过没有"，而是"读者照着注释做会不会走错":
      · `kb engine` 只允许出现在**否定语境**（说明它不存在），不允许出现在迁移指引里；
      · `SUPERSEDED` / `DEPRECATED` 这类状态标记直接不许出现 ——
        即便写成"曾经标过 SUPERSEDED"也只是给搜索的人留了个假线索，
        转述成中文即可（2026-10-22 已按此改掉 clean.py / sync.py 的行内引用）。
    """

    def test_sync_no_fake_engine_hint(self):
        """`kb engine` 只能作为"不存在"的证据出现，不能作为迁移指引出现。

        判据：所有出现行必须同时含否定词（不存在 / 没有 / 任然…），
        不能出现"使用 `kb engine`"这类祈使句。
        """
        src = (PIPELINE / "sync.py").read_text(encoding="utf-8")
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "kb engine" not in line:
                continue
            # 说明排版常把否定词放在同一句的下一行（`敲 kb engine 得到的是…`），
            # 因此把「本行 + 下一行」并成一句判定，避免误判换行。
            ctx = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            with self.subTest(line=line.strip()):
                self.assertTrue(
                    any(w in ctx for w in ("不存在", "没有", "不成立")),
                    f"`kb engine` 出现在非否定语境：{line.strip()}")
        self.assertNotIn("DEPRECATED", src.split("import argparse")[0])

    def test_clean_no_false_superseded(self):
        src = (PIPELINE / "clean.py").read_text(encoding="utf-8")
        self.assertNotIn("SUPERSEDED", src)

    def test_claimed_plugin_routes_really_exist(self):
        """注释说"动作经 ops_plugin / organization_plugin 路由" —— 得真有。"""
        import ops_plugin
        import organization_plugin
        ops = ops_plugin.OpsPlugin._ROUTES
        for key in ("sync_dry-run", "sync_apply", "sync_rollback",
                    "sync_history", "sync_ingest-any"):
            with self.subTest(key=key):
                self.assertIn(key, ops, f"注释声称已路由，但 {key} 不存在")
                self.assertEqual(ops[key][0], "sync.py")
        self.assertGreaterEqual(len(organization_plugin.OrganizationPlugin._ROUTES), 8)


class P25ConflictFieldTest(unittest.TestCase):
    """P2-5：`conflicts_with` 一个名字。"""

    def setUp(self):
        self.schema = json.loads(
            (TEMPLATE / "reference" / "kb-schema.json").read_text(encoding="utf-8"))

    def test_schema_uses_plural(self):
        fields = self.schema.get("fields") or {}
        self.assertIn("conflicts_with", fields)
        if "conflict_with" in fields:
            spec = fields["conflict_with"]
            self.assertEqual(spec.get("_maturity"), "deprecated")
            self.assertEqual(spec.get("_alias_of"), "conflicts_with")

    def test_code_writes_plural(self):
        hit = False
        for py in sorted(PIPELINE.glob("*.py")):
            if "conflicts_with" in py.read_text(encoding="utf-8"):
                hit = True
                break
        self.assertTrue(hit, "代码里没有任何地方写 conflicts_with")

    def test_readme_uses_plural(self):
        """README 里旧名只能出现在"已废弃"的说明语境里。"""
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for line in readme.splitlines():
            if "`conflict_with`" not in line:
                continue
            with self.subTest(line=line.strip()[:80]):
                self.assertTrue(
                    any(w in line for w in ("废弃", "旧名", "deprecated")),
                    f"README 仍在推荐旧名 conflict_with：{line.strip()}")

    def test_schema_strings_consistent(self):
        src = (PIPELINE / "kb_schema.py").read_text(encoding="utf-8")
        self.assertNotIn('"conflict_with"', src)


class P26ContractDriftTest(unittest.TestCase):
    """P2-6：deploy 要报契约漂移。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tests"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "deploy_mod", REPO / "tests" / "deploy.py")
        cls.dep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.dep)

    def test_no_drift_on_template_copy(self):
        tmp = Path(tempfile.mkdtemp(prefix="kb-p26-"))
        try:
            shutil.copytree(TEMPLATE / "reference", tmp / "reference")
            self.assertEqual(self.dep.contract_drift(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_detects_modified_and_missing(self):
        tmp = Path(tempfile.mkdtemp(prefix="kb-p26-"))
        try:
            shutil.copytree(TEMPLATE / "reference", tmp / "reference")
            (tmp / "reference" / "kb-schema.json").write_text("{}", encoding="utf-8")
            (tmp / "reference" / "triage-config.json").unlink()
            drift = self.dep.contract_drift(tmp)
            names = {n for n, _a, _b in drift}
            self.assertIn("kb-schema.json", names)
            self.assertIn("triage-config.json", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_private_files_never_flagged(self):
        """用户私有配置（key/用户表）不提示，否则报假警。"""
        tmp = Path(tempfile.mkdtemp(prefix="kb-p26-"))
        try:
            shutil.copytree(TEMPLATE / "reference", tmp / "reference")
            (tmp / "reference" / "users.json").write_text(
                '{"u": 1}', encoding="utf-8")
            names = {n for n, _a, _b in self.dep.contract_drift(tmp)}
            self.assertNotIn("users.json", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_warning_text(self):
        self.assertEqual(self.dep._contract_warning([]), "")
        msg = self.dep._contract_warning([("a.json", "1", "2")])
        self.assertIn("契约漂移", msg)
        self.assertIn("a.json", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
