#!/usr/bin/env python3
# ============================================================
# kb_meta.py —— L3 元调参（反馈阶梯 · 元调参层）
# ------------------------------------------------------------
# 使命：让“反馈阶梯本身”可被反馈所调参——
#   L0/L1/L2 各自修检索表层 / 喂查询 / 加权重；它们共同的目标只有一个：
#   让真实查询“答得上”。L3 站在它们头顶，用这唯一的现实结果（`kb_usage` 漏检），
#   去调 L0/L2 的**阈值旋钮**（缺口的 idf 下限、提报最小信号、对漏检的加权）。
#   它是反馈阶梯 Rungs 的第四级（元调参 L3 / 元参数调优）：
#
#     L0 检索表层修正（kb_retriage）← 已完成
#     L1 查询即反馈（kb_adaptretrieve）← 已完成
#     L2 自我加权（kb_selfweight）← 已完成
#     L3 元调参（本模块 · 调 L0/L2 的阈值，连续负 delta 回滚）← 本文件
#     L4 宪法（人工裁判）
#
#   与 kb_calibrate（离线仿真大语料 A/B 标定 DUP/MERGE/RETIRE）的区别：
#     · kb_calibrate = 一次性、离线、仿真语料、人工拍板。
#     · L3 = 运行时、真实留程、调“阶梯自己的旋钮”（L0_MIN_IDF / L2_SIGNAL_MIN /
#       L2_L1_MISS_BONUS），连续负 delta → 回滚到上一次的“好值”。
#
#  铁律（与 kb_adaptretrieve / kb_selfweight / kb_retriage / feedback_loop 一致）：
#    · 外部锚最硬：调参的信号 = 真实使用漏检（`kb_usage.suspect_failed_count`），
#       非 self-impact 打分。无 kb_usage → best-effort 降级（miss=0，绝不误调）。
#    · 接地阀（P0-1）：知识库疑似自闭（新鲜外部源<5% 且跨域<15%）时，
#       冻结“内部元调参”——闭库时松覆盖门只会给回声室加杠杆，绝不调。
#    · 有界：每个旋钮限定 [min,max] 与步长，单次只调一步；方向由“漏检+对应层是否动作”
#       的领域知识决定，而非无脑随机扫。
#    · 连续负 delta 回滚：每次 apply 记一次（漏检快照）；连续 >= L3_ROLLBACK_CONSEC 次
#       漏检都变差 → 回滚该旋钮到 last_good（写前 git checkpoint，可回滚）。
#    · 人工在环：propose 只写 jsonl；apply 确认后写运行时配置 .kb_meta_config.json + checkpoint。
#       不改 kb_constants.py（分发仓常量，不可运行时改）、不改任何笔记正文。
#    · raw/ 不改：L3 从不碰笔记，raw/ 天然隔离。
#    · best-effort：无 kb_usage / 无信号 → 静默降级，绝不锁死流水线。
#
#   旋钮方向约定：
#     - 覆盖门（L0_MIN_IDF / L2_SIGNAL_MIN）：值越大越严。漏检多且对应层“未动作”→ 下调松覆盖。
#     - 加权（L2_L1_MISS_BONUS）：值越小越忽视漏检。漏检多且 L2 “未动作”→ 上调多加权漏检。
#
#   用法:
#     python3 pipeline/kb_meta.py report --root R          # 只读：接地阀 + 漏检 + 旋钮
#     python3 pipeline/kb_meta.py propose --root R          # 只产调参提议（写 jsonl，不改库）
#     python3 pipeline/kb_meta.py apply  --root R [--id N.. | --all]   # 人工后写配置 + checkpoint
#     python3 pipeline/kb_meta.py calibrate --root R        # 元调参主回路：量漏检 → 连续负delta回滚 → 产提议
#     python3 pipeline/kb_meta.py status --root R [--json]  # 旋钮历史 + 回滚状态
# ============================================================
import argparse, json, subprocess, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi  # 复用 collect/metrics（接地阀 P0-1）

