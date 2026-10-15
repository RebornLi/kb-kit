#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ladder.py — L0–L5 反馈阶梯的**统一入口**。

## 为什么需要这个文件

L0–L5 六个模块合计 **6743 行（占 pipeline 的 25.9%）**，但在 2026-10-16 之前：

  · **没有任何插件**（`ls pipeline/plugins/` 里找不到 kb_l4/kb_l5/kb_retriage/
    kb_selfweight/kb_meta/kb_adaptretrieve）→ `kb l4`、`kb adaptretrieve` 全是
    「❓ 未知命令」，**用户根本敲不出来**；
  · 因此也从未被调度（`scripts/*.sh` 里没有任何一条调用）；
  · 生产库实测：L0–L5 的全部状态/账本文件**都不存在** —— 26% 的代码、0% 的实际动作。

而与此同时 `feedback_state.json` 已积累 **2039 个查询会话 / 1407 条命中记录**，
其中 **2039 次查询只有 382 个不同问题（5.3× 重复）**，最高频的问题问了 73 次 ——
L1 缺口检测的前提信号（"同一 query 短期再问 = 上次没答上"）**充足到溢出**。

所以本文件是那 26% 代码的**出口**：给它一个可发现、可解释、可调度的入口。

## 安全边界（重要）

`propose` **只产出提案**，不修改任何笔记、不改检索行为。落地的 `apply` 必须人工确认 ——
这不是保守，是有实测依据的：L1 契约正确时黄金集 rec@10 98.3%→100%，但**契约指向无关笔记时
top1 90.0%→76.7%、MRR 0.950→0.883**（见 rag.ADJUST_MIN_SIM 处的实测说明）。
坏契约代价远大于"少一条好契约"，所以自动化的边界停在"产出提案"。

用法：
    python3 pipeline/ladder.py status            # 只读：L0–L5 各级状态
    python3 pipeline/ladder.py propose           # 产出提案（不改笔记）
    python3 pipeline/ladder.py run               # propose + 简报（cron 用）
    python3 pipeline/ladder.py apply --level L1 --id 3
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# (代号, 显示名, 模块名, 一句话职责)
LEVELS: List[Tuple[str, str, str, str]] = [
    ("L0", "再分诊", "kb_retriage", "入库分类走错 → 提议重新分诊"),
    ("L1", "查询即反馈", "kb_adaptretrieve", "反复再问同一问题 → 提议检索修正"),
    ("L2", "自权重", "kb_selfweight", "命中/未命中信号 → 提议调整权重"),
    ("L3", "元调参", "kb_meta", "长期结果 → 提议调整旋钮"),
    ("L4", "宪法裁决", "kb_l4", "闸门与信任：连续判错则冻结该级"),
    ("L5", "元策略", "kb_l5", "对上面几级自身的权重做训练"),
]


def _mod(name: str):
    """惰性导入：某一级坏了不该拖垮整个阶梯。"""
    try:
        return __import__(name)
    except Exception as e:      # noqa: BLE001
        return e


def _status_one(name: str, root: Path) -> Dict[str, Any]:
    m = _mod(name)
    if isinstance(m, Exception):
        return {"error": f"导入失败：{type(m).__name__}"}
    try:
        return dict(m.status(root) or {})
    except Exception as e:      # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}


