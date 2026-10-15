#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dedup.py — 近重复检测（纯标准库，MinHash + LSH）。

流程：正文 shingling → MinHash 签名 → LSH 分桶 → 候选对 + 签名相似度估计。
**只产出候选**（保守阈值），不自动删除；人工确认后走 `kb ingest move`（merge）。

参考：MinHash/LSH 是 C4 / RefinedWeb / RedPajama 等语料去重的主力，能在近线性
时间内发现近重复；Shingle 用 Jaccard，MinHash 签名一致率≈Jaccard 相似度。
"""
import hashlib
import heapq
import re

_MASK = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15


def shingles(text, k=5):
    """正文 → k-gram 集合（去空白，中英文通用）。"""
    t = re.sub(r"\s+", "", text or "")
    if len(t) < k:
        return {t} if t else set()
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def minhash(shingle_set, num_perm=64):
    """MinHash 签名：每个位置取所有 shingle 哈希的最小值。

    ⚠️ 已被 `bottomk_signature` 取代（保留仅为兼容外部调用）。
    本实现的每个 shingle 要做 `num_perm=64` 次线性置换
    （`h + i*GOLDEN`，即 4500 篇语料约 5.8 亿次纯 Python 整数运算），
    且线性置换只是"近似两两独立"，估计有偏。新代码请用 bottom-k。
    """
    sig = [_MASK] * num_perm
    if not shingle_set:
        return sig
    for sh in shingle_set:
        h = int.from_bytes(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(num_perm):
            hv = (h + i * _GOLDEN) & _MASK  # 线性置换近似两两独立哈希
            if hv < sig[i]:
                sig[i] = hv
    return sig


# ── bottom-k（KMV）：每 shingle 只算 1 次哈希 ────────────────────────
#   为什么换：MinHash 的 num_perm 份签名要让每个 shingle 算 num_perm 次哈希；
#   bottom-k 只保留 k 个最小的哈希值，每 shingle **1 次** blake2b，
#   而 |A∩B| / |A∪B| 仍是对 Jaccard 的无偏估计。
#   实测（真实库 4431 篇）：哈希调用量降约 64×，签名相似度与 Jaccard 的
#   误差同量级；且顺带修掉线性置换的独立性偏差。
_SALT = b"kb-dedup-bottomk"
# 估计值低于 (threshold - 该余量) 的候选直接丢弃；其余做精确 Jaccard 复核。
# 0.15 是实测选择：再小会漏真近重复，再大则精确复核的对数上升（仍可接受）。
_VERIFY_MARGIN = 0.15


def bottomk_signature(shingles, k=64, salt=_SALT):
    """bottom-k（KMV）签名：返回 k 个最小哈希值（升序元组）。"""
    if not shingles:
        return ()
    hs = (int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8, key=salt).digest(),
                         "big") for s in shingles)
    return tuple(heapq.nsmallest(k, hs))


def bottomk_similarity(a, b):
    """bottom-k 估计 Jaccard：|A∩B| / |A∪B|。任一为空 → 0。"""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    union = len(sa | sb)
    return len(sa & sb) / union if union else 0.0


def containment(a, b):
    """包含度 |A∩B| / min(|A|,|B|)：区分"重复片段"与"重复笔记"。

    A 包含于 B（containment≈1）但 Jaccard 不高 → 是**片段重复**，应提示"该段已存在于
    另一篇"，而不是把两篇合并（合并会丢掉另一篇的独有内容）。
    """
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    m = min(len(sa), len(sb))
    return len(sa & sb) / m if m else 0.0


def _band_keys(sig, bands):
    """把签名切成 bands 个 band，返回 (band_index, band_hash)。"""
    rows = len(sig) // bands
    for b in range(bands):
        band = sig[b * rows:(b + 1) * rows]
        yield b, hashlib.blake2b(b"".join(x.to_bytes(8, "big") for x in band),
                                 digest_size=8).hexdigest()


def signature_similarity(a, b):
    """两签名的估计 Jaccard 相似度（一致位置占比）。"""
    if not a or not b:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def near_duplicate_pairs(docs, threshold=0.7, k=64, shingle_k=5):
    """在 docs={rel: body} 中找近重复对（bottom-k 候选 → 阈值过滤）。

    候选生成：bottom-k 值的**倒排索引** —— 两篇只要共享 ≥1 个 bottom-k 值就成候选，
    不需 banding 参数，且天然近线性。
    额外过滤：**size filter**（Jaccard ≥ t 蕴含 |A|/|B| ≥ t，长度比差太远的对直接跳过）。

    Returns: [(sim, rel_a, rel_b)]，按 sim 降序（**保持旧返回形状**，调用方无需改）。
    """
    return [(sim, a, b) for sim, a, b, _c, _kind
            in near_duplicate_pairs_detailed(docs, threshold, k, shingle_k)]


def near_duplicate_pairs_detailed(docs, threshold=0.7, k=64, shingle_k=5):
    """同上，但返回更丰富的信息：[(sim, a, b, containment, kind)]。

    kind ∈ {"note-dup"（整体近重复）, "section-dup"（片段重复：含于对方但整体不同）}。
    片段重复**不应合并** —— 合并会丢掉另一篇的独有内容，只该提示"该段已在别处"。
    """
    sh = {r: shingles(body, shingle_k) for r, body in docs.items()}
    sigs = {r: bottomk_signature(s) for r, s in sh.items()}
    sizes = {r: len(s) for r, s in sh.items()}
    post = {}
    for r, sig in sigs.items():
        for hv in sig:
            post.setdefault(hv, []).append(r)
    seen, out = set(), []
    for members in post.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                la, lb = sizes[a], sizes[b]
                if la and lb:                       # size filter：Jaccard≥t ⇒ 长度比≥t
                    ratio = min(la, lb) / max(la, lb)
                    if ratio < threshold:
                        continue
                est = bottomk_similarity(sigs[a], sigs[b])
                # bottom-k 估计只用于**筛掉明显不相似的**（它是无偏但有方差，
                # 在阈值边界会漏；实测阈值 0.7 时漏掉一批真近重复）。
                # 存活者用**精确 Jaccard** 复核 —— 候选只有几百对，精确算很便宜，
                # 这样既保住 64× 的哈希节省，又拿到精确的 precision/recall。
                if est < threshold - _VERIFY_MARGIN:
                    continue
                A, B = sh[a], sh[b]
                union = len(A | B)
                sim = (len(A & B) / union) if union else 0.0
                if sim < threshold:
                    continue
                c = containment(A, B)
                kind = "section-dup" if (c >= 0.9 and sim < threshold + 0.15) else "note-dup"
                out.append((round(sim, 3), a, b, round(c, 3), kind))
    out.sort(reverse=True)
    return out

    return out
