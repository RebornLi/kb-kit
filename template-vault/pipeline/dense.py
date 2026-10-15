#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dense.py — 稠密语义通道（纯标准库 + 本地 embedding 端点）。

## 为什么需要（本项目自测决定，不是跟风）

`tests/probe_semantic_gap.py` 在真实库上实测：让模型写**换措辞、不复用原文关键词**的查询，
现有**纯词法**检索只能命中源笔记 **6/16 = 38%**，而稠密向量最近邻命中 **15/16 = 94%**
—— **62% 的情况下只有语义通道能找到**。

同时另一个实测说明**不能**怎么做：把语义分当成"替换排序"是**负增益**
（`KB_RERANK=embed`：top1 90.0%→86.7%、MRR 0.950→0.933、慢 6 倍），因为首段排序靠的是
**元数据强信号**（分层/tags/kb_summary/标题）。文献同向：本库三次独立重排实验全部有害。

→ 所以本模块的定位是 **补召回**：把词法漏掉、但语义相近的笔记**注入**候选，
绝不重新排序词法已经排好的首段。

## 关键工程决策

1. **稀疏随机投影降维**（4096 → 256）。原始向量：1530 篇 × 4096 × 4B ≈ 25 MB —— 比整个
   索引（7.6 MB）还大 3 倍。用 Li et al. 2006 的"very sparse random projection"
   （每列约 √D 个非零，只取 ±1）降到 256 维：**1.6 MB**，且 Johnson-Lindenstrauss
   保证近邻结构基本保持。稀疏矩阵让**查询投影只要 ~1.6 万次乘加**（≈5 ms）。
2. **固定种子**，投影矩阵可复现 —— 增量索引与全量索引产生的向量必须一致。
3. **best-effort**：端点不可用/超时/任何异常 → 稠密通道静默缺席，检索退回纯词法，
   绝不拖垮主流程（与 `embed_rerank` 同样的原则）。
4. 向量存进索引（`IDX_VERSION = 8` 起），与词法部分同生共死，不存在"索引更新了向量没更新"。

用法（自检）：
    python3 pipeline/dense.py probe --root .
