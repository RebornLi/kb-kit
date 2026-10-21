"""rag_score — 检索评分原语（从 rag.py 拆分，P1-3）。

提供向量相似度（cosine）、BM25、混合融合（RRF / dense）、检索调整（adjustments）、
倒排索引查询、chunk 去重、重要性/标签解析等评分层原语。

是 rag_index 和 rag_query 的共同底层依赖，不反向依赖任何 rag 模块。
"""
import os
import re
import math
import json
import time
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from kb_text import cos as _common_cos  # noqa: F401

# ── 评分常量 ──────────────────────────────────────────────


def _score_cosine_postings(postings, docs, idf, qv, norms):
    """postings 版余弦：只遍历查询词命中的文档。与 `_score_cosine_fast` 数学等价。"""
    acc = {}
    for t, w in qv.items():
        p = postings.get(t)
        if not p:
            continue
        iw = idf.get(t, 1.0)
        for i in range(0, len(p), 2):
            r = docs[p[i]]
            # 写成 `f * idf * w`（左结合）与 `_score_cosine_fast` **逐位一致**；
            # 先把 idf*w 预乘会改变浮点结合律，在近平分上会翻转名次（测试实测抓到）。
            acc[r] = acc.get(r, 0.0) + p[i + 1] * iw * w
    out = []
    for r, s in acc.items():
        if s > 0:
            out.append((r, s / norms.get(r, 1.0)))
    return out

def _bm25_postings(postings, docs, q_tokens, k1=1.5, b=0.75, dl=None, avgdl=None):
    """postings 版 BM25：只遍历命中文档。与 `_bm25_scores` 数学等价。

    postings 里存的是 doc id，而 `dl` 按 rel 索引 → 这里必须用 `docs` 还原。
    """
    postings = postings or {}
    dl = dl or {}
    N = len(dl) or 1
    if avgdl is None:
        avgdl = (sum(dl.values()) / N) or 1.0
    k1p1 = k1 + 1
    acc = {}
    d0 = {}
    for t in q_tokens:
        p = postings.get(t)
        if not p:
            continue
        n = len(p) // 2
        idf_t = math.log((N - n + 0.5) / (n + 0.5) + 1)
        for i in range(0, len(p), 2):
            r = docs[p[i]]
            f = p[i + 1]
            den = d0.get(r)
            if den is None:
                den = d0[r] = k1 * (1 - b + b * dl[r] / avgdl)
            acc[r] = acc.get(r, 0.0) + idf_t * (f * k1p1) / (f + den)
    return acc

def _score_cosine_fast(tf_all, idf, qv, norms):
    """cos(d,q) = Σ_t tf_d[t]·idf_t·qv_t / ‖d‖ —— 不物化文档向量。

    与 `{r:_vec(...)} + _cos` 数学等价；省掉「为全库每篇建归一化稀疏向量」这一趟
    （现役库实测 147ms → 2ms）。累加顺序不同，末位有 1e-12 级差异，不影响排序
    （已用真实索引 8 条查询验证 top-10 顺序完全一致）。
    """
    out = []
    for r, c in tf_all.items():
        s = 0.0
        for t, w in qv.items():
            f = c.get(t)
            if f:
                s += f * idf.get(t, 1.0) * w
        if s > 0:
            out.append((r, s / norms.get(r, 1.0)))
    return out

def _vec(counter, vocab, idf):
    v = {t: c * idf.get(t, 1.0) for t, c in counter.items() if t in idf}
    n = math.sqrt(sum(x * x for x in sorted(v.values()))) or 1.0
    return {k: x / n for k, x in v.items()}

def _cos(a, b):
    """稀疏向量余弦。委托 kb_common.cos（唯一实现；实测 200 组逐位一致）。"""
    return _common_cos(a, b)

# ── P0 Stage3: 混合检索（TF-IDF 余弦 × BM25）──────────────
HYBRID_ALPHA = 0.5   # 余弦权重；BM25 权重 = 1 - HYBRID_ALPHA