# 旋钮范围/步长 + 回滚阈值/漏检阈值：单一事实源，收敛在 kb_constants（与 L0/L2 常量同中心）
from kb_constants import (
    L0_MIN_IDF, L2_SIGNAL_MIN, L2_L1_MISS_BONUS,
    L3_KNOB_MIN, L3_KNOB_MAX, L3_KNOB_STEP,
    L3_ROLLBACK_CONSEC, L3_MISS_HIGH,
)

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)  # template-vault 根

PROPOSAL_FILE = ".kb_meta_proposals.jsonl"     # 瞬态调参提议（gitignore）
STATE_FILE = ".kb_meta_state.json"            # 运行态：旋钮当前值 + 历史 + 回滚锚（gitignore）
CONFIG_FILE = ".kb_meta_config.json"          # 运行时旋钮配置（人工 apply 写，gitignore）

# 该层“是否动作过”的来源（feedback_state 的 L0/L2 上次产出，best-effort）
L0_PROPOSAL_REL = str(Path("pipeline") / ".kb_retriage_proposals.jsonl")
L2_PROPOSAL_REL = str(Path("pipeline") / ".kb_selfweight_proposals.jsonl")

# 旋钮定义（kind: gate=覆盖门 down-松 / weight=加权 up-多）+ 默认值（来自 L0/L2 常量）
_L3_KNOB_SPECS = {
    "L0_MIN_IDF": {"kind": "gate", "why": "L0 缺口 token 的 idf 下限（越高越严；漏检多且 L0 未动作 → 松覆盖下调）"},
    "L2_SIGNAL_MIN": {"kind": "gate", "why": "L2 提议提升所需最小融合信号（越高越严；漏检多且 L2 未动作 → 松覆盖下调）"},
    "L2_L1_MISS_BONUS": {"kind": "weight", "why": "L2 融合信号对 L1 漏检的折算权重（越低忽视漏检；漏检多且 L2 未动作 → 多加权上调）"},
}
DEFAULT_KNOBS = {"L0_MIN_IDF": L0_MIN_IDF, "L2_SIGNAL_MIN": L2_SIGNAL_MIN, "L2_L1_MISS_BONUS": L2_L1_MISS_BONUS}
# 提议排序优先级（最底层、与检索覆盖最直接者居前）
_KNOB_PRIORITY = {"L0_MIN_IDF": 0, "L2_SIGNAL_MIN": 1, "L2_L1_MISS_BONUS": 2}


# ── 状态 / 文件路径 ──────────────────────────────────────────
def _prop_path(root: Union[str, Path]) -> Path:
    return Path(root) / PROPOSAL_FILE


def _state_path(root: Union[str, Path]) -> Path:
    return Path(root) / STATE_FILE


def _config_path(root: Union[str, Path]) -> Path:
    return Path(root) / CONFIG_FILE


def _read_state(root: Union[str, Path]) -> Dict[str, Any]:
    p = _state_path(root)
    if not p.exists():
        return {"knovals": dict(DEFAULT_KNOBS), "cycles": [], "last_good": dict(DEFAULT_KNOBS)}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"knovals": dict(DEFAULT_KNOBS), "cycles": [], "last_good": dict(DEFAULT_KNOBS)}


def _write_state(root, state) -> None:
    p = _state_path(root)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_config(root: Union[str, Path]) -> Dict[str, Any]:
    p = _config_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(root: Union[str, Path], knovals: Dict[str, float]) -> None:
    p = _config_path(root)
    p.write_text(json.dumps({"knovals": knovals,
                             "updated": datetime.now().isoformat(timespec="seconds")},
                            ensure_ascii=False, indent=2), encoding="utf-8")


