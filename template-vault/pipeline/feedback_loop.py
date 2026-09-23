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
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import ROOT_DEFAULT, load_note, parse_importance as _parse_importance
from kb_constants import MIN_HITS, BUMP_CAP, MIN_BUMP
import kb_rsi  # P1-B: 复用其 load_note/vec（多样性感知所需的候选向量与领域）

STATE = ".feedback_state.json"
IDX_DIR = "vector index"
# MIN_HITS / BUMP_CAP / MIN_BUMP 已迁移至 kb_constants.py（单一权威源）

# ── P0-3: 加权信号模型（外部真反馈主锚 + RAG 次级 + 多样性 floor/cap）────
# 设计动机：纯 RAG 命中=内部人气代理，会 rich-get-richer（富者更富）→ 自闭环塌缩。
#   真价值信号 = 用户/agent 显式 useful 反馈（真实世界接地）。故：
#   - 外部显式反馈 = 主锚（EXT_WEIGHT 单次即强信号）；
#   - RAG 命中     = 次级人气代理，且 HIT_CAP 封顶（抑制富者更富）；
#   - 多样性地板   = 冷门但被外部验证过的笔记给最低提升，保护冷门有用知识；
#   - 多样性封顶   = 单轮最多 DIVERSITY_CAP 篇被提升，权重扩散不收敛到少数。
EXT_WEIGHT = 1.0        # 外部显式有用反馈权重(主锚)
INT_WEIGHT = 0.15       # RAG 命中权重(次级人气代理)
HIT_CAP = 5             # RAG 命中封顶值(超出部分不计，防富者更富)
DIVERSITY_CAP = 8       # 单轮最多提升篇数(权重扩散)
DIVERSITY_FLOOR = 0.05  # 冷门但有真实反馈的最低提升(保护冷门有用知识)

# ── P1-B: 多样性感知（防回声室收窄长尾）─────────────────────
# 偏好附着（rich-get-richer）会让 T2 把权重持续堆到同一少数领域的热门笔记 → 长尾收窄、
# 回声室。三件套（保留既有 BUMP_CAP/DIVERSITY_CAP 作为偏好附着刹车）：
#   1. 正交 novelty 项：低密度领域（被信号覆盖少）的笔记加权，挤入提升队列，保护长尾；
#   2. 相关低权重保底：与热门语义相关但自身信号低的笔记给最小保底提升，防被绞杀；
#   3. 领域占比刹车：主导领域笔记 novelty 项随其信号占比↑→0。
NOVELTY_BONUS = 0.10        # 正交 novelty 项最大权重
NOVELY_DENSITY_CAP = 0.60   # 领域信号占比超此值 → 其 novelty 项归零（刹车）
NOVELTY_SIM = 0.55          # "与热门相关"的余弦下限
NOVELTY_FLOOR = 0.06        # 相关低权重笔记的保底提升（>=MIN_BUMP 才有效）

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


def load_index(root: Union[str, Path]) -> Optional[Dict[str, Any]]:
    idx = Path(root) / IDX_DIR / "df_idf.json"
    if not idx.exists() or rag is None:
        return None
    payload = json.loads(idx.read_text(encoding="utf-8"))
    rel2vec = {r: rag._vec(payload["tf"][r], payload["vocab"], payload["idf"])
               for r in payload["tf"]}
    return {"rel2vec": rel2vec, "vocab": payload["vocab"], "idf": payload["idf"]}


def hits_for(idx: Optional[Dict[str, Any]], query: str, topk: int) -> List[str]:
    """进程内:以 query 检索,返回 score>0 的 top-k 命中 rel。"""
    if idx is None or not query:
        return []
    qv = rag._vec(Counter(rag.tokenize(query)), idx["vocab"], idx["idf"])
    scored = [(r, rag._cos(v, qv)) for r, v in idx["rel2vec"].items()]
    scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored[:topk]]


def load_state(root: Union[str, Path]) -> Dict[str, Any]:
    # P2: 改用 state_manager（FR-3.3.6 + FR-3.1.7 文件锁）
    try:
        from state_manager import StateStore
        return StateStore(root).load("feedback_state.json", {"hits": {}})
    except ImportError:
        sp = Path(root) / "pipeline" / STATE
        return json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"hits": {}}


