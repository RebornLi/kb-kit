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
        """静态护栏：写动作（index/apply/mark/fix/bitemporal/decay）不得进白名单。"""
        import plugins.rag_plugin as rp
        import plugins.recall_plugin as rcp
        import plugins.schema_plugin as sp
        import plugins.audit_plugin as ap
        import plugins.confidence_plugin as cp
        from plugins._subprocess_plugin import SubprocessPlugin
        WRITE = {"index", "apply", "mark", "fix", "bitemporal", "decay", "run", "promote"}
        for mod in (rp, rcp, sp, ap, cp):
            for v in vars(mod).values():
                if isinstance(v, type) and issubclass(v, SubprocessPlugin) \
                        and v is not SubprocessPlugin and v.IN_PROCESS_ACTIONS:
                    self.assertTrue(v.IN_PROCESS_ACTIONS.isdisjoint(WRITE),
                                    f"{mod.__name__} 把写动作放进了进程内白名单")
        self.assertIn("query", rp.RAGPlugin.IN_PROCESS_ACTIONS)


def subprocess_run(cmd, **kw):
    import subprocess as sp
    return sp.run(cmd, capture_output=True, text=True, timeout=120, **kw)
