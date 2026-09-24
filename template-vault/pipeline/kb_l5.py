#!/usr/bin/env python3
# ============================================================
# kb_l5.py —— L5 元策略（反馈阶梯 · 元策略层）
# ------------------------------------------------------------
# 使命：反馈阶梯有 L0–L4，它们各自决定"自己这步怎么走"
#   （L0 修检索表层 / L1 喂查询 / L2 加权重 / L3 调旋钮 / L4 人工裁判冻结）。
#   但"这一整架阶梯，在 *这个* vault 里到底有没有帮到它？"——没有 rung 回答。
#   L5 回答它：为 L0–L3 每个 rung 算一个「激活优先级 / 权重」∈[0,1]，权重高→
#   调度器（cron/kb）越该多跑该层。L5 站在 L4 之上，学的是"值不值"，不是"对不对"。
#
#     L0 检索表层修正（kb_retriage）← 已完成
#     L1 查询即反馈（kb_adaptretrieve）← 已完成
#     L2 自我加权（kb_selfweight）← 已完成
#     L3 元调参（kb_meta）← 已完成
#     L4 宪法（人工裁判）← 已完成
#     L5 元策略（本模块 · 由真实结果学 rung 权重）← 本文件
#
#   与 L3 / L4 的分工（关键，不重复）：
#     · L3 调 L0/L2 的**旋钮**（阈值），信号=漏检（kb_usage）。
#     · L4 由**真人裁决** L0–L3 的调整是否正确 → 连续错冻 rung（宪法 veto）。
#     · L5 不看人、不看旋钮，只看**阶梯活动 vs 真实结果**：某 rung 的"活跃期"跟着来
#       的是库变好还是变差 → 它的 running score ±单步；score 归一成激活权重。
#     · 一句话：L4 裁"对不对"，L5 学"值不值"。最硬外部锚 = raw/ 新鲜流入趋势
#       （external_inflow）+ 跨域连接（cross_ratio），非阶梯自打分（防 Goodhart 自欺）。
#
#  铁律（与 L0–L4 完全一致）：
#    · 外部锚最硬：学习的信号 = 真实结果趋势（external_inflow / cross_ratio），
#       非 self-impact。无 kb_rsi 信号 → best-effort 降级（不改分，绝不误判）。
#    · 接地阀（P0-1）：闭库（external_inflow<5% 且 cross_ratio<0.15）→ 冻结"内部自学习"
#       （给回声室加杠杆只会更锁死）：不加分，但照常记 cycle 日志（日志仍是外部事实）。
#    · 有界：score ∈[-1,1] 单步 ±L5_STEP；weight ∈[floor,1]（floor=L5_WEIGHT_FLOOR）；
#       至少 L5_MIN_SAMPLE 个 cycle 才参考（样本少→权重中性，绝不拍脑袋）。
#    · 人工在环：train 只学不强制；propose 算权重（只读）；apply 才"发布"策略
#       （写 .kb_l5_policy.json + git checkpoint）。调度器读 policy 决定跑哪些 rung、跑多频。
#    · raw/ 不改：L5 从不碰笔记，只读 kb_rsi 指标 + 各 rung 账本；只写自己的 state/policy。
#    · best-effort：空库 / 无 rung 账本 → 静默降级不崩溃。
#
#   用法:
#     python3 pipeline/kb_l5.py report --root R          # 只读：接地阀 + 每 rung 分/权重 + 循环
#     python3 pipeline/kb_l5.py train --root R            # 学一轮：记 cycle→score±步长（写 state）
#     python3 pipeline/kb_l5.py propose --root R          # 只读：由 score 算激活权重（不写库）
#     python3 pipeline/kb_l5.py apply  --root R           # 人工/调度后发布策略 .kb_l5_policy.json + checkpoint
#     python3 pipeline/kb_l5.py status --root R [--json]  # 完整 score / policy / grounding
# ============================================================
import argparse, json, subprocess, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi  # 复用 collect/metrics（接地阀 P0-1 + 真实结果快照）

# 单一事实源：收敛在 kb_constants（与 L0/L2/L3/L4 常量同中心）
from kb_constants import (
    L5_RUNGS, L5_SCORE_MIN, L5_SCORE_MAX, L5_STEP,
    L5_IMPROVE_MIN, L5_DEGRADE_MIN, L5_WEIGHT_FLOOR, L5_WEIGHT_CEIL, L5_MIN_SAMPLE,
)

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)  # template-vault 根

STATE_FILE = ".kb_l5_state.json"      # 运行态：逐 rung score + cycle 历史（gitignore）
CONFIG_FILE = ".kb_l5_policy.json"    # 发布的元策略权重（人工/调度 apply 写，gitignore）

