#!/usr/bin/env python3
# ============================================================
# kb_selfweight.py —— L2 自我加权（反馈阶梯 · 实时回路之后的“代谢回路”）
# ------------------------------------------------------------
# 反馈阶梯 Rungs：
#   L1 查询即反馈（kb_adaptretrieve，最紧·外部锚最硬）  ← M1 已完成
#   L2 自我加权（本模块，代谢·importance 只升+封顶+多样性地板，锚=真实外部接地）← 本文件
#   L3 元调参（kb_calibrate/T3，连续负 delta 回滚）
#   L4 宪法（人工裁判）
#
# 铁律（与 kb_adaptretrieve / feedback_loop / kb_contradiction 完全一致）：
#   · 外部锚最硬：L2 权重提升必须被“真实外部信号”锚定 ——
#       显式 useful 反馈（真外部）> 真实使用再问(接 L1)> RAG 人气代理（弱·封顶）> 入链（结构，弱）。
#   · 接地阀（P0-1）：知识库疑似自闭（新鲜外部源<5% 且跨域<15%）时，
#       冻结“内部人气”提升，只允许“外部显式有用”反馈回写（它本就是接地）——
#       否则自我加权会rich-get-richer、把回声室越喂越厚。
#   · 只升不降 + 封顶：importance 只增（new>=old），单 proposal delta≤BUMP_CAP，
#       累计封顶 1.0；冷门但有真实反馈者给 DIVERSITY_FLOOR 保底。
#   · 人工在环：默认只写 proposals 到 jsonl，绝不自动改库；
#       由 apply（显式 --id/--all）确认后写 frontmatter，写前 git checkpoint，可回滚。
#   · raw/ 不改：不可变外部源硬隔离守卫（复用 feedback_loop.is_raw_rel）。
#   · best-effort：无信号/接地不足 → 静默降级（0 提升+解释），绝不锁死流水线。
#
# 用法:
#   python3 pipeline/kb_selfweight.py report --root R        # 只读：接地阀 + 信号汇总
#   python3 pipeline/kb_selfweight.py propose --root R        # 只产提议（写 jsonl）
#   python3 pipeline/kb_selfweight.py apply  --root R [--id N.. | --all]
#   python3 pipeline/kb_selfweight.py status --root R [--json]
# ============================================================
import argparse, json, re, subprocess, sys, math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi            # 复用 collect/metrics（接地阀）、load_note
from kb_common import parse_importance as _parse_importance
from kb_constants import (
    EXTERNAL_MIN_CROSS, EXTERNAL_FRESH_MIN_PCT,
    L2_SIGNAL_MIN, L2_LINK_BONUS, L2_L1_MISS_BONUS,
)

# 权重/门控：复用 feedback_loop 既有 T2 参数（单一事实源，避免两套魔法数字）
from feedback_loop import (
    EXT_WEIGHT, INT_WEIGHT, HIT_CAP, BUMP_CAP, MIN_BUMP, DIVERSITY_FLOOR,
    is_raw_rel,
)

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)  # template-vault 根

PROPOSAL_FILE = ".kb_selfweight_proposals.jsonl"   # 瞬态提议（gitignore）
APPLIED_FILE = ".kb_selfweight_applied.json"       # 累计提升账本（gitignore，写前 force-add 作回滚锚）


# ── 状态 / 文件路径 ──────────────────────────────────────────
def _state(root: Union[str, Path]) -> Dict[str, Any]:
    try:
        from state_manager import StateStore
        ss = StateStore(root)
        if ss.exists("feedback_state.json"):
            try:
                return ss.load("feedback_state.json", {})
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
    except (ImportError, OSError, TypeError):
        pass
    sp = Path(root) / ".kb" / "state" / "feedback_state.json"
    if not sp.exists():
        return {}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _prop_path(root: Union[str, Path]) -> Path:
    return Path(root) / "pipeline" / PROPOSAL_FILE


def _applied_path(root: Union[str, Path]) -> Path:
    return Path(root) / "pipeline" / APPLIED_FILE


def _read_applied(root: Union[str, Path]) -> Dict[str, Any]:
    p = _applied_path(root)
    if not p.exists():
        return {"applied": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"applied": {}}


