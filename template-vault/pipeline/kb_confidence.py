#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_confidence.py — 复合置信分（P3）。

为什么需要：现在所有正典在检索里**同等可信**。但事实上有的是原文逐字抄来的，
有的是归纳的，有的是模型推的；有的只被一条记忆佐证，有的被五条；有的一周前刚确认，
有的已经放了一年。把它们一视同仁，就是让"猜的"和"抄的"平起平坐。

Engram 的做法（本模块照搬其四因子，权重可审计）：
    confidence = 抽取方式×0.50 + 佐证数×0.25 + 新近×0.15 + 校验通过×0.10

四个因子分别回答：
    extraction    这条是抄的、归纳的、还是推的？（verbatim 1.0 / extracted 0.9 / inferred 0.6）
    corroboration 有几条原记忆支持它？（1→0.4，2→0.6，≥4→1.0）
    recency       最近一次确认距今多久？（90 天内 1.0，线性衰减到 1 年）
    verification  三关校验通过项占比（接地率 + 标识符覆盖 + 有页型）

纪律：
  · **可解释**：`explain()` 必须能回答"为什么是 0.73"，不是只给一个数。
  · **只读优先**：默认 dry-run；`apply` 才写回 frontmatter 的 `confidence`。
  · 分档：`>=CONF_PUBLISH(0.55)` 正常；`<0.55` 建议人工复核；`<0.35` 只留证据层。

用法:
  python3 pipeline/kb_confidence.py show  --root R --rel <笔记> [--json]
  python3 pipeline/kb_confidence.py audit --root R [--json] [--limit N]   # 全库打分 + 分布
  python3 pipeline/kb_confidence.py apply --root R [--dry-run] [--limit N]
