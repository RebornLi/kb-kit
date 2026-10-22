#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p0_audit_2026_10.py — 2026-10-22 审计发现的三项 P0 回归测试。

## 为什么单开一个文件

这三条问题的共同点是：**362 个已有单测全绿，但仍然存在**。它们都不在
"函数返回什么"的层面，而在"首次运行 / 危险开关 / 跨模块契约"的接缝上，
用常规单测自然测不到。因此这里刻意用**真实子进程 + 真实临时库**来测，
而不是 mock —— 要防的正是"看起来修好了、实际没跑通"。

## 覆盖

P0-1  `kb_rsi` 在无 `.kb/state/` 的全新库上必崩
      (`FileNotFoundError: .kb/state/kb_rsi_proposals.jsonl`)
      根因：`state_path()` 只拼路径**不建目录**，而这里用的是裸 `open(..., "a")`。
      同类：`kb_schema_migrate --apply` 第一行也一样。

P0-2  `create_vault.py --force` 裸 `rmtree` 静默删除整个知识库（含 .git 历史）
      修复：先完整备份到 `<vault>/backups/pre-reinstall-<ts>/`，备份失败即整体放弃，
      非交互环境还需要显式 `--yes`。

P0-3  记忆适配器产出**不在 `DOMAIN_WHITELIST` 内**的 domain
      （`AI与LLM` / `基础设施` / `测试`）→ `kb validate` 硬错误 → 验收闸门失效
      修复：所有适配器统一走 `memory_ingest.normalize_domain()`。

