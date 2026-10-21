#!/usr/bin/env python3
# ============================================================
# kb_scale.py —— P3-1 规模路径预留：双层数据结构的"感知层"
# ------------------------------------------------------------
# 现状：markdown 给人读（RSI collect 全量读），vector index 给机器用（rag.py），
#   但 vector index 被 EXCLUDE 排除 → RSI 不感知索引层。本模块做"元数据桥接"，
#   让未来的双档逻辑能按需 switch：flat（全量 markdown）→ indexed（倒排索引查询）。
#   现在**不建检索**，只留接口 + 规模档位 + 索引层覆盖感知。
# ------------------------------------------------------------
#   用法:
#     python3 pipeline/kb_scale.py bracket --n N     # 规模档位（纯函数）
#     python3 pipeline/kb_scale.py fabric --root R    # 索引层覆盖感知
#     python3 pipeline/kb_scale.py scale --root R     # 给 engine/main 合入的 scale dict

# ── 路径自举（P2-2 移包）────────────────────────────────────
# 本文件被**直接** `python3 pipeline/_research/<name>.py` 运行时，sys.path[0]
# 是 _research/ 而不是 pipeline/，裸 import 生产层会失败。走垫片
# （`python3 pipeline/<name>.py`）或经 `import _research` 则没有这个问题，
# 但补上这段让两种跑法都成立。
import os as _os
import sys as _sys
_PIPE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PIPE not in _sys.path:
    _sys.path.insert(0, _PIPE)
import argparse, json, sys
from pathlib import Path

IDX_DIR = "vector index"
# 索引主格式 v6 起为 df_idf.pkl（旧库仍可能是 .json）→ 用 rag 的解析器
try:
    from rag import IDX_VERSION as EXPECTED_VERSION   # 单一事实源，别再手写（曾停在 2 而 rag 已是 4）
except ImportError:                                   # pragma: no cover
    EXPECTED_VERSION = 4


def indexable_count(root: Path) -> int:
    """「应入索引」的篇数——分母必须与 rag._full_rebuild 同源。

    以前分母用 `kb_rsi.collect`（含 raw/、不排除生成物），分子是 RAG 索引篇数
    （排除 raw/生成物）→ 两者不同源，覆盖率数字没有意义。
    """
    from kb_io import iter_notes, load_meta
    from kb_metautil import is_generated_report, index_excluded
    from kb_text import tokenize
    n = 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue
        fm, body = load_meta(p)
        if index_excluded(fm):
            continue
        if not tokenize(body):
            continue
        n += 1
    return n

# 规模档位：笔记数 → 建议处理策略（未来 engine 据此 switch flat/indexed）
BRACKET_RULES = (
    (100,   "flat",        "全量 markdown 直读；O(n²) 操作无压力"),
    (500,   "flat-warn",   "接近 O(n²) 阈值（400）；建议开始建索引层"),
    (2000,  "indexed",     "应走双层：索引层做机器查询/矛盾 lint，markdown 给人读"),
)


def bracket(n):
    """笔记数 → 规模档位 + 提示。档位数越高越需要双层检索。"""
    for limit, tag, note in BRACKET_RULES:
        if n < limit:
            return {"notes": n, "bracket": tag, "note": note}
    return {"notes": n, "bracket": "indexed-required",
            "note": "超大库；必须走双层检索，禁止全量 O(n²) 操作"}


def _index_path(root):
    """取当前可用的索引文件。v6 起主格式是 `df_idf.pkl`（旧库仍是 .json）。"""
    try:
        import rag
        return rag.index_file(Path(root) / IDX_DIR)
    except ImportError:                       # pragma: no cover - rag 必然存在
        return None


def _load_index(idx_path):
    """统一委托 rag.load_index：它能同时读 pickle（v6）与 JSON（旧库）。"""
    if not idx_path:
        return None, 0
    try:
        import rag
        return rag.load_index(idx_path)
    except ImportError:                       # pragma: no cover
        return None, 0


def _doc_hash_set(payload):
    """v2 payload 的 doc_hashes 归一化为 rel 集合（dict 键 / list 元素）。"""
    if not payload:
        return None
    dh = payload.get("doc_hashes")
    if isinstance(dh, dict):
        return set(dh.keys())
    if isinstance(dh, list):
        return {x.get("rel", x) if isinstance(x, dict) else x for x in dh}
    return None


def fabric(root):
    """感知索引层：读 rag 索引，报告已索引篇数/总markdown篇数/覆盖/切换就绪。"""
    from kb_rsi import collect  # 延迟 import，避免 kb_rsi↔kb_scale 循环
    root = Path(root)
    n_markdown = indexable_count(root) if root.exists() else 0
    idx_path = _index_path(root)
    payload, ver = _load_index(idx_path)
    hashes = _doc_hash_set(payload) if payload else None
    n_indexed = len(hashes) if hashes else (payload.get("n_docs", 0) if payload else 0)
    coverage = round(n_indexed / n_markdown, 3) if n_markdown else 0.0
    ready = ver >= EXPECTED_VERSION and n_indexed > 0
    br = bracket(n_markdown)["bracket"]
    switch_ready = ready and br in ("indexed", "indexed-required")
    return {
        "markdown_docs": n_markdown,
        "indexed_docs": n_indexed,
        "coverage": coverage,
        "index_version": ver,
        "expected_version": EXPECTED_VERSION,
        "indexed_available": ready,
        "bracket": br,
        "switch_ready": switch_ready,
        "note": ("索引层可用且规模到位，可切换双档逻辑" if switch_ready
                 else "尚未建索引层/版本过旧(跑 rag.py index 重建)或规模未到"),
    }


def scale_for_metrics(root):
    """给 engine/main 消费的 scale dict（档位 + 索引层状态），并入运行记录。"""
    fab = fabric(root)
    return {"notes": fab["markdown_docs"], "bracket": fab["bracket"],
            "indexed_docs": fab["indexed_docs"], "coverage": fab["coverage"],
            "indexed_available": fab["indexed_available"],
            "switch_ready": fab["switch_ready"], "note": fab["note"]}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bracket"); b.add_argument("--n", type=int, required=True)
    f = sub.add_parser("fabric"); f.add_argument("--root", required=True)
    s = sub.add_parser("scale"); s.add_argument("--root", required=True)
    a = ap.parse_args()
    if a.cmd == "bracket":
        print(json.dumps(bracket(a.n), ensure_ascii=False, indent=2))
    elif a.cmd == "fabric":
        print(json.dumps(fabric(Path(a.root)), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(scale_for_metrics(Path(a.root)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