def _summary_line(code: str, label: str, st: Dict[str, Any]) -> str:
    """把各级 status 压成一行可读摘要（键名各模块不同，逐个兼容）。"""
    if "error" in st:
        return f"{code} {label:6} ⚠️ {st['error']}"
    # ⚠️ `frozen` 是 {rung: bool} 字典 —— 即使全为 False 也是真值，
    # 直接 `if frozen` 会把"都没冻"误报成"已冻结"（本文件初版就踩了这个坑）。
    frozen = st.get("frozen")
    if isinstance(frozen, dict) and any(frozen.values()):
        who = [r for r, v in frozen.items() if v]
        return f"{code} {label:6} 🧊 已被 L4 冻结（{','.join(who)}，连续判错，需人工解冻）"
    if frozen is True:      # 兼容将来可能出现的标量写法
        return f"{code} {label:6} 🧊 已被 L4 冻结（连续判错，需人工解冻）"
    # 各级 status 的"待办数"键名不同（count / docket_count / pending_proposals /
    # 直接给列表 / n_pending），逐个兼容并归一化成整数。
    n = st.get("count", st.get("docket_count", st.get("pending_proposals")))
    if n is None:
        n = st.get("n_pending")
    if isinstance(n, list):
        n = len(n)
    extras = []
    if st.get("grounded") is False:
        extras.append("未接地")
    if st.get("insufficient"):
        extras.append("样本不足")
    if st.get("n_cycles"):
        extras.append(f"cycles={st['n_cycles']}")
    tail = ("  " + " · ".join(extras)) if extras else ""
    return f"{code} {label:6} 提案/待办 {n if n is not None else '—'}{tail}"


def _jsonable(o):
    """把任意 status 结构变成可 JSON 序列化的形态。

    为什么需要：各级 status 里存在**元组键**的字典（如 L2/L5 的权重表），
    `json.dumps(default=str)` 只兜值、不兜键 —— 会抛
    `TypeError: keys must be str...`（本文件初版就踩了这个坑）。
    """
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


def cmd_status(root: Path, as_json: bool = False) -> int:
    out = {}
    for code, label, name, _duty in LEVELS:
        out[code] = _status_one(name, root)
    if as_json:
        print(json.dumps(_jsonable(out), ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"🪜 L0–L5 反馈阶梯 · {root}")
    print()
    for code, label, name, duty in LEVELS:
        st = out[code]
        print("  " + _summary_line(code, label, st))
        print(f"        {duty}")
    def _n(st):
        v = st.get("count", st.get("docket_count", st.get("pending_proposals"))) \
            or st.get("n_pending") or 0
        return len(v) if isinstance(v, list) else int(v or 0)
    total = sum(_n(out[c]) for c in out)
    print()
    print(f"  待人工裁决提案合计：{total}")
    print("  提示：`kb ladder propose` 产出提案；`kb ladder apply --level L1 --id N` 确认落地")
    return 0


def cmd_propose(root: Path, quiet: bool = False) -> Dict[str, Any]:
    """跑各级的 propose。**只写提案文件，不碰笔记。**"""
    res: Dict[str, Any] = {}
    for code, label, name, _duty in LEVELS:
        m = _mod(name)
        if isinstance(m, Exception):
            res[code] = {"ok": False, "err": f"导入失败：{type(m).__name__}"}
            continue
        fn = getattr(m, "propose", None) or getattr(m, "docket", None)
        if fn is None:
            res[code] = {"ok": True, "n": None, "note": "该级无 propose（由 apply/judge 驱动）"}
            continue
        t0 = time.time()
        try:
            fn(root)
            st = _status_one(name, root)
            res[code] = {"ok": True, "n": st.get("count", st.get("docket_count")),
                         "secs": round(time.time() - t0, 1)}
        except Exception as e:      # noqa: BLE001  单级失败不影响其它级
            res[code] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:100]}"}
        if not quiet:
            r = res[code]
            mark = "✅" if r.get("ok") else "❌"
            detail = f"提案 {r.get('n')}" if r.get("ok") else r.get("err")
            print(f"  {mark} {code} {label:6} {detail}")
    return res


