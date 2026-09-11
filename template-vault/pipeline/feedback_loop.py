#!/usr/bin/env python3
# ============================================================
# feedback_loop.py —— 反馈回路引擎(成长 引擎②)
#   把 RAG 命中当作"需求信号":每篇笔记被多少查询命中 = 价值信号
#   (命中计数 PageRank)。累积成 importance 上浮。
#     ingest : 跑查询→累计命中→给出建议提升(默认,不改文件)
#     apply  : 把提升写进 frontmatter(importance 只升不降,上限 BUMP_CAP,
#              写前 checkpoint,可回滚)
#   参数:MIN_HITS 命中门槛 / BUMP_CAP 单笔记累计上限 / MIN_BUMP 最小提升
# 用法:
#   python3 pipeline/feedback_loop.py ingest [--root R]
#   python3 pipeline/feedback_loop.py apply  [--root R]
# ============================================================
import argparse, os, re, sys, json, math, datetime
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT, load_note


def _parse_importance(s):
    """robustly parse frontmatter 'importance' to float.
    Handles numeric ("0.8"), range/placeholder ("0.0-1.0"), wikilink-ish
    ("importance:: 0.8"), empty/missing -> always returns a float (0.0 on failure).
    Used by intake/recall/feedback to avoid float() crashes on placeholders.
    """
    try:
        v = str(s)
    except (TypeError, ValueError):
        return 0.0
    m = re.search(r"[-+]?\d*\.?\d+", v)
    return float(m.group()) if m else 0.0

STATE = ".feedback_state.json"
IDX_DIR = "vector index"
MIN_HITS = 3        # 命中>=3 才视为有价值信号
BUMP_CAP = 0.30     # 单笔记 importance 累计提升上限(防刷)
MIN_BUMP = 0.05     # 不足此提升不写

# ── 命中反馈权重设计（FR-3.1.3）────────────────────────────
# 显式 useful: +1.0（最强信号，agent 明确说这条有用）
# explicit_not_useful: +0.0（记录但不累积）
# auto_session: +0.3（rag.py --session 自动记录）
# query_only: +0.1（仅调用未反馈，弱信号）
# internal_self: +0.0（废弃，内部自引用不再累积 importance）
HIT_WEIGHTS = {
    "explicit_useful": 1.0,
    "explicit_not_useful": 0.0,
    "auto_session": 0.3,
    "query_only": 0.1,
    "internal_self": 0.0,
}
# 显式反馈的 importance 累积因子（单次 useful → +0.05）
EXPLICIT_NORM_FACTOR = 0.05

try:
    import rag
except (ImportError, ModuleNotFoundError, OSError):
    rag = None


def load_index(root):
    idx = Path(root) / IDX_DIR / "df_idf.json"
    if not idx.exists() or rag is None:
        return None
    payload = json.loads(idx.read_text(encoding="utf-8"))
    rel2vec = {r: rag._vec(payload["tf"][r], payload["vocab"], payload["idf"])
               for r in payload["tf"]}
    return {"rel2vec": rel2vec, "vocab": payload["vocab"], "idf": payload["idf"]}


def hits_for(idx, query, topk):
    """进程内:以 query 检索,返回 score>0 的 top-k 命中 rel。"""
    if idx is None or not query:
        return []
    qv = rag._vec(Counter(rag.tokenize(query)), idx["vocab"], idx["idf"])
    scored = [(r, rag._cos(v, qv)) for r, v in idx["rel2vec"].items()]
    scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored[:topk]]


def load_state(root):
    # P2: 改用 state_manager（FR-3.3.6 + FR-3.1.7 文件锁）
    try:
        from state_manager import StateStore
        return StateStore(root).load("feedback_state.json", {"hits": {}})
    except ImportError:
        sp = Path(root) / "pipeline" / STATE
        return json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"hits": {}}


