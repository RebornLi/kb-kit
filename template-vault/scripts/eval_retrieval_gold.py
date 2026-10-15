#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_retrieval_gold.py — 检索质量的**确定性**评测（不需要 LLM 裁判）。

与 `kb eval`（RAGAS 式，需要本地模型当裁判、且依赖人工确认的 gold 集）不同，
本脚本只做**检索排序**评测：拿黄金集里每题的目标笔记路径，看它们有没有被排进前 K。
因此它是可复现、零成本、可进 CI 的。

数据来源（按优先级）：
  `.kb/eval/gold_set.jsonl`        —— 人工确认过的（推荐）
  `.kb/eval/gold_candidates.jsonl` —— `kb eval gold --generate` 生成的候选
两者的字段都可识别：`q`（问题）+ 目标路径（`gold_rels` / `sources` / `relevant`）。

用法：
  python3 scripts/eval_retrieval_gold.py                       # 评测当前 rag
  python3 scripts/eval_retrieval_gold.py --module rag_b        # A/B：换一份 rag 实现
  python3 scripts/eval_retrieval_gold.py --top 10 --json
  python3 scripts/eval_retrieval_gold.py --show-misses

为什么需要它：`pipeline/rag.py` 的倒排加成是**加性**的，量级远大于 RRF 融合分，
看起来像量纲缺陷。实测下来它才是排序的主力（30 题黄金集：现状 recall@10 98.3%，
改成乘性+IDF 加权后掉到 55.0%）。**任何对排序的改动都必须先用本脚本复测。**

