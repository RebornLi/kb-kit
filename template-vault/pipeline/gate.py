#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate.py — 写入前质量门禁（三态）与来源分级。

## 为什么是"三态"而不是"通过/拒绝"

2024–2026 的语料质量实践（Gopher / FineWeb / datatrove）与本次调研的结论一致：
**拒绝是不可逆的，放过是可挽回的**。个人知识库里"拒掉一篇真笔记"意味着知识永久丢失，
而"放进一篇噪声"可以用降权 + 复核队列救回来。所以：

  · **pass**       正常入库
  · **quarantine** 入库但**标记 + 降权 + 进复核队列**（噪声嫌疑，但不武断否定）
  · **reject**     **只用于不变量**：没有来源的"编译产物"、schema 关键字段缺失、
                   正文为空/纯占位符。这些是"结构上不可能正确"，不是"质量存疑"。

## 来源分级与 LLM 回灌防护

`source_tier`（1 最高）参与检索打分与冲突裁决：
  1 官方文档/规范原文 · 2 同行评议/标准 · 3 工程博客 · 4 个人笔记 · 5 LLM 生成

实证风险：2025 年中约 **35%** 的新发布网页被判为 AI 生成或辅助，AI 文本平均语义相似度
比人类高 **33%** —— 即"语义收缩"。若不设防，LLM 产物回灌知识库会让库越来越同质。
防护：LLM 产物一律 `derived: true`，且**必须挂 ≥1 条非 LLM 的 `source_ref`**，否则 reject。

用法：
    python3 pipeline/gate.py check [--root R] [--json]     # 扫描存量笔记的三态分布
    python3 pipeline/gate.py probe <rel>                    # 看单篇的判定与理由
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 来源分级（数字越小越权威）
SOURCE_TIER = {
    "official": 1, "spec": 1, "标准": 1, "官方": 1,
    "paper": 2, "标准文献": 2, "同行评议": 2,
    "blog": 3, "工程博客": 3,
    "note": 4, "个人笔记": 4,
    "llm": 5, "generated": 5, "ai": 5,
}
TIER_NAME = {1: "官方/规范", 2: "同行评议", 3: "工程博客", 4: "个人笔记", 5: "LLM 生成"}

# 硬性不变量（→ reject）
MIN_BODY_CHARS = 15
# ⚠️ 这个正则**不能**写成 `^(?:...|-+|\s)*$` 之类 —— `-+` 嵌套在 `(?:...)*` 里，遇到
#   「很多 - 后面跟个非 -」的正文会**灾难性回溯**（实测每多 2 字符耗时 ×4：n=26 就 2.5 秒，
#   n=40 是几小时）。一篇带 `----` 分隔线的笔记就能把 gate.classify 卡死（faulthandler 定位）。
#   改成字符类单量词（线性）+ 单独匹配占位词。


def _is_placeholder(body: str) -> bool:
    b = body.strip()
    if not b:
        return True
    if re.fullmatch(r"[\s\-_.·•*]+", b):
        return True                      # 只有空白/横线/点/下划线/星号 → 占位符
    return re.fullmatch(r"(?i)(todo|tbd|待补|占位|placeholder|待补充|内容待补)[\s\-_.]*", b) is not None


def _tier_of(fm: Dict[str, Any]) -> int:
    v = str(fm.get("source_tier") or "").strip().lower()
    if v.isdigit() and 1 <= int(v) <= 5:
        return int(v)
    return SOURCE_TIER.get(v, 4)          # 缺省当"个人笔记"


