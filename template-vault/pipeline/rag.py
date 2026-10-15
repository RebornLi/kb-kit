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
import argparse, os, pickle, re, sys, json, math, time, datetime, hashlib, uuid
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import cos as _common_cos
from kb_common import ROOT_DEFAULT, EXCLUDE, FM, load_meta, iter_notes, tokenize, count_tokens, content_fingerprint, index_excluded, is_generated_report, is_stale, retrievable_status, kb_layer_of, layer_multiplier, is_index_stub, LAYER_MULTIPLIER, parent_of, root_of

try:                       # 上下文强化（P2）：缺失/异常都不影响索引可用
    from contextual import cch as _cch
except Exception:          # pragma: no cover
    def _cch(rel, fm, body):
        return ""

# 是否把上下文字符串（CCH）拼进索引文本。默认开；`KB_CONTEXT_IN_INDEX=0` 可关闭。
# 开关存在的意义是**可量化**：Anthropic 的 Contextual Retrieval 报告失败率 −35%/−49%/−67%，
# 但那是他们语料上的数字。本库的增益此前**从未单独测量**（代码注释里只有"默认开"），
# 于是留下一处"看起来有依据、实际没测过"的配置。见 tests/../docs/EVOLUTION-REVIEW.md 实测记录。
CONTEXT_IN_INDEX = os.environ.get("KB_CONTEXT_IN_INDEX", "1") not in ("0", "false", "no")

IDX_DIR = "vector index"
IDX_STEM = "df_idf"
IDX_PICKLE = IDX_STEM + ".pkl"    # 主格式：pickle 加载 70ms vs json 161ms（2.3×），体积还小 20%
IDX_JSON = IDX_STEM + ".json"      # 兼容旧库读取 / 可读导出
IDX_VERSION = 8  # v8 + dense（稀疏随机投影后的语义向量，用于补召回）
                  # v7 + postings（词→[doc,tf,...] 扁平表）：打分只访问命中文档，
                  #    并可从长度导出 df、免掉 6.7MB 的文档主序 tf
                  # v6 + df（BM25 免每查询重算词频文档数：71.3ms→1.2ms，分数逐位一致）
                  #    + docs/倒排 postings 改存整数 doc id（省 3 MB 重复路径文本）；
                  # v5 + norms/dl/avgdl（查询期免物化全库向量）；
                 # v2 doc_hashes/doc_mtimes/inverted_index；v3 layer/chunk_of；
                 # v4 context（上下文强化，只进索引）+ parent/root（父子块：查子块、返回父块）


# pickle 白名单：索引 payload 实际用到的非内置（但仍属纯数据）类型。
# 注意 Counter 是 dict 子类，用 isinstance 检查会被漏过 —— 必须显式列出。
_SAFE_PICKLE_GLOBALS = {
    ("collections", "Counter"),
    # v8 起索引里存稠密向量（array('f')）。**加任何新类型都必须登记到这里**，
    # 否则 unpickle 会抛 UnpicklingError → load_index 当"索引损坏"静默吞掉 →
    # 查询报 "index not found"（这正是本轮踩到的坑：v8 加 array 后忘了登记）。
    # tests/test_p0_fixes.py::IndexPayloadRoundTripTest 会拦住这类遗漏。
    ("array", "_array_reconstructor"),
    ("array", "array"),
}


class _SafeUnpickler(pickle.Unpickler):
    """只允许内置容器/标量与白名单内的纯数据类型，其余一律拒绝。

    索引是本地派生缓存（`vector index/` 被 gitignore、`kb rag index` 随时重建），
    但「一个可写文件 = 任意代码执行」是不必要的风险面：payload 全是纯数据，
    所以把 find_class 收紧到白名单 —— 速度不变（每个全局只查一次），风险归零。
    """

    def find_class(self, module, name):
        if (module, name) in _SAFE_PICKLE_GLOBALS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"索引里出现非白名单对象：{module}.{name}")


