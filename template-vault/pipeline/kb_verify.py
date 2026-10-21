#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_verify.py — 一行验收：五项全绿检查（可定期跑 / 可进 CI）。

把散落在五个脚本里的验收口径收成一条命令，输出**统一的 PASS/FAIL**，
并有明确退出码（0=全绿，1=有失败），方便 cron/CI 卡点：

  ① 契约     kb schema check          → 字段/枚举/溯源 全符合 kb-schema.json
  ② 校验     kb validate              → 0 硬错误（软告警单独报）
  ③ 巡检     kb healthcheck summary   → 9 项阈值全过
  ④ 探针     scripts/verify_curate.py → 结晶链路断言（P0–P3）
  ⑤ 单测     unittest discover        → tests/ 全过
  （可选 --with-index 加第 ⑥ 项：检索质量 canon@K/top1 不低于基线）

为什么需要：
  · 五项口径分散，改完代码要手动跑五个命令，容易漏（本轮就漏过：改了 API 语义导致单测红）
  · 每项的输出格式都不同，人眼判断"是否全绿"很累，也难被自动化消费
  · 验收必须**可重复**——否则"五项全绿"只是某一刻的运气

用法:
  python3 pipeline/kb_verify.py [--root R] [--json] [--with-index] [--timeout S]
  # 退出码: 0=全绿 · 1=有 FAIL