# 各 rung「本 cycle 是否动作过」的来源（= 该层自己的 applied/状态账本，best-effort，缺一 rung 不炸）
#   l0/l2 读 applied 字典；l1 读 adjustments 列表；l3 读 meta 状态 cycles 计数。
_L5_ACTIVITY = {
    "l0": str(Path("pipeline") / ".kb_retriage_applied.json"),
    "l1": str(Path("pipeline") / ".kb_retrieval_adjust.json"),
    "l2": str(Path("pipeline") / ".kb_selfweight_applied.json"),
    "l3": ".kb_meta_state.json",
}


# ── 状态 / 文件路径 ──────────────────────────────────────────
def _state_path(root: Union[str, Path]) -> Path:
    return Path(root) / STATE_FILE


def _config_path(root: Union[str, Path]) -> Path:
    return Path(root) / CONFIG_FILE


def _read_state(root: Union[str, Path]) -> Dict[str, Any]:
    p = _state_path(root)
    if not p.exists():
        return {"scores": {r: 0.0 for r in L5_RUNGS}, "cycles": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("scores"), dict) and isinstance(d.get("cycles"), list):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"scores": {r: 0.0 for r in L5_RUNGS}, "cycles": []}


def _write_state(root: Union[str, Path], state: Dict[str, Any]) -> None:
    p = _state_path(root)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_config(root: Union[str, Path]) -> Dict[str, Any]:
    p = _config_path(root)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_config(root: Union[str, Path], config: Dict[str, Any]) -> None:
    p = _config_path(root)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _checkpoint(root: Union[str, Path], message: str, paths: List[Path]) -> bool:
    """把所选 transient 文件 force-add（作回滚锚）并原子提交。返回是否真的提交。"""
    for pt in paths:
        if pt.exists():
            subprocess.run(["git", "-C", str(root), "add", "--force", "--", str(pt)],
                           capture_output=True)
    if not subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip():
        return False
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message],
                   capture_output=True)
    return True


# ── 接地阀 + 真实结果快照（P0-1，复用 kb_rsi，与 L0–L4 完全一致）────────────────────
def grounding(root: Union[str, Path]) -> Tuple[bool, str, Dict[str, Any]]:
    m = kb_rsi.metrics(kb_rsi.collect(root), root=root)
    if not m:
        return False, "库为空或无编译笔记，无法评估接地", {}
    grounded = m["external_inflow"] >= 5.0 and m["cross_ratio"] >= 0.15
    if grounded:
        note = f"外部接地正常（新鲜外部源 {m['external_inflow']}%≥5% · 跨域 {m['cross_ratio']}≥0.15）"
    else:
        note = (f"⚠️ 外部接地不足（新鲜外部源 {m['external_inflow']}%<5% 或 跨域 {m['cross_ratio']}<0.15）"
                "→ 冻结内部元策略自学习（给回声室加杠杆只会更锁死；cycle 日志仍记）")
    return grounded, note, m