def index_file(idx_dir: Union[str, Path]) -> Optional[Path]:
    """返回该目录下可用的索引文件：优先 pickle，回退 JSON（旧库）。"""
    d = Path(idx_dir)
    for name in (IDX_PICKLE, IDX_JSON):
        p = d / name
        if p.exists():
            return p
    return None


def load_index(idx_path: Union[str, Path]) -> Tuple[Optional[Dict[str, Any]], int]:
    """加载索引。`idx_path` 可以是 `vector index/` 目录，也可以是具体文件。

    返回 (payload, version)：
      - (None, 0)  → 文件不存在或损坏
      - (dict, 0)  → 旧索引（无 version 字段），可正常读取，建议重建
      - (dict, N)  → 版本 N 索引

    格式：v6 起写 `df_idf.pkl`（受限 unpickle，见 `_SafeUnpickler`），
    仍能读旧的 `df_idf.json`（向后兼容，不强制迁移；重建后才拿到 pickle 的速度）。
    """
    p = Path(idx_path)
    if p.is_dir():
        found = index_file(p)
        if found is None:
            return None, 0
        p = found
    if not p.exists():
        return None, 0
    try:
        if p.suffix == ".pkl":
            with p.open("rb") as f:
                payload = _SafeUnpickler(f).load()
        else:
            payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:      # noqa: BLE001  损坏的缓存一律当"无索引"，让调用方重建
        return None, 0
    if not isinstance(payload, dict):
        return None, 0
    return payload, payload.get("version", 0)


def save_index(idx_dir: Union[str, Path], payload: Dict[str, Any]) -> Path:
    """写索引：主格式 pickle（快 2.3×、小 20%），写成功后清掉遗留的旧 JSON。

    顺序很关键：先原子写 pickle，确认落盘再删旧文件 —— 中途失败时旧索引仍在，
    下一个查询照常可用（不会出现「既没有 pkl 也没有 json」的空窗）。
    """
    d = Path(idx_dir)
    d.mkdir(parents=True, exist_ok=True)
    pkl = d / IDX_PICKLE
    tmp = d / (IDX_PICKLE + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(payload, f, protocol=4)   # 协议 4：Python 3.4+ 可读，性能与 5 持平
    os.replace(tmp, pkl)
    stale = d / IDX_JSON
    if stale.exists():
        try:
            stale.unlink()
        except OSError:
            pass
    return pkl

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
    q.add_argument("--no-record", action="store_true", dest="no_record",
                   help="不写入使用信号（agent_hits）；评测/探针/脚本调用必须带它，"
                        "否则会把「评测流量」灌进再问率、反过来压低 fitness")
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
                         include_raw=args.include_raw, include_stubs=args.include_stubs,
                         no_record=args.no_record)
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


def _title_text(rel: str, fm: dict) -> str:
    """标题/路径信号文本（查询时加权用）。

    为什么需要：自指类问题（"知识结晶正典是什么"）的答案在 docs/*.md 里，
    但"正典"在千词长文里只出现几次 → TF-IDF 被淹没，排不上来。
    标题/路径是**高置信的定位信号**：标题里有"正典/契约/结晶"的文档，几乎一定更对题。
    """
    title = str(fm.get("title") or Path(rel).stem)
    summary = str(fm.get("kb_summary") or "")[:120]
    tags = " ".join(str(t) for t in (fm.get("tags") or []) if isinstance(t, (str, int)))
    parts = [str(x) for x in Path(rel).parts]
    return " ".join([title, summary, tags, " ".join(parts)])


def _imp_of(fm: dict) -> float:
    """importance（0–1）；缺省 0.5（中性）。"""
    try:
        return max(0.0, min(1.0, float(fm.get("importance"))))
    except (TypeError, ValueError):
        return 0.5