def current_knobs(root: Union[str, Path]) -> Dict[str, float]:
    """当前旋钮值：优先运行时配置（apply 后），否则回到默认。"""
    cfg = _read_config(root)
    kv = cfg.get("knovals") if isinstance(cfg, dict) else None
    base = dict(DEFAULT_KNOBS)
    if isinstance(kv, dict):
        for k in DEFAULT_KNOBS:
            if k in kv:
                base[k] = float(kv[k])
    return base


def _checkpoint(root: Union[str, Path], message: str, paths: List[Path]) -> bool:
    """把所选 transient 文件 force-add（作回滚锚）并原子提交。返回是否真的提交。"""
    for p in paths:
        if p.exists():
            subprocess.run(["git", "-C", str(root), "add", "--force", "--", str(p)],
                           capture_output=True)
    if not subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip():
        return False
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message],
                   capture_output=True)
    return True


# ── 接地阀（P0-1，复用 kb_rsi 指标，与 L0/L2 完全一致）────────────────────
def grounding(root: Union[str, Path]) -> Tuple[bool, str, Dict[str, Any]]:
    m = kb_rsi.metrics(kb_rsi.collect(root), root=root)
    if not m:
        return False, "库为空或无编译笔记，无法评估接地", {}
    grounded = m["external_inflow"] >= 5.0 and m["cross_ratio"] >= 0.15
    if grounded:
        note = f"外部接地正常（新鲜外部源 {m['external_inflow']}%≥5% · 跨域 {m['cross_ratio']}≥0.15）"
    else:
        note = (f"⚠️ 外部接地不足（新鲜外部源 {m['external_inflow']}%<5% 或 跨域 {m['cross_ratio']}<0.15）"
                "→ 知识库疑似自闭，冻结内部元调参（松覆盖门只会给回声室加杠杆）")
    return grounded, note, m


# ── 现实结果信号：kb_usage 漏检（best-effort，不连模型）────────────────────
def outcome(root: Union[str, Path]) -> Dict[str, Any]:
    """读 `kb_usage.analyze` 的真实使用漏检信号（suspect_failed_count）。
    best-effort：无 kb_usage / 无事件 → miss=0 且 note，绝不报错。"""
    try:
        import kb_usage
        a = kb_usage.analyze(root, use_embedding=False, use_llm=False)
        miss = int(a.get("suspect_failed_count") or 0)
        return {"miss": miss, "requery_rate": a.get("requery_rate"),
                "query_success_proxy": a.get("query_success_proxy"),
                "n_queries": a.get("n_queries"), "note": ""}
    except (ImportError, OSError, AttributeError, TypeError, KeyError, ValueError):
        return {"miss": 0, "requery_rate": None, "query_success_proxy": None,
                "n_queries": 0, "note": "无 kb_usage 信号（best-effort 降级）"}


def _rung_action_count(root: Union[str, Path], rel: str) -> int:
    """best-effort：某层上次产出提议数（>0=该层“动作过”）。缺失/损坏 → 0。"""
    p = Path(root) / rel
    if not p.exists():
        return 0
    try:
        return sum(1 for l in p.read_text(encoding="utf-8").splitlines() if l.strip())
    except OSError:
        return 0


def _rung_signals(root: Union[str, Path]) -> Dict[str, Any]:
    """漏检偏多 + 各层是否“未动作”（idle）。未动作→需松其覆盖门/多加权。"""
    o = outcome(root)
    return {"miss": o["miss"], "miss_high": o["miss"] >= L3_MISS_HIGH,
            "l0_idle": _rung_action_count(root, L0_PROPOSAL_REL) == 0,
            "l2_idle": _rung_action_count(root, L2_PROPOSAL_REL) == 0}