"""
import argparse, json, re, subprocess, sys, time
from pathlib import Path
from typing import Any, Dict, List, Optional

from kb_io import ROOT_DEFAULT

CHECKS = [
    # (键, 显示名, argv 模板, 结果解析器)
    ("schema", "契约合规", ["kb", "schema", "check"], "schema"),
    ("validate", "笔记校验", ["kb", "validate"], "validate"),
    ("healthcheck", "巡检阈值", ["kb", "healthcheck", "summary"], "healthcheck"),
    ("probe", "结晶探针", [sys.executable, "scripts/verify_curate.py"], "probe"),
    # 单元测试的 `tests/` 在**源码仓库**（活库不分发测试），所以这一项走"自动定位"：
    #   先看本仓库有没有 tests/，没有就去常见的源码布局找。
    ("unittest", "单元测试", None, "unittest"),
]


def _find_tests_dir(root: Path) -> Optional[Path]:
    """定位 tests/ 目录：原地 → 兄弟源码仓库布局 → 常见路径。"""
    cands = [root / "tests", root / "template-vault" / "tests",
             root.parent / "kb-kit-pure" / "tests", root.parent / "workspace" / "kb-kit-pure" / "tests"]
    for c in cands:
        if (c / "test_reachability.py").exists() or any(c.glob("test_*.py")):
            return c
    return None


def _resolve_argv(argv: List[str], root: Path) -> Optional[List[str]]:
    """把检查项参数解析为**目标库自己的**启动器 + 显式 `--root`。

    为什么必须这样：`kb` 是 PATH 上的符号链接（可能指向另一个库/另一个安装），
    而这些子命令默认从 `KB_ROOT` 或「模块所在目录」推断 vault，**不认 `--root` 的父进程**。
    实测后果：`kb verify --root <模板库>` 里的 schema/validate/healthcheck 三项
    实际测量的是 `/home/mushan/kb-kit-pure`（现役库）—— 也就是**验收了另一个知识库**，
    数字、结论、退出码全都属于别人。验收工具绝不能这样。
    """
    if not argv:
        return None
    if argv[0] == "kb":
        launcher = root / "pipeline" / "kb_launcher.py"
        if not launcher.exists():
            return None            # 目标不是 kb-kit 库（没有 pipeline/）
        return [sys.executable, str(launcher), *argv[1:], "--root", str(root)]
    if argv[0] == sys.executable and len(argv) > 1 and argv[1].startswith("scripts/"):
        return [argv[0], str(root / argv[1]), *argv[2:], "--root", str(root)]
    return list(argv)


def _run(argv: List[str], cwd: Path, timeout: int) -> Dict[str, Any]:
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return {"code": r.returncode, "out": r.stdout or "", "err": r.stderr or "",
                "secs": round(time.time() - t0, 1)}
    except FileNotFoundError as e:
        return {"code": 127, "out": "", "err": f"命令不存在: {e}", "secs": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        return {"code": 124, "out": "", "err": f"超时（>{timeout}s）", "secs": round(time.time() - t0, 1)}


def _parse_schema(res: Dict[str, Any], limit: int = 4) -> Dict[str, Any]:
    """契约：kb schema check 的退出码 + '✅ 全部符合契约' 文本。"""
    ok = res["code"] == 0 and "✅" in res["out"]
    detail = "全部符合契约" if ok else ""
    if not ok:
        m = re.findall(r"发现\s*(\d+)\s*处偏差", res["out"])
        detail = f"发现 {m[0] if m else '?'} 处偏差"
    return {"pass": ok, "detail": detail}


def _parse_validate(res: Dict[str, Any]) -> Dict[str, Any]:
    """校验：退出码 0 且硬错误为 0。"""
    hard = soft = None
    m = re.search(r"硬错误\s*(\d+)", res["out"])
    if m:
        hard = int(m.group(1))
    m2 = re.search(r"软告警\s*(\d+)", res["out"])
    if m2:
        soft = int(m2.group(1))
    ok = res["code"] == 0 and (hard == 0 if hard is not None else True)
    return {"pass": bool(ok), "detail": f"硬错误 {hard} · 软告警 {soft}"}


def _parse_healthcheck(res: Dict[str, Any]) -> Dict[str, Any]:
    """巡检：summary 是 JSON，逐项看 pass。

    ⚠️ 把「**回归**」和「**卫生 backlog**」分开：
      · 回归（代码/内容坏了，今天没坏明天不该冒出来）：deadlinks、frontmatter、
        empty、discipline、tags、freshness —— **硬失败**。
      · 卫生 backlog（历史遗留的待办，可以合法地长期存在）：skeletons（空壳页）、
        overlong（超长笔记）、orphans（无链笔记）—— **只告警，不判 fail**。

    为什么必须分开：`kb-verify-daily` 是每日**验收/回归闸门**。把它和卫生 backlog 混在一起，
    只要 backlog 没清（骨架/超长/孤儿清不完），闸门就天天红 → 连续失败 10 次后被
    openclaw 自动 `disabled (10x)` → **从此没有任何每日验收**（实测 2026-10-16 就是这样：
    verify 任务已停 5 天）。回归检测的价值远高于"天天提醒同一个 backlog"。
    """
    HYGIENE = {"skeletons", "overlong", "orphans"}
    try:
        d = json.loads(res["out"])
        checks = d.get("checks") or {}
    except json.JSONDecodeError:
        return {"pass": False, "detail": "输出非 JSON（healthcheck 异常）"}
    regression = {k: v.get("count") for k, v in checks.items()
                  if not v.get("pass") and k not in HYGIENE}
    backlog = {k: v.get("count") for k, v in checks.items()
               if not v.get("pass") and k in HYGIENE}
    # 注意：**不要**看 res["code"] —— healthcheck 自己遇到卫生 backlog 也会退出码 1，
    # 那样又把"回归"和"backlog"混回去了。这里只看解析出来的 checks JSON 里有没有"回归"项。
    pass_ = not regression
    detail = (f"{len(checks)} 项全过" if (not regression and not backlog)
              else (f"未过: {regression}" if regression
                    else f"⚠️ 仅卫生 backlog: {backlog}（不算失败）"))
    return {"pass": bool(pass_), "detail": detail, "backlog": backlog}


def _parse_probe(res: Dict[str, Any]) -> Dict[str, Any]:
    """探针：'结果：PASS n · WARN n · FAIL n'。"""
    m = re.search(r"PASS\s*(\d+)\s*·\s*WARN\s*(\d+)\s*·\s*FAIL\s*(\d+)", res["out"])
    if not m:
        return {"pass": False, "detail": "未解析到探针结果"}
    p, w, f = (int(x) for x in m.groups())
    return {"pass": (f == 0 and res["code"] == 0), "detail": f"PASS {p} · WARN {w} · FAIL {f}"}


def _parse_unittest(res: Dict[str, Any]) -> Dict[str, Any]:
    txt = res["out"] + res["err"]
    m = re.search(r"Ran\s+(\d+)\s+tests?", txt)
    n = m.group(1) if m else "?"
    ok = res["code"] == 0 and bool(re.search(r"^OK", txt, re.M))
    fails = re.findall(r"^(FAIL|ERROR):\s*(\S+)", txt, re.M)
    return {"pass": ok, "detail": f"{n} 个测试通过" if ok else f"失败 {[x[1] for x in fails][:3]}"}


PARSERS = {"schema": _parse_schema, "validate": _parse_validate,
           "healthcheck": _parse_healthcheck, "probe": _parse_probe,
           "unittest": _parse_unittest}


def main() -> int:
    ap = argparse.ArgumentParser(description="一行验收：五项全绿检查")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--with-index", action="store_true", help="额外跑检索质量评测（慢，需索引）")
    ap.add_argument("--timeout", type=int, default=600, help="每项超时秒数")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：缺 tests/ 也算失败（源码仓库/CI 用）。"
                         "默认宽松：活库不分发 tests/，该项记为「跳过」而非失败 —— "
                         "否则任何部署出来的库都必然 FAIL，这条「一行验收」就失去意义")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    results = []
    if not args.as_json:
        print(f"🔎 kb-kit 验收（全绿即通过）· root={root}\n")
    for key, name, argv, parser in CHECKS:
        if argv is None:                       # unittest：先定位 tests/
            td = _find_tests_dir(root)
            if td is None:
                skipped = not args.strict
                results.append({"key": key, "name": name,
                                "pass": False if args.strict else None,
                                "skipped": skipped, "secs": 0.0,
                                "code": 0 if skipped else 1,
                                "detail": "未找到 tests/ 目录（活库不分发测试）"
                                          + ("" if skipped else "；--strict 下计为失败")})
                if not args.as_json:
                    mark = "⏭️" if skipped else "❌"
                    print(f"  {mark} {name:8} {results[-1]['detail']}")
                continue
            argv = [sys.executable, "-m", "unittest", "discover", "-s", str(td)]
        else:
            argv = _resolve_argv(list(argv), root)
            if argv is None:
                results.append({"key": key, "name": name, "pass": False, "secs": 0.0, "code": 1,
                                "detail": f"{root} 不是 kb-kit 库（缺 pipeline/kb_launcher.py）"})
                if not args.as_json:
                    print(f"  ❌ {name:8} {root} 不是 kb-kit 库（缺 pipeline/kb_launcher.py）")
                continue
        res = _run(argv, root, args.timeout)
        parsed = PARSERS[parser](res)
        parsed.update({"key": key, "name": name, "secs": res["secs"], "code": res["code"]})
        results.append(parsed)
        if not args.as_json:
            mark = "✅" if parsed["pass"] else "❌"
            print(f"  {mark} {name:8} {parsed['detail']:<40} {res['secs']:>5.1f}s")
            if not parsed["pass"]:
                tail = (res["err"] or res["out"]).strip().splitlines()
                for ln in tail[-3:]:
                    print(f"       ↳ {ln[:110]}")

    if args.with_index:
        res = _run(_resolve_argv([sys.executable, "scripts/eval_retrieval.py",
                                  "--top", "10", "--json"], root), root, args.timeout * 3)
        canon = None
        try:
            canon = json.loads(res["out"]).get("now", {}).get("canon_share")
        except (json.JSONDecodeError, AttributeError):
            pass
        ok = canon is not None and canon >= 0.85
        results.append({"key": "retrieval", "name": "检索质量", "pass": ok,
                        "detail": f"canon@K {canon:.1%}（阈值 ≥85%）" if canon else "评测失败",
                        "secs": res["secs"], "code": res["code"]})
        if not args.as_json:
            print(f"  {'✅' if ok else '❌'} {'检索质量':8} {results[-1]['detail']:<40} {res['secs']:>5.1f}s")

    # 注意用 `is False`：跳过项的 pass 是 None，不能被 `not r["pass"]` 当成失败
    n_fail = sum(1 for r in results if r["pass"] is False)
    n_skip = sum(1 for r in results if r.get("skipped"))
    verdict = "PASS" if n_fail == 0 else "FAIL"
    if args.as_json:
        print(json.dumps({"verdict": verdict, "n_fail": n_fail, "n_skip": n_skip,
                          "checks": results}, ensure_ascii=False, indent=2))
    else:
        print()
        skip_note = f" · 跳过 {n_skip} 项（活库不分发 tests/）" if n_skip else ""
        print(f"  {'✅ 全部通过' if n_fail == 0 else f'❌ {n_fail} 项未通过'}  "
              f"（{len(results)} 项 · 合计 {sum(r['secs'] for r in results):.0f}s）{skip_note}")
        if n_fail:
            print("  失败项: " + ", ".join(r["name"] for r in results if r["pass"] is False))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