def _ts_of(fm: dict) -> int:
    """updated/created 的 days-since-epoch（时效信号）；缺失返回 0。"""
    import datetime as _dt
    for k in ("updated", "created"):
        v = str(fm.get(k) or "")[:10]
        try:
            return (_dt.date.fromisoformat(v) - _dt.date(1970, 1, 1)).days
        except ValueError:
            continue
    return 0


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
    # 用解析器而不是写死 .json：v6 起主格式是 .pkl，写死会让增量以为"没有旧索引"
    # 从而每次全量重建（正是之前修过的"假增量"的同类问题）。
    idx_path = index_file(idx) or (idx / IDX_JSON)

    # 尝试增量
    if incremental:
        old_payload, old_ver = load_index(idx_path)
        if old_payload is not None and old_ver == IDX_VERSION:
            return _incremental_update(root, idx, old_payload)

    # 全量重建
    return _full_rebuild(root, idx)


def _build_dense(root, texts: Dict[str, str], hashes=None, old_dense=None,
                 old_hashes=None):
    """构建/更新稠密向量。任何不可用/失败 → 返回 `old_dense`（保持旧值，绝不弄坏索引）。

    增量友好：正文指纹未变的文档**复用旧向量**（哈希一致 ⇒ 文本一致 ⇒ 向量一致），
    只对新增/变更的文档调端点。这让人工改一篇笔记后的重索引只需 1 次嵌入调用。
    """
    if os.environ.get("KB_DENSE_INDEX", "") in ("0", "false", "no"):
        return old_dense
    try:
        import dense as _dense
    except ImportError:
        return old_dense
    if not _dense.available(root):
        print("⚠️  embedding 端点不可用：本次不建语义向量（检索退回纯词法）", file=sys.stderr)
        return old_dense
    prev = (old_dense or {}).get("vecs") or {}
    reuse = {}
    if prev and hashes and old_hashes:
        for rel, v in prev.items():
            if rel in hashes and hashes[rel] == (old_hashes or {}).get(rel):
                reuse[rel] = v
    todo = {r: tx for r, tx in texts.items() if r not in reuse}
    if todo:
        print(f"🔎 建语义向量：复用 {len(reuse)} 篇 · 新算 {len(todo)} 篇 …", file=sys.stderr)
    t0 = time.time()
    got = _dense.build_vectors(root, todo) if todo else ({}, int((old_dense or {}).get("dim_in") or 0))
    if got is None:
        print("⚠️  语义向量构建中断：沿用旧向量", file=sys.stderr)
        return old_dense
    new_vecs, dim_in = got
    vecs = dict(reuse)
    vecs.update(new_vecs)
    vecs = {r: v for r, v in vecs.items() if r in texts}    # 丢弃已移除的文档
    if todo:
        print(f"   语义向量就绪：{len(vecs)} 篇 · {time.time() - t0:.0f}s", file=sys.stderr)
    return {"dim": _dense.DIM_OUT, "dim_in": dim_in,
            "seed": int((old_dense or {}).get("seed") or 20261016), "vecs": vecs}


