#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_p1_audit_2026_10.py — 2026-10-22 审计发现的五项 P1 回归测试。

## 这五条的共同点

都不是算法错，而是**"看起来在工作，实际没在工作"**——
校验项必然误报、定时任务验错库、退出码恒 0、路径写死。
单测测不出，因为它们各自调用时都"正常返回"。

P1-1 `scripts/verify.py` 硬编码索引文件名 `df_idf.json`，
     而引擎索引从 v8 起是 `df_idf.pkl` → 索引存在也永远报"不存在"
P1-2 6 个 cron 脚本把默认库路径写死成开发者的私人路径
     `/home/mushan/kb-kit-pure`（别人装了就跑到别人库上）
P1-3 `kb-verify-cron.sh` 三个缺陷：`cd $VAULT && kb verify` 会被全局
     `KB_ROOT` 劫持（验的是别的库）；`kb` 未必在 cron 的 PATH 里；
     `timeout` 的 124 与业务失败的 1 混为一谈
P1-4 `kb query` 在索引缺失/陈旧时返回 0，调用方无法区分
     "查到了"与"拿的是过期答案"
P1-5 代码注释引用两个从未存在于 git 历史的 `docs/*.md` 死指针

运行：python3 -m unittest discover -s tests
"""

import json
import os
import pickle
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


def _run(cmd, cwd=None, timeout=240, env=None):
    e = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _make_vault(tmp: Path) -> Path:
    """铺一个最小可用库：pipeline + reference + 几篇笔记 + 建索引。"""
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
    # kb 启动器：真实安装必有，kb-verify-cron.sh 靠它定位目标库。
    # 少了它，测试在极简 PATH 下会走到"找不到 kb"分支（脚本行为正确，
    # 但测的就不是我们想测的那条路径了）。
    shutil.copy2(TEMPLATE / "kb", vault / "kb")
    os.chmod(vault / "kb", 0o755)
    return vault


class P11IndexFilenameTest(unittest.TestCase):
    """P1-1：verify.py 必须能认出**现行**索引格式。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "scripts"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "verify_mod", REPO / "scripts" / "verify.py")
        cls.verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verify)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-p11-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_hardcoded_json_filename(self):
        """源码守卫：不许只认 df_idf.json 而不认现行的 .pkl。

        判据不是"字符串出现没有"（旧格式候选名合法出现），而是
        **pkl 必须排在 json 之前被探测** —— 顺序反了就等于仍以 json 为准。
        """
        import ast as _ast
        tree = _ast.parse((REPO / "scripts" / "verify.py").read_text(encoding="utf-8"))
        lits = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                lits.append(node.value)
        # 只认字符串字面量：docstring/注释里的说明不算候选顺序
        names = [v for v in lits if v in ("df_idf.pkl", "df_idf.json")]
        self.assertIn("df_idf.pkl", names, "verify.py 的代码里不认 df_idf.pkl")
        self.assertLess(names.index("df_idf.pkl"), names.index("df_idf.json"),
                        "verify.py 仍以 df_idf.json 为第一候选（v8 起索引是 .pkl）")

    def test_pkl_index_recognized(self):
        """现行 .pkl 索引必须判为存在。"""
        vault = _make_vault(self.tmp)
        idx = vault / "vector index" / "df_idf.pkl"
        self.assertTrue(idx.exists(), "前置断言：索引应是 .pkl")
        item = self.verify.check_vector_index(vault)
        self.assertTrue(item.passed,
                        f".pkl 索引被判失败：{item.detail}")

    def test_json_index_recognized(self):
        """旧 .json 索引（v7 及更早）也不能误报。"""
        vault = _make_vault(self.tmp)
        pkl = vault / "vector index" / "df_idf.pkl"
        data = pickle.loads(pkl.read_bytes())
        pkl.unlink()
        (vault / "vector index" / "df_idf.json").write_text(
            json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        item = self.verify.check_vector_index(vault)
        self.assertTrue(item.passed, f".json 索引被判失败：{item.detail}")

    def test_missing_index_reports_failure(self):
        """真的没有索引时必须失败（不能为了修误报而变成永不失败）。"""
        vault = _make_vault(self.tmp)
        for name in ("df_idf.pkl", "df_idf.json"):
            (vault / "vector index" / name).unlink(missing_ok=True)
        item = self.verify.check_vector_index(vault)
        self.assertFalse(item.passed)
        self.assertIn("不存在", item.detail)


class P12NoPrivatePathsTest(unittest.TestCase):
    """P1-2：定时脚本不得把默认库写死成开发者私人路径。"""

    def test_no_hardcoded_home_path(self):
        offenders = []
        for sh in sorted(SCRIPTS.glob("*.sh")):
            text = sh.read_text(encoding="utf-8")
            if "/home/mushan" in text:
                offenders.append(sh.name)
        self.assertEqual(offenders, [],
                         f"这些脚本仍写死私人路径：{offenders}")

    def test_vault_default_follows_self_dir(self):
        """默认库必须是 `$1 > KB_ROOT > 脚本自身位置`，三者都不给就要报错退出。

        两种合法形态：
          A. `${1:-${KB_ROOT:-$SELF_DIR/..}}`   —— 三者都落空时退回本库
          B. `${1:-${KB_ROOT:-}}` + 空值检查 exit 2 —— 明确要求调用方指定
        非法形态：把某台机器上的私人绝对路径当默认值（P1-2 的原始缺陷）。
        """
        for sh in sorted(SCRIPTS.glob("*.sh")):
            text = sh.read_text(encoding="utf-8")
            if not any(l.startswith("VAULT=") for l in text.splitlines()):
                continue
            with self.subTest(script=sh.name):
                has_self_default = ("${KB_ROOT:-$SELF_DIR/..}" in text
                                    or '${1:-$SELF_DIR/..}' in text)
                requires_explicit = ('${KB_ROOT:-}}' in text
                                     and "用法:" in text)
                self.assertTrue(
                    has_self_default or requires_explicit,
                    f"{sh.name} 的默认库既不是 SELF_DIR 推导、也未强制要求显式指定")

    def test_cron_scripts_are_executable(self):
        """cron 直接执行，没有 +x 会静默不跑。"""
        for sh in sorted(SCRIPTS.glob("*.sh")):
            with self.subTest(script=sh.name):
                self.assertTrue(os.access(sh, os.X_OK),
                                f"{sh.name} 没有可执行位")


class P13VerifyCronTest(unittest.TestCase):
    """P1-3：验收 cron 必须验的是**目标库**，且分得清失败原因。"""

    SCRIPT = SCRIPTS / "kb-verify-cron.sh"

    def test_explicit_root_overrides_kb_root(self):
        """即使全局设了 KB_ROOT，也要验 --root 指定的库。

        这是本项的核心：修复前 `cd $VAULT && kb verify` 会被 KB_ROOT 劫持，
        实测"目标库契约文件被删、脚本仍输出五项全绿"。
        """
        tmp = Path(tempfile.mkdtemp(prefix="kb-p13-"))
        try:
            vault = _make_vault(tmp)
            # 破坏目标库：删掉契约文件
            (vault / "reference" / "kb-schema.json").unlink()
            rc, out = _run(["sh", str(self.SCRIPT), str(vault)],
                           env={"KB_ROOT": str(vault.parent / "decoy")})
            self.assertNotEqual(rc, 0, f"目标库已损坏却返回 0：\n{out[-800:]}")
            self.assertIn(f"root={vault}", out, "报告未回显目标库，无法自证身份")
            self.assertIn("验收失败", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_healthy_vault_reports_ok(self):
        """健康库必须报 OK。

        这里用 `create_vault.py` **真实安装**一个库，而不是手工铺最小库：
        最小库缺 demo 内容/探针结构，`kb verify` 的巡检阈值与结晶探针会
        真实地不达标 —— 那不是脚本的缺陷，拿它断言"必须全绿"是夹具错了。
        """
        tmp = Path(tempfile.mkdtemp(prefix="kb-p13-"))
        try:
            vault = tmp / "vault"
            # --no-verify：本机若无任何 Agent，verify 的 kb-agent.json 项会失败、
            # create_vault 随之返回 1（设计如此），但那与"库是否健康"无关。
            rc_i, out_i = _run(
                [sys.executable, str(REPO / "create_vault.py"),
                 "--vault", str(vault), "--yes", "--no-global-kb",
                 "--no-verify"],
                timeout=300)
            self.assertEqual(rc_i, 0, f"夹具库安装失败：\n{out_i[-800:]}")
            rc, out = _run(["sh", str(self.SCRIPT), str(vault)], timeout=600)
            self.assertEqual(rc, 0, f"健康库未通过：\n{out[-800:]}")
            self.assertIn("全绿", out)
            self.assertIn(f"root={vault}", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_kb_launcher_fails_loudly(self):
        """库内没有 kb 启动器、PATH 里也没有 → 必须明确报错，不能假装成功。"""
        tmp = Path(tempfile.mkdtemp(prefix="kb-p13-"))
        try:
            vault = _make_vault(tmp)
            (vault / "kb").unlink()
            rc, out = _run(["sh", str(self.SCRIPT), str(vault)],
                           env={"PATH": "/usr/bin:/bin"})
            self.assertNotEqual(rc, 0, f"找不到启动器却返回 0：\n{out[-500:]}")
            self.assertIn("找不到 kb 启动器", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_vault_dir(self):
        rc, out = _run(["sh", str(self.SCRIPT), "/tmp/definitely-not-here-xyz"])
        self.assertNotEqual(rc, 0)
        self.assertIn("不存在", out)

    def test_clears_kb_root_in_child_env(self):
        """源码守卫：必须 `env -u KB_ROOT`，否则子进程仍可能读到它。"""
        src = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn("env -u KB_ROOT", src)

    def test_timeout_distinguished_from_failure(self):
        """源码守卫：124（超时）要与业务失败分开陈述。"""
        src = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"$CODE" -eq 124', src)


class P14StaleIndexTest(unittest.TestCase):
    """P1-4：索引陈旧必须可被调用方感知。"""

    @classmethod
    def setUpClass(cls):
        import kb_metautil
        cls.meta = kb_metautil

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-p14-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_index_no_false_positive(self):
        """刚建好的索引必须判为新 —— 否则 strict 模式会天天误报。"""
        vault = _make_vault(self.tmp)
        r = self.meta.index_freshness(vault)
        self.assertFalse(r["stale"], f"健康库误报陈旧：{r}")
        self.assertIn("一致", r["reason"])

    def test_new_note_detected(self):
        vault = _make_vault(self.tmp)
        (vault / "20-技术 Technology" / "new.md").write_text(
            "---\ntags: [开发]\nstatus: active\ndomain: 开发\n"
            "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.5\n"
            "kb_summary: 新笔记\n---\n\n新正文。\n", encoding="utf-8")
        r = self.meta.index_freshness(vault)
        self.assertTrue(r["stale"])
        self.assertIn("20-技术 Technology/new.md", r["missing"])

    def test_modified_note_detected(self):
        vault = _make_vault(self.tmp)
        p = vault / "20-技术 Technology" / "a.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n追加内容改变 mtime。\n",
                     encoding="utf-8")
        r = self.meta.index_freshness(vault)
        self.assertTrue(r["stale"])
        self.assertTrue(r["changed"])

    def test_reports_and_raw_not_counted_as_missing(self):
        """raw/、报告产物本就不进索引，不能算成 missing。

        这是实现时实际踩到的坑：首轮只套了 index_excluded(fm)，
        漏了 is_generated_report(rel)，导致健康库长期误报 2 篇。
        """
        vault = _make_vault(self.tmp)
        (vault / "raw").mkdir()
        # raw/ 的排除依据是 frontmatter 的 is_source，不是路径本身
        # （rag_index 只套 is_generated_report + index_excluded，不看目录名）
        (vault / "raw" / "ev.md").write_text(
            "---\nis_source: true\nstatus: active\n---\n\n原文，不该进索引。\n",
            encoding="utf-8")
        (vault / "recall_schedule.md").write_text(
            "# 运行期报告\n\n自动生成。\n", encoding="utf-8")
        r = self.meta.index_freshness(vault)
        self.assertFalse(r["stale"], f"报告/raw 被误判为待建索引：{r['missing']}")

    def test_strict_fresh_exit_code(self):
        """--strict-fresh 时陈旧必须返回 3（查询成功但答案可能过期）。"""
        vault = _make_vault(self.tmp)
        (vault / "20-技术 Technology" / "new.md").write_text(
            "---\ntags: [开发]\nstatus: active\ndomain: 开发\n"
            "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.5\n"
            "kb_summary: 新笔记\n---\n\n新正文。\n", encoding="utf-8")
        rc, out = _run([sys.executable, str(vault / "pipeline" / "rag.py"),
                        "query", "测试", "--root", str(vault),
                        "--strict-fresh", "--no-record"], timeout=180)
        self.assertEqual(rc, 3, f"strict-fresh 未返回 3：\n{out[-600:]}")
        self.assertIn("过期", out)

    def test_default_stays_zero(self):
        """默认模式必须仍返回 0 —— 大量脚本/Agent 依赖这一点。"""
        vault = _make_vault(self.tmp)
        (vault / "20-技术 Technology" / "new.md").write_text(
            "---\ntags: [开发]\nstatus: active\ndomain: 开发\n"
            "created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.5\n"
            "kb_summary: 新笔记\n---\n\n新正文。\n", encoding="utf-8")
        rc, out = _run([sys.executable, str(vault / "pipeline" / "rag.py"),
                        "query", "测试", "--root", str(vault),
                        "--no-record"], timeout=180)
        self.assertEqual(rc, 0, f"默认模式退出码变了：\n{out[-600:]}")
        self.assertIn("过期", out, "默认模式也应给出警告")

    def test_rag_cli_accepts_flag(self):
        """rag.py 必须认 --strict-fresh。"""
        src = (PIPELINE / "rag.py").read_text(encoding="utf-8")
        self.assertIn("strict_fresh", src)
        self.assertIn("--strict-fresh", src)


class P15DanglingDocsRefsTest(unittest.TestCase):
    """P1-5：注释不得引用仓内不存在的文件。"""

    def test_no_dangling_doc_pointers(self):
        """生产代码注释里不得出现指向不存在 docs/*.md 的具体文件名。"""
        dangling = []
        for py in sorted(PIPELINE.glob("*.py")) + sorted((PIPELINE / "_research").glob("*.py")):
            text = py.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if "docs/" not in line:
                    continue
                # 只查"docs/xxx.md"这类**具体文件**引用；目录豁免说明不算
                for name in ("concepts.md", "EVOLUTION-REVIEW.md",
                             "write-side-protocol.md", "CODE-SURVEY.md"):
                    if f"docs/{name}" not in line:
                        continue
                    # "原注释指向 docs/x，已不在仓内" 这类**修复说明**是合法的，
                    # 它是在记录死指针已被清除，不是新的死指针。
                    if any(w in line for w in ("原注释", "已不在仓内", "该文件已")):
                        continue
                    dangling.append(f"{py.name}:{i} → docs/{name}")
        self.assertEqual(dangling, [], f"仍引用不存在的文档：{dangling}")

    def test_docs_dir_exemption_still_documented(self):
        """反向守卫：`docs/` 目录豁免是**真实功能**，注释不能被我删掉。"""
        for name in ("validate.py", "kb-healthcheck.py", "kb_io.py"):
            src = (PIPELINE / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertIn("docs/", src,
                              f"{name} 丢了 docs/ 豁免说明")


if __name__ == "__main__":
    unittest.main(verbosity=2)
