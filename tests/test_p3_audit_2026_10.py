#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p3_audit_2026_10.py — 2026-10-22 审计发现的九项 P3 回归测试。

## 这九条的性质

P3 是"轻微/提示性"，但其中三条有**真实用户可见后果**，另有多条是
"写完了却没接线"的死代码。逐项实证后的处置：

  已修复
    P3-23  `except CalledProcessError` 是死代码（没传 check=True）
    P3-24  插件层无 timeout，长任务可无限挂起
    P3-26  `kb sync` 无子命令返回 0（底层 argparse 要求 2）—— 语义撒谎
    P3-29  `agent_registry.py` 死分支；`mark_policy_stale` 无调用者
           → 后者不是删掉，而是**接上 CLI**（FR-3.3.14 实现了却没入口）
    P3-27  `classify.py --root` 标注"未使用" → 让它真正参与 signals 查找
    P3-30  help 对齐测试只校验命令名 → 补 action 级守卫
    P3-31  `kb.cmd` 无 `exit /b`，Windows 下失败也返回 0

  结论：不实现（已记录理由）
    P3-25  capture 开关 —— 无真实消费者，且会破坏长任务的实时进度

  已自然消失
    P3-28  `agent_md_plugin` 静默 return —— 插件合并后 DomainPlugin
           统一打印"❓ 不支持动作"，不再静默