def _full_rebuild(root, idx):
    docs, tf_all, meta_all = {}, {}, {}
    doc_hashes, doc_mtimes = {}, {}
    dense_texts = {}
    df = Counter()
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue  # 运行期报告产物不入检索
        fm, body = load_meta(p)
        if index_excluded(fm):
            continue  # 归档/来源/显式退出：不入检索
        ctx = _cch(rel, fm, body) if CONTEXT_IN_INDEX else ""
        dense_texts[rel] = (ctx + "\n" + body)[:2000]
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
                         "confidence": _conf_of(fm),
                         "importance": _imp_of(fm),
                         "ctype": str(fm.get("content_type") or ""),
                         "updated_ts": _ts_of(fm),
                         # author / kb_summary 只为倒排索引服务：存进 meta 后，
                         # 重建倒排就不必再把整个库读一遍。
                         "author": str(fm.get("author") or ""),
                         "kb_summary": str(fm.get("kb_summary") or ""),
                         "title_text": _title_text(rel, fm)}
        doc_hashes[rel] = content_fingerprint(body)
        doc_mtimes[rel] = p.stat().st_mtime
    N = len(docs)
    vocab = sorted(df)
    idf = {t: math.log((N + 1) / (df[t] + 1)) + 1 for t in vocab}
    doc_list = sorted(tf_all)                       # 稳定顺序，便于跨重建比对
    doc_ids = {rel: i for i, rel in enumerate(doc_list)}
    postings = _build_postings(tf_all, doc_ids)
    inverted = _build_inverted_index(root, tf_all, meta_all, doc_ids)
    norms = _build_norms(tf_all, idf)
    dl = {r: sum(c.values()) for r, c in tf_all.items()}
    avgdl = round((sum(dl.values()) / N), 4) if N else 0.0
    payload = {"version": IDX_VERSION, "n_docs": N, "vocab": vocab, "idf": idf,
               "meta": meta_all,   # 注意：v7 起不再写 tf（由 postings 取代）
               "postings": postings,      # 词 → [doc_id, tf, ...]（扁平；df = 长度/2）
               "docs": doc_list,          # doc id → rel 还原表（postings/倒排共用）
               "doc_hashes": doc_hashes, "doc_mtimes": doc_mtimes,
               "norms": norms, "dl": dl, "avgdl": avgdl,
               # 不再写 df：`len(postings[t]) // 2` 就是（v7 起）
               "inverted_index": inverted}
    dense = _build_dense(root, dense_texts, hashes=doc_hashes)
    if dense:
        payload["dense"] = dense
    save_index(idx, payload)
    print(f"✅ 全量索引就绪  文档 {N}  词表 {len(vocab)}"
          + (f"  语义向量 {len(dense['vecs'])}" if dense else "（无语义向量）")
          + f"  → {idx}/{IDX_PICKLE}")
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
        # mtime 快路径必须在**任何读盘之前**：以前先 load_meta(p) 再比 mtime，
        # 于是「增量」也要把整个库读一遍（实测改 1 个文件读 65 个、无变更读 35 个）。
        # 只有「上轮已入索引且 mtime 未变」才可安全跳过：文件内容没变，是否排除也就没变。
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if old_mtimes.get(rel) == mtime and rel in old_hashes:
            current_hashes[rel] = old_hashes.get(rel, "")
            current_mtimes[rel] = mtime
            continue
        fm, body = load_meta(p)
        if index_excluded(fm):
            # 归档/停用/来源：不入本次索引；同时确保旧索引里的同 rel 被清掉
            # （文件仍在 current_files 里，不会被“物理删除”分支捕获，故显式记录）
            retired.add(rel)
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
    # v7 起索引不再写 tf（由 postings 取代）→ 增量构建需要还原一次文档主序 tf
    dense_texts = {}
    tf_all = dict(old.get("tf") or {})
    if not tf_all and old.get("postings"):
        tf_all = tf_from_postings(old)
    meta_all = dict(old.get("meta", {}))
    for rel, p, fm, body in changed:
        ctx = _cch(rel, fm, body) if CONTEXT_IN_INDEX else ""
        dense_texts[rel] = (ctx + "\n" + body)[:2000]
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
                         "confidence": _conf_of(fm),
                         "importance": _imp_of(fm),
                         "ctype": str(fm.get("content_type") or ""),
                         "updated_ts": _ts_of(fm),
                         "author": str(fm.get("author") or ""),
                         "kb_summary": str(fm.get("kb_summary") or ""),
                         "title_text": _title_text(rel, fm)}
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
    doc_list = sorted(tf_all)
    doc_ids = {rel: i for i, rel in enumerate(doc_list)}
    postings = _build_postings(tf_all, doc_ids)
    inverted = _build_inverted_index(root, tf_all, meta_all, doc_ids)
    norms = _build_norms(tf_all, idf)
    dl = {r: sum(c.values()) for r, c in tf_all.items()}
    avgdl = round((sum(dl.values()) / N), 4) if N else 0.0
    payload = {"version": IDX_VERSION, "n_docs": N, "vocab": vocab, "idf": idf,
               "meta": meta_all,   # 注意：v7 起不再写 tf（由 postings 取代）
               "postings": postings,
               "docs": doc_list,
               "doc_hashes": current_hashes, "doc_mtimes": current_mtimes,
               "norms": norms, "dl": dl, "avgdl": avgdl,
               # 不再写 df：`len(postings[t]) // 2` 就是（v7 起）
               "inverted_index": inverted}
    dense = _build_dense(root, dense_texts, hashes=current_hashes,
                         old_dense=old.get("dense"), old_hashes=old_hashes)
    if dense:
        payload["dense"] = dense
    save_index(idx, payload)
    print(f"✅ 增量索引完成  变更 {len(changed)}  删除 {len(deleted)}  总文档 {N}")
    return 0