# ── 接地阀（P0-1，复用 kb_rsi 指标）───────────────────────────
def grounding(root: Union[str, Path]) -> Tuple[bool, str, Dict[str, Any]]:
    """返回 (grounded, note, metrics)。grounded = 新鲜外部源≥5% 且 跨域≥15%。"""
    m = kb_rsi.metrics(kb_rsi.collect(root), root=root)
    if not m:
        return False, "库为空或无编译笔记，无法评估接地", {}
    grounded = m["external_inflow"] >= EXTERNAL_FRESH_MIN_PCT and \
        m["cross_ratio"] >= EXTERNAL_MIN_CROSS
    if grounded:
        note = f"外部接地正常（新鲜外部源 {m['external_inflow']}%≥{EXTERNAL_FRESH_MIN_PCT}% · 跨域 {m['cross_ratio']}≥{EXTERNAL_MIN_CROSS}）"
    else:
        note = (f"⚠️ 外部接地不足（新鲜外部源 {m['external_inflow']}%<{EXTERNAL_FRESH_MIN_PCT}% "
                f"或 跨域 {m['cross_ratio']}<{EXTERNAL_MIN_CROSS}）→ 知识库疑似自闭，"
                f"冻结内部人气提升，仅接受外部显式有用反馈回写")
    return grounded, note, m


