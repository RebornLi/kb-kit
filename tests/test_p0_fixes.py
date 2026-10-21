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
import plugin_base
from plugin_base import PluginContext  # noqa: E402
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


# ── 跨模块 P0 回归子集（其余按模块拆分到 test_p0_*.py）──────


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
        """静态护栏：写动作（index/apply/mark/fix/bitemporal/decay）不得进白名单。

        P2-1 第二期后，`IN_PROCESS_ACTIONS` 的**唯一权威来源**是聚合插件的
        `_ROUTES` 第三列（True = 进程内快路径）。旧插件的类级
        `IN_PROCESS_ACTIONS` 一律为空 —— 它们已降为 shim（继承聚合类）。

        故本测试改为直接扫 `_ROUTES`：这比原来的"扫类属性"更严格，
        因为它覆盖全部 5 个域（含 ops），而不只是抽查 5 个旧插件文件。
        """
        from plugins._domain_plugin import DomainPlugin
        from plugins.ops_plugin import OpsPlugin
        # 必须先导入各域模块，否则聚合类还没定义，__subclasses__() 是空的
        # （实测踩到：只数到 ops 的 23 条，其余 4 域完全没被检查）
        import plugins.crystallization_plugin  # noqa: F401
        import plugins.ingestion_plugin        # noqa: F401
        import plugins.organization_plugin     # noqa: F401
        import plugins.retrieval_plugin        # noqa: F401
        WRITE = {"index", "apply", "mark", "fix", "bitemporal", "decay", "run", "promote"}
        offenders = []
        checked = 0

        def _all_subclasses(base):
            """递归收集全部后代类 —— `__subclasses__()` 只给直接子类，
            IngestionPlugin 继承的是 DomainPluginWithDelegates（中间层），
            用直接子类会漏掉它（实测只扫到 23 条）。"""
            out = []
            for sub in base.__subclasses__():
                out.append(sub)
                out.extend(_all_subclasses(sub))
            return out

        for cls in (OpsPlugin, *_all_subclasses(DomainPlugin)):
            routes = getattr(cls, "_ROUTES", {}) or {}
            for key, val in routes.items():
                if not (isinstance(val, tuple) and len(val) >= 3):
                    continue
                _script, _sub, in_proc = val[0], val[1], val[2]
                act = key[1] if isinstance(key, tuple) else key
                checked += 1
                if in_proc and act in WRITE:
                    offenders.append(f"{cls.__name__}.{act}")
        self.assertEqual(offenders, [], f"写动作被放进进程内白名单: {offenders}")
        self.assertGreater(checked, 30, "没扫到路由表，护栏形同虚设")

        # rag.query 仍必须保留快路径（它是唯一有实测收益的只读高频命令）
        from plugins.retrieval_plugin import RetrievalPlugin
        self.assertTrue(RetrievalPlugin._ROUTES[("rag", "query")][2],
                        "rag.query 的进程内快路径被误删（读命令，省 ~40ms）")


def subprocess_run(cmd, **kw):
    import subprocess as sp
    return sp.run(cmd, capture_output=True, text=True, timeout=120, **kw)