# ── 单旋钮候选（有界·单步·方向由领域知识定）────────────────────
def _candidate(knob: str, cur: float, signals: Dict[str, bool]) -> Optional[Tuple[float, str, str]]:
    """返回 (new_value, reason, direction) 或 None（无有效步 / 已触边界）。有界在 [min,max] 单步。"""
    spec = _L3_KNOB_SPECS[knob]
    lo, hi, step = L3_KNOB_MIN[knob], L3_KNOB_MAX[knob], L3_KNOB_STEP[knob]
    if spec["kind"] == "gate":
        if not signals["miss_high"]:
            return None
        if knob == "L0_MIN_IDF" and not signals["l0_idle"]:
            return None          # L0 已在补 token，不必再松 L0 门
        if knob == "L2_SIGNAL_MIN" and not signals["l2_idle"]:
            return None          # L2 已在提报，不必再松 L2 门
        new = round(max(lo, round(cur - step, 3)), 3)
        if new >= cur:
            return None          # 已达下限，无法再松
        return new, f"漏检偏多(≥{L3_MISS_HIGH})且 L0/L2 未动作 → 松覆盖门(下调)", "down"
    else:  # weight
        if not signals["miss_high"] or not signals["l2_idle"]:
            return None
        new = round(min(hi, round(cur + step, 3)), 3)
        if new <= cur:
            return None          # 已达上限，无法再调
        return new, f"漏检偏多(≥{L3_MISS_HIGH})且 L2 未动作 → 多加权 L1 漏检(上调)", "up"


# ── 提议生成 ─────────────────────────────────────────────────
def propose(root: Union[str, Path]) -> Dict[str, Any]:
    """当前旋钮值 + 现实漏检信号 → 调参提议（只写 jsonl，不改库·不改 kb_constants）。
    接地不足或漏检偏低 → 静默降级（0 提议 + 解释）。排序：旋钮优先级。"""
    grounded, ground_note, _ = grounding(root)
    sig = _rung_signals(root)
    current = current_knobs(root)
    proposals: List[Dict[str, Any]] = []
    if not grounded:
        _write_proposals(root, [])
        return {"grounded": False, "ground_note": ground_note, "signals": sig,
                "n_proposals": 0, "proposals": [],
                "note": f"接地不足 → 冻结内部元调参：{ground_note}"}
    if not sig["miss_high"]:
        _write_proposals(root, [])
        return {"grounded": True, "ground_note": ground_note, "signals": sig,
                "n_proposals": 0, "proposals": [],
                "note": f"漏检偏低(<{L3_MISS_HIGH}) → 阶梯运行良好，无需元调参"}
    for knob in sorted(_L3_KNOB_SPECS, key=lambda k: _KNOB_PRIORITY[k]):
        cand = _candidate(knob, current[knob], sig)
        if not cand:
            continue
        new, why, direction = cand
        proposals.append({"knob": knob, "old": current[knob], "new": new,
                          "direction": direction, "step": L3_KNOB_STEP[knob],
                          "why": f"{_L3_KNOB_SPECS[knob]['why']}；{why}",
                          "ground_note": ground_note,
                          "outcome_snapshot": sig["miss"]})
    proposals.sort(key=lambda p: _KNOB_PRIORITY[p["knob"]])
    for i, p in enumerate(proposals, 1):
        p["id"] = i
    _write_proposals(root, proposals)
    return {"grounded": True, "ground_note": ground_note, "signals": sig,
            "n_proposals": len(proposals), "proposals": proposals,
            "note": f"元调参提议 {len(proposals)} 个（人工在环，未改库）"}


def _write_proposals(root: Union[str, Path], proposals: List[Dict[str, Any]]) -> None:
    p = _prop_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in proposals)
        + ("\n" if proposals else ""),
        encoding="utf-8")


# ── 连续负 delta 回滚（纯函数：对给定历史计数尾部连续负 delta）────────────────────
def tail_consecutive_negative(cycles: List[Dict[str, Any]]) -> int:
    """cycles 按 oldest→newest 顺序，每个含 'delta'（漏检下降>0=改善）。返回尾部连续负 delta 次数。"""
    tail = 0
    for c in reversed(cycles):
        if c.get("delta", 0) < 0:
            tail += 1
        else:
            break
    return tail


