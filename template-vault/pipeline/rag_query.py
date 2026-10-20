"""rag_query — 查询执行与结果组装（从 rag.py 拆分，P1-3）。

提供 cmd_query 主流程：过滤、标题加权、命中构建、上下文预算、JSON 输出、
同义词扩展、摘要（best_sentence）、LLM 问答（llm_answer）。

依赖 rag_index（索引加载 / all_doc_vectors）与 rag_score（评分 / 融合 / 去重）。
"""
import os
import re
import sys
import json
import time
import uuid
import hashlib
import datetime
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from kb_common import (
    LAYER_MULTIPLIER, count_tokens, is_index_stub, is_stale,
    load_meta, retrievable_status, tokenize,
)
from rag_index import (
    IDX_DIR, IDX_VERSION, index_file, load_index, strip_body, all_doc_vectors,
)
from rag_score import (
    CONF_WEIGHT, INVERTED_HIT_BOOST, PRIORITY_MULTIPLIER, RRF_CAP, RRF_K,
    _apply_retrieval_adjustments, _bm25_postings, _bm25_scores, _cos,
    _dedup_chunks, _has_any_tag, _inverted_lookup, _parse_importance,
    _parse_tags, _rrf_fuse, _score_cosine_fast, _score_cosine_postings,
    _vec, dense_fill_tail, dense_rank,
)

# ── 查询常量 ──────────────────────────────────────────────


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

TITLE_BOOST = 0.55      # 标题/路径命中：最多把分数提到 1+0.55 倍（保守，避免压过真正高相关）

TITLE_BOOST_CAP = 200   # 只对前 N 个候选算（控开销）