def save_state(root, st):
    try:
        from state_manager import StateStore
        StateStore(root).save("feedback_state.json", st)
    except ImportError:
        sp = Path(root) / "pipeline" / STATE
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def ingest(root):
    idx = load_index(root)
    state = load_state(root)
    hits = state.setdefault("hits", {})
    sources = 0
    if rag:
        for p in rag.iter_notes(root):
            rel = str(p.relative_to(root))
            fm, body = rag.load_meta(p)  # rag.load_meta 返回 (fm, body)
            if fm.get("kb_action") == "retire":
                continue
            try:
                imp = _parse_importance(fm.get("importance"))
            except (ValueError, AttributeError):
                continue  # importance 非数值(占位符/空/wikilink)的笔记跳过
            if imp < 0.1:
                continue  # 报表/产物不参与信号
            q = fm.get("kb_summary") or body.strip().splitlines()[0].strip()[:80]
            if not q:
                continue
            for h in hits_for(idx, q, 5):
                hits[h] = hits.get(h, 0) + 1
            sources += 1
    state["last_ingest"] = _today()
    state["run_queries"] = sources
    save_state(root, state)
    return hits, sources


def bumps(hits):
    """命中 → importance 提升建议(累计, capped BUMP_CAP)。"""
    maxh = max(hits.values()) if hits else 1
    out, zero = [], []
    for rel, hc in hits.items():
        if hc < MIN_HITS:
            continue
        norm = hc / maxh
        delta = round(min(BUMP_CAP, norm * BUMP_CAP), 2)
        if delta >= MIN_BUMP:
            out.append({"rel": rel, "hits": hc, "norm": round(norm, 2), "delta": delta})
    hits_sorted = sorted(hits.values(), reverse=True)
    zero_n = sum(1 for v in hits.values() if v < MIN_HITS)
    out.sort(key=lambda x: x["hits"], reverse=True)
    return out, zero_n


def report(hits, sources, suggested, zero_n):
    if not hits:
        return "📡 命中信号为空(索引缺失或无可用笔记?)\n"
    out = ["# 📡 反馈回路 · 命中信号", "",
           f"> 查询源 {sources} 篇 · 累计命中笔记 {len(hits)} 个 · 建议提升 {len(suggested)} 篇",
           f"> MIN_HITS={MIN_HITS} · BUMP_CAP={BUMP_CAP} · 仅升不降(写前 checkpoint 可回滚)"]
    if not suggested:
        out += ["\n✅ 本次无达门槛(≥%d 命中)的提升项" % MIN_HITS]
        return "\n".join(out) + "\n"
    out += ["\n## 🔥 建议提升 importance(Top 15)"]
    for b in suggested[:15]:
        out += [f"- **{b['rel']}**  命中{b['hits']}次 · +{b['delta']:.2f}「{b['norm']:.2f}」"]
    out += [f"\n> 命中不足 {MIN_HITS} 次(可能是冷门但有用的知识)的笔记: {zero_n} 篇"]
    out += ["\n> 提示:运行 `apply` 将上述提升写入 frontmatter(importance 只升不降)。"]
    return "\n".join(out) + "\n"


def apply_bumps(root):
    state = load_state(root)
    hits = state.get("hits", {})
    applied = state.setdefault("applied", {})
    suggested, _ = bumps(hits)
    if not suggested:
        print("✅ 无达门槛的提升项(需命中 ≥%d 次)" % MIN_HITS)
        return 0
    changed = []
    for b in suggested:
        rel = b["rel"]
        p = Path(root) / rel
        if not p.exists():
            continue
        fm, text, body = _read(p)
        try:
            old = _parse_importance(fm.get("importance"))
        except ValueError:
            continue
        remaining = BUMP_CAP - float(applied.get(rel, 0))
        actual = min(b["delta"], remaining, 1.0 - old)
        if actual < MIN_BUMP:
            continue
        new_imp = round(min(1.0, old + actual), 2)
        _write_importance(p, new_imp)
        applied[rel] = round(float(applied.get(rel, 0)) + actual, 3)
        changed.append({"rel": rel, "old": old, "new": new_imp, "delta": round(actual, 3)})
    state["applied"] = applied
    save_state(root, state)
    if not changed:
        print("✅ 无达门槛的提升项")
        return 1
    rels = [c["rel"] for c in changed]
    import subprocess as sp
    sp.run(["git", "-C", root, "add", "--", *rels], capture_output=True)
    if sp.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        sp.run(["git", "-C", root, "commit", "-q",
                "-m", f"kb: 反馈回路 bump {len(rels)} 篇 importance(只升不降,可回滚)"],
               capture_output=True)
    for c in changed:
        print(f"✅ {c['rel']}  importance {c['old']:.2f} → {c['new']:.2f} (+{c['delta']})")
    print(f"✅ 已提升 {len(changed)} 篇(累计提升记于 pipeline/{STATE},可手改/删除回滚)")
    return 0


