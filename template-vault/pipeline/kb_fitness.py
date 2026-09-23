#!/usr/bin/env python3
# ============================================================
# kb_fitness.py —— 外部锚定的 fitness（P1-A：修 fitness 的 Goodhart 闭环）
# ------------------------------------------------------------
# 问题（Goodhart）：原来 T3  tunes 的是 health_score =
#   100 − min(45, orphan_pct·3) − min(45, stale_pct·3) − 接地惩罚。
#   而 T1/T2 自己的改动（把孤儿补链→orphan_pct↓、retire→stale_pct↓）会直接
#   把这分抬高 → T3 在优化一个"系统能自抬的数字" → 自证其美、趋近 Model Collapse。
#
# 解法：给 T3 一个**外部锚定的 fitness**，它的四个分量**都无法被 T1/T2 通过编辑
#   编译笔记而刷爆**：
#   1. source_diversity  —— raw/（不可变外部源）的领域 Shannon 熵归一化。
#                          raw/ 只在"摄入真实外部源"时才变，编辑编译笔记改不了它。
#   2. external_feedback —— 被真实显式 useful 反馈覆盖的编译笔记占比（读 feedback_state）。
#                          只有真人/agent 说有用才累积，引擎自改刷不出来。
#   3. contradiction_zero_score —— 矛盾检测 lint 的零分（kb_contradiction）。
#                          矛盾靠人工复核才消，T1/T2 不会自动删以藏矛盾 → 难以作弊。
#   4. query_success      —— 查询成功率代理 = 1 − 再问率（Layer 2，读 agent_hits）。
#                          再问 = 前次没答上，真实外部信号；无查询数据时中性=1.0。
#
# 各分量 ∈[0,1]，fitness = 加权和。T3 改以 fitness 的**趋势**做调参依据，
#   internal health 仅作辅助显示。结论：只有"真实外部接地在变好"时 fitness 才升，
#   自闭式自我编辑再也刷不出正趋势 → T3 不会在真空中自我膨胀。
#
# 用法:
#   python3 pipeline/kb_fitness.py [--root R] [--json]
# ============================================================
import argparse, os, sys, json, math
from pathlib import Path
from collections import Counter

import kb_rsi
import feedback_loop as fl

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)

# 各分量权重（合计 1.0）
W_SOURCE = 0.28          # 来源多样性
W_FEEDBACK = 0.28        # 外部反馈率
W_CONTRAD = 0.24         # 矛盾零分
W_QUERY = 0.20           # 查询成功率代理（Layer 2：1 − 再问率，读 agent_hits；无数据=中性 1.0）

# 外部反馈率归一化目标：>= 此比例的编译笔记被真实反馈覆盖 → 满分
FEEDBACK_TARGET = 0.10

EXCLUDE_DIRS = kb_rsi.EXCLUDE_DIRS


def _shannon_norm(counts):
    """Counter → 归一化 Shannon 熵 ∈[0,1]（1 = 均匀分布在所有类别）。"""
    total = sum(counts.values())
    if total == 0 or len(counts) <= 1:
        return 0.0
    h = -sum(c / total * math.log(c / total) for c in counts.values())
    return h / math.log(len(counts))  # 除以 log(k) 归一化到 [0,1]


def source_diversity(root, notes):
    """raw/ 外部源的领域分布多样性（Simpson 多样性指数 1-Σp²，随领域数/均衡度上升）。"""
    srcs = [n for n in notes if n["kind"] == "raw"]
    dom = Counter(n["fm"].get("domain", "-") for n in srcs)
    if not dom:
        return 0.0, 0, {}
    total = sum(dom.values())
    simpson = round(1.0 - sum((c / total) ** 2 for c in dom.values()), 4)
    return simpson, len(srcs), dict(dom)


def external_feedback_rate(root, compiled_rels):
    """被真实显式 useful 反馈覆盖的编译笔记占比（读 feedback_state）。"""
    try:
        state = fl.load_state(root)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        state = {}
    fed = {fb.get("hit") for fb in state.get("explicit_feedback", []) if fb.get("useful")}
    touched = fed & set(compiled_rels)
    total = len(compiled_rels) or 1
    rate = len(touched) / total
    return round(rate, 4), len(touched)


