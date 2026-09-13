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
import argparse, os, re, sys, json, math, time, datetime, hashlib
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT, EXCLUDE, FM, load_meta, iter_notes, tokenize, count_tokens, content_fingerprint

IDX_DIR = "vector index"
IDX_VERSION = 2  # df_idf.json schema 版本；v2 新增 doc_hashes/doc_mtimes/inverted_index


def load_index(idx_path):
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

def strip_body(text):
    m = FM.search(text)
    if m:
        end = FM.search(text, m.end())
        if end:
            return text[end.end():]
    return text

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("index"); d.add_argument("--root", default=ROOT_DEFAULT)
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
    args = ap.parse_args()

    if args.cmd == "index":
        return cmd_index(args.root)
    if args.cmd == "query":
        return cmd_query(args.root, args.q, args.top, args.answer,
                         as_json=args.as_json, context=args.context,
                         context_budget=args.context_budget, session=args.session,
                         domain=args.domain, content_type=args.content_type,
                         author=args.author, min_importance=args.min_importance,
                         enforce_level=args.enforce_level, tags=args.tags,
                         date_from=args.date_from, date_to=args.date_to)
    return 1

def cmd_index(root, incremental=True):
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
        fm, body = load_meta(p)
        if fm.get("kb_action") == "retire":
            continue  # 归档/停用不入检索
        toks = tokenize(body + " " + " ".join(fm.get("tags", "").split()))
        if not toks:
            continue
        c = Counter(toks)
        docs[rel] = c
        for t in c: df[t] += 1
        tf_all[rel] = c
        meta_all[rel] = {"title": fm.get("title", p.stem),
                         "domain": fm.get("domain", "-"), "tags": fm.get("tags", "")}
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

    for rel, p in current_files.items():
        fm, body = load_meta(p)
        if fm.get("kb_action") == "retire":
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

    deleted = [rel for rel in old_hashes if rel not in current_files]

    if not changed and not deleted:
        print("✅ 索引已是最新（无变更）")
        return 0

    # 增量更新 tf/meta
    tf_all = dict(old.get("tf", {}))
    meta_all = dict(old.get("meta", {}))
    for rel, p, fm, body in changed:
        toks = tokenize(body + " " + " ".join(fm.get("tags", "").split()))
        if not toks:
            tf_all.pop(rel, None)
            meta_all.pop(rel, None)
            continue
        tf_all[rel] = Counter(toks)
        meta_all[rel] = {"title": fm.get("title", p.stem),
                         "domain": fm.get("domain", "-"), "tags": fm.get("tags", "")}
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


# ── 检索结果去重（FR-3.1.8）──────────────────────────────────
def _dedup_chunks(scored, top):
    """同一笔记多 chunk 命中时只保留最高分，并标注 chunk_siblings。"""
    seen = {}  # rel -> (score, count)
    for rel, sc in scored:
        if rel in seen:
            seen[rel] = (max(seen[rel][0], sc), seen[rel][1] + 1)
        else:
            seen[rel] = (sc, 1)
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


def _build_hit(rel, score, fm, body, query, context):
    """构造单条 JSON hit。"""
    snippet = best_sentence(body, query)
    if len(snippet) > context:
        snippet = snippet[:context] + "…"
    return {
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


def _record_hits(query, hits, session, root):
    """记录命中到 .feedback_state.json（权重 0.3，auto_session）。"""
    sp = Path(root) / "pipeline" / ".feedback_state.json"
    try:
        state = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    except (json.JSONDecodeError, OSError):
        state = {}
    state.setdefault("agent_hits", {})[session] = {
        "query": query,
        "hits": [h["path"] for h in hits],
        "timestamp": datetime.datetime.now().isoformat(),
        "weight": 0.3,
    }
    sp.parent.mkdir(parents=True, exist_ok=True)
    try:
        sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def cmd_query(root, q, top, answer, as_json=False, context=240, context_budget=None,
              session=None, domain=None, content_type=None, author=None,
              min_importance=None, enforce_level=None, tags=None,
              date_from=None, date_to=None):
    t0 = time.time()
    # P2: 检索缓存（FR-3.1.9）
    cache_key = hashlib.sha256(json.dumps({
        "q": q, "top": top, "domain": domain, "content_type": content_type,
        "author": author, "min_importance": min_importance,
        "enforce_level": enforce_level, "tags": tags,
        "date_from": date_from, "date_to": date_to,
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

    # 检索结果去重（FR-3.1.8）
    deduped = _dedup_chunks(scored, top)
    hits = [(item[0], item[1]) for item in deduped]
    chunk_siblings_map = {item[0]: item[2] for item in deduped if len(item) > 2}

    # P2: 重排 rerank（FR-3.2.6）
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
            h = _build_hit(rel, sc, fm, body, q, context)
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
                    lambda c: c.update({cache_key: {"result": output, "ts": time.time()}}) or c)
            except Exception:
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
                    lambda c: c.update({cache_key: {"text": text_output, "ts": time.time()}}) or c)
            except Exception:
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


def best_sentence(body, q):
    qs = Counter(tokenize(q))
    if not qs: return body.strip()[:240]
    sents = re.split(r"[。！？\n]", body)
    sents = [s.strip() for s in sents if s.strip()]
    def score(s):
        st = Counter(tokenize(s))
        kw_score = sum(st[t] * qs.get(t, 0) for t in st) or 0
        # 结论词加成
        concl_bonus = 0.5 if any(w in s for w in CONCLUSION_WORDS) else 0
        # 长度偏好：30-120 字最佳
        L = len(s)
        if 30 <= L <= 120:
            len_bonus = 0.3
        elif L > 120:
            len_bonus = 0.1
        else:
            len_bonus = 0
        return kw_score + concl_bonus + len_bonus
    best = max(sents, key=score) if sents else ""
    return (best[:240] + "…" if len(best) > 240 else best)

def llm_answer(root, q, hits):
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