def _today():
    return datetime.date.today().isoformat()


def _read(p):
    return load_note(p)


def _write_importance(p, new):
    """把笔记 importance 设为 new(只增路径,写前须先算实际增量)。"""
    text = p.read_text(encoding="utf-8")
    m = re.compile(r"^---\s*$", re.M).search(text)
    if not m:
        return
    end = re.compile(r"^---\s*$", re.M).search(text, m.end())
    block, body = (text[m.end():end.start()] if end else ""), (text[end.end():] if end else text[m.end():])
    nl = re.compile(r"^importance:\s*([0-9.]+)", re.M)
    if nl.search(block):
        block = nl.sub(f"importance: {new:.2f}", block, count=1)
    else:
        block = f"importance: {new:.2f}\n" + block
    # 补全 frontmatter 边界：---\n<block>\n---\n<body>
    p.write_text("---\n" + block + "\n---\n" + body, encoding="utf-8")


# ── 命中反馈闭环（FR-3.1.2 + FR-3.1.3）──────────────────────
def record_hit(root, query, hit_path, useful):
    """记录 agent 显式命中反馈 + importance 累积。

    Args:
        root: vault 根路径
        query: 查询文本
        hit_path: 命中笔记的相对路径
        useful: True=有用（权重 1.0）/ False=无用（权重 0.0，记录但不累积）
    """
    weight = HIT_WEIGHTS["explicit_useful"] if useful else HIT_WEIGHTS["explicit_not_useful"]
    state = load_state(root)
    state.setdefault("explicit_feedback", []).append({
        "query": query,
        "hit": hit_path,
        "useful": useful,
        "timestamp": datetime.datetime.now().isoformat(),
        "weight": weight,
    })
    save_state(root, state)
    if weight > 0:
        _accumulate_importance(root, hit_path, weight)
    return weight


def _accumulate_importance(root, rel, weight, norm_factor=None):
    """importance 累积：new_imp = min(1.0, old_imp + weight * norm_factor)。

    只升不降，上限 1.0。写前 git commit checkpoint。
    """
    if norm_factor is None:
        norm_factor = EXPLICIT_NORM_FACTOR
    p = Path(root) / rel
    if not p.exists():
        return False
    try:
        fm, text, body = load_note(p)
        old = _parse_importance(fm.get("importance"))
    except (OSError, ValueError):
        return False
    delta = weight * norm_factor
    new_imp = min(1.0, old + delta)
    if new_imp <= old:
        return False
    # 写前 checkpoint
    import subprocess as sp
    sp.run(["git", "-C", root, "add", "-u"], capture_output=True)
    if sp.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        sp.run(["git", "-C", root, "commit", "-q", "-m", "pre-feedback checkpoint"], capture_output=True)
    _write_importance(p, round(new_imp, 4))
    sp.run(["git", "-C", root, "add", "--", rel], capture_output=True)
    sp.run(["git", "-C", root, "commit", "-q",
            "-m", f"kb: feedback hit importance {old:.2f}→{new_imp:.2f}"], capture_output=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest"); ing.add_argument("--root", default=ROOT_DEFAULT)
    apb = sub.add_parser("apply"); apb.add_argument("--root", default=ROOT_DEFAULT)
    # 新增 hit 子命令（FR-3.1.2）
    hit = sub.add_parser("hit")
    hit.add_argument("--query", required=True)
    hit.add_argument("--hit", required=True, dest="hit_path")
    hit.add_argument("--useful", required=True)
    hit.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()
    if args.cmd == "ingest":
        hits, sources = ingest(args.root)
        suggested, zero_n = bumps(hits)
        rep = report(hits, sources, suggested, zero_n)
        (Path(args.root) / "feedback_hits.md").write_text(rep, encoding="utf-8")
        print(f"📡 ingest 完成: {sources} 查询源 · {len(suggested)} 篇建议提升  已写入 feedback_hits.md")
        print("\n" + rep)
        return 0
    if args.cmd == "apply":
        return apply_bumps(args.root)
    if args.cmd == "hit":
        useful = str(args.useful).lower() in ("true", "1", "yes")
        weight = record_hit(args.root, args.query, args.hit_path, useful)
        print(f"✅ 已记录命中反馈: {args.hit_path} useful={useful} weight={weight}")
        if useful:
            print(f"   importance 已累积（+{weight * EXPLICIT_NORM_FACTOR:.4f}）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