"""
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from array import array
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DIM_OUT = 256          # 投影后维度（原 4096）
BATCH = 32             # 每批送嵌入的文本数
BUDGET_S = 8.0         # 查询侧总预算（秒），超了就当稠密通道不存在
CAND_M = 200           # 稠密通道给出的候选数（融合时只取前 M）


# ── 稀疏随机投影 ──────────────────────────────────────────────
def projection(dim_in: int, dim_out: int, seed: int = 20261016):
    """生成稀疏随机投影矩阵（Li et al. 2006 very sparse RP）。

    返回 `(rows, cols)`：`rows[i]` 是输入第 i 维对应的 (输出下标, 符号) 列表。
    每个**列**约有 √dim_in 个非零，全矩阵非零数 ≈ dim_out·√dim_in。
    """
    nz_per_col = max(3, int(math.sqrt(dim_in)))
    rows: List[List[Tuple[int, float]]] = [[] for _ in range(dim_in)]
    for j in range(dim_out):
        # 用确定性哈希派生该列的非零行，避免依赖 random 的实现细节
        for t in range(nz_per_col):
            h = hashlib.blake2b(f"{seed}:{j}:{t}".encode(), digest_size=8).digest()
            i = int.from_bytes(h, "big") % dim_in
            sign = 1.0 if (h[0] & 1) else -1.0
            rows[i].append((j, sign * math.sqrt(1.0 / nz_per_col)))
    return rows


class Projector:
    """把 dim_in 维向量投影到 dim_out 维（稀疏，快）。"""

    def __init__(self, dim_in: int, dim_out: int = DIM_OUT, seed: int = 20261016):
        self.dim_in, self.dim_out, self.seed = dim_in, dim_out, seed
        self.rows = projection(dim_in, dim_out, seed)

    def __call__(self, vec: Sequence[float]) -> array:
        out = array("f", bytes(4 * self.dim_out))
        n = min(len(vec), self.dim_in)
        for i in range(n):
            v = vec[i]
            if v:
                for j, s in self.rows[i]:
                    out[j] += v * s
        return out

    def key(self) -> str:
        return f"rp{self.dim_in}x{self.dim_out}s{self.seed}"


# ── 端点 ──────────────────────────────────────────────────────
def _cfg(root):
    from kb_common import model_config
    return model_config(root)


def available(root) -> bool:
    """embedding 端点是否可用（短超时，失败即 False）。"""
    cfg = _cfg(root)
    if not cfg.get("embed_url"):
        return False
    try:
        req = urllib.request.Request(
            cfg["embed_url"].rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {cfg.get('api_key', '')}"})
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status == 200
    except Exception:       # noqa: BLE001  best-effort
        return False


def embed_texts(root, texts: List[str], timeout: int = 120) -> Optional[List[List[float]]]:
    """批量取嵌入。失败返回 None（调用方须降级）。"""
    cfg = _cfg(root)
    if not cfg.get("embed_url") or not texts:
        return None
    payload = json.dumps({"model": cfg["embed_model"], "input": texts}).encode()
    req = urllib.request.Request(cfg["embed_url"].rstrip("/") + "/embeddings", data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {cfg.get('api_key', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return [d["embedding"] for d in data["data"]]
    except Exception:       # noqa: BLE001  best-effort
        return None


# ── 索引期：批量建向量 ────────────────────────────────────────
def build_vectors(root, texts: Dict[str, str], dim_out: int = DIM_OUT,
                  progress=None) -> Optional[Tuple[Dict[str, array], int]]:
    """{rel: text} → ({rel: array('f')}, dim_in)。失败返回 None。

    一次性成本：N/32 次 HTTP + N·dim_out 次乘加。1530 篇实测约 1 分钟。
    """
    if not available(root):
        return None
    rels = sorted(texts)
    probe = embed_texts(root, [texts[rels[0]][:2000]])
    if not probe:
        return None
    proj = Projector(len(probe[0]), dim_out)
    out: Dict[str, array] = {}
    for i in range(0, len(rels), BATCH):
        chunk = rels[i:i + BATCH]
        vs = embed_texts(root, [texts[r][:2000] for r in chunk])
        if vs is None:
            return None
        for r, v in zip(chunk, vs):
            pv = proj(v)
            n = math.sqrt(sum(x * x for x in pv)) or 1.0
            out[r] = array("f", (x / n for x in pv))
        if progress:
            progress(min(i + BATCH, len(rels)), len(rels))
    return out, len(probe[0])


# ── 查询期：投影 + 暴力扫描 ───────────────────────────────────
def query_vector(root, q: str, dim_in: int, dim_out: int = DIM_OUT,
                 seed: int = 20261016) -> Optional[array]:
    vs = embed_texts(root, [q])
    if not vs:
        return None
    pv = Projector(dim_in, dim_out, seed)(vs[0])
    n = math.sqrt(sum(x * x for x in pv)) or 1.0
    return array("f", (x / n for x in pv))


def top_k(vecs: Dict[str, array], qv: array, k: int = CAND_M) -> List[Tuple[str, float]]:
    """暴力余弦 top-k。

    N·dim_out 次乘加：1530×256 ≈ 39 万次 → 标准库 `sum(map(mul,...))` 实测约 10–20 ms，
    无需 ANN（文献亦不建议在此规模引入 faiss/hnswlib）。负数分（方向相反）直接丢。
    """
    from operator import mul
    out = []
    for rel, v in vecs.items():
        s = sum(map(mul, qv, v))
        if s > 0:
            out.append((rel, s))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:k]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="稠密语义通道（自检）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("probe"); p1.add_argument("--root", default=".")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    print(f"  端点可用: {available(root)}")
    cfg = _cfg(root)
    print(f"  embed_url: {cfg.get('embed_url')} · model: {cfg.get('embed_model')}")
    t0 = time.time()
    vs = embed_texts(root, ["测试文本"])
    print(f"  单条嵌入: {len(vs[0]) if vs else '失败'} 维 · {(time.time() - t0) * 1000:.0f} ms")
    if vs:
        import random
        random.seed(1)
        fake = {f"d{i}": array("f", (random.random() for _ in range(DIM_OUT))) for i in range(1530)}
        qv = Projector(len(vs[0]))(vs[0])
        t0 = time.time()
        r = top_k(fake, qv, 200)
        print(f"  1530 篇扫描: {(time.time() - t0) * 1000:.0f} ms（取 top-{len(r)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
