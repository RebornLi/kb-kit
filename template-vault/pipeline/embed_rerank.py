#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""embed_rerank.py — 用**本地嵌入模型**做真正的相关性重排（P2）。

为什么需要：
  原来的 `rerank.py` 是 5 维启发式加权（token 重叠 / frontmatter / importance /
  enforce_level / 时效性），它不是"相关性判断"，只是把已知的元数据揉成一个分数。
  真实相关性应该看 (查询, 文本) 的语义相似度——本地 `:8081` 的 qwen3-embedding
  就是为此存在（已实测可用，4096 维）。

做法（两段式，控延迟）：
  1. 快速通道（RRF 融合）先给候选；
  2. 只对 **top-N（默认 25）** 算嵌入相似度，融合成最终分数：
        final = α·norm(嵌入余弦) + (1-α)·norm(RRF 分)
  3. 嵌入不可用 / 超时 / 报错 → 原样返回（best-effort，绝不拖垮检索）。

缓存：`.kb/embed_cache.json`（文本指纹 → 向量），避免重复算同一段文本。

用法（自检）:
  python3 pipeline/embed_rerank.py probe         # 端点可用性与延迟
  python3 pipeline/embed_rerank.py score "查询" "文本"
"""
import argparse, hashlib, json, os, sys, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CACHE = Path(".kb") / "embed_cache.json"
TOP_N = 50            # 重排前 N（cross-encoder 实测 50 条仅 45ms，可以放宽）
ALPHA = 0.6           # 语义分权重（其余给 RRF 分）
BUDGET_S = 6.0        # 重排总预算（秒）；超预算立即放弃，返回原始顺序

# ── 真 cross-encoder 重排（首选）──────────────────────────────
# 实测（2026-10-01）：bge-reranker-v2-m3 在 :8082，25 条 36ms / 50 条 45ms，
# 只占 1.7GB 显存。**比 bi-encoder（嵌入余弦）更准且更快** ——
# 之前用 qwen3-embedding 算 (query,doc) 余弦来"重排"，等于用错工具：
# bi-encoder 把 query 与 doc 分别编码，天然弱于 cross-encoder 的联合编码。
RERANK_URL = "http://127.0.0.1:8082/v1/rerank"
RERANK_MODEL = "bge-reranker-v2-m3"


class CrossEncoderReranker:
    """用 /v1/rerank 做真正的 cross-encoder 重排（best-effort）。"""

    def __init__(self, url: str = RERANK_URL, model: str = RERANK_MODEL,
                 top_n: int = TOP_N, alpha: float = ALPHA, budget_s: float = BUDGET_S):
        self.url = url
        self.model = model
        self.top_n = top_n
        self.alpha = alpha
        self.budget_s = budget_s

    def available(self) -> bool:
        try:
            import urllib.request, json as _j
            from kb_common import model_config
            cfg = model_config()
            base = self.url.split("/v1/")[0]
            req = urllib.request.Request(f"{base}/v1/models",
                                         headers={"Authorization": f"Bearer {cfg.get('api_key','')}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status < 400
        except Exception:
            return False

    def rerank(self, query: str, hits, texts):
        """返回 [(rel, 融合分)]；任何异常 → 原样返回。"""
        if not hits:
            return hits
        head, tail = hits[:self.top_n], hits[self.top_n:]
        docs = [str(texts.get(rel) or rel)[:2000] for rel, _ in head]
        try:
            import urllib.request, json as _j, time
            from kb_common import model_config
            cfg = model_config()
            payload = _j.dumps({"model": self.model, "query": query, "documents": docs}).encode()
            req = urllib.request.Request(self.url, data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {cfg.get('api_key','')}"})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=max(10, self.budget_s)) as r:
                d = _j.loads(r.read())
            if time.time() - t0 > self.budget_s:
                return hits
            res = d.get("results") or []
        except Exception:
            return hits
        scored = []
        for item in res:
            i = item.get("index")
            if not isinstance(i, int) or i >= len(head):
                continue
            rel, base_sc = head[i]
            scored.append((rel, float(item.get("relevance_score") or 0.0), base_sc))
        if not scored:
            return hits
        mx = max(s for _r, s, _b in scored) or 1.0
        mn = min(s for _r, s, _b in scored)
        span = (mx - mn) or 1.0
        bmx = max(b for _r, _s, b in scored) or 1.0
        bmn = min(b for _r, _s, b in scored)
        bspan = (bmx - bmn) or 1.0
        out = [(rel, self.alpha * ((s - mn) / span) + (1 - self.alpha) * ((b - bmn) / bspan))
               for rel, s, b in scored]
        out.sort(key=lambda x: x[1], reverse=True)
        return out + tail


def _norm(v: List[float]) -> List[float]:
    import math
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _cos(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:24]


class EmbedReranker:
    def __init__(self, root: Path, top_n: int = TOP_N, alpha: float = ALPHA,
                 budget_s: float = BUDGET_S, enabled: bool = True):
        self.root = root
        self.top_n = top_n
        self.alpha = alpha
        self.budget_s = budget_s
        self.enabled = enabled
        self._cache: Dict[str, List[float]] = {}
        self._cache_dirty = False
        self._load_cache()

    # ── 缓存 ──
    def _load_cache(self) -> None:
        try:
            self._cache = json.loads((self.root / CACHE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._cache = {}

    def save(self) -> None:
        if not self._cache_dirty:
            return
        try:
            p = self.root / CACHE
            p.parent.mkdir(parents=True, exist_ok=True)
            keys = list(self._cache)[-4000:]          # 控制体积
            p.write_text(json.dumps({k: self._cache[k] for k in keys}, ensure_ascii=False),
                         encoding="utf-8")
        except OSError:
            pass

    def _embed(self, text: str):
        fp = _fp(text)
        if fp in self._cache:
            return self._cache[fp]
        try:
            import kb_embed
            v = kb_embed.embed(text[:4000])
        except Exception:
            return None
        if not v:
            return None
        v = _norm(list(v))
        self._cache[fp] = v
        self._cache_dirty = True
        return v

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            import kb_embed
            return bool(kb_embed.available())
        except Exception:
            return False

    def rerank(self, query: str, hits: List[Tuple[str, float]],
               texts: Dict[str, str]) -> List[Tuple[str, float]]:
        """对 top-N 命中做语义重排；任何异常 → 原样返回。"""
        if not hits or not self.available():
            return hits
        head, tail = hits[:self.top_n], hits[self.top_n:]
        t0 = time.time()
        qv = self._embed(query)
        if qv is None:
            return hits
        scored = []
        for rel, base in head:
            if time.time() - t0 > self.budget_s:
                return hits                            # 超预算：放弃，保持原序
            tv = self._embed(texts.get(rel) or rel)
            if tv is None:
                return hits
            scored.append((rel, _cos(qv, tv), base))
        if not scored:
            return hits
        mx = max(base for _r, _s, base in scored) or 1.0
        mn = min(base for _r, _s, base in scored)
        span = (mx - mn) or 1.0
        out = [(rel, self.alpha * sim + (1 - self.alpha) * ((base - mn) / span))
               for rel, sim, base in scored]
        out.sort(key=lambda x: x[1], reverse=True)
        self.save()
        return out + tail


# ── 自检 ────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="本地嵌入重排（自检）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("probe"); p1.add_argument("--root", default=".")
    p2 = sub.add_parser("score"); p2.add_argument("q"); p2.add_argument("text")
    p2.add_argument("--root", default=".")
    args = ap.parse_args()
    root = Path(args.root)
    rr = EmbedReranker(root)
    if args.cmd == "probe":
        ok = rr.available()
        print(f"嵌入端点可用: {ok}")
        if ok:
            t0 = time.time(); _ = rr._embed("探针文本"); dt = time.time() - t0
            print(f"单段嵌入延迟: {dt*1000:.0f} ms")
            print(f"缓存条目: {len(rr._cache)}")
        return 0 if ok else 1
    if args.cmd == "score":
        rr._cache = {}
        v1 = rr._embed(args.q); v2 = rr._embed(args.text)
        if not (v1 and v2):
            print("✗ 嵌入不可用")
            return 1
        print(f"语义相似度: {_cos(v1, v2):.4f}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
