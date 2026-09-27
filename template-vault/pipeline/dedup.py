#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dedup.py — 近重复检测（纯标准库，MinHash + LSH）。

流程：正文 shingling → MinHash 签名 → LSH 分桶 → 候选对 + 签名相似度估计。
**只产出候选**（保守阈值），不自动删除；人工确认后走 `kb ingest move`（merge）。

参考：MinHash/LSH 是 C4 / RefinedWeb / RedPajama 等语料去重的主力，能在近线性
时间内发现近重复；Shingle 用 Jaccard，MinHash 签名一致率≈Jaccard 相似度。
"""
import hashlib
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
    """MinHash 签名：每个位置取所有 shingle 哈希的最小值。"""
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


def near_duplicate_pairs(docs, threshold=0.7, num_perm=64, bands=16):
    """在 docs={rel: body} 中找近重复对（LSH 候选 → 阈值过滤）。

    Returns: [(sim, rel_a, rel_b)]，按 sim 降序。
    """
    sigs = {r: minhash(shingles(body), num_perm) for r, body in docs.items()}
    buckets = {}
    for r in docs:
        for b, key in _band_keys(sigs[r], bands):
            buckets.setdefault((b, key), []).append(r)
    seen, out = set(), []
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                sim = signature_similarity(sigs[a], sigs[b])
                if sim >= threshold:
                    out.append((round(sim, 3), a, b))
    out.sort(reverse=True)
    return out