def _title_boost(scored, query: str, meta_all: dict):
    """按「查询词在标题/路径/摘要里的覆盖度」给候选加权。"""
    q_tokens = {t for t in tokenize(query) if len(t) > 1}
    if not q_tokens:
        return scored
    out = []
    for i, (rel, sc) in enumerate(scored):
        if i >= TITLE_BOOST_CAP or not sc:
            out.append((rel, sc))
            continue
        m = meta_all.get(rel) or {}
        tt = str(m.get("title_text") or "")
        if not tt:
            out.append((rel, sc)); continue
        hit = {t for t in q_tokens if t in tt}
        if not hit:
            out.append((rel, sc)); continue
        cov = len(hit) / len(q_tokens)
        out.append((rel, sc * (1.0 + TITLE_BOOST * cov)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out

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

def _build_json_output(query, hits, n_indexed, latency_ms, adjustments=None,
                       dense_candidates=0):
    """构造完整 JSON 输出。

    `adjustments` 是本次实际应用的检索修正契约条目（L1 回路），列出来是为了**可审计**：
    谁被加成、因为哪条契约，一眼可查。
    """
    out = {
        "query": query,
        "hits": hits,
        "meta": {"n_indexed": n_indexed, "latency_ms": latency_ms},
    }
    if dense_candidates:
        out["meta"]["dense_candidates"] = dense_candidates
    if adjustments:
        out["adjustments_applied"] = adjustments
    return out

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
              include_raw: bool = False, include_stubs: bool = False,
              no_record: bool = False) -> int:
    t0 = time.time()
    # 是否记录使用信号。评测/探针（kb eval、kb_adaptretrieve、对照实验）必须关掉：
    # 它们用 rag query 当 oracle，若照常写 agent_hits，一小时内的重跑会把再问率顶到 1、
    # 成功率代理打到 0，直接压低 fitness —— 测量行为污染被测对象。
    record = (not no_record) and os.environ.get("KB_NO_FEEDBACK", "") not in ("1", "true", "yes")
    # 捕获打通：无 session 时自动生成，确保每次查询都写 per-query 事件（供 kb_usage 量再问率）
    if not session and record:
        session = _gen_session()
    # P2: 检索缓存（FR-3.1.9）
    cache_key = hashlib.sha256(json.dumps({
        "q": q, "top": top, "domain": domain, "content_type": content_type,
        "author": author, "min_importance": min_importance,
        "enforce_level": enforce_level, "tags": tags,
        "date_from": date_from, "date_to": date_to,
        # context（片段长度）与 answer（是否调 LLM 作答）都会改变输出，必须入键；
        # as_json 决定缓存里存的是 result 还是 text，不入键会导致「文本查一次、
        # 5 分钟内同问题加 --json 查」命中错形状的条目 → KeyError: 'result'。
        "context": context, "answer": bool(answer), "as_json": bool(as_json),
        "exclude_stale": bool(exclude_stale),
        "include_raw": bool(include_raw), "include_stubs": bool(include_stubs),
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    try:
        from state_manager import StateStore
        # no_record（探针/评测/脚本调用）不应读/写检索缓存：既要拿**最新**结果，
        # 也不该把 .kb/state/query_cache.json 写进模板库（kb-context 插件/评测跑在
        # 模板上会污染模板，触发 test_no_state_dir_in_template）。写缓存已在下方用
        # `if cache is not None` 守卫，这里置 None 即同时禁掉读与写。
        cache = StateStore(root).load("query_cache.json", {}) if record else None
        entry = cache.get(cache_key) if cache is not None else None
        if entry and (time.time() - entry.get("ts", 0)) < 300:  # TTL 5 分钟
            # 老条目可能只存了 text 或只存了 result（as_json 以前不在键里）：
            # 取不到就跳过缓存走正常检索，绝不 KeyError。
            cached_out = entry.get("result") if as_json else entry.get("text")
            if cached_out is not None:
                if as_json:
                    print(json.dumps(cached_out, ensure_ascii=False, indent=2))
                else:
                    print(cached_out)
                # 反馈闭环：命中缓存也要记录本次查询命中（供 kb_usage 量再问率），
                # 不能被缓存短路，否则重复查询不再产生反馈信号。
                paths = entry.get("paths") or []
                if session and paths:
                    if record:
                        _record_hits(q, [{"path": p} for p in paths], session, root)
                return 0
    except ImportError:
        cache = None

    idx_path = index_file(Path(root) / IDX_DIR)     # pickle 优先，回退旧 json
    payload, ver = load_index(idx_path) if idx_path else (None, 0)
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
    vocab, idf, meta_all = payload["vocab"], payload["idf"], payload["meta"]
    tf_all = payload.get("tf")                 # v7 起不再写；仅在旧索引回退路径用到
    postings = payload.get("postings")         # v7 主结构：词 → [doc_id, tf, ...]
    docs = payload.get("docs") or []
    # 查询扩展（FR-3.2.4）
    q_expanded = _expand_query(q)
    qv = _vec(Counter(tokenize(q_expanded)), vocab, idf)
    norms = payload.get("norms")
    if postings and norms:
        scored = _score_cosine_postings(postings, docs, idf, qv, norms)
    elif norms and tf_all is not None:
        scored = _score_cosine_fast(tf_all, idf, qv, norms)
    else:
        # 旧索引（v<5）没有预算范数：回退到物化全库向量，并提示重建
        scored = [(r, _cos(v, qv)) for r, v in all_doc_vectors(payload).items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = [(r, s) for r, s in scored if s > 0]

    # P2: 混合检索 —— 余弦 + BM25 用 RRF（Reciprocal Rank Fusion）融合
    #   为什么换掉线性加权：两路打分量纲不同（余弦∈[0,1]、BM25 无上界），
    #   归一化系数对语料敏感；RRF 只看**排名**，对分数量纲免疫，是混合检索的行业标准。
    if postings:
        bm25 = _bm25_postings(postings, docs, tokenize(q_expanded),
                              dl=payload.get("dl"), avgdl=payload.get("avgdl"))
    else:
        bm25 = _bm25_scores(tf_all, tokenize(q_expanded),
                            dl=payload.get("dl"), avgdl=payload.get("avgdl"),
                            df=payload.get("df"))
    if bm25:
        scored = _rrf_fuse(scored, sorted(bm25.items(), key=lambda x: x[1], reverse=True),
                           k=RRF_K, cap=RRF_CAP)

    # 倒排索引命中加成（FR-3.2.2）
    inverted_index = payload.get("inverted_index", {})
    if inverted_index:
        inv_hits = _inverted_lookup(tokenize(q), inverted_index, docs=payload.get("docs"))
        scored_dict = dict(scored)
        for rel, count in inv_hits.items():
            if rel in scored_dict:
                scored_dict[rel] += INVERTED_HIT_BOOST * count
            else:
                scored_dict[rel] = INVERTED_HIT_BOOST * count
        scored = list(scored_dict.items())
        scored.sort(key=lambda x: x[1], reverse=True)

    # 稠密语义通道只做**尾部槽位填充**（见 dense_fill_tail 的实测说明），
    # 不参与头部排序 —— 所以先算出来，等去重拿到 top-k 后再填。
    _dense_hits = dense_rank(root, q, payload)

    # L1 检索修正契约（kb adaptretrieve apply 产出）：同一 query 再问时把上次漏掉的笔记拉回来
    scored, _adjust_applied = _apply_retrieval_adjustments(
        root, q, scored, indexed=set(docs) if docs else None)

    # P5：标题/路径/摘要匹配加成 —— 修「自指问题」盲区
    #   原理：长文里关键词稀疏，纯 TF-IDF/BM25 会把高信号文档排到后面；
    #   而标题/路径里出现查询词，是**高置信的定位信号**。
    # **默认关闭**：受控实测 top3 全正典 70% → 60%（负增益）。
    #   机理推测：标题/路径命中的文档与"问题本身更相关"并不总是同一回事，
    #   加权会把工程报告类文档（标题含系统术语）顶到前面，挤掉真正的领域笔记。
    if scored and meta_all and os.environ.get("KB_TITLE_BOOST", "0") == "1":
        scored = _title_boost(scored, q, meta_all)

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
    if _dense_hits:
        deduped = dense_fill_tail(deduped, _dense_hits, top)
    hits = [(item[0], item[1]) for item in deduped]
    chunk_siblings_map = {item[0]: item[2] for item in deduped if len(item) > 2}

    # 索引过期守卫：索引只校验 version，笔记被删/改名后仍会以 #1 返回「幽灵命中」
    # （snippet 空、domain "-"、退出码 0、零警告）。这里做一次存在性核对并剔除。
    ghost = {rel for rel, _sc in hits if not (Path(root) / rel).exists()}
    if ghost:
        hits = [(rel, sc) for rel, sc in hits if rel not in ghost]
        print(f"⚠️ 索引已过期：{len(ghost)} 条命中在磁盘上已不存在，已剔除"
              f"（跑 `kb rag index` 重建索引）：{', '.join(sorted(ghost)[:3])}"
              f"{' …' if len(ghost) > 3 else ''}", file=sys.stderr)

    # P2: 语义重排（本地嵌入）—— 只看 top-N，超预算/不可用即原样返回
    #   KB_NO_EMBED_RERANK=1 可关闭（对照实验 / 端点故障时手动降级）
    # **默认关闭**（受控实测 2026-10-01，30 题黄金集，禁查询缓存）：
    #   不重排           canon@K 88.7% · top1 90% · top3 70%
    #   +cross-encoder   canon@K 87.7% · top1 70% · top3 50%   ← 明显更差
    #   原因：首段（分层+RRF+标题加权）已经足够好，重排器用**纯语义**覆盖了
    #   带元数据强信号的首段排序，把"元数据明显更对题"的结果换成了"语义上像"的结果。
    #   保留能力与开关，但默认不启用：KB_RERANK=cross|embed|0
    _mode = os.environ.get("KB_RERANK", "0").strip().lower()
    try:
        if _mode not in ("0", "off", "none"):
            _texts = {}
            for rel, _sc in hits[:60]:
                m = meta_all.get(rel) or {}
                _f, _b = load_meta(Path(root) / rel)
                _texts[rel] = (str(m.get("context") or "") + "\n" + _b)[:2000]
            if _mode == "embed":
                from embed_rerank import EmbedReranker
                _er = EmbedReranker(Path(root))
                if _er.available():
                    hits = _er.rerank(q, hits, _texts)
            else:
                from embed_rerank import CrossEncoderReranker
                _ce = CrossEncoderReranker()
                if _ce.available():
                    hits = _ce.rerank(q, hits, _texts)
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
        # 已索引篇数：v7 没有 tf → 用 docs（等价于原 len(tf_all)）
        output = _build_json_output(q, hit_list, payload.get("n_docs") or len(docs), latency_ms,
                                    adjustments=_adjust_applied,
                                    dense_candidates=len(_dense_hits))
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
    if record and session and hits:
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
    """查询扩展（双向，P5 修复）。

    修前只做「同义词 → 规范词」：查「正典」时 reverse 把它映射回「正典」自己，
    于是展开后**完全没有新增词**——同义词表等于白建（实测：自指类问题检索失败的原因之一）。

    现在做**两个方向**：
      · 同义词 → 规范词（gather 到用户实际用的说法）
      · 规范词/同义词 → 该条目的**全部同义说法**（把库里的另一种表述也拉进来）
    """
    # **默认关闭**：受控实测 canon@K 88.7% → 88.3%、top3 70% → 67%（轻微负增益）。
    #   机理：双向扩展把大量同义表述塞进查询，等于给查询加了噪声——
    #   而自指问题的痛点已由 docs/concepts.md（集中定义）解决，不靠扩展。
    if os.environ.get("KB_SYNONYM_EXPAND", "0") != "1":
        # P6：可选 **LLM 查询改写**（KB_QUERY_EXPAND=llm）
        #   实测 30 题：canon@K 91.3% → **95.3%**（top1 100% 不降 / top3 90% 不降）
        #   代价：每次查询 +10~20s（本地 chat 串行）→ 默认关，按需开
        if os.environ.get("KB_QUERY_EXPAND", "").strip().lower() == "llm":
            try:
                from llm_rerank import expand_query as _ex
                return _ex(q) or q
            except Exception:
                return q
        return q
    table, reverse = _load_synonyms()
    toks = tokenize(q)
    expanded = set(toks)
    for t in toks:
        canon = reverse.get(t.lower()) or reverse.get(t) or t
        if canon:
            expanded.add(canon)
            expanded.update(tokenize(canon))
        # 反向补全：把该条目下所有同义说法都加进来（含英文/缩写/别称）
        for key in (canon, t, t.lower()):
            syns = table.get(key)
            if not syns:
                continue
            for s in syns:
                expanded.add(str(s))
                expanded.update(tokenize(str(s)))
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