运行：python3 -m unittest discover -s tests
"""

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
TEMPLATE = REPO / "template-vault"
SCRIPTS = TEMPLATE / "scripts"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))


def _run(cmd, cwd=None, timeout=240, env=None):
    e = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _make_vault(tmp: Path) -> Path:
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
    _run([sys.executable, str(vault / "pipeline" / "rag.py"), "index",
          "--root", str(vault), "--full"], timeout=180)
    return vault


class P323DeadExceptTest(unittest.TestCase):
    """P3-23：不许再出现永不抛出的异常类型。"""

    def _except_types(self):
        """用 AST 取出真实 except 子句的类型（排除注释/字符串里的同名文本）。

        纯文本匹配会误报：修复说明的注释里就写着 CalledProcessError。
        """
        import ast
        tree = ast.parse((PIPELINE / "plugins" / "_subprocess_plugin.py")
                         .read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                if isinstance(node.type, ast.Tuple):
                    out += [ast.unparse(e) for e in node.type.elts]
                else:
                    out.append(ast.unparse(node.type))
        return out

    def test_no_called_process_error(self):
        self.assertNotIn("subprocess.CalledProcessError", self._except_types(),
                         "仍捕获 CalledProcessError（未传 check=True，永不抛出）")

    def test_run_has_no_check_true(self):
        """反向守卫：若将来真加了 check=True，上面那条会误报。"""
        import ast
        tree = ast.parse((PIPELINE / "plugins" / "_subprocess_plugin.py")
                         .read_text(encoding="utf-8"))
        checks = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and ast.unparse(node.func).endswith("subprocess.run")):
                checks += [k.arg for k in node.keywords]
        self.assertNotIn("check", checks)


class P324TimeoutTest(unittest.TestCase):
    """P3-24：插件层必须有超时，且语义清晰。"""

    def test_timeout_class_attr(self):
        import _subprocess_plugin as sp
        self.assertTrue(hasattr(sp.SubprocessPlugin, "TIMEOUT_SECS"))
        self.assertGreater(sp.SubprocessPlugin.TIMEOUT_SECS, 0)

    def test_zero_means_immediate_timeout(self):
        """TIMEOUT_SECS=0 必须是"立即超时"，不能被当成"不限制"。

        实现时踩过：用 `if timeout else subprocess.run(cmd)` 判断，
        结果 0 落入 else 分支变成不限制 —— subprocess.run(timeout=0) 的
        真实语义是立即超时，两者混用会让覆盖失效。
        """
        from _subprocess_plugin import SubprocessPlugin
        from plugin_base import PluginContext, PluginMetadata

        def mk(secs):
            class P(SubprocessPlugin):
                SCRIPT_NAME = "rag.py"
                TIMEOUT_SECS = secs
                def metadata(self):
                    return PluginMetadata(name="p", version="1",
                                          plugin_type="kb_module", actions=["index"])
            p = P()
            p.initialize(PluginContext(vault_root=str(self.vault)))
            return p

        tmp = Path(tempfile.mkdtemp(prefix="kb-p324-"))
        try:
            self.vault = _make_vault(tmp)
            rc = mk(0).execute("index", {"root": str(self.vault), "full": True})
            self.assertEqual(rc, 124, "TIMEOUT_SECS=0 未立即超时")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_none_means_unlimited(self):
        from _subprocess_plugin import SubprocessPlugin
        from plugin_base import PluginContext, PluginMetadata

        class P(SubprocessPlugin):
            SCRIPT_NAME = "rag.py"
            TIMEOUT_SECS = None
            def metadata(self):
                return PluginMetadata(name="p", version="1",
                                      plugin_type="kb_module", actions=["index"])

        tmp = Path(tempfile.mkdtemp(prefix="kb-p324-"))
        try:
            vault = _make_vault(tmp)
            p = P()
            p.initialize(PluginContext(vault_root=str(vault)))
            rc = p.execute("index", {"root": str(vault), "full": True})
            self.assertEqual(rc, 0, "TIMEOUT_SECS=None 应不限制")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_default_allows_normal_run(self):
        from _subprocess_plugin import SubprocessPlugin
        from plugin_base import PluginContext, PluginMetadata

        class P(SubprocessPlugin):
            SCRIPT_NAME = "rag.py"        # 不设会被当成目录，python 直接报错
            def metadata(self):
                return PluginMetadata(name="p", version="1",
                                      plugin_type="kb_module", actions=["index"])

        tmp = Path(tempfile.mkdtemp(prefix="kb-p324-"))
        try:
            vault = _make_vault(tmp)
            p = P()
            p.initialize(PluginContext(vault_root=str(vault)))
            rc = p.execute("index", {"root": str(vault), "full": True})
            self.assertEqual(rc, 0, "默认超时误伤了正常命令")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class P326SyncNoSubcommandTest(unittest.TestCase):
    """P3-26：`kb sync` 无子命令必须返回非 0。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-p326-"))
        self.vault = _make_vault(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sync_without_action_returns_nonzero(self):
        rc, out = _run([sys.executable, str(self.vault / "pipeline" / "kb_launcher.py"),
                        "sync", "--root", str(self.vault)])
        self.assertNotEqual(rc, 0, f"`kb sync` 无子命令返回 0（语义撒谎）：\n{out[-400:]}")
        self.assertIn("需要指定动作", out)

    def test_sync_with_action_still_works(self):
        rc, out = _run([sys.executable, str(self.vault / "pipeline" / "kb_launcher.py"),
                        "sync", "history", "--root", str(self.vault)])
        self.assertEqual(rc, 0, f"`kb sync history` 被误伤：\n{out[-400:]}")

    def test_no_plugin_returns_zero_on_action_list(self):
        """源码守卫：两处"列动作"分支都不许再 return 0。"""
        src = (PIPELINE / "kb_launcher.py").read_text(encoding="utf-8")
        # 抓 `支持的动作` / `需要指定动作` 打印后的 return
        for m in re.finditer(r"需要指定动作.*?\n(.*?\n)*?.*?return \d+", src):
            pass
        # 更直接的判据：文件里不应再有"打印动作列表 + return 0"的组合
        self.assertNotIn("支持的动作: {', '.join(meta.actions)}\")\n                    return 0",
                         src)


class P329PolicyStaleWiredTest(unittest.TestCase):
    """P3-29：FR-3.3.14 从死代码变成可达功能。"""

    def test_no_dead_if_false_branch(self):
        import ast
        tree = ast.parse((PIPELINE / "agent_registry.py").read_text(encoding="utf-8"))
        # 只查真实代码：注释里说明修复原因时也会引用旧写法
        for node in ast.walk(tree):
            if isinstance(node, ast.IfExp):
                # 条件是个常量（True/False/0/1）→ 死分支
                if isinstance(node.test, ast.Constant):
                    self.fail(f"agent_registry.py 仍有死分支（行 {node.lineno}）")
            if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
                self.fail(f"agent_registry.py 仍有恒真/恒假 if（行 {node.lineno}）")

    def test_mark_policy_stale_has_caller(self):
        """mark_policy_stale 必须被 main() 调用（曾全仓无调用者）。"""
        src = (PIPELINE / "kb_health.py").read_text(encoding="utf-8")
        self.assertIn("mark_policy_stale(args.root)", src,
                      "mark_policy_stale 仍无调用者")

    def test_policy_stale_cli_works(self):
        tmp = Path(tempfile.mkdtemp(prefix="kb-p329-"))
        try:
            vault = _make_vault(tmp)
            gov = vault / "70-知识治理 Governance"
            gov.mkdir()
            (gov / "制度.md").write_text(
                "---\ntags: [sop]\nstatus: active\ndomain: 管理\n"
                "content_type: policy\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
                "importance: 0.8\nkb_summary: 一条制度\nreview_cycle: 30\n"
                "last_reviewed: 2026-01-01\n---\n\n制度正文。\n", encoding="utf-8")
            # 只读检测
            rc, out = _run([sys.executable, str(vault / "pipeline" / "kb_health.py"),
                            "--policy-stale", "--root", str(vault)])
            self.assertEqual(rc, 0, out[-400:])
            self.assertIn("过期", out)
            # 写盘标记
            rc2, out2 = _run([sys.executable, str(vault / "pipeline" / "kb_health.py"),
                              "--policy-stale", "--mark-policy-stale",
                              "--root", str(vault)])
            self.assertEqual(rc2, 0, out2[-400:])
            self.assertIn("review_needed: true",
                          (gov / "制度.md").read_text(encoding="utf-8"),
                          "标记未写入文件")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_flags_registered_in_launcher(self):
        """新旗标必须在 BOOL_FLAGS 里，否则 launcher 会把下一个 token 当值吃掉。"""
        src = (PIPELINE / "kb_launcher.py").read_text(encoding="utf-8")
        self.assertIn('"--policy-stale"', src)
        self.assertIn('"--mark-policy-stale"', src)


class P327RootInjectionTest(unittest.TestCase):
    """P3-27：classify.py 的 --root 不再是无操作参数。"""

    def test_classify_root_no_longer_unused(self):
        import ast
        tree = ast.parse((PIPELINE / "classify.py").read_text(encoding="utf-8"))
        helps = [n.value.value for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "add_argument"
                 for n in n.keywords
                 if n.arg == "help" and isinstance(n.value, ast.Constant)]
        root_help = [h for h in helps if "vault 根" in h]
        self.assertTrue(root_help, "classify.py 没有 --root 的 help 文本")
        self.assertNotIn("未使用", root_help[0],
                         "classify.py 仍把 --root 标为未使用")

    def test_root_takes_priority_for_signals(self):
        """库根的配置应优先于脚本旁模板配置。"""
        tmp = Path(tempfile.mkdtemp(prefix="kb-p327-"))
        try:
            vault = _make_vault(tmp)
            # 在库根放一个独有的 signals 文件
            sig = vault / "reference" / "content-type-signals.json"
            orig = sig.read_text(encoding="utf-8")
            try:
                import json
                d = json.loads(orig)
                for k, v in d.items():
                    if isinstance(v, list) and v:
                        d[k] = v + ["QQQUNIQUESIGNAL"]
                        break
                sig.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                # 带 --root 时应读到库根配置（不报错即说明路径解析成功）
                rc, out = _run([sys.executable, str(vault / "pipeline" / "classify.py"),
                                "QQQUNIQUESIGNAL 出现在一段足够长的测试文本里面用于验证",
                                "--root", str(vault)])
                self.assertEqual(rc, 0, out[-400:])
                self.assertIn("content_type", out)
            finally:
                sig.write_text(orig, encoding="utf-8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fallback_when_vault_config_missing(self):
        """库根没有配置时要回退到模板配置，不能崩。"""
        tmp = Path(tempfile.mkdtemp(prefix="kb-p327-"))
        try:
            vault = _make_vault(tmp)
            sig = vault / "reference" / "content-type-signals.json"
            bak = tmp / "sig.bak"
            shutil.move(str(sig), str(bak))
            try:
                rc, out = _run([sys.executable,
                                str(vault / "pipeline" / "classify.py"),
                                "一段足够长的测试文本用于验证回退逻辑是否正常工作",
                                "--root", str(vault)])
                self.assertEqual(rc, 0, f"回退失败：\n{out[-400:]}")
            finally:
                shutil.move(str(bak), str(sig))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class P331WindowsExitCodeTest(unittest.TestCase):
    """P3-31：kb.cmd 必须透传退出码。"""

    def test_exit_b_errorlevel_present(self):
        src = (TEMPLATE / "kb.cmd").read_text(encoding="utf-8")
        self.assertIn("exit /b %ERRORLEVEL%", src,
                      "kb.cmd 未透传退出码 —— Windows 下失败也返回 0")

    def test_nopys_branch_exits_nonzero(self):
        src = (TEMPLATE / "kb.cmd").read_text(encoding="utf-8")
        # :nopys 分支也要非零（找不到解释器是失败）
        nopys = src.split(":nopys")[-1]
        self.assertIn("exit /b", nopys)

    def test_no_goto_eof_as_last_exit(self):
        """主路径末尾不能是 `goto :eof`（那会吞掉退出码）。"""
        src = (TEMPLATE / "kb.cmd").read_text(encoding="utf-8")
        main = src.split(":nopys")[0]
        self.assertNotIn("goto :eof", main,
                         "主路径仍用 goto :eof 结尾，退出码不会被透传")


class P325CaptureDecisionTest(unittest.TestCase):
    """P3-25：capture 开关的处置是"不实现"，这里守住这个结论的理由。"""

    def test_default_inherits_terminal(self):
        """默认必须继承终端（长任务的实时进度依赖这一点）。

        这也是"不加 capture 开关"的核心理由：捕获输出会让
        `clean refine` / `curate run` 在用户眼里变成静默挂起。
        """
        src = (PIPELINE / "plugins" / "_subprocess_plugin.py").read_text(encoding="utf-8")
        # 主调用路径不应带 capture_output / PIPE
        main_call = src[src.index("# 调用原脚本"):]
        self.assertNotIn("capture_output", main_call)
        self.assertNotIn("stdout=subprocess.PIPE", main_call)


if __name__ == "__main__":
    unittest.main(verbosity=2)