def contradiction_component(root):
    import kb_contradiction as kc
    pairs = kc.detect(root)
    return kc.contradiction_zero_score(len(pairs)), len(pairs)


def query_success_component(root, window=3600):
    """查询成功率代理 = 1 − 再问率（读 agent_hits，仅精确匹配，确定不快）。
    无 per-query 数据 → 中性 1.0（不因“未采集真实流量”而惩罚 fitness）。"""
    try:
        import kb_usage
        a = kb_usage.analyze(root, window=window, use_embedding=False, use_llm=False)
    except (ImportError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        a = {}
    if a.get("status") == "ok" and a.get("query_success_proxy") is not None:
        return round(a["query_success_proxy"], 4), a.get("n_queries")
    return 1.0, a.get("n_queries")


def fitness(root):
    """计算外部锚定 fitness（0-1）及各分量，供 T3 做调参趋势依据。"""
    root = Path(root)
    kb_rsi.root = root  # kb_rsi.metrics/is_moc 依赖全局 root（仅其 main() 会设）；外部调用方须先设
    notes = kb_rsi.collect(root)
    m = kb_rsi.metrics(notes=notes)
    if not m:
        return {"valid": False, "fitness": None, "note": "库为空或无可用编译笔记"}

    compiled_rels = [n["rel"] for n in notes if n["kind"] != "raw"]

    src_div, n_raw, dom_dist = source_diversity(root, notes)
    fb_rate, n_fed = external_feedback_rate(root, compiled_rels)
    fb_component = round(min(1.0, fb_rate / FEEDBACK_TARGET), 4)
    cz, n_pairs = contradiction_component(root)
    qs_rate, n_queries = query_success_component(root)

    fitness = round(W_SOURCE * src_div + W_FEEDBACK * fb_component + W_CONTRAD * cz
                    + W_QUERY * qs_rate, 4)
    return {
        "valid": True,
        "fitness": fitness,
        "components": {
            "source_diversity": round(src_div, 4),
            "external_feedback_rate": fb_rate,
            "external_feedback_component": fb_component,
            "contradiction_zero_score": cz,
            "query_success": qs_rate,
            "query_success_component": qs_rate,
        },
        "w": {"source": W_SOURCE, "feedback": W_FEEDBACK, "contradiction": W_CONTRAD, "query": W_QUERY},
        "n_raw_sources": n_raw,
        "raw_domain_dist": dom_dist,
        "n_feedbacked_notes": n_fed,
        "n_contradictions": n_pairs,
        "n_queries": n_queries,
        "compiled_total": m["total"],
        "note": ("fitness 锚定在真实外部接地（多样外部源 + 真实反馈 + 少矛盾 + 高查询成功率），"
                 "T1/T2 的自编辑刷不动它，故 T3 不再优化自抬数字。"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    f = fitness(args.root)
    if args.json:
        print(json.dumps(f, ensure_ascii=False, indent=2))
    else:
        if not f.get("valid"):
            print("⚠️ " + f["note"]); return 1
        c = f["components"]
        print(f"💪 fitness = {f['fitness']:.3f}  (0-1，外部锚定)")
        print(f"   - 来源多样性(raw领域熵)      {c['source_diversity']:.3f}  "
              f"({f['n_raw_sources']} 外部源, {f['raw_domain_dist']})")
        print(f"   - 外部反馈率                 {c['external_feedback_rate']:.3f}  "
              f"({f['n_feedbacked_notes']} 篇被真实反馈覆盖; 满分目标 ≥{FEEDBACK_TARGET*100:.0f}%)")
        print(f"   - 矛盾零分                   {c['contradiction_zero_score']:.3f}  "
              f"({f['n_contradictions']} 对矛盾)")
        print(f"   - 查询成功率代理             {c['query_success']:.3f}  "
              f"(= 1 − 再问率; {f['n_queries']} 查询事件; 无数据=中性 1.0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