# ── 信号通道 ─────────────────────────────────────────────────
def _feedback_signals(state: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """(external_useful_count, internal_hits_count)。外部 = 显式 useful；内部 = RAG 命中计数。"""
    external = defaultdict(int)
    for fb in state.get("explicit_feedback", []):
        if fb.get("useful"):
            external[fb.get("hit")] += 1
    internal = dict(state.get("hits", {}) or {})
    return dict(external), internal


def _inbound_links(root: Union[str, Path]) -> Dict[str, int]:
    """入链计数：多少篇编译笔记用 [[...]] 引用了 rel（枢纽信号，结构·弱锚）。有界截断。"""
    inb = defaultdict(int)
    info = {n["rel"]: n for n in kb_rsi.collect(root) if n["kind"] != "raw"}
    for n in info.values():
        for link in n["links"]:
            tgt = link.split("#")[0].split("|")[0]
            if tgt in info:
                inb[tgt] += 1
    return dict(inb)


def _l1_signal(root: Union[str, Path]) -> Dict[str, int]:
    """L1 重问命中：kb_usage 标出的“窗口内被重问”事件中，作为命中的次数（真实使用行为，接 L1）。
    best-effort：kb_usage 缺失/无事件 → 空 dict，绝不报错。"""
    out: Dict[str, int] = defaultdict(int)
    try:
        import kb_usage
        a = kb_usage.analyze(root, use_embedding=False, use_llm=False)
        for s in a.get("suspect_failed", []):
            for p in s.get("paths", []):
                if p:
                    out[p] += 1
    except (ImportError, OSError, AttributeError, TypeError, KeyError, ValueError):
        pass
    return dict(out)


def _note_imp(root, rel) -> Optional[float]:
    """读取笔记当前 importance；非数值/缺失 → None（表示该笔记不可参与加权）。"""
    p = Path(root) / rel
    if not p.exists():
        return None
    try:
        fm, _, _ = kb_rsi.load_note(p)
    except (OSError, UnicodeDecodeError):
        return None
    if is_raw_rel(rel):
        return None
    kb_action = fm.get("kb_action", "") or ""
    if kb_action == "retire":
        return None
    try:
        return _parse_importance(fm.get("importance"))
    except (ValueError, AttributeError, TypeError):
        return None


# ── 融合信号（外部锚 + 真实使用，接地门控）────────────────────
def signal(root: Union[str, Path]) -> Dict[str, Any]:
    """计算每篇可加权的编译笔记的融合信号，返回汇总（供 propose / report 复用）。
    返回: {grounded, ground_note, signals: {rel: {...}}, grounded_metric}."""
    grounded, ground_note, metric = grounding(root)
    state = _state(root)
    external, internal = _feedback_signals(state)
    inbound = _inbound_links(root)
    l1 = _l1_signal(root)

    notes = kb_rsi.compiled_notes(root)
    sigs: Dict[str, Dict[str, Any]] = {}
    for n in notes:
        rel = n["rel"]
        if is_raw_rel(rel):
            continue
        if (n.get("kb_action") or "") == "retire":
            continue
        ext = external.get(rel, 0)
        hits = internal.get(rel, 0)
        inb = inbound.get(rel, 0)
        l1m = l1.get(rel, 0)
        # 接地门控：自闭库时冻结“内部人气”，只允许“外部显式有用”回写（它本就是接地）
        if grounded:
            raw = (EXT_WEIGHT * ext
                   + INT_WEIGHT * min(hits, HIT_CAP)
                   + L2_LINK_BONUS * inb
                   + L2_L1_MISS_BONUS * l1m)
            gate = "接地OK · 外部反馈+命中+入链+L1重问"
        else:
            raw = EXT_WEIGHT * ext        # 仅外部显式有用
            gate = "接地不足·仅外部显式有用反馈（人气冻结）"
        if not grounded and ext == 0:
            continue  # 自闭且无外部锚 → 该笔记不参与
        sigs[rel] = {"ext": ext, "hits": hits, "inbound": inb, "l1_miss": l1m,
                     "raw": round(raw, 3), "gate": gate}
    return {"grounded": grounded, "ground_note": ground_note,
            "metric": metric, "signals": sigs}


# ── 提议生成 ─────────────────────────────────────────────────
def propose(root: Union[str, Path]) -> Dict[str, Any]:
    """融合信号 → 提议（只写 jsonl，不改库）。每条含 old/new importance、delta、门控理由。
    排序（确定性）：外部锚(ext) > 融合信号(raw) > rel 路径；id 稳定。"""
    s = signal(root)
    sigs = s["signals"]
    out: Dict[str, Any] = {
        "grounded": s["grounded"], "ground_note": s["ground_note"],
        "n_signals": len(sigs), "n_proposals": 0, "proposals": [],
    }
    if not s["grounded"] and not any(v["ext"] > 0 for v in sigs.values()):
        out["note"] = f"外部接地不足 → 冻结自我加权：{s['ground_note']}"
        _write_proposals(root, [])
        return out

    cand = [dict(rel=r, **v) for r, v in sigs.items() if v["raw"] >= L2_SIGNAL_MIN]
    if not cand:
        out["note"] = f"无达门槛(L2_SIGNAL_MIN={L2_SIGNAL_MIN})的提升项"
        _write_proposals(root, [])
        return out

    maxs = max(c["raw"] for c in cand) or 1.0
    proposals: List[Dict[str, Any]] = []
    for c in cand:
        old = _note_imp(root, c["rel"])
        if old is None:
            continue
        delta = round(min(BUMP_CAP, (c["raw"] / maxs) * BUMP_CAP), 2)
        if c["ext"] > 0 and delta < DIVERSITY_FLOOR:
            delta = round(DIVERSITY_FLOOR, 2)   # 冷门但有真实反馈 → 地板提升
        if delta < MIN_BUMP:
            continue
        new_imp = round(min(1.0, old + delta), 2)
        proposals.append({
            "rel": c["rel"], "old_importance": round(old, 2), "delta": delta,
            "new_importance": new_imp,
            "signal": c["raw"], "ext": c["ext"], "hits": c["hits"],
            "inbound": c["inbound"], "l1_miss": c["l1_miss"],
            "gate": c["gate"],
            "reason": (f"融合信号 {c['raw']:.2f}（外部{c['ext']}·命中{c['hits']}·入链{c['inbound']}·L1重问{c['l1_miss']}）"),
        })
    proposals.sort(key=lambda p: (-p["ext"], -p["signal"], p["rel"]))
    for i, p in enumerate(proposals, 1):
        p["id"] = i
    _write_proposals(root, proposals)
    out["n_proposals"] = len(proposals)
    out["proposals"] = proposals
    out["note"] = (f"接地{'正常' if s['grounded'] else '不足'} · 提议 {len(proposals)} 篇提升"
                   "（人工在环，未改库）")
    # 演进日志（T2 加权事件）
    try:
        import evolution_log
        evolution_log.append(root, "T2",
                             f"L2 自我加权提议 {len(proposals)} 篇 · 接地{'OK' if s['grounded'] else '不足'}",
                             detail=s["ground_note"])
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return out


def _write_proposals(root: Union[str, Path], proposals: List[Dict[str, Any]]) -> None:
    p = _prop_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in proposals)
        + ("\n" if proposals else ""),
        encoding="utf-8")


