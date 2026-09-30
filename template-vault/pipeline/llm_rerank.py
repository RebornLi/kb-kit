#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_rerank.py — 用本地 chat 模型做重排（P6 实验）。

为什么值得试（而 cross-encoder 不值得）：
  · cross-encoder 只判「文本语义像不像在讲这个问题」——实测把 top1 从 90% 打到 63%；
  · chat 模型**能读懂"这段内容能不能回答问题"**，这是本库排序真正需要的目标函数
    （库里有大量"提到了关键词但没回答问题"的工程流水笔记）。
代价：每次查询多 1–3 秒 + 一次生成。45 GB 空闲显存后才划算。

设计（保守、可降级、可测）：
  · 只把首段 **top-N**（默认 15）交给模型，要求返回**相关性排名**（不是改写、不是摘要）
  · 用**编号 + 标题 + 片段**呈现，模型只输出 `[3,1,7,...]` 形式，解析极简
  · 模型失败/超时/输出不合法 → **原样返回**（best-effort，绝不拖垮检索）
  · 与首段分数**混合**（ALPHA），避免完全丢掉元数据强信号

用法:
  python3 pipeline/llm_rerank.py probe                       # 端点可用性 + 单次延迟
  python3 pipeline/llm_rerank.py try --query "..." [--top 15] [--json]
"""
import argparse, json, os, re, sys, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import ROOT_DEFAULT, model_config, load_meta

TOP_N = 12
ALPHA = 0.4          # 首段分权重（0.4 首段 + 0.6 模型判断）
BUDGET_S = 60.0
MAX_TOKENS = 4096     # 实测断崖：2048 会被 reasoning 吃光（finish=length, content 空）
CACHE = Path(".kb") / "llm_rerank_cache.json"

PROMPT = """按「能否回答用户问题」给下列资料排序，最相关在前。
只输出 JSON：{{"order": [编号, ...]}}，包含全部 {n} 个编号，不重复，不要解释。

用户问题：{query}

{cands}"""


class LLMReranker:
    def __init__(self, root: Path, top_n: int = TOP_N, alpha: float = ALPHA,
                 budget_s: float = BUDGET_S, enabled: bool = True):
        self.root = Path(root)
        self.top_n = top_n
        self.alpha = alpha
        self.budget_s = budget_s
        self.enabled = enabled
        self._cache: Dict[str, List[int]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            self._cache = json.loads((self.root / CACHE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._cache = {}

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            p = self.root / CACHE
            p.parent.mkdir(parents=True, exist_ok=True)
            keys = list(self._cache)[-1500:]
            p.write_text(json.dumps({k: self._cache[k] for k in keys}, ensure_ascii=False),
                         encoding="utf-8")
        except OSError:
            pass

    def available(self) -> bool:
        return bool(self.enabled and (model_config().get("api_key") or ""))

    def rerank(self, query: str, hits: List[Tuple[str, float]],
               texts: Optional[Dict[str, str]] = None) -> List[Tuple[str, float]]:
        """按「能否回答问题」重排 top-N；任何异常 → 原样返回。"""
        if not hits or not self.available():
            return hits
        head, tail = hits[:self.top_n], hits[self.top_n:]
        key = f"{query}\u0000" + "\u0001".join(r for r, _ in head)
        order = self._cache.get(key)
        if order is None:
            order = self._ask(query, head, texts or {})
            if order is None:
                return hits                      # 失败：原样（绝不降级成更差的结果）
            self._cache[key] = order
            self._dirty = True
        # 把 order 变成 0..1 的排名分（第一名 1.0，末名 0.0）
        rank_of = {idx: pos for pos, idx in enumerate(order)}
        m = len(head)
        lats = []
        for i, (rel, base) in enumerate(head):
            pos = rank_of.get(i, m)              # 未出现在 order 里的排最后
            llm = 1.0 - (pos / max(m - 1, 1))
            bmax = max(b for _r, b in head) or 1.0
            lats.append((rel, self.alpha * (base / bmax) + (1 - self.alpha) * llm))
        lats.sort(key=lambda x: x[1], reverse=True)
        self.save()
        return lats + tail

    def _ask(self, query: str, head, texts) -> Optional[List[int]]:
        rows = []
        for i, (rel, _sc) in enumerate(head):
            fm, body = load_meta(Path(self.root) / rel)
            title = str(fm.get("title") or Path(rel).stem)
            # 只给"首条结论"而非整段：长候选会把 reasoning 推到 2000+（实测）
            first = next((l.strip("- ").strip() for l in (body or "").splitlines()
                          if l.strip().startswith("-")), "")
            rows.append(f"[{i}] {title}｜{first[:80]}")
        prompt = PROMPT.format(n=len(head), query=query, cands="\n".join(rows))
        try:
            import urllib.request
            cfg = model_config()
            payload = json.dumps({"model": cfg.get("chat_model") or "ornith1.5-35b",
                                  "temperature": 0.0, "max_tokens": MAX_TOKENS,
                                  "messages": [{"role": "user", "content": prompt}]}).encode()
            req = urllib.request.Request((cfg.get("base_url") or "").rstrip("/") + "/chat/completions",
                                         data=payload,
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {cfg.get('api_key','')}"})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=self.budget_s) as r:
                d = json.loads(r.read())
            if time.time() - t0 > self.budget_s:
                return None
            txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception:
            return None
        m = re.search(r"\[[\d,\s]+\]", txt)
        if not m:
            return None
        try:
            order = [int(x) for x in re.findall(r"\d+", m.group(0))]
        except ValueError:
            return None
        valid = [x for x in order if 0 <= x < len(head)]
        if len(valid) < 2:
            return None
        seen, uniq = set(), []
        for x in valid:
            if x not in seen:
                seen.add(x); uniq.append(x)
        return uniq


EXPAND_PROMPT = """把下面的检索问题改写成更适合**关键词检索**的查询词串。
要求：
1. 补上可能的中文近义说法与英文术语（如"正典"→"canon 结晶 正典"，"检索"→"检索 search query"）
2. 只输出词，用空格分隔，不要句子、不要解释
3. 最多 12 个词