def _outcome(root: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """真实结果快照（kb_rsi.metrics）。best-effort：空库/无编译笔记 → None。"""
    try:
        m = kb_rsi.metrics(kb_rsi.collect(root), root=root)
        return m
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ── 逐 rung 本 cycle 是否动作过（读该层自己的账本，best-effort）────────────────────
def _rung_activity(root: Union[str, Path], rung: str) -> int:
    """返回某 rung 本 cycle 累计动作量（>0=动作过）。缺失/损坏 → 0。"""
    rel = _L5_ACTIVITY.get(rung)
    if not rel:
        return 0
    try:
        d = json.loads((Path(root) / rel).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(d, dict):
        return 0
    if rung in ("l0", "l2"):
        return len(d.get("applied") or {})
    if rung == "l1":
        return len(d.get("adjustments") or [])
    if rung == "l3":
        return len(d.get("cycles") or [])
    return 0


def _classify(prev: Optional[Dict[str, Any]], cur: Dict[str, Any]) -> str:
    """用"外部锚趋势"判定本 cycle 结果：improved / degraded / stable。
    主判据 = external_inflow(新鲜外部流入%）的 Δ；跨域 cross_ratio 仅作平稳带的平级裁决，
    避免对结构性小幅抖动过度反应（结果锚始终是最硬的外部锚，非自打分）。"""
    if not prev:
        return "stable"
    d_ext = float(cur["external_inflow"]) - float(prev["external_inflow"])
    d_cross = float(cur["cross_ratio"]) - float(prev["cross_ratio"])
    if d_ext >= L5_IMPROVE_MIN:
        return "improved"
    if d_ext <= -L5_DEGRADE_MIN:
        return "degraded"
    if d_cross >= 0.05:
        return "improved"
    if d_cross <= -0.05:
        return "degraded"
    return "stable"


# ── train：学一轮（记 cycle → score±步长）。主回路（cron 调）────────────────────
def train(root: Union[str, Path]) -> Dict[str, Any]:
    grounded, ground_note, _ = grounding(root)
    m = _outcome(root)
    if not m:
        return {"grounded": grounded, "ground_note": ground_note, "updated": False,
                "classification": None, "score_deltas": {}, "scores": {}, "n_cycles": 0,
                "note": "库为空/无编译笔记 → best-effort 降级（不改分）"}

    state = _read_state(root)
    cycles = state.get("cycles")
    if not isinstance(cycles, list):
        cycles = []
    prev = cycles[-1] if cycles else None
    activity = {r: _rung_activity(root, r) for r in L5_RUNGS}
    classification = _classify(prev, m)

    deltas: Dict[str, float] = {}
    # 接地阀：闭库 → 冻结内部自学习（只记 cycle，不加分；日志仍是外部事实）。
    #   变差惩罚允许（保护：某 rung 与变差同期 → 降分），但闭库时一律不动。
    if grounded and classification != "stable":
        for r in L5_RUNGS:
            cur = float(state.get("scores", {}).get(r, 0.0))
            delta = 0.0
            if activity.get(r, 0) > 0:  # 该 rung 本 cycle 动作过，才给予奖惩（动作过=真有投入）
                if classification == "improved":
                    delta = min(L5_STEP, round(L5_SCORE_MAX - cur, 6))
                elif classification == "degraded":
                    delta = max(-L5_STEP, round(L5_SCORE_MIN - cur, 6))
            deltas[r] = delta
            state.setdefault("scores", {})[r] = round(cur + delta, 3)

    cycles.append({"ts": datetime.now().isoformat(timespec="seconds"),
                   "external_inflow": m["external_inflow"], "cross_ratio": m["cross_ratio"],
                   "activity": activity, "classification": classification})
    if len(cycles) > 64:  # 只留近 64 轮，防历史无限膨胀
        cycles = cycles[-64:]
    state["cycles"] = cycles
    _write_state(root, state)

    n = len(cycles)
    return {"grounded": grounded, "ground_note": ground_note, "updated": True,
            "classification": classification, "score_deltas": deltas,
            "scores": state["scores"], "n_cycles": n,
            "note": ("接地不足·冻结内部自学习（仅记日志）" if not grounded
                     else f"已学一轮（{classification}）· 本轮 {sum(1 for v in deltas.values() if v)} 个 rung 改分")}


# ── 由 score 归一成激活权重（有界：[floor,1]；样本少→中性 0.5）────────────────────
def _weights_from_scores(scores: Dict[str, Any], n_cycles: int) -> Tuple[Dict[str, float], bool]:
    insufficient = n_cycles < L5_MIN_SAMPLE
    w: Dict[str, float] = {}
    for r in L5_RUNGS:
        s = float(scores.get(r, 0.0))
        weight = 0.5 + 0.5 * (s / L5_SCORE_MAX)   # score∈[-1,1] → [0,1]（0→0.5 中性）
        weight = min(L5_WEIGHT_CEIL, max(L5_WEIGHT_FLOOR, weight))  # 绝不归零（保留探索·防塌缩）
        w[r] = round(weight, 3)
    return w, insufficient


def propose(root: Union[str, Path]) -> Dict[str, Any]:
    """由当前 score 算激活权重（只读，不写库）。样本少→权重中性并标注。"""
    state = _read_state(root)
    grounded, ground_note, _ = grounding(root)
    scores = state.get("scores", {})
    n_cycles = len(state.get("cycles", []))
    w, insufficient = _weights_from_scores(scores, n_cycles)
    note = "样本不足（<%d cycle）→ 权重中性，勿据此调度" % L5_MIN_SAMPLE if insufficient else "权重由真实结果学习得出"
    return {"grounded": grounded, "ground_note": ground_note, "scores": scores,
            "weights": w, "n_cycles": n_cycles, "insufficient": insufficient,
            "ranking": sorted(L5_RUNGS, key=lambda r: w[r], reverse=True), "note": note}


def apply(root: Union[str, Path]) -> Dict[str, Any]:
    """人工/调度后发布策略：写 .kb_l5_policy.json + 一次 git checkpoint（回滚锚）。"""
    p = propose(root)
    cfg = {"weights": p["weights"], "scores": p["scores"], "n_cycles": p["n_cycles"],
           "grounded": p["grounded"],
           "updated": datetime.now().isoformat(timespec="seconds"), "note": p["note"]}
    _write_config(root, cfg)
    ranked = "，".join(f"{r}={p['weights'][r]}" for r in p["ranking"])
    _checkpoint(root, f"kb: L5 元策略发布 [{ranked}]（真实结果学习·可回滚）",
                [_config_path(root)])
    return {"weights": p["weights"], "ranking": p["ranking"], "n_cycles": p["n_cycles"],
            "note": p["note"]}


# ── status ────────────────────────────────────────────────────
def status(root: Union[str, Path]) -> Dict[str, Any]:
    p = propose(root)
    state = _read_state(root)
    cycles = state.get("cycles", [])
    last = cycles[-1] if cycles else {}
    return {**p, "last_classification": last.get("classification"),
            "last_activity": last.get("activity"),
            "last_cycle_ext": last.get("external_inflow"),
            "conservative": (not p["grounded"])}


# ── 渲染 ────────────────────────────────────────────────────
def render(root: Union[str, Path], summary: Optional[Dict[str, Any]] = None) -> str:
    s = status(root) if summary is None else summary
    L = ["# 🎚️ L5 元策略（反馈阶梯 · 元策略层）", "",
         f"> 接地阀: **{s['grounded']}** — {s['ground_note']}",
         f"> 学\"值不值\"：每 rung 的活跃期跟着来的是库变好还是变差 → running score ±单步 → 激活权重",
         f"> 结果锚 = raw/ 新鲜流入(external_inflow) + 跨域(cross_ratio)（非阶梯自打分）"
         f" · 需 ≥{L5_MIN_SAMPLE} cycle 才有参考 · weight ∈[{L5_WEIGHT_FLOOR},1]·永不归零"]

    if not s["grounded"]:
        L += ["", "⛔ 接地不足 → 冻结内部元策略自学习（只记 cycle，不加分）。"]
    if s.get("insufficient"):
        L += ["", f"⏳ 样本不足（{s['n_cycles']} cycle < {L5_MIN_SAMPLE}）→ 权重中性，暂不据此调度。"]

    # 权重表
    L += ["", "## 🎛️ 每 rung 激活权重（由真实结果学习）", ""]
    L.append("| rung | running score | 激活权重 | 本 cycle 动作 | 累计 cycle | 优先级 |")
    L.append("|------|-------------|--------|-----------|---------|------|")
    by_r = {r: w for r, w in zip(s["ranking"], range(1, len(s["ranking"]) + 1))}
    last_act = s.get("last_activity") or {}
    for r in s["ranking"]:
        L.append(f"| `{r}` | {s['scores'].get(r, 0.0)} | {s['weights'][r]} "
                 f"| {last_act.get(r, 0)} "
                 f"| {s['n_cycles']} | #{by_r[r]} |")

    ranking_str = " › ".join(f"`{r}={s['weights'][r]}`" for r in s["ranking"])
    L += ["", f"> 优先级排序：`{ranking_str}`"]
    if s.get("last_classification"):
        L += ["", f"末次 cycle 结果判定：**{s['last_classification']}**"
               f"（external_inflow={s.get('last_cycle_ext')} · 动作 {s.get('last_activity')}）"]
    return "\n".join(L) + "\n"


# ── CLI ─────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="L5 元策略（反馈阶梯 · 元策略层）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("report", "propose", "apply", "status"):
        sp = sub.add_parser(name); sp.add_argument("--root", required=True)
        if name == "status":
            sp.add_argument("--json", action="store_true")
    tr = sub.add_parser("train"); tr.add_argument("--root", required=True)

    a = ap.parse_args()
    root = a.root
    if a.cmd == "report":
        print(render(root, status(root)))
        return 0
    if a.cmd == "propose":
        print(render(root, propose(root)))
        return 0
    if a.cmd == "apply":
        r = apply(root)
        pub = " › ".join(f"{r['weights'][x]}({x})" for x in r["ranking"])
        print(f"✅ L5 元策略发布：{pub}")
        print(f"   备注: {r.get('note', '')}")
        return 0
    if a.cmd == "train":
        r = train(root)
        print(render(root, status(root)))
        print(f"\n🎓 已学一轮（{r['classification']}）· score 改动："
              + (", ".join(f"{rk}±{dv}" for rk, dv in r["score_deltas"].items() if dv) or "无"))
        return 0
    if a.cmd == "status":
        stt = status(root)
        print(json.dumps(stt, ensure_ascii=False, indent=2) if getattr(a, "json", False) else render(root, stt))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