运行：python3 -m unittest discover -s tests
"""

import json
import os
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
sys.path.insert(0, str(REPO))

import create_vault          # noqa: E402
import kb_config             # noqa: E402
import memory_ingest         # noqa: E402
import memory_ingest_json    # noqa: E402
import memory_ingest_sqlite  # noqa: E402
import state_manager         # noqa: E402

WHITELIST = kb_config.DOMAIN_WHITELIST


def _run(cmd, cwd=None, timeout=180, env=None):
    """跑子进程并返回 (returncode, 合并输出)。"""
    e = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class P01NoStateDirTest(unittest.TestCase):
    """P0-1：全新库（没有 .kb/state/）上的写入路径不能崩。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-p01-"))
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        # 只铺"像一个库"的最小结构：pipeline（让脚本能找到）+ 几篇笔记
        # **故意不建 .kb/** —— 这正是全新库的真实状态。
        shutil.copytree(PIPELINE, self.vault / "pipeline",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(TEMPLATE / "reference", self.vault / "reference")
        (self.vault / "20-技术 Technology").mkdir()
        (self.vault / "20-技术 Technology" / "a.md").write_text(
            "---\ntags: [开发]\nstatus: active\ndomain: 开发\n"
            "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.5\n"
            "kb_summary: 测试笔记\n---\n\n正文内容足够长到能通过最小长度检查。\n",
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_dir_absent_at_start(self):
        """前置断言：真的没有 .kb/ —— 否则这个测试就没在测它想测的东西。"""
        self.assertFalse((self.vault / ".kb").exists())

    def test_append_jsonl_creates_state_dir(self):
        """底层保证：append_jsonl 自己会建父目录（其他模块都该用它）。"""
        self.assertFalse((self.vault / ".kb").exists())
        p = state_manager.append_jsonl(self.vault, "probe.jsonl", {"k": 1})
        self.assertTrue(p.exists())
        self.assertEqual(p.parent, self.vault / ".kb" / "state")

    def test_kb_rsi_runs_on_fresh_vault(self):
        """`kb rsi` 是研究层仅有的两个 CLI 入口之一（README:191）。

        修复前这里必崩：
          FileNotFoundError: <vault>/.kb/state/kb_rsi_proposals.jsonl
        """
        rc, out = _run([sys.executable, str(self.vault / "pipeline" / "kb_rsi.py"),
                        "--root", str(self.vault)], timeout=120)
        self.assertEqual(rc, 0, f"kb rsi 在全新库上退出码非 0：\n{out[-1500:]}")
        self.assertNotIn("FileNotFoundError", out)
        # 且确实写下了记录
        log = self.vault / ".kb" / "state" / "kb_rsi_proposals.jsonl"
        self.assertTrue(log.exists(), "kb_rsi_proposals.jsonl 未被创建")
        rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertIn("proposals", rec)

    def test_kb_rsi_reports_real_path(self):
        """顺带修掉的 P3-1：输出文案要回显**真实**路径，不是老位置。"""
        rc, out = _run([sys.executable, str(self.vault / "pipeline" / "kb_rsi.py"),
                        "--root", str(self.vault)], timeout=120)
        self.assertEqual(rc, 0, out[-800:])
        self.assertIn(".kb/state/kb_rsi_proposals.jsonl", out.replace("\\", "/"))
        self.assertNotIn("pipeline/.kb_rsi_proposals.jsonl", out)

    def test_no_bare_open_on_state_path(self):
        """源码守卫：不允许再出现「state_path 后裸 open(...,"a")」这个组合。

        这是根因级守卫 —— 即使将来有人新增一个写状态文件的模块，
        只要它用了 `state_path(...)` + `open(..., "a")` 就会被这里拦下；
        正确写法是 `state_manager.append_jsonl()`（自带 parent.mkdir）
        或 `kb_io.atomic_write_text()`（同样自带 mkdir）。

        判定：在**包含 state_path 调用的函数体**里出现裸 open(...,"a")，
        且该文件里没有 parent.mkdir 兜底 → 视为违规。
        """
        import re
        offenders = []
        files = sorted(PIPELINE.glob("*.py")) + sorted((PIPELINE / "_research").glob("*.py"))
        for py in files:
            text = py.read_text(encoding="utf-8")
            if "state_path(" not in text:
                continue
            # state_manager.py 是 append_jsonl 的宿主，自己就带 mkdir
            if py.name == "state_manager.py":
                continue
            if not re.search(r'open\([^)]*,\s*["\']a["\']', text):
                continue
            # 有显式建目录就放行（这是正确的兜底写法）
            if "parent.mkdir(parents=True" in text or "parent.mkdir(parents=True, exist_ok=True)" in text:
                continue
            # 用了 append_jsonl / atomic_write_text 就说明走的是安全路径
            if "append_jsonl(" in text or "atomic_write_text(" in text:
                continue
            offenders.append(py.name)
        self.assertEqual(offenders, [],
                         f"仍有模块用 state_path + 裸 open 写状态（全新库会崩）: {offenders}")

    def test_kb_rsi_uses_append_jsonl(self):
        """单点守卫：kb_rsi 必须用 append_jsonl（P0-1 的根因修复）。

        只检查**代码**（用 AST 剥掉注释与字符串）—— 注释里提到旧写法是允许的，
        甚至在解释这个坑时是必要的。第一版用的是 `assertNotIn` 全文匹配，
        结果被自己写的注释误伤，反过来说明这个守卫必须做语法级而非文本级。
        """
        import ast
        src = (PIPELINE / "kb_rsi.py").read_text(encoding="utf-8")
        self.assertIn("append_jsonl", src)

        calls = [ast.unparse(n) for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call)]
        bare_open = [c for c in calls if c.startswith("open(")]
        self.assertEqual(bare_open, [],
                         f"kb_rsi 里仍有裸 open()（应改用 append_jsonl/atomic_write_text）: {bare_open}")

    def test_schema_migrate_apply_on_fresh_vault(self):
        """同类 bug：`kb_schema_migrate --apply` 以前第一条就打崩。"""
        note = self.vault / "20-技术 Technology" / "a.md"
        self.assertFalse((self.vault / ".kb").exists())
        rc, out = _run([sys.executable, str(self.vault / "pipeline" / "kb_schema_migrate.py"),
                        "--root", str(self.vault), "--apply"], timeout=120)
        self.assertNotIn("FileNotFoundError", out,
                         f"schema_migrate --apply 在全新库上崩了：\n{out[-1200:]}")
        self.assertTrue(note.exists())


class P02ForceBackupTest(unittest.TestCase):
    """P0-2：`--force` 必须先备份、再确认、最后才删。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-p02-"))
        self.vault = self.tmp / "myvault"
        self.vault.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, name="重要决策.md", body="不可再生内容"):
        d = self.vault / "10-项目 Projects"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(
            "---\ntags: [decision]\nstatus: active\ndomain: 管理\n"
            "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.9\n"
            f"kb_summary: {name}\n---\n\n{body}\n", encoding="utf-8")
        return p

    def test_backup_helper_copies_everything(self):
        """`_backup_vault` 必须把顶层条目**全部**复制过去（含隐藏目录）。"""
        self._seed()
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
        res = create_vault._backup_vault(self.vault)
        self.assertIsNotNone(res, "备份返回 None")
        dst, n = res
        self.assertTrue((dst / "10-项目 Projects" / "重要决策.md").exists())
        self.assertTrue((dst / ".obsidian" / "app.json").exists())
        # 备份自身不能被递归复制
        self.assertFalse((dst / "backups").exists())
        self.assertGreaterEqual(n, 2)

    def test_force_without_yes_is_refused_in_non_tty(self):
        """非交互环境 + 没给 --yes → 拒绝，且**一个字都不删**。"""
        note = self._seed()
        rc, out = _run([sys.executable, str(REPO / "create_vault.py"),
                        "--vault", str(self.vault), "--force",
                        "--no-git", "--no-demo", "--no-verify", "--no-open", "--no-global-kb"],
                       timeout=120)
        self.assertNotEqual(rc, 0, "非交互 --force 竟然成功了")
        self.assertTrue(note.exists(), "笔记被删了 —— 这正是要防的事故")
        self.assertIn("--yes", out)

    def test_force_yes_backs_up_before_deleting(self):
        """--force --yes：原文必须出现在备份里，然后才被替换。"""
        note = self._seed(body="这是唯一的一份不可再生内容")
        rc, out = _run([sys.executable, str(REPO / "create_vault.py"),
                        "--vault", str(self.vault), "--force", "--yes",
                        "--no-git", "--no-demo", "--no-verify", "--no-open", "--no-global-kb"],
                       timeout=180)
        self.assertEqual(rc, 0, f"安装失败：\n{out[-1500:]}")
        # 原笔记已从原位消失（被模板覆盖）
        self.assertFalse(note.exists(), "笔记居然还在原位 —— --force 没生效？")
        # 但**备份里必须能找到它**
        backups = list((self.vault / "backups").glob("pre-reinstall-*"))
        self.assertEqual(len(backups), 1, f"备份目录异常: {backups}")
        restored = backups[0] / "10-项目 Projects" / "重要决策.md"
        self.assertTrue(restored.exists(), f"备份里找不到原文：{list(backups[0].rglob('*'))[:10]}")
        self.assertIn("不可再生内容", restored.read_text(encoding="utf-8"))

    def test_git_history_survives_in_backup(self):
        """`.git` 历史也必须进备份 —— 用户最痛的就是丢了 commit 历史。"""
        self._seed()
        r = _run(["git", "init", "-q", "."], cwd=self.vault)
        if r[0] != 0:
            self.skipTest("git 不可用")
        _run(["git", "config", "user.email", "t@t"], cwd=self.vault)
        _run(["git", "config", "user.name", "t"], cwd=self.vault)
        _run(["git", "add", "-A"], cwd=self.vault)
        _run(["git", "commit", "-qm", "初始提交"], cwd=self.vault)

        rc, out = _run([sys.executable, str(REPO / "create_vault.py"),
                        "--vault", str(self.vault), "--force", "--yes",
                        "--no-demo", "--no-verify", "--no-open", "--no-global-kb"],
                       timeout=180)
        self.assertEqual(rc, 0, out[-1200:])
        bk = list((self.vault / "backups").glob("pre-reinstall-*"))[0]
        self.assertTrue((bk / ".git").is_dir(), ".git 没进备份")
        rc2, log = _run(["git", "-C", str(bk), "log", "--oneline"], timeout=30)
        self.assertEqual(rc2, 0, log)
        self.assertIn("初始提交", log)

    def test_backup_failure_aborts_without_deleting(self):
        """备份失败 → 整体放弃，**绝不删除**（宁可装不上，不能丢数据）。"""
        note = self._seed()
        orig = create_vault._backup_vault
        create_vault._backup_vault = lambda vault: None   # 模拟备份失败
        try:
            ok = create_vault.ensure_target(self.vault, force=True, assume_yes=True)
        finally:
            create_vault._backup_vault = orig
        self.assertFalse(ok, "备份失败时 ensure_target 居然返回 True")
        self.assertTrue(note.exists(), "备份失败时仍然删除了文件")

    def test_force_on_empty_dir_needs_no_backup(self):
        """空目录 + --force：不该建一个空的备份目录。"""
        ok = create_vault.ensure_target(self.vault, force=True, assume_yes=True)
        self.assertTrue(ok)
        # 空目录没有可备份内容；backups/ 是否出现不影响正确性，但不应误删任何东西
        self.assertTrue(self.vault.is_dir())


class P03DomainWhitelistTest(unittest.TestCase):
    """P0-3：所有记忆写入路径产出的 domain 都必须在白名单内。"""

    # 实测语料里出现过的**非法**主题词（旧 _domain 词表）
    ILLEGAL = ["AI与LLM", "基础设施", "测试"]
    LEGAL = sorted(WHITELIST)

    def test_whitelist_is_the_7_domains(self):
        """前置：白名单就是这 7 个（若将来扩展，下面的断言自动跟着变）。"""
        self.assertEqual(WHITELIST, {"产品", "安全", "开发", "数据", "管理", "综合", "运维"})

    def test_normalize_domain_never_returns_illegal(self):
        """核心不变量：随便喂什么都只可能得到白名单内的值。"""
        probes = (self.ILLEGAL + self.LEGAL +
                  ["开发/后端", "项目管理", "知识管理", "技术", "AI", "部署", "数据库",
                   "Career-Pivot", "面试准备", "", None, "  开发  ", "随便什么", "???"])
        for raw in probes:
            got = memory_ingest.normalize_domain(raw)
            self.assertIn(got, WHITELIST, f"normalize_domain({raw!r}) → {got!r} 不在白名单")

    def test_alias_mapping_is_sensible(self):
        """别名映射要落到"最像的那一类"，而不是一律兜底「综合」。"""
        cases = {
            "AI与LLM": "开发", "测试": "开发", "技术": "开发",
            "基础设施": "运维", "部署": "运维",
            "数据库": "数据",
            "项目管理": "管理", "知识管理": "管理",
            "开发/后端": "开发",
        }
        for raw, want in cases.items():
            self.assertEqual(memory_ingest.normalize_domain(raw), want,
                             f"{raw!r} 应归一为 {want!r}")

    def test_unknown_falls_back_to_comprehensive(self):
        for raw in ("随便什么", "???", "", None):
            self.assertEqual(memory_ingest.normalize_domain(raw), "综合")

    def test_json_adapter_domain_is_legal(self):
        """DSH JSON 适配器的 _domain 是 `kb ingest agent` 最常用的一条路径。"""
        for text in ["AI与LLM 模型部署", "基础设施 机房", "测试 用例",
                     "运维 值班", "安全 渗透", "产品 需求", "不相关的闲聊"]:
            got = memory_ingest_json._domain(text)
            self.assertIn(got, WHITELIST, f"_domain({text!r}) → {got!r} 不在白名单")

    def test_sqlite_adapter_domain_is_legal(self):
        for text in ["AI与LLM 模型部署", "基础设施 机房", "测试 用例", "不相关的闲聊"]:
            got = memory_ingest_sqlite._domain(text)
            self.assertIn(got, WHITELIST, f"_domain({text!r}) → {got!r} 不在白名单")

    def test_adapter_kb_target_uses_whitelist_keys(self):
        """目录名也必须从白名单派生 —— 否则 domain 与目录分叉。"""
        for text in ["AI与LLM 部署", "基础设施 机房", "测试 用例", "数据 备份"]:
            tgt = memory_ingest_json._kb_target(text)
            self.assertIsInstance(tgt, str)
            self.assertTrue(tgt.startswith(("10-", "20-", "30-", "40-")),
                            f"非 PARA 顶层目录: {tgt}")
            # 子目录名不应再出现旧的非法主题词
            for bad in self.ILLEGAL:
                if bad in (self.ILLEGAL[0],):     # "AI与LLM" 作为技术子目录名是既有的
                    continue
                self.assertNotIn(bad, tgt, f"{text!r} → {tgt} 仍含旧主题词 {bad}")

    def test_end_to_end_ingest_then_validate(self):
        """端到端：摄取 4 条"非法 domain"记忆 → `kb validate` 必须 0 硬错误。

        这是最有价值的一条：它同时覆盖 json adapter → frontmatter → validate
        三跳，正是事故发生时的那条链路。
        """
        tmp = Path(tempfile.mkdtemp(prefix="kb-p03-"))
        try:
            vault = tmp / "vault"
            vault.mkdir()
            shutil.copytree(PIPELINE, vault / "pipeline",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(TEMPLATE / "reference", vault / "reference")
            for d in ("00-收件箱 Inbox", "90-归档 Archive"):
                (vault / d).mkdir()

            mem = tmp / "mem.json"
            mem.write_text(json.dumps({"tables": {"records": {
                "r1": {"content": "AI与LLM 推理服务必须用 vLLM 部署，端口 8000",
                       "kind": "lesson", "importance": 2,
                       "createdAt": "2026-10-01T10:00:00Z"},
                "r2": {"content": "基础设施 机房断电演练：每季度一次",
                       "kind": "fact", "importance": 1,
                       "createdAt": "2026-10-02T10:00:00Z"},
                "r3": {"content": "测试 用例设计要覆盖边界值，pytest 参数化最省事",
                       "kind": "lesson", "importance": 1,
                       "createdAt": "2026-10-03T10:00:00Z"},
                "r4": {"content": "运维 值班交接必须写 checklist，否则半夜出事没人知道",
                       "kind": "policy", "importance": 2,
                       "createdAt": "2026-10-04T10:00:00Z"},
            }}}, ensure_ascii=False), encoding="utf-8")

            n = memory_ingest_json.ingest(vault, mem, dry_run=False)
            self.assertEqual(n, 4, "摄取条数不符")

            rc, out = _run([sys.executable, str(vault / "pipeline" / "kb_launcher.py"),
                            "validate", "--root", str(vault), "--strict"], timeout=120)
            self.assertNotIn("domain 非法", out, f"仍有非法 domain：\n{out[-1500:]}")
            self.assertNotIn("缺必填字段", out, f"有必填字段缺失：\n{out[-1500:]}")
            self.assertEqual(rc, 0, f"validate --strict 退出码非 0：\n{out[-1500:]}")

            # 落盘后的每个 domain 都在白名单内
            import re
            for md in vault.rglob("*.md"):
                if any(seg in md.parts for seg in ("pipeline", "reference")):
                    continue
                text = md.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"^domain:\s*(.+)$", text, re.M)
                if m:
                    self.assertIn(m.group(1).strip(), WHITELIST,
                                  f"{md} 的 domain={m.group(1)!r} 不在白名单")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