def save_state(root: Union[str, Path], st: Dict[str, Any]) -> None:
    try:
        from state_manager import StateStore
        StateStore(root).save("feedback_state.json", st)
    except ImportError:
        sp = Path(root) / "pipeline" / STATE
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def ingest(root: Union[str, Path]) -> Tuple[Dict[str, int], int]:
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
    # P2-2: ingest 事件追加到统一演进日志（人可读 EVOLVED.md + 机器可读 jsonl）
    try:
        import evolution_log
        evolution_log.append(root, "INGEST",
                             f"喂入 {sources} 篇外部源 · 命中 {len(hits)} 笔记",
                             detail=f"新增外部源 {sources} 篇 · 加权依据")
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return hits, sources


def weighted_signal(external_count: Dict[str, int], internal_hits: Dict[str, int]) -> Dict[str, float]:
    """P0-3 主锚信号 = 外部真反馈(主) + RAG 命中(次级,封顶)。
    score = EXT_WEIGHT*外部反馈次数 + INT_WEIGHT*min(RAG命中, HIT_CAP)。"""
    rels = set(external_count) | set(internal_hits)
    out = {}
    for rel in rels:
        ext = external_count.get(rel, 0)
        inc = min(internal_hits.get(rel, 0), HIT_CAP)
        out[rel] = round(EXT_WEIGHT * ext + INT_WEIGHT * inc, 3)
    return out


def compute_signals(state: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, int], Dict[str, int]]:
    """从 state 计算 (signals, external_count, internal_hits)。
    external_count = 显式 useful 反馈计数；internal_hits = RAG 命中计数。"""
    external_count = defaultdict(int)
    for fb in state.get("explicit_feedback", []):
        if fb.get("useful"):
            external_count[fb.get("hit")] += 1
    internal_hits = dict(state.get("hits", {}))
    return weighted_signal(external_count, internal_hits), dict(external_count), internal_hits


def bumps(signals: Dict[str, float], external_count: Dict[str, int],
          internal_hits: Dict[str, int], root: Optional[Union[str, Path]] = None) -> Tuple[List[Dict[str, Any]], int]:
    """weighted signal → importance 提升建议(P0-3 外部主锚 + 多样性 floor/cap；P1-B 多样性感知)。

    流程：纯冷门(无外部反馈且低命中)→ 只计不升 → 对其余候选：
      1. 外部主锚加权(EXT·外部 + INT·RAG封顶)；
      2. P1-B 正交 novelty 项(低密度领域加权，挤入队列保护长尾)；
      3. P1-B 相关低权重保底(与热门语义相关但自身弱的笔记给 NOVELTY_FLOOR)；
      4. 过滤 MIN_BUMP → 按 (外部, score) 排序 → DIVERSITY_CAP 封顶。
    既有 BUMP_CAP/DIVERSITY_CAP 仍作为偏好附着刹车。
    """
    if not signals:
        return [], 0
    maxs = max(signals.values())
    if maxs <= 0:
        return [], 0
    cand, zero = [], []
    for rel, sc in signals.items():
        ext = external_count.get(rel, 0)
        hc = internal_hits.get(rel, 0)
        if hc < MIN_HITS and ext == 0:
            zero.append(rel)           # 纯冷门：无外部反馈又低命中 → 只计数不提升
            continue
        delta = round(min(BUMP_CAP, (sc / maxs) * BUMP_CAP), 2)
        if ext > 0 and delta < MIN_BUMP:
            delta = round(DIVERSITY_FLOOR, 2)   # 冷门但有真实反馈 → 地板提升
        cand.append({"rel": rel, "score": round(sc, 3), "external": ext,
                     "hits": hc, "delta": delta})
    # P1-B: 正交 novelty 项 + 相关低权重保底（都在过滤前，保护长尾/相关弱项）
    if root:
        _enhance_with_novelty(root, cand, maxs)
        _apply_related_floor(root, cand)
    # 过滤 + 排序 + 封顶
    out = [c for c in cand if c["delta"] >= MIN_BUMP]
    out.sort(key=lambda x: (x["external"], x["score"]), reverse=True)
    out = out[:DIVERSITY_CAP]            # 多样性封顶：单轮扩散
    return out, len(zero)


# 中文按字符单字、英文按词建向量（kb_rsi.vec 把整段中文当单一 token，致中文笔记间余弦恒近 0，
# 故相似度/相关判定用本地 _char_vec/_rcos，与 kb_contradiction 同约定）。
_CN_CHARS = re.compile(r"[\u4e00-\u9fff]")
_LAT = re.compile(r"[A-Za-z0-9]+")


def _char_vec(s):
    c = Counter()
    for w in _LAT.findall(s or ""):
        c[w.lower()] += 1
    for ch in _CN_CHARS.findall(s or ""):
        c[ch] += 1
    n = math.sqrt(sum(v * v for v in c.values())) or 1.0
    return {k: v / n for k, v in c.items()}


