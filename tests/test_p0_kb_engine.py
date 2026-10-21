#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import research_src  # noqa: E402
sys.path.insert(0, str(PIPELINE / "plugins"))
sys.path.insert(0, str(REPO / "scripts"))   # precheck / setup_obsidian
sys.path.insert(0, str(REPO))               # create_vault

import kb_engine  # noqa: E402
import kb_meta  # noqa: E402



class EngineDeltaTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-neg-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_neg_delta_skips_health_rows(self):
        """每轮末尾追加的 tier=health 行没有 ab_snapshot，不能让它把统计 break 掉。"""
        log = self.root / "pipeline" / ".kb_engine_proposals.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"ab_snapshot": {"delta": -0.1}},
                {"ab_snapshot": {"delta": -0.2}},
                {"tier": "health", "health": 0.9}]
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        self.assertEqual(kb_engine._count_consecutive_neg_delta(self.root), 2)

    def test_config_path_follows_root(self):
        """kb_engine 的配置必须跟着 --root 走（且落在统一的 .kb/state/），不写进模块目录。

        位置演进：模块目录（写脏源码树）→ `<root>/pipeline/` → **`<root>/.kb/state/`**
        （状态文件统一约定）。旧位置仍可读，见 test_legacy_config_location_is_read。
        """
        other = Path(tempfile.mkdtemp(prefix="kb-p0-cfg-"))
        try:
            kb_engine.save_config(dict(kb_engine.CONFIG_DEFAULT, confidence_floor=0.71), other)
            self.assertTrue((other / ".kb" / "state" / "kb_rsi_config.json").exists(),
                            "配置应写入 .kb/state/（统一约定）")
            self.assertEqual(kb_engine.load_config(other)["confidence_floor"], 0.71)
            self.assertEqual(kb_engine.config_path(), kb_engine._LEGACY_CONFIG_PATH)  # 无 root 向后兼容
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_legacy_config_location_is_read(self):
        """旧位置 pipeline/.kb_rsi_config.json 必须仍能读到（否则升级后开关全部复位）。"""
        other = Path(tempfile.mkdtemp(prefix="kb-p0-cfg2-"))
        try:
            (other / "pipeline").mkdir(parents=True, exist_ok=True)
            (other / "pipeline" / ".kb_rsi_config.json").write_text(
                json.dumps({"confidence_floor": 0.42}), encoding="utf-8")
            self.assertEqual(kb_engine.load_config(other)["confidence_floor"], 0.42)
        finally:
            shutil.rmtree(other, ignore_errors=True)


class T3RatchetTest(unittest.TestCase):
    def test_t3_has_symmetric_relaxation_and_stable_is_not_stalled(self):
        src = research_src("kb_engine")
        self.assertIn("TUNED_RELAX", src, "T3 只有收紧、没有放宽（单向棘轮）")
        self.assertNotIn("fitness_trend <= 0", src,
                         "把 trend==0（稳定）当「停滞」会每次运行都收紧一档")
        self.assertIn("稳定不是失败信号", src)


# ── 54~57：查询路径性能（v5 索引预存范数）──────────────────


class L4FreezeGateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-l4-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_not_grounded_does_not_freeze_rung(self):
        """未接地时不得把候选冻结写盘 —— 否则「闭锁系统更锁死」。"""
        import kb_l4
        from kb_constants import L4_FREEZE_CONSEC, L4_MIN_SAMPLE
        p = kb_l4._verdict_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        need = max(L4_FREEZE_CONSEC, L4_MIN_SAMPLE) + 2
        p.write_text("\n".join(
            json.dumps({"rung": "lc", "verb": "wrong", "ts": f"2026-10-{10 + i:02d}T00:00:00"})
            for i in range(need)) + "\n", encoding="utf-8")
        grounded, _note, _ = kb_l4.grounding(self.root)
        res = kb_l4.freeze(self.root)
        cfg = json.loads(kb_l4._config_path(self.root).read_text(encoding="utf-8"))
        if not grounded:
            self.assertTrue(res["newly_frozen"], "测试前置不成立：本应产生候选")
            self.assertFalse(cfg["frozen"].get("lc"),
                             "未接地却把 rung 冻上了（自锁 bug 回归）")