def _bm25_scores(tf_all, q_tokens, k1=1.5, b=0.75, dl=None, avgdl=None, df=None):
    """纯标准库 BM25（Okapi）。返回 {rel: score}（>0）。

    · dl/avgdl 可由索引传入（v5 起已预存），省掉每次查询重算文档长度。
    · df 可由索引传入（v6 起已预存）：以前每次查询都要遍历全库「文档×词次」
      （1530 篇 × 458 词 ≈ 70 万次）重算一遍，纯属浪费 —— df 只与 tf_all 有关。
      实测 71.3 ms → 1.2 ms，且分数逐位一致（<1e-12）。旧索引无 df 时回退现场重算。

    另把两个循环不变量提到外层：idf 只依赖查询词、文档归一因子只依赖文档。
    """
    N = len(tf_all) or 1
    if df is None:                      # 兼容 v5 及更早索引：没有 df 就现场算（慢但正确）
        df = Counter()
        for c in tf_all.values():
            for t in c:
                df[t] += 1
    if dl is None:
        dl = {r: sum(c.values()) for r, c in tf_all.items()}
    if avgdl is None:
        avgdl = (sum(dl.values()) / N) or 1.0
    # 查询词的 idf：只与词有关，提到文档循环外（每个词只算一次 log）
    q_idf = {}
    for t in q_tokens:
        if t in q_idf:
            continue
        n = df.get(t, 0)
        if n:
            q_idf[t] = math.log((N - n + 0.5) / (n + 0.5) + 1)
    k1p1 = k1 + 1
    out = {}
    for r, c in tf_all.items():
        # 文档长度归一因子：只与文档有关，也提到词循环外
        d0 = k1 * (1 - b + b * dl[r] / avgdl)
        s = 0.0
        for t in q_tokens:
            f = c.get(t, 0)
            if not f:
                continue
            s += q_idf[t] * (f * k1p1) / (f + d0)
        if s > 0:
            out[r] = s
    return out

# ── 倒排索引（FR-3!（3.2.2）──────────────────────────────────
# 🔒 这个加成是**加性**的，量级（0.15×命中数，可达 ~1.8）远大于 RRF 融合分上限
#    （2/61≈0.033）—— 也就是说最终排序主要由「查询词命中 tag / kb_summary」决定，
#    BM25+RRF 更像粗筛。看起来像量纲缺陷，**但实测它是正确的**：
#
#    在 30 题 × 4 目标（共 120 个目标笔记，全部在索引内）的黄金候选集上实测：
#      · 现状（本实现）            top1 90.0% · recall@10 98.3% · MRR 0.950
#      · 改成乘性 + IDF 加权封顶    top1 60.0% · recall@10 55.0% · MRR 0.684   ← 腰斩
#      · 干脆去掉加成              top1 56.7% · recall@10 45.8% · MRR 0.632
#      · 加性 + IDF 加权（4 档 α）  top1 80.0% · recall@10 94.2% · MRR 0.892
#      · 剔除单字 CJK token        top1 80.0% · recall@10 83.3% · MRR 0.883
#      · 只认 tag 字段             top1 36.7% · recall@10 40.0% · MRR 0.469
#
#    原因：本库的 tag / kb_summary 是**人工标注的主题词**，命中它比命中正文里的
#    普通词强得多；而 IDF 加权恰好会压掉那些「常见但正是主题词」的 token。
#    → 结论：**不要**把它改成乘性/IDF 加权；真要改必须先用
#      `scripts/eval_retrieval_gold.py` 在同一黄金集上复测（该脚本支持
#      `--module` 指定另一份 rag 实现做 A/B 对照）。
INVERTED_HIT_BOOST = 0.15

# ── L1 检索修正契约的运行时消费（kb adaptretrieve apply 产出）──────
#   闭环缺失的那一跳就在这里：契约原本只被写成文件、没有任何代码读它。
#   量级与 INVERTED_HIT_BOOST 同档（融合分上限只有 2/61≈0.033，加性小量根本推不动排序）。
ADJUST_BOOST = 0.90     # 命中契约的笔记加多少分

ADJUST_MAX = 20         # 单次查询最多应用几条契约（有界，防契约膨胀后拖慢查询）

