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
# ── 检索域门面（P1-3 拆分后）────────────────────────────────
# 实现已拆到三个模块：
#   rag_score  — 评分原语（向量相似度 / BM25 / RRF / 调整 / 去重）
#   rag_index  — 索引构建与持久化（增量更新 / 加载 / 保存）
#   rag_query  — 查询执行与结果组装（过滤 / 命中 / JSON / LLM 答案）
#
# 本文件承担**两个不可删的职责**：
#   1. `main()` —— CLI 入口。plugins/rag_plugin.py 的 SCRIPT_NAME="rag.py"
#      按文件名 subprocess 调它；tests/deploy.py 也按路径加载。**不能删**。
#   2. 兼容门面 —— 显式列出对外暴露的名称（原先是 `import *` 隐式泄露，
#      连 Counter 这类 stdlib 名都会被带出去，看模块看不出真实依赖）。
#
# 关于「为什么不删门面」：`tests/test_p0_rag.py` 等 33 个回归测试以
# `rag.<X>` 单入口覆盖整个检索 API（IDX_VERSION / load_index / _vec /
# _SafeUnpickler …），这是**有意的**测试设计——它们验证的是检索域整体契约，
# 不该被迫知道内部模块边界。故保留显式 re-export，但把依赖边写清楚。
# 生产代码（memory_sync / recall_schedule / feedback_loop …）请直引子模块。
# ============================================================
import argparse
import sys

from kb_io import ROOT_DEFAULT

# ── 显式 re-export（原 `from rag_score import *` 的显式化）────
# 名单与拆分前逐一核对：原 `import *` 带出的全部公开名 + 原显式块的下划线名。
# 评分常量 + 原语
from rag_score import (       # noqa: F401
    ADJUST_MIN_SIM, ADJUST_BOOST, ADJUST_MAX,
    DENSE_M, DENSE_SLOTS_FRAC,
    HYBRID_ALPHA, RRF_CAP, RRF_K,
    CHUNK_FAMILY_MAX, CONF_WEIGHT,
    INVERTED_HIT_BOOST, PRIORITY_MULTIPLIER,
    dense_fill_tail, dense_rank, load_retrieval_adjustments,
    _score_cosine_postings, _score_cosine_fast,
    _bm25_postings, _bm25_scores,
    _vec, _cos, _adjust_key,
    _apply_retrieval_adjustments, _inverted_lookup,
    _rrf_fuse, _root_of, _dedup_chunks,
    _parse_importance, _parse_tags, _has_any_tag,
)
# 索引层
from rag_index import (       # noqa: F401
    IDX_DIR, IDX_JSON, IDX_PICKLE, IDX_VERSION, IDX_STEM,
    CONTEXT_IN_INDEX,
    all_doc_vectors, cmd_index, index_file, indexed_docs,
    load_index, save_index, strip_body, tf_from_postings,
    _SafeUnpickler, _SAFE_PICKLE_GLOBALS, _CHUNK_SUFFIX,
    _infer_parent_from_name, _title_text, _imp_of, _ts_of,
    _conf_of, _parent_path, _root_path, _build_dense,
    _full_rebuild, _incremental_update, _doc_norm,
    _build_norms, _build_postings, _build_inverted_index,
)
# 查询层
from rag_query import (       # noqa: F401
    CONCLUSION_WORDS, PARENT_WINDOW,
    TITLE_BOOST, TITLE_BOOST_CAP, SYNONYMS_PATH,
    best_sentence, cmd_query, llm_answer,
    _synonyms_cache, _synonyms_reverse,
    _apply_filters, _is_index_like, _parent_block, _read_head,
    _title_boost, _build_hit, _build_json_output,
    _truncate_to_tokens, _context_budget, _gen_session,
    _record_hits, _load_synonyms, _expand_query,
    _snippet_candidate,
)
# 检索域间接依赖的领域工具（原 `import *` 从子模块的 import 语句里带出）。
# 这些名字 rag 本身不用，但历史上经 rag 暴露，测试与旧调用方在用；
# 显式声明比继续隐式泄露更好维护。
from kb_io import EXCLUDE, FM, iter_notes, load_meta             # noqa: F401
from kb_metautil import (LAYER_MULTIPLIER, index_excluded,       # noqa: F401
                         is_index_stub, is_stale, kb_layer_of,
                         parent_of, retrievable_status, root_of)
from kb_text import content_fingerprint, count_tokens            # noqa: F401

# ── 兼容转发：这些名字以前经 `import *` 从子模块的 import 语句里
#    被**无意**带出（如 kb_text.tokenize / Counter / kb_metautil.*）。
#    有测试与旧调用方在用，显式声明比继续隐式泄露更好维护。
from collections import Counter                                    # noqa: F401
from kb_metautil import is_generated_report                        # noqa: F401
from kb_io import iter_notes, load_meta                            # noqa: F401
from kb_text import tokenize                                       # noqa: F401


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


if __name__ == "__main__":
    sys.exit(main())