def classify(fm: Dict[str, Any], body: str, rel: str = "") -> Dict[str, Any]:
    """三态判定 + 来源分级。返回 {verdict, reasons, source_tier, tier_name}。"""
    reasons: List[str] = []
    body = body or ""
    tier = _tier_of(fm)
    derived = str(fm.get("derived", "")).strip().lower() in ("true", "1", "yes") or tier == 5

    # ── 硬性不变量 → reject（结构上不可能正确，不是"质量存疑"）──
    if len(body.strip()) < MIN_BODY_CHARS:
        reasons.append(f"正文过短（{len(body.strip())} < {MIN_BODY_CHARS} 字）")
    if _is_placeholder(body):
        reasons.append("正文是占位符")
    if not str(fm.get("status") or "").strip():
        reasons.append("缺 status（契约必填）")
    # LLM 回灌防护：derived 内容必须有非 LLM 的来源指针
    if derived:
        sref = fm.get("source_ref") or fm.get("source") or []
        items = sref if isinstance(sref, list) else [x for x in str(sref).split(",") if x.strip()]
        if not [x for x in items if str(x).strip()]:
            reasons.append("derived/LLM 生成内容缺少 source_ref（无法追溯到非 LLM 来源）")
    if reasons:
        return {"verdict": "reject", "reasons": reasons, "source_tier": tier,
                "tier_name": TIER_NAME.get(tier, "?")}

    # ── 噪声嫌疑 → quarantine（入库但标记降权 + 复核）──
    soft: List[str] = []
    if tier == 5:
        soft.append("LLM 生成（tier 5）：默认降权，不进正典候选")
    lines = [l for l in body.splitlines() if l.strip()]
    if lines:
        dup = len(lines) - len(set(lines))
        if dup / len(lines) > 0.4:
            soft.append(f"重复行占比 {dup / len(lines):.0%}")
    # 信息密度代理：符号/空白占比过高通常是抓取残渣
    nonspace = [c for c in body if not c.isspace()]
    if nonspace:
        punct = sum(1 for c in nonspace if not (c.isalnum() or "\u4e00" <= c <= "\u9fff"))
        if punct / len(nonspace) > 0.45:
            soft.append(f"标点/符号占比 {punct / len(nonspace):.0%}")
    long_lines = [l for l in lines if len(l) > 1200]
    if long_lines:
        soft.append(f"含 {len(long_lines)} 行超长行（可能是拼接残渣）")
    if soft:
        return {"verdict": "quarantine", "reasons": soft, "source_tier": tier,
                "tier_name": TIER_NAME.get(tier, "?")}
    return {"verdict": "pass", "reasons": [], "source_tier": tier,
            "tier_name": TIER_NAME.get(tier, "?")}


def check(root: Path, as_json: bool = False, limit: int = 20) -> int:
    from kb_common import iter_notes, load_meta, is_source_note
    import rag
    rows: List[Dict[str, Any]] = []
    counts = {"pass": 0, "quarantine": 0, "reject": 0}
    scanned = 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if rag.is_generated_report(rel):
            continue
        try:
            fm, body = load_meta(p)
        except OSError:
            continue
        if is_source_note(fm):
            continue                       # 证据层原文不按"编译产物"标准判
        v = classify(fm, body, rel)
        scanned += 1
        # ⚠️ pass 也要计数：初版只为非 pass 项建行，导致输出永远是 "pass 0"
        #   （看起来像"一篇都没通过"），属于会误导人的统计 bug。
        counts[v["verdict"]] += 1
        if v["verdict"] != "pass":
            rows.append({"rel": rel, **v})
    total = len(rows)
    if as_json:
        print(json.dumps({"scanned": scanned, "counts": counts,
                          "items": rows[:limit]}, ensure_ascii=False, indent=2))
        return 0
    print(f"🚦 写入前质量门禁 · {root}")
    print(f"   扫描 {scanned} 篇 · pass {counts['pass']} · "
          f"quarantine {counts['quarantine']} · reject {counts['reject']}")
    for verdict in ("reject", "quarantine"):
        sel = [r for r in rows if r["verdict"] == verdict][:limit]
        if not sel:
            continue
        print(f"\n   [{verdict}] 前 {len(sel)} 篇：")
        for r in sel:
            print(f"     {r['rel'][:58]}")
            for why in r["reasons"][:2]:
                print(f"        · {why}")
    print("\n   设计取舍：reject **只用于不变量**（无可追溯来源的编译产物/缺必填/空正文）——")
    print("   个人库里误拒不可恢复、误收可用降权挽回，所以噪声一律走 quarantine。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="写入前质量门禁（三态）+ 来源分级")
    ap.add_argument("cmd", nargs="?", default="check", choices=["check", "probe"])
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or ".")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("rel", nargs="?", default="")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if a.cmd == "probe":
        from kb_common import load_meta
        fm, body = load_meta(root / a.rel)
        print(json.dumps(classify(fm, body, a.rel), ensure_ascii=False, indent=2))
        return 0
    return check(root, a.as_json, a.limit)


if __name__ == "__main__":
    sys.exit(main())