def _rcos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(av * b.get(k, 0) for k, av in a.items())


def _candidate_context(root, rels):
    """返回 {rel: (domain, vec)}，供多样性感知用（bounded，超限截断 300）。"""
    ctx, rels = {}, list(rels)[:300]
    for r in rels:
        try:
            fm, _, body = kb_rsi.load_note(Path(root) / r)
        except (OSError, UnicodeDecodeError):
            continue
        v = _char_vec(body + " " + " ".join(fm.get("tags", [])))
        ctx[r] = (fm.get("domain", "-"), v)
    return ctx


def _enhance_with_novelty(root, cand, maxs):
    """给候选加正交 novelty 项（低密度领域加权），按调整后 score 重算 delta（不降既有地板）。"""
    if not cand:
        return
    ctx = _candidate_context(root, [c["rel"] for c in cand])
    total = len(cand) or 1
    dom_counts = Counter(ctx.get(c["rel"], ("-", None))[0] for c in cand)
    for c in cand:
        d = ctx.get(c["rel"], ("-", None))[0]
        share = dom_counts.get(d, 0) / total
        base_delta = c["delta"]               # 既有地板(如外部冷门的 DIVERSITY_FLOOR)作下限
        novelty = NOVELTY_BONUS * max(0.0, 1.0 - share / NOVELY_DENSITY_CAP)
        c["score"] = round(c["score"] + novelty, 3)
        c["novelty"] = round(novelty, 3)
        c["delta"] = round(min(BUMP_CAP, max(base_delta, (c["score"] / maxs) * BUMP_CAP)), 2)


def _apply_related_floor(root, cand):
    """给与热门语义相关但自身 delta 低的笔记保底提升（NOVELTY_FLOOR），防偏好附着绞杀长尾。
    在过滤 MIN_BUMP 之前作用：让"与热门相关但自身信号弱"的笔记也有机会进入提升队列。"""
    if len(cand) <= 1:
        return
    ctx = _candidate_context(root, [c["rel"] for c in cand])
    vecs = {r: v for r, (d, v) in ctx.items()}
    hot = sorted(cand, key=lambda c: c["score"], reverse=True)
    for c in cand:
        if c["delta"] >= NOVELTY_FLOOR:
            continue
        vb = vecs.get(c["rel"])
        if not vb:
            continue
        for h in hot:
            if h["rel"] == c["rel"]:
                continue
            vh = vecs.get(h["rel"])
            if vh and _rcos(vb, vh) >= NOVELTY_SIM:
                c["delta"] = NOVELTY_FLOOR
                c["floor"] = True
                break
def report(hits: Dict[str, int], sources: int, suggested: List[Dict[str, Any]], zero_n: int) -> str:
    if not hits and not suggested:
        return "📡 命中信号为空(索引缺失或无可用笔记?)\n"
    out = ["# 📡 反馈回路 · 外部主锚信号", "",
           f"> 查询源 {sources} 篇 · 累计命中笔记 {len(hits)} 个 · 建议提升 {len(suggested)} 篇",
           f"> EXT_WEIGHT·外部主锚 + INT_WEIGHT·RAG次级(HIT_CAP封顶) · BUMP_CAP·多样性 floor/cap · 仅升不降(写前 checkpoint 可回滚)"]
    if not suggested:
        out += ["\n✅ 本次无达门槛(外部主锚+多样性门)的提升项（冷门{0}篇只计不升）".format(zero_n)]
        return "\n".join(out) + "\n"
    out += ["\n## 🔥 建议提升 importance(Top 15)（外部反馈优先）"]
    for b in suggested[:15]:
        out += [f"- **{b['rel']}**  外部反馈{b['external']}次·命中{b['hits']}次 · 价值{b['score']} · +{b['delta']:.2f}"]
    out += [f"\n> 冷门但可能有用的笔记（命中<{MIN_HITS}次且无外部反馈）: {zero_n} 篇"]
    out += ["\n> 提示:运行 `apply` 将上述提升写入 frontmatter(importance 只升不降)。"]
    return "\n".join(out) + "\n"


def apply_bumps(root: Union[str, Path]) -> int:
    state = load_state(root)
    applied = state.setdefault("applied", {})
    # P0-3：外部真反馈主锚 + RAG 次级封顶 + 多样性 floor/cap
    signals, external_count, internal_hits = compute_signals(state)
    suggested, zero_n = bumps(signals, external_count, internal_hits, root)
    if not suggested:
        print(f"✅ 无达门槛的提升项(外部主锚+多样性门；冷门{zero_n}篇只计不升)")
        return 0
    changed = []
    for b in suggested:
        rel = b["rel"]
        p = Path(root) / rel
        if not p.exists():
            continue
        if is_raw_rel(rel):        # P0-1: raw/ 不可变，绝不 bump
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