# 置信地板：契约里 sim 低于此值不予采信。契约由机器挖缺口产出、人工 apply，
# 一旦指向无关笔记代价很大 —— 黄金集实测：
#   · 契约正确（18 条来自真实缺口）：top1 90.0%→96.7% · recall@4 82.5%→90.0% · recall@10 98.3%→100%
#   · 契约全指向无关笔记：        top1 90.0%→76.7% · MRR 0.950→0.883（**显著变差**）
# 所以：机制有效，但**安全完全取决于契约质量** → 保留人工 apply + 置信地板 + 条数上限
# + `KB_RETRIEVAL_ADJUST=0` 逃生门 + `--json` 里的 adjustments_applied 审计。
ADJUST_MIN_SIM = 0.50   # 与 kb_adaptretrieve.SIM_MIN 同档

def _adjust_key(q: str) -> str:
    """查询归一化键：去空白/标点 + 小写。

    契约里的 query 来自真实历史查询，用**精确（归一化后）匹配**而不是相似度阈值 ——
    相似度匹配会让一条契约"泛化"到别的查询上，后果不可预测；归一化精确匹配是
    L1 回路的原意（同一问题再问 → 上次漏掉的笔记这次要出现）。
    """
    return re.sub(r"[\s\W_]+", "", (q or "").lower())

def load_retrieval_adjustments(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """读 L1 检索修正契约（规范位置 .kb/state/，回退旧 pipeline/ 位置）。缺/损坏 → []。"""
    try:
        from state_manager import read_state_path
        p = read_state_path(root, "kb_retrieval_adjust.json",
                            legacy=("pipeline/.kb_retrieval_adjust.json",))
        if p is None:
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ImportError, ValueError):
        return []
    adjs = data.get("adjustments") if isinstance(data, dict) else None
    return adjs if isinstance(adjs, list) else []

def _apply_retrieval_adjustments(root: Union[str, Path], q: str,
                                 scored: List[Tuple[str, float]],
                                 indexed: Optional[set] = None) -> Tuple[List[Tuple[str, float]], List[str]]:
    """按契约把「上次漏掉的笔记」拉回排序。返回 (新 scored, 本次应用的 rel 列表)。

    安全约束：
      · `KB_RETRIEVAL_ADJUST=0` 可全局关闭（实测出负增益时的逃生门）；
      · 只针对**已索引**的笔记加成，绝不注入幽灵命中；
      · 单次查询应用条数有界（ADJUST_MAX）；
      · 应用到哪些笔记会进 `--json` 输出，可审计。
    """
    if os.environ.get("KB_RETRIEVAL_ADJUST", "") in ("0", "false", "no"):
        return scored, []
    adjs = load_retrieval_adjustments(root)
    if not adjs:
        return scored, []
    key = _adjust_key(q)
    d = dict(scored)
    applied: List[str] = []
    for a in adjs[:ADJUST_MAX]:
        if not isinstance(a, dict) or _adjust_key(a.get("query")) != key:
            continue
        rel = a.get("note")
        if not rel or (indexed is not None and rel not in indexed):
            continue
        sim = a.get("sim")
        if isinstance(sim, (int, float)) and sim < ADJUST_MIN_SIM:
            continue                    # 低置信契约不采信（见 ADJUST_MIN_SIM 的实测说明）
        d[rel] = d.get(rel, 0.0) + ADJUST_BOOST
        applied.append(rel)
    if not applied:
        return scored, []
    return sorted(d.items(), key=lambda x: x[1], reverse=True), applied

def _inverted_lookup(query_tokens, inverted_index, docs=None):
    """倒排索引精确命中。返回 {rel: hit_count}。

    v6 起 postings 存整数 doc id，用 `docs` 列表还原成 rel；
    旧索引（或自测构造）postings 直接是 rel，`docs=None` 时原样使用。
    """
    hits = {}
    for tok in query_tokens:
        for field in ("tag", "kb_summary_token"):
            for item in inverted_index.get(field, {}).get(tok, []):
                rel = docs[item] if docs is not None else item
                hits[rel] = hits.get(rel, 0) + 1
    return hits

# ── 检索结果去重（FR-3.1.8 + P0：按根文档归并）─────────────────
CHUNK_FAMILY_MAX = 2   # 同一根文档最多保留几个命中（防"整篇碎片墙"顶掉其它主题）

CONF_WEIGHT = 0.20     # P3：复合置信分对排序的影响幅度（×0.9 ~ ×1.1）

RRF_K = 60             # RRF 平滑常数（行业惯用 60）