def _doc_norm(counter, idf):
    """文档向量（乘 idf 后）的 L2 范数。口径必须与 _vec 完全一致：不在 idf 里的词不计入。

    **按词排序后求和**：累加顺序会影响浮点末位，而词的插入顺序在不同建索引路径下不同
    （全量按分词顺序、增量从 postings 还原后按词序）。不排序的话，同一篇笔记在
    「增量索引」与「全量重建」后范数会差 1 ULP，进而在并列项上翻转名次 —— 基线不可复现。
    排序后三条路径（全量 / 增量 / all_doc_vectors）的范数**逐位一致**。
    """
    terms = sorted(t for t in counter if t in idf)
    s = 0.0
    for t in terms:
        w = counter[t] * idf[t]
        s += w * w
    return math.sqrt(s) or 1.0


def _build_norms(tf_all, idf):
    """为每篇文档预算 L2 范数（存进索引，供查询期免物化）。"""
    return {r: _doc_norm(c, idf) for r, c in tf_all.items()}


def _build_postings(tf_all, doc_ids):
    """文档主序 tf → 词主序 postings：{term: [doc_id, tf, doc_id, tf, ...]}（扁平）。

    为什么扁平而不是 [[doc, tf], ...]：每个 pair 都建一个小 list 会让 pickle 对象数
    翻倍（35 万个 2 元素 list），既更大也更慢。扁平化只用一个 list。
    df 不必单独存 —— `len(postings[t]) // 2` 就是。
    """
    post = {}
    # 按 `doc_ids`（= 排序后的文档序）迭代，而不是 tf_all 的插入顺序：
    # 全量构建按文件扫描序、增量构建从 postings 还原后按排序序 —— 不统一的话
    # 同一份语料在「增量」与「全量」后得到**不同字节**的 postings（结果等价但不可复现）。
    for rel in doc_ids:
        c = tf_all[rel]
        did = doc_ids[rel]
        for t, f in c.items():
            lst = post.get(t)
            if lst is None:
                post[t] = [did, f]
            else:
                lst.append(did)
                lst.append(f)
    return post


def indexed_docs(payload):
    """已索引文档的集合（rel）。v7 从 `docs` 取，v6 及更早从 `tf` 键取。

    统一入口：调用方不该再自己决定"看 tf 还是看 docs"（改格式时必崩）。
    """
    if payload.get("docs"):
        return set(payload["docs"])
    return set(payload.get("tf") or {})


def tf_from_postings(payload):
    """从 postings 还原文档主序 tf（{rel: {term: count}}）。

    只给**索引构建**用（增量更新需要把旧索引的 tf 读回来再改）：查询路径不需要它，
    打分已改走 postings。构建不是延迟敏感路径，多这一趟可接受。
    """
    docs = payload.get("docs") or []
    out = {rel: {} for rel in docs}
    for t, p in (payload.get("postings") or {}).items():
        for i in range(0, len(p), 2):
            out[docs[p[i]]][t] = p[i + 1]
    return out