⚠️ 副作用：本脚本走的是真实 `cmd_query`，因此会像普通查询那样写入
`<vault>/.kb/state/query_cache.json` 与 `feedback_state.json` 的 agent_hits
（并每次都清掉 query_cache 以保证真算）。要在生产库上跑，先确认这是可接受的。
若只想做 A/B 对照，建议先 `tar` 复制一份库到临时目录再跑。
"""
import argparse
import contextlib
import importlib
import io
import json
import os
import random
from typing import Dict, List
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parents[1]
PIPELINE = VAULT / "pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

GOLD_CANDIDATES = (".kb/eval/gold_set.jsonl", ".kb/eval/gold_candidates.jsonl")
TARGET_FIELDS = ("gold_rels", "sources", "relevant", "targets", "expected")


def load_gold(root: Path):
    """读黄金集 → [(question, [目标相对路径])]。"""
    for rel in GOLD_CANDIDATES:
        p = root / rel
        if not p.exists():
            continue
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = str(d.get("q") or d.get("query") or "").strip()
            tgt = []
            for f in TARGET_FIELDS:
                v = d.get(f)
                if isinstance(v, list) and v:
                    tgt = [str(x) for x in v]
                    break
            if q and tgt:
                rows.append((q, tgt))
        if rows:
            return rows, rel
    return [], ""


def rank(rag_mod, root: Path, query: str, top: int):
    """跑一次真实查询，取回命中路径列表（禁缓存，保证每次真算）。"""
    cache = root / ".kb" / "state" / "query_cache.json"
    try:
        cache.unlink()
    except OSError:
        pass
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        # no_record=True：评测绝不能写 agent_hits，否则会污染再问率并反过来压低 fitness
        rag_mod.cmd_query(root, query, top, False, no_record=True)
    out = []
    for line in buf.getvalue().splitlines():
        if line.startswith("⭐"):
            out.append(line.split("← ")[1].split(" · ")[0])
    return out


# ── 配对 bootstrap：小样本下判断"差异是否真实"的唯一诚实办法 ──────────
#   为什么需要：黄金集只有 30 题，1 题 = 3.3pp。之前我多次写"在噪声内"，
#   但那是**目测**；配对 bootstrap 给出的是"同一批题重采样后差异的分布"，
#   能直接回答"这个 +12pp 是不是运气"。文献口径：n=30 时只信 ≥5pp 的差异。
def _metrics_from(rows, top: int) -> Dict[str, float]:
    n = len(rows)
    if not n:
        return {}
    out = {"top1": sum(r["top1"] for r in rows) / n, "mrr": sum(r["rr"] for r in rows) / n}
    for k in (1, 4, 5, top):
        if k <= top:
            # ⚠️ 键兼容：内存里 rec 的键是 int，但**经 JSON 落盘再读回来会变成字符串**
            #    （`{"1": …}`）。只按 int 取会让基线全读到 0，从而把"与自己对比"报成
            #    巨大差异 —— 本文件初版就踩了这个坑，自检立刻抓到。
            out[f"recall@{k}"] = sum(
                (r.get("rec") or {}).get(k, (r.get("rec") or {}).get(str(k), 0.0))
                for r in rows) / n
    return out


def bootstrap_ci(per_q: List[Dict[str, object]], top: int, iters: int,
                 seed: int = 20261016) -> Dict[str, List[float]]:
    """对各指标做有放回重采样，给 95% 置信区间（百分位法）。"""
    rnd = random.Random(seed)
    n = len(per_q)
    dist: Dict[str, List[float]] = {}
    for _ in range(max(1, iters)):
        sample = [per_q[rnd.randrange(n)] for _ in range(n)]
        for k, v in _metrics_from(sample, top).items():
            dist.setdefault(k, []).append(v)
    out = {}
    for k, vs in dist.items():
        vs.sort()
        out[k] = [round(vs[int(len(vs) * 0.025)], 4), round(vs[int(len(vs) * 0.975)], 4)]
    return out


def paired_bootstrap(per_q: List[Dict[str, object]], base_json: Dict[str, object],
                     top: int, iters: int, seed: int = 20261016) -> Dict[str, object]:
    """与基线做**配对** bootstrap：按同一批题重采样，看差异分布是否跨过 0。

    配对（而不是两独立样本）是关键：同一道题在两个方案下的得分高度相关，
    配对能消掉题目难度差异，小样本下功效高得多。
    """
    base_rows = {r["q"]: r for r in (base_json.get("per_q") or [])}
    cur = {r["q"]: r for r in per_q}
    common = sorted(set(base_rows) & set(cur))
    if not common:
        return {"error": "基线的 per_q 与当前题目无交集（基线需用 --json 且含 per_q 保存）"}
    rnd = random.Random(seed)
    diffs: Dict[str, List[float]] = {}
    for _ in range(max(1, iters)):
        pick = [common[rnd.randrange(len(common))] for _ in range(len(common))]
        a = _metrics_from([cur[q] for q in pick], top)
        b = _metrics_from([base_rows[q] for q in pick], top)
        for k in a:
            if k in b:
                diffs.setdefault(k, []).append(a[k] - b[k])
    out: Dict[str, object] = {"n_common": len(common), "iters": iters, "delta": {}}
    for k, vs in diffs.items():
        vs.sort()
        lo, hi = vs[int(len(vs) * 0.025)], vs[int(len(vs) * 0.975)]
        obs = (_metrics_from([cur[q] for q in common], top).get(k, 0.0)
               - _metrics_from([base_rows[q] for q in common], top).get(k, 0.0))
        out["delta"][k] = {
            "observed": round(obs, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            # 区间跨 0 → 差异不可信（可能是运气）
            "significant": bool(lo > 0 or hi < 0),
            "trustworthy_5pp": bool(abs(obs) >= 0.05 and (lo > 0 or hi < 0)),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="检索排序确定性评测（黄金集）")
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or str(VAULT))
    ap.add_argument("--module", default="rag",
                    help="用于打分的 rag 实现模块名（在同一 pipeline/ 下），供 A/B 对照")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--bootstrap", type=int, default=0, metavar="N",
                    help="配对 bootstrap 重采样次数（如 2000）：给出各指标的 95%% 置信区间")
    ap.add_argument("--compare", default="", metavar="JSON",
                    help="与一份先前 --json 输出做配对 bootstrap 检验（n=30 时只信 ≥5pp 差异）")
    ap.add_argument("--save", default="", metavar="JSON", help="把逐题结果存下来供下次 --compare")
    ap.add_argument("--show-misses", action="store_true",
                    help="逐题打印未命中的目标笔记（诊断用）")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    gold, src = load_gold(root)
    if not gold:
        print(f"✗ 未找到黄金集（找过 {', '.join(GOLD_CANDIDATES)}）。先跑 `kb eval gold --generate`。",
              file=sys.stderr)
        return 2

    try:
        rag = importlib.import_module(args.module)
    except ImportError as e:
        print(f"✗ 无法导入模块 {args.module}: {e}", file=sys.stderr)
        return 2

    n = len(gold)
    top1 = 0
    rec = {1: 0.0, 4: 0.0, 5: 0.0, args.top: 0.0}
    rr = 0.0
    misses = []
    per_q = []          # 逐题指标 —— bootstrap 需要按题重采样（配对检验的原料）
    for q, tgt in gold:
        tset = set(tgt)
        hits = rank(rag, root, q, args.top)
        q_top1 = 1.0 if (hits and hits[0] in tset) else 0.0
        if q_top1:
            top1 += 1
        q_rec = {}
        for k in list(rec):
            if k <= args.top:
                v = sum(1 for h in hits[:k] if h in tset) / len(tset)
                rec[k] += v
                q_rec[k] = v
        pos = next((i for i, h in enumerate(hits, 1) if h in tset), None)
        q_rr = 1.0 / pos if pos else 0.0
        rr += q_rr
        per_q.append({"q": q, "top1": q_top1, "rr": q_rr, "rec": q_rec})
        if args.show_misses:
            got = [h for h in hits[: args.top] if h in tset]
            if len(got) < len(tset):
                misses.append({"q": q, "hit": got, "miss": sorted(tset - set(got))})

    m = {"n_questions": n, "source": src, "module": args.module, "top": args.top,
         "top1": round(top1 / n, 4),
         "mrr": round(rr / n, 4),
         "recall": {f"@{k}": round(v / n, 4) for k, v in sorted(rec.items()) if k <= args.top}}

    if args.bootstrap:
        m["ci95"] = bootstrap_ci(per_q, args.top, args.bootstrap)

    # 与基线对比：**配对 bootstrap**（同一批题重采样 → 差异的置信区间）
    if args.compare:
        try:
            base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
            m["compare"] = paired_bootstrap(per_q, base, args.top, args.bootstrap or 2000)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ 无法对比基线 {args.compare}: {type(e).__name__}", file=sys.stderr)

    m["per_q"] = per_q      # 逐题结果：--compare 做**配对**检验时必需（必须在 --save 之前）

    if args.save:
        Path(args.save).write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 已保存逐题结果 → {args.save}", file=sys.stderr)

    if args.as_json:
        if args.show_misses:
            m["misses"] = misses
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    print(f"🔎 检索排序评测（黄金集 {src}）")
    print(f"   {n} 题 · 每题目标 {len(gold[0][1])} 个 · 实现在 pipeline/{args.module}.py · top={args.top}")
    print("   口径：top1 = 至少一个目标排第 1；recall@K = 前 K 条覆盖了多少比例的目标；MRR = 首个命中的倒数排名均值")
    print(f"\n   top1      {m['top1']*100:6.1f}%")
    for k in sorted(rec):
        if k <= args.top:
            print(f"   recall@{k:<3} {m['recall'][f'@{k}']*100:6.1f}%")
    print(f"   MRR       {m['mrr']:6.3f}")
    if args.show_misses and misses:
        print(f"\n   未跑满的题（{len(misses)}/{n}）:")
        for x in misses[:15]:
            print(f"    · {x['q'][:40]}")
            for mm in x["miss"][:3]:
                print(f"        miss: {mm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