# ── 写回：importance 只升（本地 frontmatter writer，与 feedback_loop 同行为）────
def _write_importance(p: Path, new_imp: float) -> None:
    text = p.read_text(encoding="utf-8")
    m = re.compile(r"^\s*---\s*$", re.M).search(text)
    if not m:
        return
    rest = text[m.end():]
    e = re.compile(r"^\s*---\s*$", re.M).search(rest)
    block = rest[:e.start()] if e else rest
    body = (text[e.end():] if e else "")
    nl = re.compile(r"^importance:\s*([0-9.]+)", re.M)
    if nl.search(block):
        block = nl.sub(f"importance: {new_imp:.2f}", block, count=1)
    else:
        block = f"importance: {new_imp:.2f}\n" + block
    p.write_text("---\n" + block + "\n---\n" + body, encoding="utf-8")


# ── apply：人工确认后写 frontmatter（单次原子 checkpoint，可回滚）────────────
def apply(root: Union[str, Path], ids: Optional[List[str]] = None,
          force: bool = False) -> Dict[str, Any]:
    """人工确认后，把选定提案的 importance 写回 frontmatter。
    只升不降、单提案 delta≤BUMP_CAP、累计封顶 1.0、raw/ 不改、写前一次提交。
    id 归一化：提案存 int、CLI 传串 → 统一转 int 比对（与 kb_contradiction 同约定）。"""
    p = _prop_path(root)
    if not p.exists():
        return {"written": [], "skipped": [], "applied_ledger": 0,
                "note": "无待审提议；先 run propose"}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if ids is None and force:
        pick = recs
    else:
        want = set()
        for x in (ids or []):
            try:
                want.add(int(x))
            except (TypeError, ValueError):
                pass
        pick = [r for r in recs if r.get("id") in want]

    ledger = _read_applied(root)
    applied: Dict[str, float] = ledger.get("applied", {})
    applied_ids: set = set(ledger.get("ids", []))
    written, skipped, rels = [], [], []
    for r in pick:
        rid = r.get("id")
        if rid in applied_ids:
            skipped.append({"id": rid, "note": r.get("rel"), "reason": "已写回(幂等)"}); continue
        rel = r.get("rel")
        if not rel:
            skipped.append({"id": rid, "reason": "缺 rel"}); continue
        if is_raw_rel(rel):
            skipped.append({"id": rid, "note": rel, "reason": "raw/ 不可变"}); continue
        if Path(rel).parts[:1] == ("",):  # 防御：rel 路径异常
            skipped.append({"id": rid, "note": rel, "reason": "路径异常"}); continue
        old = _note_imp(root, rel)
        if old is None:
            skipped.append({"id": rid, "note": rel, "reason": "importance 非数值/已 retire"}); continue
        cum = float(applied.get(rel, 0.0))
        remaining = min(BUMP_CAP - cum, 1.0 - old)
        actual = min(float(r.get("delta", 0.0)), remaining)
        if actual < MIN_BUMP:
            skipped.append({"id": rid, "note": rel, "reason": f"累计已达封顶(已{cum:.2f}，剩余{remaining:.2f})"}); continue
        new_imp = round(min(1.0, old + actual), 2)
        _write_importance(Path(root) / rel, new_imp)
        applied[rel] = round(cum + actual, 3)
        applied_ids.add(rid)
        rels.append(rel)
        written.append({"id": rid, "rel": rel, "old": old, "new": new_imp, "delta": round(actual, 3)})

    # 写累计账本（transient，force-add 作回滚锚点）
    ap_path = _applied_path(root)
    ap_path.parent.mkdir(parents=True, exist_ok=True)
    ap_path.write_text(json.dumps({"applied": applied, "ids": sorted(applied_ids),
                                   "updated": datetime.now().isoformat(timespec="seconds")},
                                  ensure_ascii=False, indent=2), encoding="utf-8")

    if written:
        for rel in rels:
            subprocess.run(["git", "-C", str(root), "add", "--", rel], capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "--force", "--", str(ap_path)], capture_output=True)
        if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "-C", str(root), "commit", "-q",
                            "-m", f"kb: L2 自我加权 {len(written)} 篇 importance(只升不降·接地门·可回滚)"],
                           capture_output=True)
    try:
        import evolution_log
        if written:
            evolution_log.append(root, "T2",
                                 f"L2 自我加权写回 {len(written)} 篇",
                                 detail="、".join(f"{w['id']} {w['rel'].split('/')[-1][:20]}…" for w in written))
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return {"written": written, "skipped": skipped,
            "applied_ledger": len(applied),
            "note": f"写入 {len(written)} 条 · 跳过 {len(skipped)} 条"}