# ── P0-1: raw/ 不可变层硬隔离守卫 ─────────────────────────
def is_raw_rel(rel: str) -> bool:
    """rel 是否在 raw/ 下（不可变外部源）。与 kb_engine/kb_rsi 同约定。"""
    try:
        parts = [str(x).lower() for x in Path(rel).parts]
    except (TypeError, ValueError, AttributeError):
        return False
    return "raw" in parts[:-1]


def _is_raw_path(p):
    """p 是否在 raw/ 不可变层内（RSI 永不写入）。"""
    return is_raw_rel(str(p))


def _read(p):
    return load_note(p)


def _write_importance(p, new):
    """把笔记 importance 设为 new(只增路径,写前须先算实际增量)。
    P0-1: 硬隔离 —— raw/ 不可变外部源拒绝写入。"""
    if _is_raw_path(p):
        return
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
def record_hit(root: Union[str, Path], query: str, hit_path: str, useful: bool) -> float:
    """记录 agent 显式命中反馈 + importance 累积。

    Args:
        root: vault 根路径
        query: 查询文本
        hit_path: 命中笔记的相对路径
        useful: True=有用（权重 1.0）/ False=无用（权重 0.0，记录但不累积）
    """
    weight = HIT_WEIGHTS["explicit_useful"] if useful else HIT_WEIGHTS["explicit_not_useful"]
    # P0-1/P0-3: raw/ 不可变外部源不进信号模型——不记录反馈、不加权（仅记录不带回 raw）
    if is_raw_rel(hit_path):
        return 0.0
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
        # 返回实际写回的增量(0.0 = 被隔，如目标为 raw/ 不可变层)
        return _accumulate_importance(root, hit_path, weight)
    return 0.0


def _accumulate_importance(root, rel, weight, norm_factor=None):
    """importance 累积：new_imp = min(1.0, old_imp + weight * norm_factor)。

    只升不降，上限 1.0。写前 git commit checkpoint。
    """
    if norm_factor is None:
        norm_factor = EXPLICIT_NORM_FACTOR
    p = Path(root) / rel
    if not p.exists():
        return 0.0
    if is_raw_rel(rel):          # P0-1: raw/ 不可变，绝不 bump
        return 0.0
    try:
        fm, text, body = load_note(p)
        old = _parse_importance(fm.get("importance"))
    except (OSError, ValueError):
        return 0.0
    delta = weight * norm_factor
    new_imp = min(1.0, old + delta)
    if new_imp <= old:
        return 0.0
    # 写前 checkpoint
    import subprocess as sp
    sp.run(["git", "-C", root, "add", "-u"], capture_output=True)
    if sp.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        sp.run(["git", "-C", root, "commit", "-q", "-m", "pre-feedback checkpoint"], capture_output=True)
    _write_importance(p, round(new_imp, 4))
    sp.run(["git", "-C", root, "add", "--", rel], capture_output=True)
    sp.run(["git", "-C", root, "commit", "-q",
            "-m", f"kb: feedback hit importance {old:.2f}→{new_imp:.2f}"], capture_output=True)
    return round(delta, 4)


def main() -> int:
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
        # P0-3：ingest 后按外部主锚信号出建议
        signals, external_count, internal_hits = compute_signals(load_state(args.root))
        suggested, zero_n = bumps(signals, external_count, internal_hits, args.root)
        rep = report(hits, sources, suggested, zero_n)
        (Path(args.root) / "feedback_hits.md").write_text(rep, encoding="utf-8")
        print(f"📡 ingest 完成: {sources} 查询源 · {len(suggested)} 篇建议提升  已写入 feedback_hits.md")
        print("\n" + rep)
        return 0
    if args.cmd == "apply":
        return apply_bumps(args.root)
    if args.cmd == "hit":
        useful = str(args.useful).lower() in ("true", "1", "yes")
        delta = record_hit(args.root, args.query, args.hit_path, useful)
        print(f"✅ 已记录命中反馈: {args.hit_path} useful={useful}")
        if delta > 0:
            print(f"   importance +{delta:.4f}（写前 checkpoint，可回滚）")
        elif useful:
            print("   importance 未变动（目标为 raw/ 不可变层，已硬隔离）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
