"""rag_index — 索引构建与持久化（从 rag.py 拆分，P1-3）。

提供倒排索引 / dense 向量缓存的构建、增量更新、加载、保存，以及索引文件路径常量。
被 rag.py shim、rag_query、memory_sync 等模块依赖。

依赖 rag_score 的 _parse_tags（标签解析原语）。
_build_inverted_index 在本模块内定义（仅被索引构建函数调用）。
"""
import os
import re
import sys
import json
import math
import time
import pickle
import struct
import io
import heapq
import datetime
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from kb_common import (
    FM, ROOT_DEFAULT, EXCLUDE, load_meta, iter_notes, tokenize,
    content_fingerprint, index_excluded, is_generated_report,
    kb_layer_of, parent_of, root_of,
)
from rag_score import _parse_tags  # noqa: F401

try:                       # 上下文强化（P2）：缺失/异常都不影响索引可用
    from contextual import cch as _cch
except Exception:          # pragma: no cover
    def _cch(rel, fm, body):
        return ""

# ── 索引常量 ──────────────────────────────────────────────


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
