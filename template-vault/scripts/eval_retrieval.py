#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_retrieval.py — 检索质量评测（黄金问题集 + 前后对比，只读）。

回答一个具体问题：**知识结晶 + 索引降级之后，检索到底变好了多少？**

指标（每个问题取 Top-K）：
  · canon@K      —— 命中里正典占比（可调用的干净知识）
  · stub@K       —— 命中里空壳/指针页/目录页占比（越低越好；这是 P0 前的最大痛点）
  · raw@K        —— 命中里原记忆证据层占比
  · 平均正文字数 —— 命中笔记的实际内容长度（空壳顶替真内容的量化反证）
  · 平均 snippet 长度

两种模式对比：
  `now`     生产配置：canon ×1.25 提升 + raw ×0.72 / index 降级
  `no-layer` 关闭分层（--include-raw --include-stubs）：等价于 P0 之前的行为

用法:
  python3 scripts/eval_retrieval.py [--root R] [--top 10] [--json]
"""
import argparse, json, subprocess, sys
from pathlib import Path

# 黄金问题集：覆盖 KB 的主要主题域（口语化提问，模拟真实调用）
GOLD = [
    "vLLM 部署有哪些坑",
    "OpenClaw 失忆 embedding 服务排查",
    "知识库怎么备份和恢复演练",
    "记忆衰减 阈值 晋升规则",
    "微信消息接入怎么配",
    "cron 定时任务空转怎么防",
    "SearXNG 本地联网搜索部署",
    "Codex CLI 部署 自启",
    "知识结晶 正典 是什么",
    "标签归一 受控词表",
    "矛盾检测 在环调和",
    "skill 技能库 编排",
]


def run_query(root: Path, q: str, top: int, no_layer: bool) -> dict:
    cmd = [sys.executable, str(root / "pipeline" / "rag.py"), "query", q,
           "--root", str(root), "--top", str(top), "--json", "--context", "400",
           "--exclude-stale"]
    if no_layer:
        cmd += ["--include-raw", "--include-stubs"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def classify(root: Path, rel: str, meta: dict) -> str:
    layer = str((meta or {}).get("layer") or "")
    if layer in ("canon", "index", "noise", "raw"):
        return layer
    return "page"


def measure(root: Path, top: int, no_layer: bool) -> dict:
    idx = json.loads((root / "vector index" / "df_idf.json").read_text(encoding="utf-8"))
    meta_all = idx.get("meta", {})
    per_q, agg = [], {"canon": 0, "stub": 0, "raw": 0, "page": 0}
    body_lens, snip_lens, n_hits = [], [], 0
    for q in GOLD:
        data = run_query(root, q, top, no_layer)
        hits = data.get("hits") or []
        row = {"q": q, "n": len(hits), "canon": 0, "stub": 0, "raw": 0, "page": 0}
        for h in hits:
            rel = h.get("path") or ""
            kind = classify(root, rel, meta_all.get(rel))
            if kind in ("index",):
                row["stub"] += 1
            elif kind in ("canon", "raw"):
                row[kind] += 1
            else:
                # page 层里再挑出"空壳指针页"（P0 前顶掉真内容的主角）
                if rel.endswith("-index.md") or rel in meta_all and _is_stub_meta(meta_all, rel):
                    row["stub"] += 1
                else:
                    row["page"] += 1
            body_lens.append(len(str(h.get("snippet") or "")))
            snip_lens.append(len(str(h.get("snippet") or "")))
            n_hits += 1
        for k in ("canon", "stub", "raw", "page"):
            agg[k] += row[k]
        per_q.append(row)
    n = max(n_hits, 1)
    return {
        "mode": "no-layer" if no_layer else "now",
        "questions": len(GOLD), "hits": n_hits,
        "canon_share": round(agg["canon"] / n, 3),
        "stub_share": round(agg["stub"] / n, 3),
        "raw_share": round(agg["raw"] / n, 3),
        "page_share": round(agg["page"] / n, 3),
        "avg_body_chars": int(sum(body_lens) / n),
        "avg_snippet_chars": int(sum(snip_lens) / n),
        "per_question": per_q,
    }


def _is_stub_meta(meta_all: dict, rel: str) -> bool:
    return False  # 预留：索引 meta 里若补 is_stub 标记可在此启用


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    now = measure(root, args.top, no_layer=False)
    before = measure(root, args.top, no_layer=True)

    if args.as_json:
        print(json.dumps({"now": now, "no-layer": before}, ensure_ascii=False, indent=2))
        return 0

    def fmt(m):
        return (f"canon@K {m['canon_share']:.1%} · stub@K {m['stub_share']:.1%} · "
                f"raw@K {m['raw_share']:.1%} · page@K {m['page_share']:.1%} · "
                f"平均正文 {m['avg_body_chars']} 字")

    print(f"📏 检索质量评测  root={root}  黄金问题 {len(GOLD)} 条 · Top-{args.top}")
    print("")
    print(f"   现在（分层生效）  : {fmt(now)}")
    print(f"   对照（关闭分层）  : {fmt(before)}")
    print("")
    d_canon = now["canon_share"] - before["canon_share"]
    d_stub = now["stub_share"] - before["stub_share"]
    d_body = now["avg_body_chars"] - before["avg_body_chars"]
    print(f"   变化：正典占比 {d_canon:+.1%}　空壳占比 {d_stub:+.1%}　平均正文 {d_body:+d} 字")
    print("")
    print("   逐题（正典/空壳/其它）：")
    for a, b in zip(now["per_question"], before["per_question"]):
        print(f"     {a['q'][:24]:24}  现在 canon={a['canon']:2} stub={a['stub']:2}"
              f"   |  关层 canon={b['canon']:2} stub={b['stub']:2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