"""
import argparse, json, re, sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import ROOT_DEFAULT, iter_notes, load_note_full as load, is_raw_path, is_generated_report

W = {"extraction": 0.50, "corroboration": 0.25, "recency": 0.15, "verification": 0.10}
CONF_PUBLISH = 0.55          # 低于此 → 建议人工复核
CONF_EVIDENCE_ONLY = 0.35    # 低于此 → 只留证据层（降出主检索）
RECENCY_FULL_DAYS = 90
RECENCY_ZERO_DAYS = 365


def _score_abs(fm: Dict[str, Any], body: str, today: Optional[date] = None) -> float:
    """只算绝对分（给 population_cutoffs 用，避免递归）。"""
    srcs = fm.get("source_ref")
    n_src = len(srcs) if isinstance(srcs, list) else (1 if srcs else 0)
    e, _ = _basis_field(body)
    c, _ = _corroboration(n_src)
    r, _ = _recency(fm.get("updated") or fm.get("created"), today)
    v, _ = _verification(fm)
    return round(W["extraction"] * e + W["corroboration"] * c + W["recency"] * r + W["verification"] * v, 3)


def _basis_field(body: str) -> Tuple[float, Optional[str]]:
    """抽取方式因子：看正典正文里有多少条是"推断"（`^[推断]`）。"""
    lines = [l for l in str(body or "").splitlines() if l.strip().startswith("-")]
    if not lines:
        return 0.9, None                       # 无列表 → 按归纳处理
    inferred = sum(1 for l in lines if "^[推断]" in l)
    if not inferred:
        return 0.9, None
    ratio = inferred / len(lines)
    # 连续：0% 推断 → 0.9；≥50% 推断 → 0.6
    val = round(0.9 - 0.6 * min(ratio, 0.5), 3)
    return val, f"{inferred}/{len(lines)}={ratio:.0%} 条标为推断"


def _corroboration(n_sources: int) -> Tuple[float, str]:
    """佐证因子：支持该结论的原记忆条数（对数连续，避免粗档位丢分辨率）。"""
    if n_sources <= 0:
        return 0.1, "无 source_ref（无法回溯）"
    import math
    # 1→0.35 · 2→0.55 · 3→0.7 · 4→0.75 · 8+→1.0
    val = min(1.0, 0.35 + 0.65 * math.log2(n_sources) / 3.0)
    return round(val, 3), f"{n_sources} 条原记忆"


def _recency(updated: Any, today: Optional[date] = None) -> Tuple[float, str]:
    """新近因子：最近一次确认距今多久。"""
    today = today or date.today()
    s = str(updated or "")[:10]
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return 0.7, "无有效 updated"
    age = (today - d).days
    if age <= RECENCY_FULL_DAYS:
        return 1.0, f"{age} 天前"
    if age >= RECENCY_ZERO_DAYS:
        return 0.15, f"{age} 天前（超一年）"
    span = RECENCY_ZERO_DAYS - RECENCY_FULL_DAYS
    return round(1.0 - 0.85 * (age - RECENCY_FULL_DAYS) / span, 3), f"{age} 天前"


def _verification(fm: Dict[str, Any]) -> Tuple[float, str]:
    """校验通过因子：接地事实数、页型、标识符覆盖、压缩比 —— 只计"确实存在"的信号。"""
    parts, notes = [], []
    g = fm.get("grounded_facts")
    if isinstance(g, list) and g:
        parts.append(min(1.0, len(g) / 5.0)); notes.append(f"接地 {len(g)} 条")
    elif isinstance(g, list):
        parts.append(0.2); notes.append("无接地事实")
    cov = fm.get("coverage")
    try:
        parts.append(min(1.0, float(cov))); notes.append(f"标识符覆盖 {float(cov):.0%}")
    except (TypeError, ValueError):
        pass
    cr = fm.get("canon_ratio")
    try:
        r = float(cr)
        parts.append(1.0 if 0.05 <= r <= 0.85 else 0.5); notes.append(f"压缩比 {r:.0%}")
    except (TypeError, ValueError):
        pass
    if str(fm.get("canon_type") or "").strip():
        parts.append(1.0); notes.append("有页型")
    if not parts:
        return 0.4, "无可校验信号"
    return round(sum(parts) / len(parts), 3), " · ".join(notes)


POP_CACHE: Dict[str, Tuple[float, float]] = {}


def population_cutoffs(root: Path, refresh: bool = False) -> Tuple[float, float]:
    """算 canon 群体的 p10 / p3 分位（相对分档用）；带缓存避免重复扫库。"""
    key = str(root)
    if not refresh and key in POP_CACHE:
        return POP_CACHE[key]
    vals = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel) or is_generated_report(rel):
            continue
        fm, _t, _b, body = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        v = _score_abs(fm, body)
        vals.append(v)
    vals.sort()
    if not vals:
        POP_CACHE[key] = (0.0, 0.0)
        return POP_CACHE[key]
    n = len(vals)
    POP_CACHE[key] = (vals[max(0, int(n * 0.10) - 1)], vals[max(0, int(n * 0.03) - 1)])
    return POP_CACHE[key]


def score(fm: Dict[str, Any], body: str, today: Optional[date] = None,
          pop_p10: Optional[float] = None, pop_p3: Optional[float] = None) -> Dict[str, Any]:
    """算一篇的复合置信分。返回含四因子明细与解释的可审计结构。"""
    srcs = fm.get("source_ref")
    n_src = len(srcs) if isinstance(srcs, list) else (1 if srcs else 0)
    e, e_note = _basis_field(body)
    c, c_note = _corroboration(n_src)
    r, r_note = _recency(fm.get("updated") or fm.get("created"), today)
    v, v_note = _verification(fm)
    total = W["extraction"] * e + W["corroboration"] * c + W["recency"] * r + W["verification"] * v
    total = round(total, 3)
    tier = ("publish" if total >= CONF_PUBLISH
            else ("review" if total >= CONF_EVIDENCE_ONLY else "evidence-only"))  # 先给绝对档
    # 相对档（在 canon 群体内比）：低于群体 p10 → review，低于 p3 → evidence-only。
    #   为什么需要：正典都过了三关，绝对分天然集中（实测 0.75–0.84），
    #   绝对阈值分不开；"相对同侪"才是可行动的信号。
    if pop_p10 is not None and total < pop_p10:
        tier = "evidence-only" if (pop_p3 is not None and total < pop_p3) else "review"
    return {
        "confidence": total,
        "tier": tier,
        "factors": {
            "extraction": {"value": e, "weight": W["extraction"], "note": e_note or "逐字/归纳"},
            "corroboration": {"value": c, "weight": W["corroboration"], "note": c_note},
            "recency": {"value": r, "weight": W["recency"], "note": r_note},
            "verification": {"value": v, "weight": W["verification"], "note": v_note},
        },
    }


def explain(root: Path, rel: str) -> Dict[str, Any]:
    fm, _t, _b, body = load(root / rel)
    p10, p3 = population_cutoffs(root)
    res = score(fm, body, pop_p10=p10, pop_p3=p3)
    res["pop_p10"], res["pop_p3"] = p10, p3
    res["rel"] = rel
    res["explain"] = " + ".join(
        f"{k} {d['value']}×{d['weight']}={round(d['value']*d['weight'], 3)}（{d['note']}）"
        for k, d in res["factors"].items())
    return res


def _upsert_fm(text: str, updates: Dict[str, Any]) -> Optional[str]:
    if not text.startswith("---"):
        return None
    m = re.search(r"^---\s*$", text, re.M)
    if not m:
        return None
    end = re.search(r"^---\s*$", text[m.end():], re.M)
    if not end:
        return None
    head = text[m.end():m.end() + end.start()]
    rest = text[m.end() + end.end():]
    lines = head.strip("\n").splitlines()
    done = set()
    for i, ln in enumerate(lines):
        for k, val in updates.items():
            if re.match(rf"^{re.escape(k)}\s*:", ln):
                lines[i] = f"{k}: {val}"
                done.add(k)
    for k, val in updates.items():
        if k not in done:
            lines.append(f"{k}: {val}")
    return "---\n" + "\n".join(lines) + "\n---\n" + rest


def cmd_audit(root: Path, limit: int, as_json: bool) -> int:
    rows, tiers = [], {"publish": 0, "review": 0, "evidence-only": 0}
    p10, p3 = population_cutoffs(root)
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel) or is_generated_report(rel):
            continue
        fm, _t, _b, body = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        r = score(fm, body, pop_p10=p10, pop_p3=p3)
        r["rel"] = rel
        r["stored"] = fm.get("confidence")
        r["explain"] = " + ".join(
            f"{k} {d['value']}×{d['weight']}={round(d['value']*d['weight'], 3)}"
            for k, d in r["factors"].items())
        rows.append(r)
        tiers[r["tier"]] += 1
    rows.sort(key=lambda x: x["confidence"])
    if as_json:
        print(json.dumps({"n": len(rows), "tiers": tiers, "rows": rows[:limit]},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"🎚 复合置信分审计：{len(rows)} 篇正典")
    print(f"   分档：publish {tiers['publish']} · review {tiers['review']} · evidence-only {tiers['evidence-only']}")
    print(f"   （绝对阈值：≥{CONF_PUBLISH} 发布 / ≥{CONF_EVIDENCE_ONLY} 待复核；"
          f"相对分档：群体 p10={p10:.3f} / p3={p3:.3f}）")
    n_infer = sum(1 for r in rows if "推断" in r["explain"])
    n_nosrc = sum(1 for r in rows if "无 source_ref" in r["explain"])
    n_stale = sum(1 for r in rows if "超一年" in r["explain"])
    if n_infer or n_nosrc or n_stale:
        print(f"   可行动信号：含推断标记 {n_infer} 篇 · 无 source_ref {n_nosrc} 篇 · 超一年未确认 {n_stale} 篇")
    print("\n   最低 8 篇：")
    for r in rows[:8]:
        print(f"   {r['confidence']:.3f} [{r['tier']:<13}] {r['rel'][:52]}")
        print(f"        {r['explain']}")
    print("\n   最高 3 篇：")
    for r in rows[-3:]:
        print(f"   {r['confidence']:.3f} [{r['tier']:<13}] {r['rel'][:52]}")
    return 0


def cmd_apply(root: Path, dry: bool, limit: int) -> int:
    """把算出的置信分写回 frontmatter（默认干跑）。"""
    changed = 0
    p10, p3 = population_cutoffs(root)
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel) or is_generated_report(rel):
            continue
        fm, _t, _b, body = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        r = score(fm, body, pop_p10=p10, pop_p3=p3)
        updates = {"confidence": r["confidence"], "confidence_tier": r["tier"]}
        text = p.read_text(encoding="utf-8")
        new = _upsert_fm(text, updates)
        if new and new != text:
            changed += 1
            if not dry:
                p.write_text(new, encoding="utf-8")
        if limit and changed >= limit:
            break
    print(f"{'🔍 dry-run' if dry else '✅ 已写回'}：{changed} 篇正典的 confidence / confidence_tier")
    if dry and changed:
        print("   应用：kb confidence apply")
    return 0


# ── P3：原则性遗忘（functional，读时计算，从不删文件）──────────────
DECAY_MIN_ACCESS = 1        # 低于此访问次数视为"从未被用到"
DECAY_STALE_DAYS = 120


def decay_verdict(root: Path, rel: str, fm: Dict[str, Any], body: str,
                  hits: int = 0, today: Optional[date] = None) -> Dict[str, Any]:
    """该篇是否该"退居证据层"（遗忘 = 降层，不是删除）。

    依据（Engram 的 Ebbinghaus 思路，简化可实现版）：
      · 长期零命中 + 从未被检索到（hits=0）
      · 陈旧（updated 超过 DECAY_STALE_DAYS）
      · 低重要度（importance < 0.4）
      · 低置信（confidence 分档非 publish）
    命中 3 项及以上 → 建议降层；文件与原文始终保留，`kb raw` 可随时调回。
    """
    today = today or date.today()
    sc = score(fm, body)
    upd = str(fm.get("updated") or fm.get("created") or "")[:10]
    try:
        age = (today - date.fromisoformat(upd)).days
    except ValueError:
        age = 0
    try:
        imp = float(fm.get("importance") or 0)
    except (TypeError, ValueError):
        imp = 0
    reasons = []
    if hits <= 0:
        reasons.append("从未被检索命中")
    if age > DECAY_STALE_DAYS:
        reasons.append(f"{age} 天未更新")
    if imp < 0.4:
        reasons.append(f"importance {imp}<0.4")
    if sc["tier"] != "publish":
        reasons.append(f"置信分档 {sc['tier']}")
    return {"rel": rel, "demote": len(reasons) >= 3, "reasons": reasons,
            "hits": hits, "age_days": age, "importance": imp,
            "confidence": sc["confidence"], "tier": sc["tier"]}


def _read_hits(root: Path) -> Dict[str, int]:
    """读反馈账本里的命中次数（kb feedback 写的 .kb/state/feedback_state.json）。"""
    for cand in (root / ".kb" / "state" / "feedback_state.json",
                 root / "pipeline" / ".feedback_state.json"):
        try:
            d = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hits = d.get("hits") or d.get("counts") or {}
        if isinstance(hits, dict):
            return {k: int(v.get("hits") if isinstance(v, dict) else v)
                    for k, v in hits.items() if isinstance(v, (dict, int))}
    return {}


def cmd_decay(root: Path, as_json: bool, limit: int) -> int:
    hits = _read_hits(root)
    rows = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel) or is_generated_report(rel):
            continue
        fm, _t, _b, body = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        rows.append(decay_verdict(root, rel, fm, body, hits.get(rel, 0)))
    cand = [r for r in rows if r["demote"]]
    if as_json:
        print(json.dumps({"canon": len(rows), "demote": len(cand), "rows": cand[:limit]},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"🌫 原则性遗忘审计：{len(rows)} 篇正典 → 建议降层 {len(cand)} 篇")
    print("   纪律：降层只是**退出主检索**，文件与原文始终保留（kb raw 可随时调回）")
    for r in cand[:limit]:
        print(f"   {r['rel'][:56]}")
        print(f"      理由：{' · '.join(r['reasons'])}")
    if not cand:
        print("   （没有同时满足「零命中 + 陈旧 + 低重要度 + 低置信」的笔记）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="复合置信分（P3）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("--root", default=ROOT_DEFAULT)
    s.add_argument("--rel", required=True); s.add_argument("--json", action="store_true", dest="as_json")
    a = sub.add_parser("audit"); a.add_argument("--root", default=ROOT_DEFAULT)
    a.add_argument("--json", action="store_true", dest="as_json"); a.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("apply"); p.add_argument("--root", default=ROOT_DEFAULT)
    p.add_argument("--dry-run", action="store_true", dest="dry"); p.add_argument("--limit", type=int, default=0)
    dc = sub.add_parser("decay"); dc.add_argument("--root", default=ROOT_DEFAULT)
    dc.add_argument("--json", action="store_true", dest="as_json"); dc.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "show":
        r = explain(root, args.rel)
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.as_json
              else f"🎚 {args.rel}\n   confidence = {r['confidence']} [{r['tier']}]\n   {r['explain']}")
        return 0
    if args.cmd == "audit":
        return cmd_audit(root, args.limit, args.as_json)
    if args.cmd == "apply":
        return cmd_apply(root, args.dry, args.limit)
    if args.cmd == "decay":
        return cmd_decay(root, args.as_json, args.limit)
    return 1


if __name__ == "__main__":
    sys.exit(main())