# ── apply：人工确认后写运行时配置 + checkpoint（记漏检快照，供下次 calibrate 算 delta）────
def apply(root: Union[str, Path], ids: Optional[List[str]] = None,
          force: bool = False) -> Dict[str, Any]:
    """把选定调参提议的旋钮值写进运行时配置 .kb_meta_config.json（不改仓内常量/笔记），
    写前一次提交（回滚锚）；并记一次漏检快照到运行态，供 calibrate 算连续负 delta。
    id 归一化：提案存 int、CLI 传串 → 统一转 int 比对（与 kb_contradiction 同约定）。"""
    p = _prop_path(root)
    if not p.exists():
        return {"written": [], "skipped": [], "note": "无待审提议；先 run calibrate/propose"}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if ids is None and force:
        pick = recs
    else:
        want: set = set()
        for x in (ids or []):
            try:
                want.add(int(x))
            except (TypeError, ValueError):
                pass
        pick = [r for r in recs if r.get("id") in want]

    # 接地不足时冻结：即使 apply 也拒绝改旋钮
    grounded, ground_note, _ = grounding(root)

    knovals = current_knobs(root)
    state = _read_state(root)
    if "cycles" not in state or not isinstance(state["cycles"], list):
        state["cycles"] = []
    written, skipped = [], []
    for r in pick:
        knob = r.get("knob")
        if knob not in _L3_KNOB_SPECS:
            skipped.append({"id": r.get("id"), "reason": f"未知旋钮 {knob}"}); continue
        if not grounded:
            skipped.append({"id": r.get("id"), "knob": knob, "reason": "接地不足·冻结元调参"}); continue
        old = knovals[knob]
        new = float(r.get("new", old))
        # 有界兜底：限制在旋钮范围内
        new = round(min(L3_KNOB_MAX[knob], max(L3_KNOB_MIN[knob], new)), 3)
        knovals[knob] = new
        o = outcome(root)
        # 记快照：apply 时的漏检；delta 由下次 calibrate 算（before - after）
        state["cycles"].append({"knob": knob, "old": old, "new": new,
                                "outcome_before": o["miss"],
                                "applied": datetime.now().isoformat(timespec="seconds")})
        written.append({"id": r.get("id"), "knob": knob, "old": old, "new": new,
                        "outcome_before": o["miss"]})

    if written:
        _write_config(root, knovals)
        _write_state(root, state)
        _checkpoint(root, f"kb: L3 元调参旋钮 {', '.join(w['knob'] for w in written)} → "
                          f"{', '.join(str(w['new']) for w in written)}（接地门·可回滚）",
                    [_config_path(root), _state_path(root)])
    return {"written": written, "skipped": skipped,
            "note": f"写入 {len(written)} 个旋钮 · 跳过 {len(skipped)} 个"}