def cmd_apply(root: Path, level: str, ids: Optional[List[str]], force: bool) -> int:
    """人工确认后落地。**必须显式指定级别**（避免误伤）。"""
    code = level.upper()
    hit = [l for l in LEVELS if l[0] == code]
    if not hit:
        print(f"✗ 未知级别 {level}（可选 {', '.join(l[0] for l in LEVELS)}）")
        return 2
    _, label, name, _ = hit[0]
    m = _mod(name)
    if isinstance(m, Exception) or not hasattr(m, "apply"):
        print(f"✗ {code} 不可 apply")
        return 2
    if not ids and not force:
        print(f"⚠️ {code} {label}：未指定 --id。"
              f"先看 `kb ladder status`，确认后再 --id N（或 --all --force 明确全量）")
        return 1
    try:
        m.apply(root, ids=ids, force=force)
        print(f"✅ {code} {label} 已落地：{ids or '全部'}")
    except TypeError:
        m.apply(root, ids=ids)          # kb_l5.apply 无 force 参数
        print(f"✅ {code} {label} 已落地：{ids or '全部'}")
    return 0


def cmd_adjudicate(root: Path, levels: List[str], dry: bool, limit) -> int:
    """转发给 adjudicate.py（本地模型自主裁决）。"""
    import adjudicate
    return adjudicate.cmd_run(root, levels, dry, limit)


def cmd_verdicts(root: Path) -> int:
    import adjudicate
    return adjudicate.cmd_log(root)


def cmd_run(root: Path) -> int:
    """cron 入口：产出提案 + 打一份可投递的简报。退出码始终 0（与其它 cron 脚本同约定）。"""
    print(f"===== 反馈阶梯 {time.strftime('%F %H:%M')} =====")
    print(f"库: {root}")
    print()
    print("【① 各级提案】")
    res = cmd_propose(root)
    print()
    print("【② 阶梯状态】")
    for code, label, name, _duty in LEVELS:
        st = _status_one(name, root)
        print("  " + _summary_line(code, label, st))
    print()
    def _n(st):
        v = st.get("count", st.get("docket_count", st.get("pending_proposals"))) \
            or st.get("n_pending") or 0
        return len(v) if isinstance(v, list) else int(v or 0)
    pending = sum(_n(_status_one(n, root)) for _c, _l, n, _d in LEVELS)
    print("【③ 待人工裁决】")
    if pending:
        print(f"  {pending} 条提案在队列里 —— 人工确认后才落地（`kb ladder status` 看详情）")
    else:
        print("  队列为空")
    print()
    ok = all(r.get("ok") for r in res.values())
    print(f"【结论】{'✅ 阶梯正常产出' if ok else '⚠️ 部分级别产出失败（见上）'}")
    print("===== 报告结束 =====")
    return 0        # 与 kb-daily-supervisor.sh / kb-curate.sh 同约定：让简报一定投递出去


def main() -> int:
    ap = argparse.ArgumentParser(description="L0–L5 反馈阶梯统一入口")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["status", "propose", "run", "apply", "adjudicate", "verdicts"])
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or ".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--level", default="", help="apply 用：L0..L5")
    ap.add_argument("--id", action="append", default=[], help="apply 用：提案 id（可重复）")
    ap.add_argument("--all", action="store_true", help="apply 用：全部")
    ap.add_argument("--force", action="store_true", help="apply 用：与 --all 组合确认全量")
    ap.add_argument("--dry-run", action="store_true", help="adjudicate 用：只预演")
    ap.add_argument("--max", type=int, default=None, help="adjudicate 用：单级上限")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if a.cmd == "status":
        return cmd_status(root, a.json)
    if a.cmd == "propose":
        cmd_propose(root)
        return 0
    if a.cmd == "run":
        return cmd_run(root)
    if a.cmd == "adjudicate":
        lv = [x.upper() for x in a.level_list] if hasattr(a, "level_list") else (["L1", "L2", "L3"] if a.all else ["L1"])
        return cmd_adjudicate(root, lv, a.dry_run, a.max)
    if a.cmd == "verdicts":
        return cmd_verdicts(root)
    return cmd_apply(root, a.level, (a.id or None) if not a.all else None, a.force)


if __name__ == "__main__":
    sys.exit(main())
