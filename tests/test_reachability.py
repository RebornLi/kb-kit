#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reachability.py — pipeline 模块可达性守卫（P3-1）。

架构约束：pipeline/*.py 要么被接线（插件封装 / COMPAT_MAP / 脚本调用 / 被可达模块
import），要么在 INTERNAL_MODULES 里被显式声明为“内部研究层/库”。本测试防止再出现
“写了却没入口、也没人声明”的静默死代码。

运行：
  python3 -m unittest discover -s tests
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
PLUGINS = PIPELINE / "plugins"
SCRIPTS = REPO / "template-vault" / "scripts"
# P2-2 起，研究层整体住在 `pipeline/_research/` 子包里 —— 下面这个目录名就是
# "这里是内部实验代码" 的结构化宣告，不必再逐模块手写清单。
RESEARCH_DIR = "_research"

# 例外：**不在** `_research/` 里、但仍是内部模块的（必须写明理由）。
# 新增条目请先问「能不能干脆把它搬进 _research/」—— 不能的原因通常就是
# 外部按路径调用，那正是这个清单存在的意义。
INTERNAL_MODULES: dict[str, str] = {
    "kb_worker.py": "常驻查询 worker：kb-context-dsh/src/worker.js 按绝对路径 "
                    "`${kbRoot}/pipeline/kb_worker.py` spawn（移动路径会打断外部进程），"
                    "非 kb CLI 入口",
}

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def research_modules() -> set:
    """`_research/` 下的模块名（即结构化推导出的 internal 集合）。"""
    d = PIPELINE / RESEARCH_DIR
    if not d.is_dir():
        return set()
    return {p.name for p in d.glob("*.py")
            if not p.name.startswith("__")}


def internal_modules() -> set:
    """全部 internal 模块 = `_research/` 下全部 + 手写例外清单。"""
    return research_modules() | set(INTERNAL_MODULES)


def pipeline_modules() -> set:
    return {p.name for p in PIPELINE.glob("*.py") if not p.name.startswith("__")}


def plugin_scripts() -> set:
    """插件层封装的引擎脚本名。

    两种接线形态都要认：
      1. 经典 per-plugin 壳：    SCRIPT_NAME = "rag.py"
      2. 域聚合插件（P2-1 起）： _ROUTES = {"action": ("kb_audit.py", "scan", True), ...}
                                 元组第一元素即脚本名

    研究层垫片也要认：`pipeline/kb_l4.py` 之类 re-export 了 `_research/kb_l4.py`，
    扫到的是原路径名，故无需特殊处理。
    """
    out = set()
    for p in PLUGINS.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r'SCRIPT_NAME\s*=\s*"([^"]+)"', text):
            out.add(m.group(1))
        for m in re.finditer(r'\(\s*"([A-Za-z0-9_.-]+\.py)"\s*,', text):
            out.add(m.group(1))
    return out


def script_refs() -> set:
    out = set()
    # cron/辅助脚本
    for p in SCRIPTS.glob("*.sh"):
        for m in re.finditer(r"([A-Za-z0-9_-]+\.py)", p.read_text(encoding="utf-8")):
            out.add(m.group(1))
    # 启动器（kb / kb.cmd）本身也是一条接线根
    for name in ("kb", "kb.cmd"):
        f = REPO / "template-vault" / name
        if f.exists():
            for m in re.finditer(r"([A-Za-z0-9_-]+\.py)", f.read_text(encoding="utf-8")):
                out.add(m.group(1))
    return out


def imports_of(module: str) -> set:
    try:
        text = (PIPELINE / module).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {m.group(1) + ".py" for m in _IMPORT_RE.finditer(text)}


def reachable() -> set:
    """从接线根（插件脚本 + 脚本调用 + 传递 import）做 BFS。"""
    roots = plugin_scripts() | script_refs()
    seen, stack = set(), list(roots)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        stack.extend(d for d in imports_of(m) if d not in seen)
    return seen


class ReachabilityTest(unittest.TestCase):
    def test_every_module_wired_or_internal(self):
        """三种状态都可以：已接线 / 在 _research/ 里 / 在手写例外清单里。

        P2-2 起 internal 集合由目录结构推导（`_research/`），不再逐模块手写 ——
        手写清单历史上改一个漏一个，18 条里 2 条的理由已经过期。
        """
        unknown = (pipeline_modules() - reachable()
                   - internal_modules()
                   - research_modules())
        self.assertEqual(
            unknown, set(),
            f"未接线且未声明 internal 的模块（应接线，或搬进 {RESEARCH_DIR}/）: {sorted(unknown)}")

    def test_internal_list_not_stale(self):
        """手写例外清单不得腐化：已可达的、已不存在的都要删。"""
        mods, reach = pipeline_modules(), reachable()
        stale = [m for m in INTERNAL_MODULES if m in reach or m not in mods]
        self.assertEqual(stale, [], f"手写 INTERNAL 中已可达/不存在的条目应移除: {stale}")

    def test_internal_is_not_still_a_handwritten_pile(self):
        """防回退：手写例外清单必须保持**小**（历史上 18 条，现在只该剩真例外）。

        若有人把 _research/ 里的模块又抄回这个清单，说明没理解
        "internal 由目录结构推导" —— 这条测试就是为了拦住那种回退。
        """
        self.assertLessEqual(
            len(INTERNAL_MODULES), 3,
            f"INTERNAL_MODULES 已涨到 {len(INTERNAL_MODULES)} 条："
            f"研究层模块应住进 {RESEARCH_DIR}/（自动算 internal），"
            "这里只留外部按路径调用之类的真例外")

    def test_research_dir_has_no_production_reverse_import(self):
        """边界守卫：研究层内部可以 import 生产公共层，反之不行。

        允许：`_research/*.py` → kb_io / kb_text / state_manager / rag …
        禁止：生产模块 → `_research.*` 的**静态** import
        （ladder / adjudicate 的动态 `__import__` 是唯一许可的入口形态）。

        实现注意（踩过）：不能写 `if text.lstrip().startswith("#"): continue`
        来跳过"纯注释文件" —— 生产模块几乎都带 `#!/usr/bin/env python3` shebang，
        那样会把 80/81 个文件全跳过，守卫静默失效。这里只跳过 `__` 开头的
        包初始化文件，且用"去掉 shebang 与注释行后的代码"做匹配。

        另一个坑：`pipeline/` 根下的 `<name>.py` 是**兼容垫片**，本体就写着
        `from _research.<name> import *`。它们是 P2-2 有意保留的桥（让
        ladder/adjudicate 的 `__import__("kb_l4")` 与旧路径调用继续工作），
        不是边界破坏。故用「文件是否是垫片」排除：垫片的判定 = 存在同名
        `_research/<name>.py` 且本文件含 `from _research.<name> import`。
        """
        violations = []
        for p in sorted(PIPELINE.glob("*.py")):
            if p.name.startswith("__"):
                continue
            twin = PIPELINE / RESEARCH_DIR / p.name
            lines = [l for l in p.read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#")]
            code = "\n".join(lines)
            is_shim = (twin.exists()
                       and f"from {RESEARCH_DIR}.{p.stem} import" in code)
            if is_shim:
                continue          # 兼容垫片是有意的桥，不是违规
            for m in re.finditer(r'^\s*from\s+_research[.\s]|^\s*import\s+_research\b',
                                 code, re.M):
                line_no = code[:m.start()].count("\n") + 1
                violations.append(f"{p.name}:~{line_no}")
                break
        self.assertEqual(
            violations, [],
            f"生产模块静态 import 了研究层（边界破坏，应经 ladder/adjudicate 动态入口）: "
            f"{violations}")

    def test_shims_are_only_re_exports(self):
        """垫片必须保持"薄"：只 re-export + 转发 CLI，不长出独立实现。

        否则会出现"改了垫片以为改了实现"的静默分叉 —— 与 §九 ① 删
        kb_common shim 之前踩过的坑同源。
        判定：把文档字符串与**所有 import 语句**（含跨行的 `from x import (`
        括号块）整体剥掉后，剩下的不应有实质代码 —— 只允许
        `if __name__ == "__main__":` 那几行 CLI 转发。
        """
        fat = []
        for p in sorted(PIPELINE.glob("*.py")):
            twin = PIPELINE / RESEARCH_DIR / p.name
            if not twin.exists():
                continue
            body = p.read_text(encoding="utf-8")
            if f"from {RESEARCH_DIR}.{p.stem} import" not in body:
                continue
            # 剥文档字符串
            s = re.sub(r'"""(?:.|\n)*?"""', '', body)
            # 剥 import：单行 + 跨行括号块，反复直到稳定（嵌套 import 块）
            prev = None
            while prev != s:
                prev = s
                s = re.sub(r'^\s*from\s+[\w.]+\s+import\s*\((?:[^)]*)\)',
                           '', s, flags=re.M)
                s = re.sub(r'^\s*(?:from\s+[\w.]+\s+import\s+[^\n(]+|import\s+[\w.]+(?:\s+as\s+\w+)?)\s*$',
                           '', s, flags=re.M)
            leftovers = [
                l for l in s.splitlines()
                if l.strip() and not l.lstrip().startswith("#")
                # CLI 转发那几行是允许的
                and not re.match(r'^\s*(if __name__|_sys\.exit|import sys as|from _research)', l)
            ]
            if leftovers:
                fat.append(f"{p.name}: {len(leftovers)} 行非 re-export 代码 "
                           f"（例: {leftovers[0].strip()[:50]}）")
        self.assertEqual(fat, [], f"垫片长胖了（应只 re-export）: {fat}")


if __name__ == "__main__":
    unittest.main()