# ── calibrate：元调参主回路（量漏检 → 连续负 delta 回滚 → 产提议）────────────────────
def calibrate(root: Union[str, Path]) -> Dict[str, Any]:
    """测量当前漏检 → 为每个旋钮末次周期算 delta（before - after），连算连续负 delta，
    连续 >= L3_ROLLBACK_CONSEC → 回滚该旋钮到 last_good（写配置+checkpoint）；
    再产下一轮调参提议（接地冻结则 0）。这是 cron 应调的主入口。"""
    grounded, ground_note, _ = grounding(root)
    o = outcome(root)
    miss = o["miss"]
    state = _read_state(root)
    if "cycles" not in state or not isinstance(state["cycles"], list):
        state["cycles"] = []

    rolled_back: List[Dict[str, Any]] = []
    if grounded and state.get("cycles"):
        # 按旋钮分组，为末次周期算 delta（delta = before - after；漏检下降>0=改善），
        #   并连算尾部连续负 delta —— 连续 >= L3_ROLLBACK_CONSEC 即回滚到 last_good。
        for knob in _L3_KNOB_SPECS:
            kc = sorted([c for c in state["cycles"] if c.get("knob") == knob],
                        key=lambda c: c.get("applied") or "")
            if not kc:
                continue
            last = kc[-1]
            last["outcome_after"] = miss
            last["delta"] = last.get("outcome_before", miss) - miss
            last["consecutive_negative"] = tail_consecutive_negative(kc)
            if last["consecutive_negative"] >= L3_ROLLBACK_CONSEC:
                good = float(state["last_good"].get(knob, last["old"]))
                old = float(current_knobs(root).get(knob, good))
                if abs(old - good) > 1e-9:  # 仅在“当前值确实偏离好值”时回滚
                    rolled_back.append({"knob": knob, "old": old, "new": good,
                                        "consecutive_negative": last["consecutive_negative"]})
                    state["last_good"][knob] = good
                    state["cycles"].append({"knob": knob, "old": old, "new": good,
                                            "rolled_back": True, "reason": "连续负delta",
                                            "applied": datetime.now().isoformat(timespec="seconds")})
        # 持久化 delta/连续负 delta（跨 calibrate 累积，否则回滚历史每次重置）
        _write_state(root, state)
        # 把受回滚的旋钮值落回运行时配置 + checkpoint
        if rolled_back:
            kv = current_knobs(root)
            for rb in rolled_back:
                kv[rb["knob"]] = rb["new"]
            _write_config(root, kv)
            rb_msg = " → ".join(f"{r['knob']}={r['new']}" for r in rolled_back)
            _checkpoint(root, f"kb: L3 元调参回滚 {rb_msg}（连续负delta）",
                        [_config_path(root), _state_path(root)])

    next_propose = propose(root) if grounded else {
        "n_proposals": 0, "proposals": [], "note": f"接地不足 → 冻结：{ground_note}"}
    return {"grounded": grounded, "ground_note": ground_note, "outcome": o,
            "rolled_back": rolled_back, "proposals": next_propose.get("proposals", []),
            "n_proposals": next_propose.get("n_proposals", 0), "note": next_propose.get("note", "")}


# ── status：旋钮历史 + 回滚状态 ─────────────────────────────
def status(root: Union[str, Path]) -> Dict[str, Any]:
    grounded, ground_note, _ = grounding(root)
    o = outcome(root)
    state = _read_state(root)
    # 汇总每个旋钮的尾部连续负 delta（用于快速判“是否需要回滚”）
    by_knob: Dict[str, List[Dict[str, Any]]] = {}
    for c in state.get("cycles", []):
        by_knob.setdefault(c.get("knob"), []).append(c)
    rollback_state = {}
    for knob, kc in by_knob.items():
        kc.sort(key=lambda c: c.get("applied") or "")
        cn = tail_consecutive_negative(kc)
        rollback_state[knob] = {"consecutive_negative": cn,
                                "needs_rollback": cn >= L3_ROLLBACK_CONSEC,
                                "last_good": state.get("last_good", {}).get(knob)}
    p = _prop_path(root)
    pending = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []
    return {"grounded": grounded, "ground_note": ground_note, "outcome": o,
            "current_knobs": current_knobs(root), "default_knobs": dict(DEFAULT_KNOBS),
            "rollback_state": rollback_state, "pending_proposals": pending,
            "n_cycles": len(state.get("cycles", []))}


