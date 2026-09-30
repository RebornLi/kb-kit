#!/usr/bin/env python3
# ============================================================
# rag.py —— 本地语义检索（方案 v4.0 §6）离线 TF-IDF / BM25 风格
#   index   : 扫描知识层笔记 → 向量索引存入 vector index/
#   query   : 自然语言提问 → Top-k 带出处答案（检索 + 最佳片段）
#   分词    : 中文字符 unigram+bigram（无 jieba 兜底）+ 拉丁词
#   可选答案: --answer 且环境变量设有 ORNITH 凭证时，调用 vLLM 生成答案
# 用法:
#   python3 pipeline/rag.py index  [--root R]
#   python3 pipeline/rag.py query  "<问题>" [--top 3] [--root R] [--answer]
# ============================================================
import argparse, os, re, sys, json, math, time, datetime, hashlib, uuid
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import ROOT_DEFAULT, EXCLUDE, FM, load_meta, iter_notes, tokenize, count_tokens, content_fingerprint, index_excluded, is_generated_report, is_stale, retrievable_status, kb_layer_of, layer_multiplier, is_index_stub, LAYER_MULTIPLIER, parent_of, root_of

try:                       # 上下文强化（P2）：缺失/异常都不影响索引可用
    from contextual import cch as _cch
except Exception:          # pragma: no cover
    def _cch(rel, fm, body):
        return ""

CONTEXT_IN_INDEX = True    # 是否把上下文串拼进索引文本（默认开）

IDX_DIR = "vector index"
IDX_VERSION = 4  # v2 doc_hashes/doc_mtimes/inverted_index；v3 layer/chunk_of；
                 # v4 context（上下文强化，只进索引）+ parent/root（父子块：查子块、返回父块）