# ── 稠密语义通道（补召回）────────────────────────────────────
#   实测依据（tests/probe_semantic_gap.py，真实库 16 题改写式查询）：
#     纯词法命中源笔记 6/16 = 38% · 稠密 15/16 = 94% · **仅稠密能找回 62%**
#   但**绝不能**用它替换排序：KB_RERANK=embed 实测 top1 90.0%→86.7%、MRR 0.950→0.933。
#   → 用**加权 RRF 融合**：词法名次不动，稠密通道只贡献增量名次。
DENSE_M = 200          # 稠密通道取前 M 名候选

# 尾部**预留槽位**比例：词法前 (1-FRAC)·k 名按构造完全不变，其余槽位给"稠密-only"笔记。
# 为什么不直接用 RRF 融合（实测，2026-10-16，30 题黄金集）：
#   w=0.3 → top1 90.0%→83.3%、rec@4 82.5%→65.0%
#   w=0.5 → top1 76.7% · w=1.0 → top1 60.0%（单调变差）
# 与"语义重排"同因：稠密名次混进头部会顶掉元数据强信号（分层/tags/kb_summary/标题）。
# 预留槽位后 top1/rec@4 **不可能**回退（头部未动），而语义相近但用词不同的笔记
# 仍能进入 top-k —— 这才是 probe_semantic_gap 测出的那 62% 缺口需要的通道。
DENSE_SLOTS_FRAC = 0.4   # 默认 0.4：改写式查询 top-10 命中 37.5%→68.8%（+31.2pp），

                         # 黄金集 rec@10 98.3%→97.5%（-0.8pp，n=30 噪声内），头部指标不变。
                         # 想要"黄金集零变动"可设 KB_DENSE_FRAC=0.2（探针 +18.7pp）。
RRF_CAP = 300          # 每路只取前 N 参与融合（控制复杂度）