问题：{q}
查询词："""

_EXPAND_CACHE: Dict[str, str] = {}
_EXPAND_CACHE_PATH = Path(".kb") / "query_expand_cache.json"


def expand_query(text: str, root: Path = None) -> str:
    """LLM 查询改写（用于词法检索）。

    实测（30 题黄金集）：canon@K **91.3% → 95.3%**，top1 100%、top3 90% 均不降。
    代价：每次 +10~20s（本地 chat 串行）。失败一律返回原查询（best-effort）。
    """
    global _EXPAND_CACHE
    root = Path(root or Path(__file__).resolve().parents[1])
    if not _EXPAND_CACHE:
        try:
            _EXPAND_CACHE = json.loads((root / _EXPAND_CACHE_PATH).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _EXPAND_CACHE = {}
    if text in _EXPAND_CACHE:
        return _EXPAND_CACHE[text]
    cfg = model_config()
    if not cfg.get("api_key"):
        return text
    try:
        import urllib.request
        payload = json.dumps({"model": cfg.get("chat_model") or "ornith1.5-35b",
                              "temperature": 0.0, "max_tokens": MAX_TOKENS,
                              "messages": [{"role": "user",
                                            "content": EXPAND_PROMPT.format(q=text)}]}).encode()
        req = urllib.request.Request((cfg.get("base_url") or "").rstrip("/") + "/chat/completions",
                                     data=payload,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {cfg.get('api_key','')}"})
        with urllib.request.urlopen(req, timeout=BUDGET_S) as r:
            d = json.loads(r.read())
        txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        out = " ".join(txt.replace("\n", " ").split())[:160] or text
    except Exception:
        return text
    _EXPAND_CACHE[text] = out
    try:
        (root / _EXPAND_CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)
        keys = list(_EXPAND_CACHE)[-2000:]
        (root / _EXPAND_CACHE_PATH).write_text(
            json.dumps({k: _EXPAND_CACHE[k] for k in keys}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 当重排器（实验）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("--root", default=ROOT_DEFAULT)
    p.add_argument("--query", default="vLLM 部署有哪些坑"); p.add_argument("--top", type=int, default=TOP_N)
    a = sub.add_parser("try"); a.add_argument("--root", default=ROOT_DEFAULT)
    a.add_argument("--query", required=True); a.add_argument("--top", type=int, default=TOP_N)
    a.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    root = Path(args.root)
    r = LLMReranker(root, top_n=args.top)
    print(f"LLM 重排可用: {r.available()}")
    if not r.available():
        return 1
    # 用真实检索结果试一次（走 rag 的进程内接口，避免缓存干扰）
    import subprocess
    out = subprocess.run([sys.executable, str(root / "pipeline" / "rag.py"), "query", args.query,
                          "--root", str(root), "--top", str(args.top), "--json"],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        print(f"检索失败：{(out.stderr or '')[:200]}"); return 2
    hits = [(h["path"], float(h.get("score") or 0))
            for h in (json.loads(out.stdout or "{}").get("hits") or [])]
    t0 = time.time()
    new = r.rerank(args.query, hits)
    dt = time.time() - t0
    print(f"\n问题：{args.query}   耗时 {dt:.1f}s")
    print("\n重排前：")
    for rel, sc in hits[:8]:
        print(f"   {sc:.3f}  {rel[:70]}")
    print("\n重排后：")
    for rel, sc in new[:8]:
        print(f"   {sc:.3f}  {rel[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