def load_index(idx_path: Path) -> Tuple[Optional[Dict[str, Any]], int]:
    """加载 df_idf.json 索引，兼容无 version 字段的旧索引。
    返回 (payload, version)：
      - (None, 0)  → 文件不存在或损坏
      - (dict, 0)  → 旧索引（无 version 字段），可正常读取，建议重建
      - (dict, N)  → 版本 N 索引
    向后兼容：旧索引无 version 字段时读作 0，不破坏原有 key 结构。
    向前兼容：新索引的 version 字段被旧代码忽略（新增字段不影响原有解析）。"""
    if not idx_path.exists():
        return None, 0
    try:
        payload = json.loads(idx_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, 0
    return payload, payload.get("version", 0)

def strip_body(text: str) -> str:
    m = FM.search(text)
    if m:
        end = FM.search(text, m.end())
        if end:
            return text[end.end():]
    return text

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("index"); d.add_argument("--root", default=ROOT_DEFAULT)
    d.add_argument("--full", action="store_true",
                   help="强制全量重建（默认增量；改过索引字段/分层标记后用）")
    q = sub.add_parser("query"); q.add_argument("q"); q.add_argument("--top", type=int, default=3)
    q.add_argument("--root", default=ROOT_DEFAULT); q.add_argument("--answer", action="store_true")
    # JSON API + 过滤器 + 上下文预算 + 命中记录
    q.add_argument("--json", action="store_true", dest="as_json")
    q.add_argument("--context", type=int, default=240, help="snippet 字符数上限")
    q.add_argument("--context-budget", type=int, default=None, dest="context_budget", help="上下文 token 预算")
    q.add_argument("--session", default=None, help="会话 ID，自动记录命中")
    # 8 种过滤器
    q.add_argument("--domain", default=None)
    q.add_argument("--content-type", default=None, dest="content_type")
    q.add_argument("--author", default=None)
    q.add_argument("--min-importance", type=float, default=None, dest="min_importance")
    q.add_argument("--enforce-level", default=None, dest="enforce_level")
    q.add_argument("--tags", default=None, help="逗号分隔的标签")
    q.add_argument("--date-from", default=None, dest="date_from")
    q.add_argument("--date-to", default=None, dest="date_to")
    q.add_argument("--exclude-stale", action="store_true", dest="exclude_stale",
                   help="排除非 active/stable 或陈旧的笔记（freshness 过滤）")
    q.add_argument("--include-raw", action="store_true", dest="include_raw",
                   help="不降级 raw/ 证据层（默认降级排序，仍可召回）")
    q.add_argument("--include-stubs", action="store_true", dest="include_stubs",
                   help="不降级分块索引页/空壳页（默认降级排序）")
    args = ap.parse_args()

    if args.cmd == "index":
        return cmd_index(args.root, incremental=not args.full)
    if args.cmd == "query":
        return cmd_query(args.root, args.q, args.top, args.answer,
                         as_json=args.as_json, context=args.context,
                         context_budget=args.context_budget, session=args.session,
                         domain=args.domain, content_type=args.content_type,
                         author=args.author, min_importance=args.min_importance,
                         enforce_level=args.enforce_level, tags=args.tags,
                         date_from=args.date_from, date_to=args.date_to,
                         exclude_stale=args.exclude_stale,
                         include_raw=args.include_raw, include_stubs=args.include_stubs)
    return 1

_CHUNK_SUFFIX = re.compile(r"-(?:p|c)\d+$")   # 只剥一层：-p2-p2 → -p2 → 主文档


def _infer_parent_from_name(rel: str) -> str:
    """无 chunk_of 时的兜底：按文件名推断父文档（-p2-p2 → -p2 → 主文档）。

    为什么需要：历史分块产物有不少没写全 chunk_of（或写了但被后续改写覆盖），
    只靠 frontmatter 会漏掉一大半父块回填。
    """
    cur = Path(rel)
    for _ in range(8):                       # 逐层向上一级（-p2-p2 的父是 -p2，再父是主文档）
        stem = cur.stem
        if not _CHUNK_SUFFIX.search(stem):
            return ""
        base = _CHUNK_SUFFIX.sub("", stem)
        if not base:
            return ""
        cand = cur.with_name(base + ".md")
        root = Path(__file__).resolve().parents[1]
        if (root / str(cand)).exists():
            return str(cand)
        cur = cand                        # 该层不存在则继续往上一层找
    return ""


def _conf_of(fm: dict) -> float:
    """正典的复合置信分（0–1）；无则中性 0.5。用于排序乘数。"""
    try:
        return max(0.0, min(1.0, float(fm.get("confidence"))))
    except (TypeError, ValueError):
        return 0.5


def _parent_path(rel: str, fm: dict) -> str:
    return parent_of(rel, fm)


def _root_path(rel: str, fm: dict) -> str:
    r = root_of(rel, fm)
    return r if r != rel else ""


def cmd_index(root: Union[str, Path], incremental: bool = True) -> int:
    idx = Path(root) / IDX_DIR
    idx.mkdir(parents=True, exist_ok=True)
    idx_path = idx / "df_idf.json"

    # 尝试增量
    if incremental:
        old_payload, old_ver = load_index(idx_path)
        if old_payload is not None and old_ver == IDX_VERSION:
            return _incremental_update(root, idx, old_payload)

    # 全量重建
    return _full_rebuild(root, idx)


def _full_rebuild(root, idx):
    docs, tf_all, meta_all = {}, {}, {}
    doc_hashes, doc_mtimes = {}, {}
    df = Counter()
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue  # 运行期报告产物不入检索
        fm, body = load_meta(p)
        if index_excluded(fm):
            continue  # 归档/来源/显式退出：不入检索
        ctx = _cch(rel, fm, body) if CONTEXT_IN_INDEX else ""
        toks = tokenize((ctx + "\n" + body) + " " + " ".join(str(fm.get("tags", "")).split()))
        if not toks:
            continue
        c = Counter(toks)
        docs[rel] = c
        for t in c: df[t] += 1
        tf_all[rel] = c
        meta_all[rel] = {"title": fm.get("title", p.stem),
                         "domain": fm.get("domain", "-"), "tags": fm.get("tags", ""),
                         "layer": kb_layer_of(fm, rel),
                         "chunk_of": str(fm.get("chunk_of", "") or ""),
                         "context": ctx,
                         "parent": _parent_path(rel, fm),
                         "root": _root_path(rel, fm),
                         "confidence": _conf_of(fm)}
        doc_hashes[rel] = content_fingerprint(body)
        doc_mtimes[rel] = p.stat().st_mtime
    N = len(docs)
    vocab = sorted(df)
    idf = {t: math.log((N + 1) / (df[t] + 1)) + 1 for t in vocab}
    inverted = _build_inverted_index(root, tf_all)
    payload = {"version": IDX_VERSION, "n_docs": N, "vocab": vocab, "idf": idf,
               "tf": tf_all, "meta": meta_all,
               "doc_hashes": doc_hashes, "doc_mtimes": doc_mtimes,
               "inverted_index": inverted}
    (idx / "df_idf.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 全量索引就绪  文档 {N}  词表 {len(vocab)}  → {idx}/df_idf.json")
    return 0


def _incremental_update(root, idx, old):
    old_hashes = old.get("doc_hashes", {})
    old_mtimes = old.get("doc_mtimes", {})
    current_files = {str(p.relative_to(root)): p for p in iter_notes(root)}

    changed, deleted = [], []
    current_hashes, current_mtimes = {}, {}
    retired = set()  # 归档/来源/显式退出：文件仍在但需从索引移除

    for rel, p in current_files.items():
        if is_generated_report(rel):
            retired.add(rel)  # 报告产物：从索引移除（若曾入过）
            continue
        fm, body = load_meta(p)
        if index_excluded(fm):
            # 归档/停用/来源：不入本次索引；同时确保旧索引里的同 rel 被清掉
            # （文件仍在 current_files 里，不会被“物理删除”分支捕获，故显式记录）
            retired.add(rel)
            continue
        mtime = p.stat().st_mtime
        if old_mtimes.get(rel) == mtime:
            # mtime 未变，跳过
            current_hashes[rel] = old_hashes.get(rel, "")
            current_mtimes[rel] = mtime
            continue
        h = content_fingerprint(body)
        current_hashes[rel] = h
        current_mtimes[rel] = mtime
        if old_hashes.get(rel) != h:
            changed.append((rel, p, fm, body))

    # 从索引移除：物理删除的文件 + 新近 retire 的文件（与 _full_rebuild 行为一致）
    deleted = [rel for rel in old_hashes if rel not in current_files or rel in retired]

    if not changed and not deleted:
        print("✅ 索引已是最新（无变更）")
        return 0

    # 增量更新 tf/meta
    tf_all = dict(old.get("tf", {}))
    meta_all = dict(old.get("meta", {}))
    for rel, p, fm, body in changed:
        ctx = _cch(rel, fm, body) if CONTEXT_IN_INDEX else ""
        toks = tokenize((ctx + "\n" + body) + " " + " ".join(str(fm.get("tags", "")).split()))
        if not toks:
            tf_all.pop(rel, None)
            meta_all.pop(rel, None)
            continue
        tf_all[rel] = Counter(toks)
        meta_all[rel] = {"title": fm.get("title", p.stem),
                         "domain": fm.get("domain", "-"), "tags": fm.get("tags", ""),
                         "layer": kb_layer_of(fm, rel),
                         "chunk_of": str(fm.get("chunk_of", "") or ""),
                         "context": ctx,
                         "parent": _parent_path(rel, fm),
                         "root": _root_path(rel, fm),
                         "confidence": _conf_of(fm)}
    for rel in deleted:
        tf_all.pop(rel, None)
        meta_all.pop(rel, None)
        current_hashes.pop(rel, None)
        current_mtimes.pop(rel, None)
    # 重新计算 df + idf
    df = Counter()
    for c in tf_all.values():
        for t in c: df[t] += 1
    N = len(tf_all)
    vocab = sorted(df)
    idf = {t: math.log((N + 1) / (df[t] + 1)) + 1 for t in vocab}
    inverted = _build_inverted_index(root, tf_all)
    payload = {"version": IDX_VERSION, "n_docs": N, "vocab": vocab, "idf": idf,
               "tf": tf_all, "meta": meta_all,
               "doc_hashes": current_hashes, "doc_mtimes": current_mtimes,
               "inverted_index": inverted}
    (idx / "df_idf.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 增量索引完成  变更 {len(changed)}  删除 {len(deleted)}  总文档 {N}")
    return 0

def _vec(counter, vocab, idf):
    v = {t: c * idf.get(t, 1.0) for t, c in counter.items() if t in idf}
    n = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {k: x / n for k, x in v.items()}

def _cos(a, b):
    if len(a) > len(b): a, b = b, a
    return sum(av * b[k] for k, av in a.items() if k in b)


# ── P0 Stage3: 混合检索（TF-IDF 余弦 × BM25）──────────────
HYBRID_ALPHA = 0.5   # 余弦权重；BM25 权重 = 1 - HYBRID_ALPHA


def _bm25_scores(tf_all, q_tokens, k1=1.5, b=0.75):
    """纯标准库 BM25（Okapi）。返回 {rel: score}（>0）。"""
    df = Counter()
    for c in tf_all.values():
        for t in c:
            df[t] += 1
    N = len(tf_all) or 1
    dl = {r: sum(c.values()) for r, c in tf_all.items()}
    avgdl = (sum(dl.values()) / N) or 1.0
    out = {}
    for r, c in tf_all.items():
        L = dl[r]
        s = 0.0
        for t in q_tokens:
            f = c.get(t, 0)
            if not f:
                continue
            n = df[t]
            idf_t = math.log((N - n + 0.5) / (n + 0.5) + 1)
            s += idf_t * (f * (k1 + 1)) / (f + k1 * (1 - b + b * L / avgdl))
        if s > 0:
            out[r] = s
    return out


# ── 倒排索引（FR-3!（3.2.2）──────────────────────────────────
INVERTED_HIT_BOOST = 0.15


def _build_inverted_index(root, tf_all):
    """构建倒排索引：tag/domain/content_type/author/kb_summary_token → [rel]"""
    inv = {"tag": {}, "domain": {}, "content_type": {},
           "author": {}, "kb_summary_token": {}}
    for rel in tf_all:
        fm, body = load_meta(Path(root) / rel)
        for t in _parse_tags(fm.get("tags", "")):
            inv["tag"].setdefault(t, []).append(rel)
        d = fm.get("domain")
        if d: inv["domain"].setdefault(d, []).append(rel)
        ct = fm.get("content_type")
        if ct: inv["content_type"].setdefault(ct, []).append(rel)
        a = fm.get("author")
        if a: inv["author"].setdefault(a, []).append(rel)
        for tok in tokenize(str(fm.get("kb_summary", ""))):
            inv["kb_summary_token"].setdefault(tok, []).append(rel)
    return inv


def _inverted_lookup(query_tokens, inverted_index):
    """倒排索引精确命中。返回 {rel: hit_count}"""
    hits = {}
    for tok in query_tokens:
        for field in ("tag", "kb_summary_token"):
            for rel in inverted_index.get(field, {}).get(tok, []):
                hits[rel] = hits.get(rel, 0) + 1
    return hits


# ── 检索结果去重（FR-3.1.8 + P0：按根文档归并）─────────────────
CHUNK_FAMILY_MAX = 2   # 同一根文档最多保留几个命中（防"整篇碎片墙"顶掉其它主题）
CONF_WEIGHT = 0.20     # P3：复合置信分对排序的影响幅度（×0.9 ~ ×1.1）
RRF_K = 60             # RRF 平滑常数（行业惯用 60）
RRF_CAP = 300          # 每路只取前 N 参与融合（控制复杂度）


def _rrf_fuse(list_a, list_b, k: int = RRF_K, cap: int = RRF_CAP):
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)。对分数量纲免疫。"""
    fused = {}
    for lst in (list_a, list_b):
        for rank, (rel, _sc) in enumerate(list(lst)[:cap], start=1):
            fused[rel] = fused.get(rel, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


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
    """解析 tags 字段为列表。"""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [t.strip() for t in re.split(r"[,\s]+", v) if t.strip()]
    return []


def _has_any_tag(tags_value, tags_filter):
    """检查 tags_value 是否包含 tags_filter 中的任一标签。"""
    note_tags = set(_parse_tags(tags_value))
    filter_tags = set(t.strip() for t in tags_filter.split(",") if t.strip())
    return bool(note_tags & filter_tags)


def _apply_filters(scored, root, domain, content_type, author,
                   min_importance, enforce_level, tags, date_from, date_to):
    """对命中结果按 8 个维度过滤（AND 逻辑）+ enforce_level 优先级加成。"""
    filtered = []
    for rel, score in scored:
        fm, body = load_meta(Path(root) / rel)
        # 8 种过滤器
        # P2: domain 两级过滤（FR-3.3.3）—— 一级匹配或完整匹配
        if domain:
            fm_domain = fm.get("domain", "")
            from kb_common import domain_primary, parse_domain
            q_primary, q_secondary = parse_domain(domain)
            fm_primary, fm_secondary = parse_domain(fm_domain)
            if domain != fm_domain and q_primary != fm_primary:
                continue
            if q_secondary and q_secondary != fm_secondary:
                continue
        if content_type and fm.get("content_type") != content_type:
            continue
        if author and fm.get("author") != author:
            continue
        if min_importance is not None and _parse_importance(fm.get("importance", 0)) < min_importance:
            continue
        if enforce_level and fm.get("enforce_level") != enforce_level:
            continue
        if tags and not _has_any_tag(fm.get("tags", ""), tags):
            continue
        if date_from or date_to:
            upd = str(fm.get("updated", ""))
            if date_from and upd < date_from:
                continue
            if date_to and upd > date_to:
                continue
        # enforce_level 优先级加成
        ct = fm.get("content_type", "")
        el = fm.get("enforce_level")
        mult = PRIORITY_MULTIPLIER.get((ct, el), PRIORITY_MULTIPLIER.get(ct, 1.0))
        filtered.append((rel, score * mult))
    return filtered


PARENT_WINDOW = 900   # 返回父块正文时的字符上限（"查子块、答父块"）


def _is_index_like(root: Path, path: str) -> bool:
    """父页是否只是"目录页"（被改写成指向子块的索引）。"""
    try:
        fm, body = load_meta(root / path)
    except Exception:
        return False
    if is_index_stub(fm, body):
        return True
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return True
    pointer = sum(1 for l in lines if l.startswith(("-", "*")) and ("→" in l or "[[" in l))
    return pointer / len(lines) >= 0.6


def _parent_block(root: Path, rel: str, meta_all: dict, context: int,
                  sib_scores: Optional[Dict[str, float]] = None) -> Dict[str, str]:
    """命中碎片时，回填所属父文档的正文窗口（父子块的"父"侧）。

    为什么：子块便于**匹配**，父块才有足够上下文让 Agent 正确**推理**——
    这正是 Anthropic「Hierarchical Chunking」的生产标准做法。
    """
    m = meta_all.get(rel) or {}
    par = str(m.get("parent") or m.get("root") or "").strip()
    if not par or par == rel:
        return {}
    pp = root / par
    if not pp.exists():
        return {}
    try:
        _fm, pbody = load_meta(pp)
    except Exception:
        return {}
    if not pbody.strip():
        return {}
    if _is_index_like(root, par):
        # 父页只是目录 → 改回填"同一父页下、本次查询得分最高的兄弟碎片"
        best, best_sc = "", -1.0
        for other, m in (meta_all or {}).items():
            if other == rel or other == par:
                continue
            if str(m.get("parent") or "") != par:
                continue
            sc = float((sib_scores or {}).get(other, -1.0))
            if sc > best_sc:
                try:
                    _f, ob = load_meta(root / other)
                except Exception:
                    continue
                if ob.strip():
                    best, best_sc = other, sc
        if best:
            return {"parent": best, "parent_excerpt": best_sentence(best_body(best), query="")[:PARENT_WINDOW]} if False else {
                "parent": best, "parent_excerpt": _read_head(root / best)[:PARENT_WINDOW], "parent_is_sibling": "1"}
        return {}
    win = best_sentence(pbody, "")            # 父块开头的结论窗口
    if len(win) < 40:
        win = pbody.strip()[:PARENT_WINDOW]
    return {"parent": par, "parent_excerpt": win[:PARENT_WINDOW]}


def _read_head(p: Path, n: int = PARENT_WINDOW) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:n]
    except OSError:
        return ""


def _build_hit(rel, score, fm, body, query, context, root=None, meta_all=None,
               sib_scores=None):
    """构造单条 JSON hit（含上下文串与父块回填）。"""
    snippet = best_sentence(body, query)
    if len(snippet) > context:
        snippet = snippet[:context] + "…"
    extra = {}
    if root is not None and meta_all:
        extra = _parent_block(Path(root), rel, meta_all, context, sib_scores)
    return {
        "context": str((meta_all or {}).get(rel, {}).get("context") or ""),
        "path": rel,
        "title": fm.get("title", Path(rel).stem),
        "domain": fm.get("domain", "-"),
        "content_type": fm.get("content_type", "unknown"),
        "score": round(score, 4),
        "snippet": snippet,
        "tags": _parse_tags(fm.get("tags", "")),
        "importance": _parse_importance(fm.get("importance", 0)),
        "enforce_level": fm.get("enforce_level"),
        "author": fm.get("author"),
        "updated": fm.get("updated"),
        **extra,
    }


def _build_json_output(query, hits, n_indexed, latency_ms):
    """构造完整 JSON 输出。"""
    return {
        "query": query,
        "hits": hits,
        "meta": {"n_indexed": n_indexed, "latency_ms": latency_ms},
    }


def _truncate_to_tokens(text, max_tokens):
    """按 token 数截断文本。"""
    result = []
    tokens_so_far = 0
    for char in text:
        result.append(char)
        if re.match(r"[\u4e00-\u9fff]", char):
            tokens_so_far += 1
        elif re.match(r"[A-Za-z0-9]", char):
            # 每 4 个拉丁字符算 1 token，这里粗略按字符累加
            tokens_so_far += 0.25
        if tokens_so_far >= max_tokens:
            break
    return "".join(result)


def _context_budget(hits, budget):
    """按 score 降序拼接 snippet，总 token 不超 budget。"""
    result, token_count, n = "", 0, 0
    for hit in hits:
        snippet = hit["snippet"]
        st = count_tokens(snippet)
        if token_count + st > budget:
            remaining = budget - token_count
            if remaining > 50:
                snippet = _truncate_to_tokens(snippet, remaining)
                result += snippet + "\n\n"
                token_count += remaining
                n += 1
            break
        result += snippet + "\n\n"
        token_count += st
        n += 1
    return {"text": result, "token_count": token_count, "budget": budget, "n_snippets": n}


def _gen_session():
    """为单次 query 生成唯一 session id（每进程一次 → 每 CLI 查询一次）。
    目的：让每次查询都写入 per-query 事件 agent_hits，供 kb_usage 量再问率。"""
    return "q-" + uuid.uuid4().hex[:12]


def _record_hits(query, hits, session, root):
    """记录命中到 feedback_state.json（StateStore，与 feedback_loop/kb_usage/kb_claim 同机制）。"""
    try:
        from state_manager import StateStore
        sp = StateStore(root)
        state = sp.load("feedback_state.json", {})
        state.setdefault("agent_hits", {})[session] = {
            "query": query,
            "hits": [h["path"] for h in hits],
            "timestamp": datetime.datetime.now().isoformat(),
            "weight": 0.3,
        }
        sp.save("feedback_state.json", state)
    except (ImportError, OSError, json.JSONDecodeError, KeyError, TypeError):
        pass


def cmd_query(root: Union[str, Path], q: str, top: int, answer: bool, as_json: bool = False,
              context: int = 240, context_budget: Optional[int] = None,
              session: Optional[str] = None, domain: Optional[str] = None,
              content_type: Optional[str] = None, author: Optional[str] = None,
              min_importance: Optional[float] = None, enforce_level: Optional[str] = None,
              tags: Optional[str] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None,
              exclude_stale: bool = False,
              include_raw: bool = False, include_stubs: bool = False) -> int:
    t0 = time.time()
    # 捕获打通：无 session 时自动生成，确保每次查询都写 per-query 事件（供 kb_usage 量再问率）
    if not session:
        session = _gen_session()
    # P2: 检索缓存（FR-3.1.9）
    cache_key = hashlib.sha256(json.dumps({
        "q": q, "top": top, "domain": domain, "content_type": content_type,
        "author": author, "min_importance": min_importance,
        "enforce_level": enforce_level, "tags": tags,
        "date_from": date_from, "date_to": date_to,
        # context（片段长度）与 answer（是否调 LLM 作答）都会改变输出，必须入键
        "context": context, "answer": bool(answer),
        "exclude_stale": bool(exclude_stale),
        "include_raw": bool(include_raw), "include_stubs": bool(include_stubs),
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    try:
        from state_manager import StateStore
        cache = StateStore(root).load("query_cache.json", {})
        entry = cache.get(cache_key)
        if entry and (time.time() - entry.get("ts", 0)) < 300:  # TTL 5 分钟
            if as_json:
                print(json.dumps(entry["result"], ensure_ascii=False, indent=2))
            else:
                print(entry["text"])
            # 反馈闭环：命中缓存也要记录本次查询命中（供 kb_usage 量再问率），
            # 不能被缓存短路，否则重复查询不再产生反馈信号。
            paths = entry.get("paths") or []
            if session and paths:
                _record_hits(q, [{"path": p} for p in paths], session, root)
            return 0
    except ImportError:
        cache = None

    idx_path = Path(root) / IDX_DIR / "df_idf.json"
    payload, ver = load_index(idx_path)
    if payload is None:
        if as_json:
            print(json.dumps({"error": "index not found", "query": q}, ensure_ascii=False))
        else:
            print("✗ 索引不存在，先跑: rag.py index", file=sys.stderr)
        return 2
    if ver < IDX_VERSION:
        if not as_json:
            print(f"⚠️ 索引格式旧（version {ver} < {IDX_VERSION}），建议跑 `rag.py index` 重建；当前仍可查询。", file=sys.stderr)
    elif ver > IDX_VERSION:
        if not as_json:
            print(f"⚠️ 索引版本 {ver} 高于当前代码支持 {IDX_VERSION}，建议升级 kb-kit 或跑 `rag.py index` 重建；当前仍可查询。", file=sys.stderr)
    vocab, idf, tf_all, meta_all = payload["vocab"], payload["idf"], payload["tf"], payload["meta"]
    rel2vec = {r: _vec(tf_all[r], vocab, idf) for r in tf_all}
    # 查询扩展（FR-3.2.4）
    q_expanded = _expand_query(q)
    qv = _vec(Counter(tokenize(q_expanded)), vocab, idf)
    scored = [(r, _cos(v, qv)) for r, v in rel2vec.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = [(r, s) for r, s in scored if s > 0]

    # P2: 混合检索 —— 余弦 + BM25 用 RRF（Reciprocal Rank Fusion）融合
    #   为什么换掉线性加权：两路打分量纲不同（余弦∈[0,1]、BM25 无上界），
    #   归一化系数对语料敏感；RRF 只看**排名**，对分数量纲免疫，是混合检索的行业标准。
    bm25 = _bm25_scores(tf_all, tokenize(q_expanded))
    if bm25:
        scored = _rrf_fuse(scored, sorted(bm25.items(), key=lambda x: x[1], reverse=True),
                           k=RRF_K, cap=RRF_CAP)

    # 倒排索引命中加成（FR-3.2.2）
    inverted_index = payload.get("inverted_index", {})
    if inverted_index:
        inv_hits = _inverted_lookup(tokenize(q), inverted_index)
        scored_dict = dict(scored)
        for rel, count in inv_hits.items():
            if rel in scored_dict:
                scored_dict[rel] += INVERTED_HIT_BOOST * count
            else:
                scored_dict[rel] = INVERTED_HIT_BOOST * count
        scored = list(scored_dict.items())
        scored.sort(key=lambda x: x[1], reverse=True)

    # 应用过滤器
    has_filters = any([domain, content_type, author, min_importance is not None,
                        enforce_level, tags, date_from, date_to])
    if has_filters:
        scored = _apply_filters(scored, root, domain, content_type, author,
                                min_importance, enforce_level, tags, date_from, date_to)

    # P0 Stage3: freshness 过滤 —— 排除非可检索状态 / 陈旧（review_after 到期或超龄）
    if exclude_stale:
        kept = []
        for r, s in scored:
            fm = load_meta(Path(root) / r)[0]
            if retrievable_status(fm) and not is_stale(fm):
                kept.append((r, s))
        scored = kept

    # P0: 知识分层降级/提升 —— 正典(canon)提升、raw 证据层与空壳降级，
    #   让「有用内容」排在「原始记录/目录页」之前。raw 仍可被 --include-raw 正常召回。
    if not include_raw or not include_stubs:
        adjusted = []
        for r, s in scored:
            m = meta_all.get(r, {}) or {}
            layer = str(m.get("layer", "") or "").lower()
            if layer == "raw" and not include_raw:
                s = s * LAYER_MULTIPLIER.get("raw", 0.72)
            elif layer in ("index", "noise") and not include_stubs:
                s = s * LAYER_MULTIPLIER.get(layer, 0.60)
            elif layer == "canon":
                s = s * LAYER_MULTIPLIER.get("canon", 1.25)
                # P3：置信分参与排序（高置信优先，低置信让位）
                try:
                    cf = float(m.get("confidence", 0.5))
                except (TypeError, ValueError):
                    cf = 0.5
                s = s * (1.0 + CONF_WEIGHT * (cf - 0.5) * 2)
            adjusted.append((r, s))
        scored = sorted(adjusted, key=lambda x: x[1], reverse=True)

    # 检索结果去重（FR-3.1.8）
    deduped = _dedup_chunks(scored, top, meta_all)
    hits = [(item[0], item[1]) for item in deduped]
    chunk_siblings_map = {item[0]: item[2] for item in deduped if len(item) > 2}

    # P2: 语义重排（本地嵌入）—— 只看 top-N，超预算/不可用即原样返回
    #   KB_NO_EMBED_RERANK=1 可关闭（对照实验 / 端点故障时手动降级）
    # 默认**关闭**：30 题黄金集实测 top1/top3 与不重排完全一致（候选本来就干净），
    #   而它带 0.3s 延迟与嵌入调用成本 → 需要时用 KB_EMBED_RERANK=1 开启。
    try:
        if os.environ.get("KB_EMBED_RERANK") != "1":
            raise ImportError("embed-rerank disabled by default")
        from embed_rerank import EmbedReranker
        _er = EmbedReranker(Path(root))
        if _er.available():
            _texts = {}
            for rel, _sc in hits[:25]:
                m = meta_all.get(rel) or {}
                _f, _b = load_meta(Path(root) / rel)
                _texts[rel] = (str(m.get("context") or "") + "\n" + _b)[:2000]
            hits = _er.rerank(q, hits, _texts)
    except Exception:
        pass

    # 兼容保留：启发式 rerank（默认让位给上面的语义重排，可用 KB_HEURISTIC_RERANK=1 启用）
    if os.environ.get("KB_HEURISTIC_RERANK") == "1":
        try:
            from rerank import rerank as _rerank
            reranked = _rerank(q, hits, root)
            hits = [(rel, new_sc) for rel, new_sc, _ in reranked]
        except ImportError:
            pass
    latency_ms = int((time.time() - t0) * 1000)

    if as_json:
        # JSON 结构化输出
        hit_list = []
        for rel, sc in hits:
            fm, body = load_meta(Path(root) / rel)
            h = _build_hit(rel, sc, fm, body, q, context, root=root, meta_all=meta_all,
                           sib_scores=dict(hits))
            if rel in chunk_siblings_map:
                h["chunk_siblings"] = chunk_siblings_map[rel]
            hit_list.append(h)
        output = _build_json_output(q, hit_list, len(tf_all), latency_ms)
        if context_budget:
            output["context"] = _context_budget(hit_list, context_budget)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        # P2: 写入缓存
        if cache is not None:
            try:
                from state_manager import StateStore
                Store = StateStore(root)
                Store.update("query_cache.json",
                    lambda c: c.update({cache_key: {
                        "result": output, "ts": time.time(),
                        "paths": [h["path"] for h in hit_list]}}) or c)
            except (ImportError, OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
    else:
        # 现有人类可读输出（保持不变）
        lines = [f"🔎 问题: {q}", f"命中 {len(hits)} 处（TF-IDF 余弦）", ""]
        for rel, sc in hits:
            body = strip_body((Path(root) / rel).read_text(encoding="utf-8"))
            snippet = best_sentence(body, q)
            lines += [f"⭐ {sc:.3f}  ← {rel} · {meta_all.get(rel,{}).get('domain','-')}",
                      f"   {snippet}", ""]
        text_output = "\n".join(lines)
        print(text_output)
        # P2: 写入缓存
        if cache is not None:
            try:
                from state_manager import StateStore
                StateStore(root).update("query_cache.json",
                    lambda c: c.update({cache_key: {
                        "text": text_output, "ts": time.time(),
                        "paths": [rel for rel, _sc in hits]}}) or c)
            except (ImportError, OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
        if answer:
            ans, detail = llm_answer(root, q, hits)
            if ans:
                print(f"\n💬 本地模型作答:\n{ans}")
            elif detail:
                print(f"\n⚠️  {detail}")
            else:
                print("\n💬 （未配置 ORNITH 凭证/地址，返回检索片段作为答案；设 ORNITH_API_KEY 与 ORNITH_BASE_URL 后可生成）")

    # 命中记录（auto_session，权重 0.3）
    if session and hits:
        hit_list_for_record = []
        for rel, sc in hits:
            fm, body = load_meta(Path(root) / rel)
            hit_list_for_record.append(_build_hit(rel, sc, fm, body, q, context))
        _record_hits(q, hit_list_for_record, session, root)

    return 0

# ── 同义词扩展（FR-3.2.4 + FR-3.2.5）────────────────────────
SYNONYMS_PATH = Path(__file__).resolve().parents[1] / "reference" / "synonyms.json"
_synonyms_cache = None
_synonyms_reverse = None


def _load_synonyms(path=None):
    global _synonyms_cache, _synonyms_reverse
    if _synonyms_cache is not None and path is None:
        return _synonyms_cache, _synonyms_reverse
    p = Path(path) if path else SYNONYMS_PATH
    if not p.exists():
        _synonyms_cache, _synonyms_reverse = {}, {}
        return _synonyms_cache, _synonyms_reverse
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.pop("_meta", None)
    # 反向索引：同义词 → 规范词
    reverse = {}
    for canonical, syns in raw.items():
        reverse[canonical] = canonical
        for s in syns:
            reverse[s.lower()] = canonical
    _synonyms_cache = raw
    _synonyms_reverse = reverse
    return raw, reverse


def _expand_query(q):
    """查询扩展：返回扩展后的查询字符串。"""
    _, reverse = _load_synonyms()
    toks = tokenize(q)
    expanded = set(toks)
    for t in toks:
        canon = reverse.get(t.lower()) or reverse.get(t)
        if canon:
            expanded.add(canon)
            expanded.update(tokenize(canon))
    return " ".join(expanded)


# 结论词偏好（FR-3.2.7 best_sentence 语义化）
CONCLUSION_WORDS = {"因此", "所以", "结论", "综上", "总之", "可见", "于是",
                    "决定", "采用", "选择", "确认", "最终", "结果", "核心",
                    "关键", "本质", "原理是", "机制是", "规则是"}


def _snippet_candidate(line: str) -> bool:
    """这一行是否值得作为检索片段：过滤溯源页脚/纯指针行，避免片段全是"见某某文件"。"""
    t = line.strip()
    if not t:
        return False
    if t.startswith((">", "|", "```", "---")):
        return False
    if t.startswith("原记忆") or "原记忆（原文保真）" in t:
        return False
    if re.match(r"^[-*]\s*\[\[", t):            # 纯 wikilink 列表项
        return False
    if re.match(r"^[-*]\s*\S{0,80}→\s*\S+$", t):  # 纯箭头指针
        return False
    return True


def best_sentence(body: str, q: str) -> str:
    """取与查询最相关的**连续片段**（1–3 句，≤320 字）。

    比"单句最相关"更实用：Agent 拿到的是自洽的一小段知识，而不是被截断的半句话；
    同时过滤溯源页脚与纯指针行（否则命中片段常常是"见 xxx.md"）。
    """
    qs = Counter(tokenize(q))
    lines = [l for l in str(body or "").splitlines() if _snippet_candidate(l)]
    if not lines:
        return str(body or "").strip()[:240]
    body2 = "\n".join(lines)
    sents = [s.strip() for s in re.split(r"[。！？\n]", body2) if s.strip()]
    if not sents:
        return ""
    if not qs:
        return body2.strip()[:240]

    def s_score(s: str) -> float:
        st = Counter(tokenize(s))
        kw = sum(st[t] * qs.get(t, 0) for t in st)
        concl = 0.5 if any(w in s for w in CONCLUSION_WORDS) else 0
        L = len(s)
        len_bonus = 0.3 if 30 <= L <= 120 else (0.1 if L > 120 else 0)
        return kw + concl + len_bonus

    base = [s_score(x) for x in sents]
    best_i, best_val = 0, -1.0
    for i in range(len(sents)):
        window, total = [], 0.0
        for j in range(i, min(i + 3, len(sents))):
            window.append(sents[j])
            total += base[j]
            if sum(len(x) for x in window) > 320:
                break
            val = total * (1.0 + 0.15 * (len(window) - 1))   # 连贯性加成，但不过度贪多
            if val > best_val:
                best_val, best_i = val, i
    out, n = [], 0
    for j in range(best_i, min(best_i + 3, len(sents))):
        if n + len(sents[j]) > 320 and out:
            break
        out.append(sents[j]); n += len(sents[j])
    txt = "。".join(out) if len(out) > 1 else out[0]
    return txt

def llm_answer(root: Union[str, Path], q: str, hits: List[Tuple[str, float]]) -> Tuple[Optional[str], str]:
    """返回 (答案文本或 None, 提示或空串)：
        (None, "")      → 未配任何 ORNITH 项，静默返回检索片段（信息级）
        (None, "提示")   → 配置不全或端点不可达，给出精准指引（让用户知道缺什么/怎么修）
        (答案, "")      → 成功作答
    """
    key = os.environ.get("ORNITH_API_KEY")
    # vLLM 地址必须通过环境变量 ORNITH_BASE_URL 显式指定，绝不连写死的 LAN 地址。
    url = os.environ.get("ORNITH_BASE_URL", "").strip()
    if not key and not url:
        return None, ""                                          # 都没配：信息级
    if not url:
        return None, ("检测到 ORNITH_API_KEY 但未设 ORNITH_BASE_URL，无法调用模型；"
                      "请 export ORNITH_BASE_URL=http://<vllm>/v1"
                      "（Windows：set ORNITH_BASE_URL=<url>），或用检索片段作答。")
    if not key:
        return None, ("已设 ORNITH_BASE_URL 但缺 ORNITH_API_KEY，无法认证；"
                      "请 export ORNITH_API_KEY=…（Windows：set ORNITH_API_KEY=…），或用检索片段作答。")
    try:
        import urllib.request
        ctx = "\n".join(f"[{r}] {best_sentence((Path(root)/r).read_text(encoding='utf-8'),q)}" for r,_ in hits)
        msgs = [{"role":"system","content":"你是知识库助手，仅依据下方资料回答。"},
                 {"role":"user","content":f"资料:\n{ctx}\n\n问题：{q}"}]
        req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(
            {"model":"ornith1.5-35b","messages":msgs,"temperature":0.2}).encode(),
            headers={"authorization":f"Bearer {key}","content-type":"application/json"})
        content = json.loads(urllib.request.urlopen(req,timeout=40).read())["choices"][0]["message"]["content"]
        return content, ""
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        return None, (f"连接 ORNITH 端点 {url} 失败（{e}）；请检查 ORNITH_BASE_URL 是否可访问，"
                      "已返回检索片段作为答案。")

if __name__ == "__main__":
    sys.exit(main())
