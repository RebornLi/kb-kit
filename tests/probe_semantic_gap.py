#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_semantic_gap.py — 量化「语义相关但用词不同」的检索缺口（只读）。

为什么需要：`gold_candidates.jsonl` 是用**同一套检索**生成的，它自然测不出
「笔记记过、但查询用词与原笔记不重合」这种情况 —— 而那正是"第二大脑"最痛的失败。
本探针用**改写式查询**制造这种缺口：

  1. 取 N 篇笔记；
  2. 让本地 chat 模型写一个「该笔记能回答、但**尽量不用原文用词**」的问题；
  3. 分别用 (a) 现有词法检索、(b) 稠密向量最近邻 找该笔记；
  4. 报告两者的命中率差距 —— 这就是稠密检索的**可量化收益上限**。

用法：python3 probe_semantic_gap.py [--root .] [--n 20] [--top 10]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PIPE = Path(__file__).resolve().parent


def _cfg(root: Path) -> dict:
    sys.path.insert(0, str(root / "pipeline"))
    from kb_config import model_config
    return model_config(root)


def _post(url: str, payload: dict, key: str, timeout: int = 120):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def paraphrase(cfg: dict, note_body: str) -> str:
    """让本地模型写一条"换措辞"的查询（明确要求不要复用原文关键词）。"""
    prompt = (
        "下面是一段知识笔记。请写**一个用户可能提出的问题**，该问题应该能被这段笔记回答，"
        "但**尽量换个说法**：不要照抄笔记里的专有名词和关键短语，用同义词或上位概念替代表达。\n"
        "只输出问题本身，一行，不要解释。\n\n笔记：\n" + note_body[:1200])
    out = _post(cfg["base_url"].rstrip("/") + "/chat/completions",
                {"model": cfg["chat_model"], "temperature": 0.3,
                 "messages": [{"role": "user", "content": prompt}]}, cfg.get("api_key", ""))
    return out["choices"][0]["message"]["content"].strip().splitlines()[0]


def embed(cfg: dict, texts):
    out = _post(cfg["embed_url"].rstrip("/") + "/embeddings",
                {"model": cfg["embed_model"], "input": texts}, cfg.get("api_key", ""))
    return [d["embedding"] for d in out["data"]]


def cos(a, b):
    import math
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return d / (na * nb)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    cfg = _cfg(root)
    if not cfg.get("base_url") or not cfg.get("embed_url"):
        print("✗ 缺少模型端点配置（.kb/model.json）", file=sys.stderr)
        return 2

    # 抽样：内容足够长、且不是索引/报告类
    cands = []
    for p in sorted(root.rglob("*.md")):
        rel = str(p.relative_to(root))
        if any(s in p.parts for s in (".git", ".kb", "pipeline", "backups", "logs",
                                      "vector index", "_curate", "raw")):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
        if 400 < len(body) < 4000:
            cands.append((rel, body))
    step = max(1, len(cands) // a.n)
    sample = cands[::step][:a.n]
    print(f"🔬 语义缺口探针 · root={root} · 抽样 {len(sample)} 篇（候选池 {len(cands)}）\n")

    # 全库向量（分批）
    print("  ① 计算全库向量（分批）…")
    docs, vecs, t0 = [], [], time.time()
    batch = 32
    for i in range(0, len(cands), batch):
        chunk = cands[i:i + batch]
        try:
            vs = embed(cfg, [c[1][:2000] for c in chunk])
        except Exception as e:      # noqa: BLE001
            print(f"     嵌入失败：{type(e).__name__}", file=sys.stderr)
            return 2
        docs += [c[0] for c in chunk]
        vecs += vs
    print(f"     {len(docs)} 篇 · {time.time() - t0:.0f}s\n")

    lex_hit = den_hit = 0
    rows = []
    print("  ② 逐题对比（词法检索 vs 稠密最近邻）")
    for rel, body in sample:
        try:
            q = paraphrase(cfg, body)
        except Exception as e:      # noqa: BLE001
            print(f"     改写失败 {rel}: {type(e).__name__}", file=sys.stderr)
            continue
        # (a) 词法
        try:
            (root / ".kb" / "state" / "query_cache.json").unlink()
        except OSError:
            pass
        env = {**os.environ}
        env.pop("KB_ROOT", None)
        r = subprocess.run([sys.executable, str(root / "pipeline" / "rag.py"), "query", q,
                            "--root", str(root), "--top", str(a.top), "--json", "--no-record"],
                           capture_output=True, text=True, env=env)
        lex = [h["path"] for h in (json.loads(r.stdout[r.stdout.find("{"):]).get("hits") or [])]
        # (b) 稠密
        qv = embed(cfg, [q])[0]
        ranked = sorted(zip(docs, vecs), key=lambda x: -cos(qv, x[1]))[:a.top]
        dense = [d for d, _ in ranked]
        in_lex, in_den = rel in lex, rel in dense
        lex_hit += in_lex
        den_hit += in_den
        rows.append({"note": rel, "query": q, "lexical": in_lex, "dense": in_den})
        mark = ("词法✓" if in_lex else "词法✗") + " " + ("稠密✓" if in_den else "稠密✗")
        print(f"     {mark}  {rel[:46]}")
        print(f"        q: {q[:70]}")

    n = len(rows)
    print(f"\n  ③ 结论（top-{a.top}，{n} 题）")
    print(f"     词法检索命中源笔记: {lex_hit}/{n} = {lex_hit / max(n, 1):.0%}")
    print(f"     稠密向量命中源笔记: {den_hit}/{n} = {den_hit / max(n, 1):.0%}")
    both = sum(1 for r in rows if r["lexical"] and r["dense"])
    only_d = sum(1 for r in rows if r["dense"] and not r["lexical"])
    only_l = sum(1 for r in rows if r["lexical"] and not r["dense"])
    print(f"     两者都中 {both} · **仅稠密中 {only_d}** · 仅词法中 {only_l}")
    print(f"\n     → 改写式查询下，纯语义能多找回 {only_d} 篇（占 {only_d / max(n, 1):.0%}）")
    out = root / ".kb" / "eval" / "semantic_gap.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"     明细 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