# ── 63~70：第七轮（研究层小缺陷）─────────────────────────────
class L4LedgerToleranceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-l4-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_one_corrupt_line_does_not_wipe_ledger(self):
        """以前账本里一行 JSON 损坏就整体返回 []，裁决历史「凭空消失」、信任度归零。"""
        import kb_l4
        p = kb_l4._verdict_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"rung":"lc","verb":"wrong","ts":"2026-10-01T00:00:00"}\n'
                     '{ 这不是 JSON\n'
                     '{"rung":"lc","verb":"correct","ts":"2026-10-02T00:00:00"}\n',
                     encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            rows = kb_l4._read_verdicts(self.root)
        self.assertEqual(len(rows), 2, "损坏行把整本账本带走了")

    def test_render_reuses_passed_summary(self):
        """render(root, summary) 必须复用传入的 summary（status 是全库扫描）。"""
        import kb_l4
        calls = {"n": 0}
        orig = kb_l4.status

        def counting(root):
            calls["n"] += 1
            return orig(root)

        kb_l4.status = counting
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                kb_l4.render(self.root, orig(self.root))
        finally:
            kb_l4.status = orig
        self.assertEqual(calls["n"], 0, "render 忽略了传入的 summary，又扫了一遍全库")


# ── 98~101：状态文件路径统一（5 套约定 → .kb/state/）──────────────
class StatePathConventionTest(unittest.TestCase):
    """状态文件曾经散在 5 套约定里：根 dotfile、`pipeline/` dotfile、`.kb/` 直下、
    `.kb/state/`（StateStore）、以及 vault 根的普通文件。

    现统一为 **`<vault>/.kb/state/<name>`**，其余位置只在 `state_manager.LEGACY_PATHS`
    里登记为**只读回退**（旧库升级后状态不会凭空清零）。这几条守住这个约定。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-state-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_module_hardcodes_a_dotfile_state_path(self):
        """除 state_manager 的旧路径表外，任何模块都不应再构造「点文件」状态路径。

        迁移前各处写 `Path(root) / ".kb_meta_state.json"` 之类；迁移后只允许作为
        `legacy=(...)` 参数出现。这条用「点文件字面量」做判据（精确指向旧约定）。
        """
        dotfile = re.compile(r'["\'](\.[A-Za-z0-9_.-]+\.(?:json|jsonl))["\']')
        # 先剥掉「旧路径登记」块（含跨行的 legacy=(...) 与 _LEGACY = {...}），再扫描。
        legacy_block = re.compile(
            r"(?:legacy\s*=\s*\([^)]*\)"                      # legacy=(...)，可跨行
            r"|^_LEGACY[A-Za-z_]*\s*=.*$"                       # 单行 _LEGACY_X = <表达式>
            r"|_LEGACY[A-Za-z_]*\s*=\s*[\{\(][^\}\)]*[\}\)])", re.S | re.M)
        offenders = []
        for f in sorted(PIPELINE.glob("*.py")):
            if f.name == "state_manager.py":
                continue                      # 旧路径表就在这里，是唯一合法出处
            src = legacy_block.sub("", f.read_text(encoding="utf-8"))
            for i, line in enumerate(src.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for m in dotfile.finditer(line):
                    offenders.append(f"{f.name}:{i} {m.group(1)}")
        self.assertEqual(offenders, [],
                         "仍有模块写死旧位置的状态文件（应改用 state_manager 原语）：\n"
                         + "\n".join(offenders))

    def test_every_registered_state_name_is_under_kb_state(self):
        from state_manager import LEGACY_PATHS, state_path, STATE_DIR
        for name in LEGACY_PATHS:
            p = state_path(self.root, name)
            self.assertEqual(p.parent.name, "state")
            self.assertEqual(p.parent.parent.name, ".kb")
            self.assertTrue(str(p).endswith(f"{STATE_DIR}/{name}".replace("/", os.sep))
                            or p.name == name)

    def test_legacy_location_is_read_when_canonical_missing(self):
        from state_manager import read_state_path, write_json
        legacy = self.root / ".kb_meta_state.json"
        legacy.write_text('{"cycles": [1]}', encoding="utf-8")
        got = read_state_path(self.root, "kb_meta_state.json",
                              legacy=(".kb_meta_state.json",))
        self.assertEqual(got, legacy, "规范文件缺失时应回退到旧位置")
        write_json(self.root, "kb_meta_state.json", {"cycles": [2]})
        self.assertEqual(read_state_path(self.root, "kb_meta_state.json",
                                         legacy=(".kb_meta_state.json",)).parent.name,
                         "state", "写了规范文件后应优先读规范位置")

    def test_write_never_touches_legacy_file(self):
        """写只写规范路径：旧文件必须原样保留（迁移期不搬、不删，避免数据丢失）。"""
        from state_manager import write_json
        legacy = self.root / ".kb_l4_state.json"
        legacy.write_text('{"trust": {}}', encoding="utf-8")
        before = legacy.read_text(encoding="utf-8")
        write_json(self.root, "kb_l4_state.json", {"trust": {"l0": 1}})
        self.assertEqual(legacy.read_text(encoding="utf-8"), before, "旧文件被改动了")
        self.assertTrue((self.root / ".kb" / "state" / "kb_l4_state.json").exists())

    def test_jsonl_append_creates_state_dir(self):
        from state_manager import append_jsonl, read_jsonl
        append_jsonl(self.root, "kb_evolution.jsonl", {"event": "X"})
        self.assertTrue((self.root / ".kb" / "state" / "kb_evolution.jsonl").exists(),
                        "追加时应自动创建 .kb/state/")
        self.assertEqual(len(read_jsonl(self.root, "kb_evolution.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()


# ── 18：kb_meta apply --all ──────────────────────────────────
class KbMetaApplyAllTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-meta-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cli_apply_all_passes_force(self):
        """`--all` 必须把 force=True 传进 apply()，否则 pick 恒空、写 0 个却报成功。"""
        seen = {}
        orig_apply, orig_argv = kb_meta.apply, sys.argv
        kb_meta.apply = lambda root, ids=None, force=False: (
            seen.update(ids=ids, force=force) or
            {"written": [], "skipped": [], "note": ""})
        try:
            sys.argv = ["kb_meta.py", "apply", "--root", str(self.root), "--all"]
            with contextlib.redirect_stdout(io.StringIO()):
                kb_meta.main()
        finally:
            kb_meta.apply = orig_apply
            sys.argv = orig_argv
        self.assertTrue(seen.get("force"), "--all 没有传出 force=True")
        self.assertIsNone(seen.get("ids"))


# ── 19~21：安装器 / 校验器 / 标定的破坏性路径 ──────────────────


# ── 75~78：状态文件原子写入（第十一轮去重）──────────────────
class AtomicWriteTest(unittest.TestCase):
    """状态文件半截 = 配置回退默认 / 用户表失效 / agent 注册表整表丢失。

    原先只有 `state_manager._atomic_write` 是原子的，另有 4 处（kb_meta 状态与配置、
    user_manager 用户表、agent_registry 注册表）直接 `write_text`。现已统一到
    `kb_io.atomic_write_text`。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb-p0-atomic-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_roundtrip_and_no_tmp_left(self):
        from kb_io import atomic_write_text
        p = self.root / ".kb" / "state" / "x.json"
        atomic_write_text(p, '{"a": 1}')
        self.assertEqual(p.read_text(encoding="utf-8"), '{"a": 1}')
        self.assertFalse(p.with_name(p.name + ".tmp").exists(), "残留临时文件")
        self.assertTrue(p.parent.is_dir(), "父目录应被自动创建")

    def test_overwrite_existing(self):
        """第二次写入必须成功（Windows 上 Path.rename 在目标存在时会 FileExistsError）。"""
        from kb_io import atomic_write_text
        p = self.root / "y.json"
        atomic_write_text(p, "1")
        atomic_write_text(p, "2")
        self.assertEqual(p.read_text(encoding="utf-8"), "2")

    def test_failure_does_not_corrupt_existing_file(self):
        """写入过程中抛错时，旧内容必须完好、且不留临时文件。"""
        from kb_io import atomic_write_text
        p = self.root / "z.json"
        atomic_write_text(p, "good")
        orig = Path.write_text

        def boom(self, *a, **k):
            raise OSError("disk full")

        Path.write_text = boom
        try:
            with self.assertRaises(OSError):
                atomic_write_text(p, "bad")
        finally:
            Path.write_text = orig
        self.assertEqual(p.read_text(encoding="utf-8"), "good", "旧内容被破坏")
        self.assertFalse(p.with_name(p.name + ".tmp").exists(), "残留临时文件")

    def test_state_writers_use_atomic_write(self):
        # kb_meta 已移入 _research/（P2-2），经 research_src 穿透垫片读真实实现
        targets = {
            "kb_meta": 2,                 # _write_state + _write_config
            "user_manager": 1,            # save_users
            "agent_registry": 1,          # save_registry
        }
        for rel, n in targets.items():
            src = research_src(rel)
            # 两条都算原子：直接 atomic_write_text，或走 state_manager.write_json（内部即原子写）
            n_atomic = src.count("atomic_write_text(") + src.count("write_json(")
            self.assertGreaterEqual(n_atomic, n, f"{rel} 状态写入未原子化")
        # state_manager 应委托共享实现，而不是保留第二份同构代码。
        # owner 已从 kb_common（shim）迁到 kb_io —— 断言"委托 kb_io 的原子写"，
        # 不再绑死中间层模块名（P1-1 收尾：shim 正在被摘掉）。
        sm = (PIPELINE / "state_manager.py").read_text(encoding="utf-8")
        self.assertIn("atomic_write_text", sm)
        self.assertRegex(sm, r"from kb_io import .*atomic_write_text|import atomic_write_text")
        self.assertNotIn("def _atomic_write\b.*\n.*text =", sm,
                         "_atomic_write 不应再保留第二份同构实现")


# ── 79~82：验收器必须验收「你要它验收的那个库」────────────────