def _rrf_fuse(list_a, list_b, k: int = RRF_K, cap: int = RRF_CAP):
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)。对分数量纲免疫。"""
    fused = {}
    for lst in (list_a, list_b):
        for rank, (rel, _sc) in enumerate(list(lst)[:cap], start=1):
            fused[rel] = fused.get(rel, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)

def dense_rank(root, q: str, payload: dict, m: int = DENSE_M) -> List[Tuple[str, float]]:
    """稠密语义通道：返回 [(rel, 余弦)] 按相似度降序；不可用/异常 → []。

    best-effort：任何异常都只让语义通道缺席，绝不影响词法检索主流程。
    """
    if os.environ.get("KB_DENSE", "") in ("0", "false", "no"):
        return []
    d = payload.get("dense") or {}
    vecs = d.get("vecs") or {}
    if not vecs:
        return []
    t0 = time.time()
    try:
        import dense as _dense
        qv = _dense.query_vector(root, q, int(d.get("dim_in") or 0),
                                 int(d.get("dim") or 0), int(d.get("seed") or 0))
        if qv is None or time.time() - t0 > _dense.BUDGET_S:
            return []
        return _dense.top_k(vecs, qv, m)
    except Exception:       # noqa: BLE001  语义通道是增强项
        return []

def dense_fill_tail(ranked: List[Tuple[str, float]], dense: List[Tuple[str, float]],
                    k: int, frac: float = DENSE_SLOTS_FRAC) -> List[Tuple[str, float]]:
    """**尾部槽位填充**：词法前 (1-frac)·k 名原样保留，其余槽位给稠密-only 笔记。

    与 RRF 融合的本质区别：头部名次**按构造不变**（不是"权重小所以影响小"），
    所以 top1 / recall@4 这类头部指标不可能回退。稠密只回答一个问题：
    「词法完全没找到、但语义相近的笔记，要不要给一个位置」。
    """
    try:                                    # 便于对槽位比例扫参做受控实验
        frac = float(os.environ.get("KB_DENSE_FRAC", frac))
    except (TypeError, ValueError):
        pass
    if not dense or not ranked or frac <= 0:
        return ranked
    n_keep = max(1, min(len(ranked), int(round(k * (1.0 - frac)))))
    if n_keep >= len(ranked):
        return ranked
    head = list(ranked[:n_keep])
    have = {r for r, _ in ranked}          # 词法已经覆盖的（含被截掉的）
    floor = min((s for _r, s in head), default=1.0) * 0.5
    slots = max(0, k - n_keep)
    added = 0
    for step, (rel, _cos) in enumerate(dense):
        if added >= slots:
            break
        if rel in have:
            continue
        have.add(rel)
        head.append((rel, floor * (1.0 - 1e-6 * (step + 1))))   # 保持稠密名次
        added += 1
    return head

def _root_of(rel, meta_all):
    """沿 chunk_of 链回溯到根文档（防 -p2-p2 级联）。meta 未知时返回 rel 自身。"""
    cur = rel
    seen = set()
    for _ in range(8):  # 链深上限，防环
        if cur in seen:
            break
        seen.add(cur)
        parent = str(meta_all.get(cur, {}).get("chunk_of", "") or "").strip()
        if not parent:
            break
        cand = str(Path(cur).parent / f"{parent}.md") if Path(cur).parent != Path(".") else f"{parent}.md"
        if cand not in meta_all:
            cand = parent if parent in meta_all else cand
        if cand in meta_all and cand != cur:
            cur = cand
        elif parent in meta_all and parent != cur:
            cur = parent
        else:
            break
    return cur

def _dedup_chunks(scored, top, meta_all=None):
    """同一笔记多 chunk 命中时只保留最高分，并标注 chunk_siblings。

    P0：同一**根文档**的兄弟碎片合并计数（含 -p2-p2 级联），且同一根家族最多出
    CHUNK_FAMILY_MAX 条 —— 否则一篇长日志的上百个碎片会整体顶掉其它主题。
    """
    meta_all = meta_all or {}
    # 先按根文档归并计数
    fam = {}  # root -> {"count": n, "hits": [(rel, sc), ...]}
    for rel, sc in scored:
        r = _root_of(rel, meta_all)
        f = fam.setdefault(r, {"count": 0, "hits": []})
        f["count"] += 1
        f["hits"].append((rel, sc))
    # 每个根文档最多保留 CHUNK_FAMILY_MAX 条最高分
    kept = []
    for r, f in fam.items():
        f["hits"].sort(key=lambda x: x[1], reverse=True)
        for rel, sc in f["hits"][:CHUNK_FAMILY_MAX]:
            kept.append((rel, sc, f["count"]))
    # 同一文件多 chunk 再归并一次（保留原有语义）
    seen = {}
    for rel, sc, cnt in kept:
        if rel in seen:
            seen[rel] = (max(seen[rel][0], sc), seen[rel][1] + cnt)
        else:
            seen[rel] = (sc, cnt)
    result = []
    for rel, (sc, count) in seen.items():
        item = [rel, sc]
        if count > 1:
            item.append(count)  # chunk_siblings
        result.append(item)
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top]

# ── enforce_level 优先级加成 ───────────────────────────────
PRIORITY_MULTIPLIER = {
    ("policy", "must"): 1.10, ("policy", "should"): 1.05, ("policy", "may"): 1.00,
    ("policy", None): 1.05,
    "reference": 0.95,
    "chat": 0.90, "chat_decision": 0.90, "chat_knowledge": 0.90, "chat_todo": 0.90,
}

# 注：_parse_importance 保留本地实现——默认值 0.5（kb_common.parse_importance 默认 0.0）。
#   rag 检索路径中 importance 缺失视为中等优先级（0.5），而非最低（0.0）。
def _parse_importance(v):
    """容错解析 importance，默认 0.5。"""
    try:
        m = re.search(r"[-+]?\d*\.?\d+", str(v))
        return float(m.group()) if m else 0.5
    except (TypeError, ValueError):
        return 0.5

def _parse_tags(v):
    """解析 tags 字段为列表。

    契约里的 tags 有两种写法：`tags: [sop, metric]`（flow 列表）与裸值 `tags: sop, metric`。
    `parse_frontmatter` 对前者返回字符串 `"[sop, metric]"`，不剥方括号就会被切成
    `['[sop','metric]']` → `--tags metric` 静默返回空。
    """
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return [t.strip().strip('"\'') for t in re.split(r"[,\s]+", s) if t.strip()]
    return []

def _has_any_tag(tags_value, tags_filter):
    """检查 tags_value 是否包含 tags_filter 中的任一标签。"""
    note_tags = set(_parse_tags(tags_value))
    filter_tags = set(t.strip() for t in tags_filter.split(",") if t.strip())
    return bool(note_tags & filter_tags)