# ── 渲染 ────────────────────────────────────────────────────
def render(root: Union[str, Path], summary: Optional[Dict[str, Any]] = None) -> str:
    s = status(root)
    L = ["# 🔧 L3 元调参（反馈阶梯 · 元调参层）", "",
         f"> 接地阀: **{s['grounded']}** — {s['ground_note']}",
         f"> 现实漏检(kb_usage.suspect_failed): **{s['outcome']['miss']}**"
         f"（阈值 ≥{L3_MISS_HIGH} 才调；连续负 delta ≥{L3_ROLLBACK_CONSEC} → 回滚）",
         "> 旋钮（作用对象 / 方向）：",
         "  - `L0_MIN_IDF` 覆盖门 ↓松  /  `L2_SIGNAL_MIN` 覆盖门 ↓松  /  `L2_L1_MISS_BONUS` 加权 ↑多"]
    if not s["grounded"]:
        L += ["", "⛔ 接地不足 → 冻结内部元调参（不给回声室加杠杆）。`apply` 亦被拒。"]
        return "\n".join(L) + "\n"
    if s["outcome"]["miss"] < L3_MISS_HIGH:
        L += ["", "✅ 漏检偏低 → 阶梯运行良好，暂无调参（`calibrate` 产出 0 提议）。"]
        return "\n".join(L) + "\n"
    # 旋钮表
    L += ["", "## 🎛️ 旋钮当前值 vs 默认", ""]
    L.append("| 旋钮 | 当前 | 默认 | 连续负 delta | 需回滚 | last_good |")
    L.append("|------|------|------|------------|--------|-----------|")
    for knob in sorted(_L3_KNOB_SPECS, key=lambda k: _KNOB_PRIORITY[k]):
        rs = s["rollback_state"].get(knob, {})
        L.append(f"| `{knob}` | {s['current_knobs'].get(knob)} | {s['default_knobs'].get(knob)} "
                 f"| {rs.get('consecutive_negative', 0)} "
                 f"| {'**是**' if rs.get('needs_rollback') else '否'} | {rs.get('last_good')} |")
    # 待写回提议
    if s["pending_proposals"]:
        L += ["", "## 📝 待应用调参（人工确认 `apply --id N`）", ""]
        for p in s["pending_proposals"]:
            L.append(f"- #{p['id']} `{p['knob']}` {p['old']} → **{p['new']}** "
                     f"（{p['direction']}）— {p['why']}")
    else:
        L += ["", "✅ 无待应用调参（已无有效步 / 全触边界）。"]
    return "\n".join(L) + "\n"


# ── CLI ─────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="L3 元调参（反馈阶梯 · 元调参层）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("report"); s.add_argument("--root", required=True)
    s = sub.add_parser("propose"); s.add_argument("--root", required=True)
    s = sub.add_parser("apply"); s.add_argument("--root", required=True)
    s.add_argument("--id", action="append", default=None)
    s.add_argument("--all", dest="all_", action="store_true")
    s = sub.add_parser("calibrate"); s.add_argument("--root", required=True)
    s = sub.add_parser("status"); s.add_argument("--root", required=True)
    s.add_argument("--json", action="store_true")

    a = ap.parse_args()
    root = a.root
    if a.cmd == "report":
        st = status(root)
        print(render(root, st))
        return 0
    if a.cmd == "propose":
        r = propose(root)
        print(render(root, r))
        return 0
    if a.cmd == "apply":
        ids = a.id if (a.all_ is False and a.id) else None
        ids = None if a.all_ else ids
        r = apply(root, ids)
        print(f"✅ L3 元调参 apply：写入 {len(r['written'])} 个旋钮 · 跳过 {len(r['skipped'])} 个")
        for w in r["written"]:
            print(f"   {w['knob']}: {w['old']} → {w['new']}")
        for sk in r["skipped"]:
            print(f"   ⏭ {sk.get('knob', sk.get('id'))} — {sk.get('reason', '')}")
        print(f"   备注: {r.get('note', '')}")
        return 0
    if a.cmd == "calibrate":
        r = calibrate(root)
        print(render(root, r))
        if r["rolled_back"]:
            print(f"\n↩️ 已回滚 {len(r['rolled_back'])} 个旋钮（连续负 delta）：")
            for rb in r["rolled_back"]:
                print(f"   {rb['knob']}: {rb['old']} → {rb['new']}")
        return 0
    if a.cmd == "status":
        st = status(root)
        print(json.dumps(st, ensure_ascii=False, indent=2) if getattr(a, "json", False) else render(root, st))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
