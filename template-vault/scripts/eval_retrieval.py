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
    # ── 事实查找 ──
    "vLLM 部署有哪些坑",
    "OpenClaw 失忆 embedding 服务排查",
    "微信消息接入怎么配",
    "SearXNG 本地联网搜索部署",
    "Codex CLI 部署 自启",
    "embedding 服务端口是多少",
    "vLLM 显存占用多少",
    "知识库备份怎么恢复",
    # ── 操作复现 ──
    "怎么重建向量索引",
    "怎么给笔记打标签归一",
    "怎么做 cron 健康巡检",
    "怎么把 agent 记忆摄取进库",
    "怎么开启知识结晶",
    "RAG 索引怎么增量更新",
    # ── 决策依据 ──
    "检索方案为什么选 BM25",
    "记忆引擎为什么保留 dsh-evolve",
    "为什么移除 memory-fortress",
    "为什么禁止直接改 OpenClaw 会话库",
    "vLLM 部署铁律是哪几条",
    # ── 多跳 / 关联 ──
    "记忆衰减 阈值 晋升规则",
    "矛盾检测 在环调和",
    "skill 技能库 编排",
    "知识结晶 正典 是什么",
    "标签归一 受控词表",
    "知识库三层结构是什么",
    "原始记忆 与 正典 什么关系",
    "索引分层 降级 规则",
    "提示词 A/B 测试结论",
    "知识契约 schema 检查什么",
    "无废话 知识 怎么保证",
]


def run_query(root: Path, q: str, top: int, no_layer: bool, no_rerank: bool = False) -> dict:
    cmd = [sys.executable, str(root / "pipeline" / "rag.py"), "query", q,
           "--root", str(root), "--top", str(top), "--json", "--context", "400",
           "--exclude-stale"]
    if no_layer:
        cmd += ["--include-raw", "--include-stubs"]
    env = dict(**__import__("os").environ)
    if not no_rerank:
        env.pop("KB_EMBED_RERANK", None)     # 默认：不开启语义重排
    else:
        env["KB_EMBED_RERANK"] = "1"         # 对照：开启语义重排
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        # 静默失败＝把崩溃当成"0 命中"，会把评测结论带偏（实测踩过：NameError 被当成 0 命中）
        raise RuntimeError(f"kb query 退出码 {r.returncode}：{(r.stderr or '').strip()[:200]}")
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"kb query 输出非 JSON：{(r.stdout or '')[:120]}") from e


def classify(root: Path, rel: str, meta: dict) -> str:
    layer = str((meta or {}).get("layer") or "")
    if layer in ("canon", "index", "noise", "raw"):
        return layer
    return "page"


def measure(root: Path, top: int, no_layer: bool, no_rerank: bool = False) -> dict:
    idx = json.loads((root / "vector index" / "df_idf.json").read_text(encoding="utf-8"))
    meta_all = idx.get("meta", {})
    per_q, agg = [], {"canon": 0, "stub": 0, "raw": 0, "page": 0}
    body_lens, snip_lens, n_hits = [], [], 0
    top1_canon = top3_canon_all = 0
    for q in GOLD:
        data = run_query(root, q, top, no_layer, no_rerank)
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
        kinds = []
        for h in hits:
            rel = h.get("path") or ""
            kinds.append(classify(root, rel, meta_all.get(rel)))
        if kinds:
            top1_canon += 1 if kinds[0] == "canon" else 0
            top3_canon_all += 1 if all(k == "canon" for k in kinds[:3]) else 0
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
        "top1_canon_rate": round(top1_canon / max(len(GOLD), 1), 3),
        "top3_canon_rate": round(top3_canon_all / max(len(GOLD), 1), 3),
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
    norank = measure(root, args.top, no_layer=False, no_rerank=True)

    if args.as_json:
        print(json.dumps({"now": now, "no-layer": before, "no-rerank": norank},
                         ensure_ascii=False, indent=2))
        return 0

    def fmt(m):
        return (f"canon@K {m['canon_share']:.1%} · stub@K {m['stub_share']:.1%} · "
                f"raw@K {m['raw_share']:.1%} · page@K {m['page_share']:.1%} · "
                f"平均正文 {m['avg_body_chars']} 字")

    print(f"📏 检索质量评测  root={root}  黄金问题 {len(GOLD)} 条 · Top-{args.top}")
    print("")
    print(f"   现在（分层生效）  : {fmt(now)}")
    print(f"   对照（关闭分层）  : {fmt(before)}")
    print(f"   对照（开启语义重排）: {fmt(norank)}"
          f"  top1正典 {norank['top1_canon_rate']:.0%} · top3全正典 {norank['top3_canon_rate']:.0%}")
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
