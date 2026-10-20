#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import importlib
import io
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
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "plugins"))
sys.path.insert(0, str(REPO / "scripts"))   # precheck / setup_obsidian
sys.path.insert(0, str(REPO))               # create_vault

import create_vault  # noqa: E402
import kb_claim  # noqa: E402
import kb_launcher  # noqa: E402
import precheck  # noqa: E402

FM_BASE = ("tags: [开发]\nstatus: active\ndomain: 开发\ncreated: 2026-01-01\n"
           "updated: 2026-01-01\nimportance: 0.5\nkb_summary: 测试\n")

def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


KBLauncher = kb_launcher.KBLauncher


def _add_argument_blocks(text: str):
    """粗粒度提取 add_argument(...) 的实参块（括号配对）。"""
    out, i = [], 0
    while True:
        m = re.search(r"add_argument\(", text[i:])
        if not m:
            break
        s = i + m.end()
        depth, j = 1, s
        while depth > 0 and j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        out.append(text[s:j - 1])
        i = j
    return out


def _store_true_flags() -> set:
    found = set()
    files = list(PIPELINE.glob("*.py")) + list((PIPELINE / "plugins").glob("*.py"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        for blk in _add_argument_blocks(text):
            if "store_true" in blk:
                found |= set(re.findall(r'["\'](--[a-zA-Z0-9-]+)["\']', blk))
    return found



# ── 19~21：安装器 / 校验器 / 标定的破坏性路径 ──────────────────
class InstallSafetyTest(unittest.TestCase):
    def test_windows_launcher_variable_expansion_is_well_formed(self):
        """kb.cmd 的 `%VAR:~0,-1%` 必须以 `%` 闭合；写成 `}` 会让 Windows 上 kb 全废。"""
        text = (REPO / "template-vault" / "kb.cmd").read_text(encoding="utf-8")
        self.assertIn("%VAULT:~0,-1%", text)
        self.assertNotIn("%VAULT:~0,-1}", text)

    def test_verify_never_suggests_force_reinstall(self):
        """verify.py 的修复建议不能让用户跑 --force（会清空整个知识库）。"""
        text = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
        self.assertNotIn("create_vault.py --vault {vault} --force", text)
        self.assertIn("REINSTALL_HINT", text)


class InstallerExitCodeTest(unittest.TestCase):
    def test_windows_batch_propagates_exit_code(self):
        """install.bat 必须 exit /b 透传退出码，否则批处理调用恒看到成功。"""
        text = (REPO / "install.bat").read_text(encoding="utf-8")
        self.assertIn("exit /b %RC%", text)
        tail = text.split("create_vault.py", 1)[1]
        self.assertNotIn("goto :eof", tail, "尾部仍然只用 goto :eof，退出码会被吞掉")

    def test_verify_install_treats_timeout_as_failure(self):
        """自检超时/异常必须按失败返回，否则「装坏了也算成功」。"""
        orig = create_vault.subprocess.run

        def boom(*a, **k):
            raise create_vault.subprocess.TimeoutExpired(cmd="x", timeout=1)

        create_vault.subprocess.run = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(create_vault.verify_install(Path("/tmp/whatever")))
        finally:
            create_vault.subprocess.run = orig

    def test_verify_windows_uses_comspec(self):
        """Windows 上 CreateProcess 不能直接跑 .cmd，必须经 cmd /c。"""
        text = (REPO / "scripts" / "verify.py").read_text(encoding="utf-8")
        self.assertIn("COMSPEC", text)


class PrecheckConsentTest(unittest.TestCase):
    def test_obsidian_requires_consent(self):
        """未装 Obsidian 时不得静默下载安装（非 TTY 一律跳过）。"""
        import precheck
        import setup_obsidian
        called = []
        orig_detect, orig_dl = setup_obsidian.detect, setup_obsidian.download_and_install
        setup_obsidian.detect = lambda: (False, None)
        setup_obsidian.download_and_install = lambda: (called.append(1), (True, "/fake"))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r1 = precheck.check_obsidian(allow_install=False)
                r2 = precheck.check_obsidian(allow_install=True)   # 测试环境非 TTY
            self.assertFalse(called, "未经用户确认就下载安装了 Obsidian")
            self.assertFalse(r1.found)
            self.assertFalse(r2.found)
        finally:
            setup_obsidian.detect, setup_obsidian.download_and_install = orig_detect, orig_dl


# ── 30~39：第三轮（插件层 CLI 一致性 / 摄取路由）──────────────


class LauncherParsingTest(unittest.TestCase):
    def test_key_equals_value(self):
        self.assertEqual(KBLauncher._parse_params(["--top=3", "hello"]),
                         {"top": "3", "_positional": ["hello"]})

    def test_bool_flag_does_not_eat_next_token(self):
        got = KBLauncher._parse_params(["--exclude-stale", "怎么备份"])
        self.assertIs(got.get("exclude_stale"), True)
        self.assertEqual(got.get("_positional"), ["怎么备份"])
        got = KBLauncher._parse_params(["--apply", "x.md"])
        self.assertIs(got.get("apply"), True)
        self.assertEqual(got.get("_positional"), ["x.md"])

    def test_bool_flags_cover_every_store_true_flag(self):
        missing = sorted(_store_true_flags() - kb_launcher.BOOL_FLAGS)
        self.assertEqual(
            missing, [],
            f"这些 store_true 旗标没进 BOOL_FLAGS，会吞掉下一个参数: {missing}")

    def _route(self, argv):
        rec = []
        l = KBLauncher(".")
        l._ensure_initialized = lambda: setattr(l, "_initialized", True)
        l.registry.execute = lambda p, a, params: (rec.append((p, a, dict(params))), 0)[1]
        l.run(argv)
        return rec[-1]

    def test_clean_apply_flag_routes_to_apply_action(self):
        plugin, action, _ = self._route(["clean", "--apply"])
        self.assertEqual((plugin, action), ("clean", "apply"))

    def test_ingest_apply_maps_to_intake_apply(self):
        self.assertIn(("ingest", "apply"), kb_launcher.COMPAT_MAP)
        plugin, action, _ = self._route(["ingest", "apply"])
        self.assertEqual((plugin, action), ("intake", "apply"))

    def test_real_flags_are_not_promoted_to_subcommands(self):
        """`--log` / `--json` 不是任何工具的子命令名，必须原样当旗标透传。"""
        plugin, action, params = self._route(["healthcheck", "--log"])
        self.assertEqual((plugin, action), ("healthcheck", "check"))
        self.assertIs(params.get("log"), True)
        _, action2, params2 = self._route(["clean", "taxonomy", "--apply"])
        self.assertEqual(action2, "taxonomy")
        self.assertIs(params2.get("apply"), True)


# ── 14：evolution_log 事件白名单 ─────────────────────────────


class LauncherFlagConsistencyTest(unittest.TestCase):
    def test_short_flag_preserved_verbatim(self):
        got = KBLauncher._parse_params(["-h"])
        self.assertEqual(got, {"-h": True})

    def test_multi_flag_collects_all_values(self):
        got = KBLauncher._parse_params(["--project-context-root", "/a", "/b", "--name", "x"])
        self.assertEqual(got["project_context_root"], ["/a", "/b"])
        self.assertEqual(got["name"], "x")

    def test_subprocess_plugin_serializes_lists_and_short_flags(self):
        import importlib
        sp = importlib.import_module("plugins._subprocess_plugin")
        root = Path(tempfile.mkdtemp(prefix="kb-p0-cmd-"))
        try:
            (root / "pipeline").mkdir(parents=True, exist_ok=True)
            (root / "pipeline" / "dummy.py").write_text("# noop\n", encoding="utf-8")

            class _P(sp.SubprocessPlugin):
                SCRIPT_NAME = "dummy.py"
                ACTION_MAP = {"go": None}

                def metadata(self):
                    from plugin_base import PluginMetadata
                    return PluginMetadata(name="dummy", version="1", plugin_type="kb_module",
                                          actions=["go"])

            captured = {}
            orig = sp.subprocess.run
            sp.subprocess.run = lambda cmd, *a, **k: (
                captured.update(cmd=cmd) or type("R", (), {"returncode": 0})())
            try:
                from plugin_base import PluginContext
                p = _P()
                p.initialize(PluginContext(vault_root=str(root)))
                p.execute("go", {"root": str(root), "project_context_root": ["/a", "/b"],
                                 "-h": True})
            finally:
                sp.subprocess.run = orig
            cmd = captured["cmd"]
            self.assertIn("-h", cmd, "短选项被改写了")
            self.assertNotIn("--h", cmd)
            self.assertEqual(cmd.count("--project-context-root"), 2,
                             "多值旗标没有逐个传值")
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ── 79~82：验收器必须验收「你要它验收的那个库」────────────────
class VerifyTargetsRightVaultTest(unittest.TestCase):
    """`kb verify --root X` 曾经把 schema/validate/healthcheck 三项**在另一个库里**跑。

    原因：这三项以裸 `kb` 作 argv[0]，而 `kb` 是 PATH 上的符号链接（指向别的库/别的安装），
    且这些子命令默认从 `KB_ROOT` 或模块目录推断 vault —— 实测 `kb verify --root <模板库>`
    测出来的数字属于 `/home/mushan/kb-kit-pure`（现役库）。
    验收工具绝不能验收错对象：数字、结论、退出码会全属于别人。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-verify-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_uses_target_own_launcher_and_root(self):
        import kb_verify
        (self.root / "pipeline").mkdir(parents=True, exist_ok=True)
        (self.root / "pipeline" / "kb_launcher.py").write_text("", encoding="utf-8")
        got = kb_verify._resolve_argv(["kb", "healthcheck", "summary"], self.root)
        self.assertNotEqual(got[0], "kb", "仍在用 PATH 上的 kb")
        self.assertEqual(got[1], str(self.root / "pipeline" / "kb_launcher.py"))
        self.assertIn("--root", got)
        self.assertEqual(got[got.index("--root") + 1], str(self.root))

    def test_resolve_refuses_non_kb_vault(self):
        import kb_verify
        self.assertIsNone(kb_verify._resolve_argv(["kb", "validate"], self.root),
                          "目标不是 kb-kit 库时应明确拒绝，而不是去测别的库")

    def test_resolve_rewrites_script_invocations(self):
        import kb_verify
        got = kb_verify._resolve_argv([sys.executable, "scripts/verify_curate.py"], self.root)
        self.assertEqual(got[1], str(self.root / "scripts" / "verify_curate.py"))
        self.assertEqual(got[got.index("--root") + 1], str(self.root))

    def test_main_actually_resolves_before_running(self):
        """真正的守卫：跑一遍 main()，看它**实际**调起的 argv 指向哪个库。

        只测 `_resolve_argv` 本身是不够的 —— 把 main 里的解析改回直接透传，
        那种测试照样全绿（已实测踩过）。
        """
        import kb_verify
        (self.root / "pipeline").mkdir(parents=True, exist_ok=True)
        (self.root / "pipeline" / "kb_launcher.py").write_text("", encoding="utf-8")
        seen = []

        def fake_run(argv, cwd, timeout):
            seen.append(list(argv))
            return {"code": 0, "out": "", "err": "", "secs": 0.0}

        orig_run, orig_argv = kb_verify._run, sys.argv
        kb_verify._run = fake_run
        sys.argv = ["kb_verify.py", "--root", str(self.root), "--json"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                kb_verify.main()
        finally:
            kb_verify._run, sys.argv = orig_run, orig_argv
        self.assertTrue(seen, "main 没有调起任何检查")
        launcher = str(self.root / "pipeline" / "kb_launcher.py")
        for argv in seen:
            if "-m" in argv and "unittest" in argv:
                continue                      # 单测项走 tests/ 定位，与 vault 无关
            self.assertNotIn("kb", argv,
                             f"main 仍以裸 kb 调起检查（会不会测到别的库）：{argv}")
            # 每一项都必须显式带上目标库的 --root
            self.assertIn("--root", argv, f"没传 --root：{argv}")
            self.assertEqual(argv[argv.index("--root") + 1], str(self.root),
                             f"--root 指向了别的库：{argv}")
            # 且必须用目标库自己的东西：kb 类走它自己的 launcher，脚本类走它自己目录下的脚本
            self.assertTrue(launcher in argv or any(str(self.root) in a for a in argv),
                            f"没有用目标库自己的入口：{argv}")

    def test_no_bare_kb_survives_in_checks(self):
        import kb_verify
        (self.root / "pipeline").mkdir(parents=True, exist_ok=True)
        (self.root / "pipeline" / "kb_launcher.py").write_text("", encoding="utf-8")
        for _key, _name, argv, _parser in kb_verify.CHECKS:
            if argv is None:
                continue
            got = kb_verify._resolve_argv(list(argv), self.root)
            self.assertNotIn("kb", got, f"argv 里仍有裸 kb：{got}")


# ── 83~84：活库不该因「未启用结晶」或「没分发 tests」而验收失败 ──


# ── 83~84：活库不该因「未启用结晶」或「没分发 tests」而验收失败 ──
class VerifyTolerantStatesTest(unittest.TestCase):
    def test_missing_tests_is_skip_not_fail(self):
        src = (PIPELINE / "kb_verify.py").read_text(encoding="utf-8")
        self.assertIn("--strict", src)
        self.assertIn('"skipped": skipped', src)
        # 统计必须用 `is False`，否则 pass=None 的跳过项会被当成失败
        self.assertIn('r["pass"] is False', src)

    def test_curate_probe_marks_unmeasured_as_na(self):
        src = (REPO / "template-vault" / "scripts" / "verify_curate.py").read_text(encoding="utf-8")
        self.assertIn('"N/A" if na', src)
        self.assertIn("curate_enabled", src)
        # 退出码只看 FAIL
        self.assertIn('if c["status"] == "FAIL"', src)


# ── 85：死链检测不得把代码示例当链接 ────────────────────────


class ClaimSourceDateTest(unittest.TestCase):
    def test_date_comes_from_note_not_today(self):
        """extracted_at 取来源材料时间，否则 raw_date > claim_date 的约束永远配不到对。"""
        self.assertEqual(kb_claim._source_date({"created": "2026-03-05"}, "2026-10-15"),
                         "2026-03-05")
        self.assertEqual(kb_claim._source_date({"event_at": "2026-01-02",
                                                "created": "2026-03-05"}, "2026-10-15"),
                         "2026-01-02")
        self.assertEqual(kb_claim._source_date({}, "2026-10-15"), "2026-10-15")
        self.assertEqual(kb_claim._source_date({"created": "not-a-date"}, "2026-10-15"),
                         "2026-10-15")


class CalibrateGuardTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-cal-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_synth_refuses_to_wipe_real_vault(self):
        import kb_calibrate
        write(self.root / "20-技术 Technology" / "我的笔记.md", "重要内容\n")
        write(self.root / ".git" / "HEAD", "ref: refs/heads/main\n")
        with self.assertRaises(SystemExit):
            kb_calibrate.synth(self.root, n=5, pairs=1)
        self.assertTrue((self.root / "20-技术 Technology" / "我的笔记.md").exists(),
                        "synth 清空了非仿真目录")

    def test_synth_allows_empty_dir(self):
        import kb_calibrate
        with contextlib.redirect_stdout(io.StringIO()):
            kb_calibrate.synth(self.root, n=5, pairs=1)
        self.assertTrue((self.root / "gt_dup.json").exists())


# ── 22~29：第二轮（研究层 / 检索 / 安装器）────────────────────