class OpsAggregatePluginTest(unittest.TestCase):
    """P2-1 域合并（ops 域）的行为不变守卫。

    这条测试锁住**两个真实踩过的坑**，都是"合并能注册、测试全绿、但 CLI 执行坏掉"
    的静默回归 —— 单测绿不等于 CLI 通，所以直接断言 CLI 构造：

      坑1. shim 保留原 `meta.actions`（那是对用户的 CLI 契约，不能改），而聚合类
           `OpsPlugin._ROUTES` 的键是消歧后的聚合名。只登记聚合名 → 所有 shim 调用
           都报"ops 不支持动作"（首次实测：`kb verify` 的巡检项挂掉）。
      坑2. `kb-healthcheck.py` 的**位置参数** `check` 是 argparse choices 且
           不含 "check"（合法值 all/summary/deadlinks/...），原插件
           `ACTION_MAP={"check": None}` 明确不传子命令。误传 → invalid choice。
    """

    @classmethod
    def setUpClass(cls):
        import plugins.ops_plugin as _ops
        import plugin_registry
        cls.ops = _ops
        cls.reg = plugin_registry.PluginRegistry
        # 用真实模板库做 vault_root（子进程/进程内都会去 pipeline/ 下找脚本）
        cls.vault = str(REPO / "template-vault")

    def _routes(self, plugin_name, actions, params):
        """执行指定插件的每一个 action，返回每次构造的 cmd。"""
        built = []
        import _subprocess_plugin as spmod
        import plugins.ops_plugin as opsmod

        reg = self.reg(str(REPO / "template-vault" / "pipeline" / "plugins"),
                       self.vault, config_path=None)
        reg.discover()
        reg.initialize_all(PluginContext(vault_root=self.vault))

        real_inproc = opsmod.SubprocessPlugin._execute_in_process
        real_run = spmod.subprocess.run

        def spy_inproc(cmd, script_path):
            built.append(list(cmd))
            return 0

        def spy_run(cmd, **kw):
            built.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        opsmod.SubprocessPlugin._execute_in_process = staticmethod(spy_inproc)
        spmod.subprocess.run = spy_run
        try:
            for a in actions:
                reg.execute(plugin_name, a, params)
        finally:
            opsmod.SubprocessPlugin._execute_in_process = real_inproc
            spmod.subprocess.run = real_run
        return built

    def test_shim_actions_all_route(self):
        """坑1：每个 shim 的原 action 都必须能路由（不能报"不支持动作"）。"""
        ops = self.ops.OpsPlugin
        missing = [a for a in ops._COMPAT_ACTIONS
                   if ops._COMPAT_ACTIONS[a] not in ops._ROUTES]
        self.assertEqual(missing, [],
                         f"_COMPAT_ACTIONS 指向了不存在的路由键: {missing}")

    def test_healthcheck_passes_no_positional(self):
        """坑2：healthcheck 的 action 不得被当位置参数传给 kb-healthcheck.py。

        kb-healthcheck.py 的 `check` 位置参数 choices 是
        all/summary/deadlinks/...，不含 "check" —— 传了就 invalid choice。
        原插件 ACTION_MAP={"check": None} 也是不传。
        """
        cmds = self._routes("healthcheck", ["check"], {"root": self.vault})
        self.assertEqual(len(cmds), 1, "healthcheck.check 应执行一次")
        self.assertEqual(
            cmds[0][1:],
            [f"{self.vault}/pipeline/kb-healthcheck.py", "--root", self.vault],
            "healthcheck 不得传位置子命令；必须与原插件 ACTION_MAP={'check': None} 一致")

    def test_every_ops_action_builds_expected_cli(self):
        """全量对照：23 个 action 的 CLI 构造必须与原 per-plugin 壳逐字节一致。"""
        V = self.vault
        EXPECTED = {
            "backup":       [f"{V}/pipeline/backup.py", "--root", V],
            "dry-run":      [f"{V}/pipeline/sync.py", "dry-run", "--root", V],
            "apply":        [f"{V}/pipeline/sync.py", "apply", "--root", V],
            "rollback":     [f"{V}/pipeline/sync.py", "rollback", "--root", V],
            "history":      [f"{V}/pipeline/sync.py", "history", "--root", V],
            "ingest-any":   [f"{V}/pipeline/sync.py", "ingest-any", "--root", V],
            "review":       [f"{V}/pipeline/memory_sync.py", "review", "--root", V],
            "promote":      [f"{V}/pipeline/memory_sync.py", "promote", "--root", V],
            "check":        [f"{V}/pipeline/kb-healthcheck.py", "--root", V],
            "metrics":      [f"{V}/pipeline/kb_health.py", "--root", V],
            "kpi":          [f"{V}/pipeline/kb_health.py", "kpi", "--root", V],
            "generate":     [f"{V}/pipeline/dashboard.py", "--root", V],
            "scan":         [f"{V}/pipeline/kb_audit.py", "scan", "--root", V],
            "fix":          [f"{V}/pipeline/kb_audit.py", "fix", "--root", V],
            "log":          [f"{V}/pipeline/kb_audit.py", "log", "--root", V],
            "run":          [f"{V}/pipeline/kb_eval_judge.py", "run", "--root", V],
            "gold":         [f"{V}/pipeline/kb_eval_judge.py", "gold", "--root", V],
            "compare":      [f"{V}/pipeline/kb_eval_judge.py", "compare", "--root", V],
            "all":          [f"{V}/pipeline/kb_verify.py", "--root", V],
            "add":          [f"{V}/pipeline/user_manager.py", "add", "--root", V],
            "list":         [f"{V}/pipeline/user_manager.py", "list", "--root", V],
            "remove":       [f"{V}/pipeline/user_manager.py", "remove", "--root", V],
            "check-perm":   [f"{V}/pipeline/user_manager.py", "check-perm", "--root", V],
        }
        # 注意键是各 shim 的原 action 名；backup 的 "backup" 与 user_manager 的无冲突
        by_plugin = {
            "backup": ["backup"],
            "sync": ["dry-run", "apply", "rollback", "history", "ingest-any"],
            "memory_sync": ["review", "promote"],
            "healthcheck": ["check"],
            "health_metrics": ["metrics", "kpi"],
            "dashboard": ["generate"],
            "audit": ["scan", "fix", "log"],
            "eval": ["run", "gold", "compare"],
            "verify": ["all"],
            "user_manager": ["add", "list", "remove", "check-perm"],
        }
        for plugin_name, actions in by_plugin.items():
            cmds = self._routes(plugin_name, actions, {"root": self.vault})
            for action, cmd in zip(actions, cmds):
                self.assertEqual(cmd[1:], EXPECTED[action],
                                 f"{plugin_name}.{action} 的 CLI 构造漂移")

    def test_ops_aggregate_actions_are_prefixed(self):
        """聚合入口（kb ops …）的 action 必须带前缀，避免与原名混用。"""
        ops = self.ops.OpsPlugin
        prefixed = {a for a in ops._ROUTES if "_" in a or "-" in a}
        self.assertIn("audit_scan", prefixed)
        self.assertIn("verify_all", prefixed)
        # 原名走 _COMPAT_ACTIONS，不是 _ROUTES 的键
        self.assertNotIn("scan", ops._ROUTES,
                         "聚合路由不应再把裸 'scan' 当键（那是 shim 兼容面）")
        self.assertIn("scan", ops._COMPAT_ACTIONS)