def status(root: Union[str, Path]) -> Dict[str, Any]:
    p = _prop_path(root)
    if not p.exists():
        return {"count": 0, "proposals": [], "grounded": None}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    try:
        g, _, _ = grounding(root)
    except Exception:
        g = None
    return {"count": len(recs), "proposals": recs, "grounded": g}


# ── 渲染 ────────────────────────────────────────────────────
def render(root: Union[str, Path], summary: Optional[Dict[str, Any]] = None) -> str:
    s = signal(root)
    st = status(root)
    L = ["# ⚖️ L2 自我加权（反馈阶梯 · 代谢回路）", "",
         f"> 接地阀: **{s['grounded']}** — {s['ground_note']}",
         f"> 候选加权笔记 {len(s['signals'])} 条 · 外部锚=显式有用 > 真实使用(接L1) > RAG人气(封顶) > 入链"]
    if not st["proposals"]:
        L += ["", "✅ 暂无达门槛的提升提议（或接地不足已冻结内部人气）→ `apply --id N` 写回。"]
        return "\n".join(L) + "\n"
    L += ["", "## 🔥 待写回提升（人工确认 `apply --id N`）", ""]
    for p in st["proposals"]:
        L += [f"- **`{p['id']}` {p['rel']}**  +{p['delta']:.2f}（{p['old_importance']:.2f}→{p['new_importance']:.2f}）",
              f"    - 信号 {p['signal']:.2f}（外部{p['ext']}·命中{p['hits']}·入链{p['inbound']}·L1重问{p['l1_miss']}）· {p['gate']}"]
    L += ["", "> 铁律：importance 只升不降、单提案 delta≤BUMP_CAP、累计封顶 1.0、raw/ 不改、写前 checkpoint 可回滚。"]
    return "\n".join(L) + "\n"


def _render_status(root: Union[str, Path]) -> str:
    return render(root)


# ── CLI ─────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="kb_selfweight: L2 自我加权（反馈阶梯·代谢回路）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("report", "propose", "apply", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=ROOT_DEFAULT)
        if name in ("apply", "status"):
            p.add_argument("--json", action="store_true")
        if name == "apply":
            p.add_argument("--id", nargs="*", default=None, help="指定提案ID")
            p.add_argument("--all", action="store_true", help="写回全部待审提议")
    args = ap.parse_args()

    if args.cmd == "report":
        s = signal(args.root)
        st = status(args.root)
        print(f"🧭 接地阀: {'✅ OK' if s['grounded'] else '⚠️ 不足（冻结内部人气）'} — {s['ground_note']}")
        print(f"🔢 候选加权笔记 {len(s['signals'])} 条 · 待写回提议 {st['count']} 条")
        if not s["grounded"]:
            print("⏹️  自我加权已冻结内部人气提升；仅外部显式有用反馈可回写。")
        return 0
    if args.cmd == "propose":
        r = propose(args.root)
        print(f"🧭 接地{r['grounded']} · 候选 {r['n_signals']} 篇 · 提议 {r['n_proposals']} 条提升")
        print(f"   {r['note']}  → {_prop_path(args.root).name}")
        return 0
    if args.cmd == "apply":
        force = bool(args.all)
        res = apply(args.root, ids=None if force else args.id, force=force)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"✅ 已写回 {len(res['written'])} 篇 · 跳过 {len(res['skipped'])} 条")
        return 0
    if args.cmd == "status":
        st = status(args.root)
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(_render_status(args.root))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