def all_doc_vectors(payload):
    """{rel: 归一化稀疏向量}，与 `{r: _vec(tf[r], vocab, idf)}` 等价。

    有 postings 就**一趟扫完**（词主序）；没有（v6 及更早）回退逐篇 `_vec`。
    这是 recall_schedule / feedback_loop / memory_sync 共用的唯一入口 ——
    以前它们各自 `_vec(tf_all[r], ...)`，索引格式一换就得跟着改。
    """
    idf = payload.get("idf") or {}
    postings = payload.get("postings")
    if not postings:
        tf_all = payload.get("tf") or {}
        return {r: _vec(c, payload.get("vocab"), idf) for r, c in tf_all.items()}
    docs = payload.get("docs") or []
    acc = {rel: {} for rel in docs}
    for t, p in postings.items():
        w = idf.get(t)
        if w is None:                      # 与 _vec 的 `if t in idf` 过滤一致
            continue
        for i in range(0, len(p), 2):
            d = acc.get(docs[p[i]])
            if d is not None:
                d[t] = p[i + 1] * w
    out = {}
    for rel, v in acc.items():
        # 与 _doc_norm 同一口径：排序后求和，保证与索引里存的 norms 逐位一致
        n = math.sqrt(sum(x * x for x in sorted(v.values()))) or 1.0
        out[rel] = {k: x / n for k, x in v.items()}
    return out


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


def _build_inverted_index(root, tf_all, meta_all=None, doc_ids=None):
    """构建倒排索引：tag/domain/content_type/author/kb_summary_token → [rel 或 doc id]

    若 `meta_all` 里已带 `tags/domain/ctype/author/kb_summary`（新版索引都会写），
    就**零磁盘读**直接重建；缺字段的 rel（旧索引 / 兼容场景）才回落到读文件。
    以前无脑对每个 rel 都 load_meta → 「增量」索引仍然把整个库读一遍。

    `doc_ids`（v6 起）：{rel: 整数 id}。给了就写整数 id，省掉把同一条长路径
    在 6 万条 postings 里重复一遍（实测 3.09 MB → 约 0.25 MB）。查询期用
    `docs` 列表还原成 rel；没给就照旧写 rel（兼容/自测）。
    """
    inv = {"tag": {}, "domain": {}, "content_type": {},
           "author": {}, "kb_summary_token": {}}
    meta_all = meta_all or {}
    # 同上：有 doc_ids 就按排序后的文档序迭代，保证与增量构建逐字节一致
    for rel in (doc_ids if doc_ids is not None else tf_all):
        m = meta_all.get(rel) or {}
        if "author" in m and "kb_summary" in m:
            tags_v, d, ct = m.get("tags", ""), m.get("domain"), m.get("ctype")
            a, summ = m.get("author"), m.get("kb_summary")
        else:
            fm, _body = load_meta(Path(root) / rel)
            tags_v, d, ct = fm.get("tags", ""), fm.get("domain"), fm.get("content_type")
            a, summ = fm.get("author"), fm.get("kb_summary")
        did = doc_ids[rel] if doc_ids is not None else rel
        for t in _parse_tags(tags_v):
            inv["tag"].setdefault(t, []).append(did)
        if d: inv["domain"].setdefault(d, []).append(did)
        if ct: inv["content_type"].setdefault(ct, []).append(did)
        if a: inv["author"].setdefault(a, []).append(did)
        for tok in tokenize(str(summ or "")):
            inv["kb_summary_token"].setdefault(tok, []).append(did)
    return inv


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
        cache = StateStore(root).load("query_cache.json", {})
        entry = cache.get(cache_key)
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

if __name__ == "__main__":
    sys.exit(main())
