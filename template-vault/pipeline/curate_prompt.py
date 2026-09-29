#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""curate_prompt.py — 结晶提示词层（四层结构 + schema 注入 + 范文库）。

设计要点（P1）：
  L1 契约层  从 reference/kb-schema.json 注入「页型 → 必填节」与字段约束
  L2 规则层  短小、可执行（接地 / 区分 verbatim-extracted-inferred / 禁改标识符）
  L3 任务层  按 verdict 分化：create / merge / demote
  L4 范文层  从**已通过校验的正典**里自动挑同类样例（few-shot）

为什么这样拆：旧版把「页型、规则、任务、示例」揉成一段 30 行铁律 + 一个固定 schema，
改一处要翻代码，也无法按内容类型分化。现在页型与节名来自契约文件，
提示词只是契约的「投影」——契约改了，提示词自动跟着改。

用法（自检）:
  python3 pipeline/curate_prompt.py show  --schema reference/kb-schema.json   # 打印四层提示词
  python3 pipeline/curate_prompt.py bank  --root R [--limit 5]               # 看范文库挑出了哪些
"""
import argparse, json, re, sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 页型 → 触发关键词（用于把原文归类到契约里的 page_types）──────────
TYPE_HINTS: Dict[str, List[str]] = {
    "canon-decision": ["决定", "决策", "选择", "选了", "方案", "拍板", "敲定", "放弃", "取舍", "采用"],
    "canon-sop":      ["步骤", "流程", "操作", "部署", "安装", "配置", "执行", "runbook", "sop", "怎么", "如何"],
    "canon-lesson":   ["坑", "踩坑", "教训", "复盘", "根因", "报错", "失败", "翻车", "隐患", "bug"],
    "canon-reference": ["速查", "参数", "清单", "对照", "表", "枚举", "端口", "阈值", "默认值"],
}

SYSTEM_HEADER = """你是知识库的「结晶器」：把原始记录（会话日志 / 操作流水 / 碎片）炼成
**可被检索、可直接调用**的干净知识。你不是摘要器，你是编辑。"""

RULES = """## 规则（违反即失败）
1. 只留有复用价值的内容：结论 / 依据 / 可操作步骤 / 坑与边界 / 决策与理由。
2. 删掉废话：寒暄、过程冗余、"我正在/接下来/好的"、无关的临时记录。
3. 逻辑重排：先结论，后依据与操作；同主题合并，绝不写成流水账。
4. **绝不编造**：原文没有的数字/命令/路径/版本，一个字都不许写。
5. 关键标识符（命令、路径、端口、版本、时长、报错串）保持**逐字精确**，不改写、不翻译、不舍入。
6. facts 必须分型：
   - `verbatim`  原文逐字出现的串（首选）
   - `extracted` 从原文归纳出的短事实（须能在原文找到依据）
   - `inferred`  你的推断（正文里必须带 `^[推断]` 标记，且不计入接地）
7. 原文确实没有可复用知识（只是过程记录）→ `verdict="raw-only"`，不要硬凑。
8. 原文只是空壳/目录/纯指针 → `verdict="noise"`。
9. 原文与「已有正典」讲同一件事 → `verdict="merge"`，并给出 `merge_into`。"""

LENGTH_RULES = """## 长度纪律（硬约束：超长会被截断而整篇作废）
- `sections` 每节最多 3 条；每条 ≤ 40 字，只写要点，不复述原文。
- `facts` 最多 8 条，每条只填标识符或短事实本身。
- `dropped` 只写 1 条（一句话概括删了什么）。
- 整个 JSON ≤ 1200 字。"""


# ── L1 契约层 ────────────────────────────────────────────────
def load_schema(root: Path) -> Optional[dict]:
    try:
        return json.loads((root / "reference" / "kb-schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def guess_page_type(body: str, fm: Dict[str, Any]) -> str:
    """按关键词把原文粗分到契约页型（决定必填节）。默认 canon-generic。"""
    text = (body or "")[:4000]
    scores = {t: sum(1 for kw in kws if kw in text) for t, kws in TYPE_HINTS.items()}
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 2:            # 证据不足 → 兜底页型
        return "canon-generic"
    return best


def schema_block(schema: Optional[dict], page_type: str) -> str:
    """把契约里的页型定义投影成提示词片段。"""
    if not schema:
        return ("## 页型（契约缺失，用兜底）\n- canon-generic：必填 `结论`；"
                "可选 `依据` / `操作` / `坑与边界`")
    pt = (schema.get("page_types") or {}).get(page_type) or {}
    req = pt.get("required_sections") or ["结论"]
    opt = pt.get("optional_sections") or []
    lines = [f"## 页型契约（来自 reference/kb-schema.json）",
             f"- 判定页型：`{page_type}`（{pt.get('when', '按内容最贴近的一类')}）",
             f"- 必填节：{' / '.join(req)}",
             f"- 可选节：{' / '.join(opt) if opt else '（无）'}",
             "- 只输出这些节名；没有内容的小节直接省略，不要写空节。"]
    return "\n".join(lines)


# ── L4 范文层：从已通过校验的正典里挑同类样例 ──────────────────
def _canon_rows(root: Path) -> List[Dict[str, Any]]:
    """扫描库里的正典，收集可作范文的（含质量分）。"""
    from kb_common import iter_notes, load_note_full as load
    rows = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if rel.startswith("raw/"):
            continue
        fm, _t, _b, body = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        sections = {}
        cur = None
        for line in body.splitlines():
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if m:
                cur = m.group(1).strip()
                sections[cur] = []
            elif cur and line.strip().startswith("-"):
                sections[cur].append(line.strip()[2:].strip())
        if len(sections) < 2:
            continue
        conf = fm.get("curate_confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.7                      # 早期正典无置信分，给中位数
        grounded = fm.get("grounded_facts") or []
        quality = conf + min(len(grounded), 10) * 0.01 + min(len(sections), 4) * 0.02
        rows.append({"rel": rel, "domain": str(fm.get("domain", "")), "title": str(fm.get("title") or p.stem),
                     "sections": sections, "quality": round(quality, 3)})
    rows.sort(key=lambda r: -r["quality"])
    return rows


def exemplars(root: Path, body: str, fm: Dict[str, Any], k: int = 2) -> List[Dict[str, Any]]:
    """挑 k 条同类范文：优先同域，其次全局高质量。"""
    rows = _canon_rows(root)
    if not rows:
        return []
    dom = str(fm.get("domain", "") or "")
    same = [r for r in rows if r["domain"] == dom]
    pool = same if len(same) >= k else rows
    # 再按与原文的用词重叠度挑最贴近的
    from kb_common import tokenize
    qt = set(tokenize(body[:2000]))
    def overlap(r):
        rt = set()
        for items in r["sections"].values():
            rt |= set(tokenize(" ".join(items)))
        return len(qt & rt) / max(len(qt | rt), 1)
    pool = sorted(pool, key=lambda r: (-round(overlap(r), 3), -r["quality"]))
    return pool[:k]


EXEMPLAR_BUDGET = 600      # 范文块字符预算（范文是"照结构"，不是"照长度"）


def exemplar_block(exs: List[Dict[str, Any]], budget: int = EXEMPLAR_BUDGET) -> str:
    """渲染范文块：2 条、每节 2 条要点、超预算即停。

    实测教训：范文给多给长会让模型"照抄长度"，把 JSON 顶到截断。
    范文只用来示范**结构与颗粒度**，所以硬性截断。
    """
    if not exs:
        return ""
    lines = ["## 范文（同类已通过校验的正典；**只照结构与颗粒度，不要照抄长度**）"]
    used = 0
    for i, e in enumerate(exs, 1):
        head = f"\n### 范文{i}：{e['title'][:36]}"
        lines.append(head); used += len(head)
        for sec, items in list(e["sections"].items())[:4]:
            lines.append(f"## {sec}"); used += len(sec) + 4
            for it in items[:2]:
                bullet = f"- {it[:44]}"
                lines.append(bullet); used += len(bullet) + 1
        if used > budget:
            break
    return "\n".join(lines)


# ── L3 任务层：JSON 契约 ─────────────────────────────────────
def json_contract(page_type: str, required: List[str], optional: List[str]) -> str:
    secs = ", ".join(f'"{x}": ["..."]' for x in (required + optional)[:5])
    return f"""## 输出契约（只输出一个 JSON 对象，不要任何解释文字）
{{
  "verdict": "canon | raw-only | noise | merge",
  "page_type": "{page_type}",
  "title": "一句话标题（≤30 字，具体，不要泛泛）",
  "summary": "一句话摘要（≤60 字）",
  "sections": {{ {secs} }},
  "facts": [{{"kind": "verbatim", "value": "原文中逐字出现的命令/路径/端口/版本/时长/报错串"}},
            {{"kind": "extracted", "value": "从原文归纳的短事实（须有原文依据）"}}],
  "sources": ["<原记忆 ID>"],
  "dropped": ["你删掉的是什么类型的内容（一句话）"],
  "confidence": 0.0,
  "merge_into": null
}}"""


def build_messages(root: Path, body: str, fm: Dict[str, Any], sid: str, rel: str,
                   page_type: Optional[str] = None, k_examples: int = 2,
                   max_body: int = 12000) -> List[Dict[str, str]]:
    """组装四层提示词，返回 OpenAI 兼容 messages。"""
    schema = load_schema(root)
    pt = page_type or guess_page_type(body, fm)
    pt_def = (schema or {}).get("page_types", {}).get(pt, {}) if schema else {}
    req = pt_def.get("required_sections") or ["结论"]
    opt = pt_def.get("optional_sections") or ["依据", "操作", "坑与边界"]

    system = "\n\n".join([SYSTEM_HEADER, RULES, LENGTH_RULES])
    exs = exemplars(root, body, fm, k=k_examples) if k_examples else []
    parts = [
        schema_block(schema, pt),
        f"## 任务\n把下面的原文结晶为一条正典（页型 `{pt}`）。判 verdict 后按输出契约作答。",
        ("### 好内容的判据（逐条自检）\n"
         "- 结论必须是**可行动 / 可判断**的陈述，不是「我们今天讨论了 X」。\n"
         "- 依据要能支撑结论，而不是流程叙述。\n"
         "- 操作要能照着复现（含命令 / 路径 / 参数）。\n"
         "- 坑与边界要写清触发条件与后果。\n"
         "- 凡「过程叙述 / 时间线 / 寒暄 / 重复表述」一律删掉。\n"),
        f"## 原文\nID: {sid}\n路径: {rel}\n<<<原文开始\n{body[:max_body]}\n原文结束>>>",
        f"## 已有正典候选（用于判断 merge；无则空）\n{_merge_candidates(root, body)}",
    ]
    if exs:
        parts.append(exemplar_block(exs))
    parts.append(json_contract(pt, req, opt))
    if len(body) > 5000:
        parts.append("【再次强调】原文很长，你的目标是**结晶不是转写**：只留可复用的结论/操作/坑，"
                     "宁少写也不要被截断。")
    return [{"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(parts)}]


def _merge_candidates(root: Path, body: str, topk: int = 3) -> str:
    """找可能同主题的已有正典（供 merge 判断）。

    与 curate._existing_canon 的差别：先按阈值筛；若一条都没有，就**兜底给质量最高的
    正典标题**——让模型至少知道库里已有什么，避免明明重复却新建。
    """
    try:
        import curate
        cands = curate._existing_canon(root, body, topk=topk)
        if cands:
            return "\n".join(f"- {c['rel']}（相似度 {c['sim']}）：{c['excerpt'][:160]}" for c in cands)
    except Exception:
        pass
    try:
        rows = _canon_rows(root)[:topk]
    except Exception:
        return "（无）"
    if not rows:
        return "（无）"
    return ("（无高相似候选；以下是库内已有正典示例，仅供判断是否重复）\n"
            + "\n".join(f"- {r['rel']}｜{r['title'][:40]}" for r in rows))


# ── 自检 CLI ────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="结晶提示词层自检")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("show"); s1.add_argument("--root", default=".")
    s1.add_argument("--text", default="决定：把检索方案定为本地 BM25 + 神经通道三选一。"
                                      "操作步骤：先跑基线，再开神经通道；坑：嵌入服务 :8081 挂了会静默降级。")
    s2 = sub.add_parser("bank"); s2.add_argument("--root", default=".")
    s2.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if args.cmd == "show":
        msgs = build_messages(root, args.text, {"domain": "开发"}, "demo/sid", "demo/x.md")
        for m in msgs:
            print(f"───── {m['role']} ─────")
            print(m["content"][:2600])
            print()
        return 0
    if args.cmd == "bank":
        rows = _canon_rows(root)
        print(f"范文库候选（已通过校验、≥2 节的 canon）：{len(rows)} 条")
        for r in rows[:args.limit]:
            print(f"  quality {r['quality']}  [{r['domain']}] {r['title'][:44]}  节={list(r['sections'])}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

# ── 默认任务层（baseline）：实测更可靠的紧凑提示词 ────────────────
# 背景：P1 引入四层结构后做过三轮同批 A/B（旧 vs 新）：新结构在 JSON 合法率 /
# 三关通过率 / 耗时 三项上都没有赢。因此**默认走 baseline**；四层结构保留为可选
# （kb curate run --prompt layered），待它在更大样本上被证明更好再切默认。
BASELINE_SYSTEM = """你是知识库的「结晶器」。你的唯一职责：把原始记录（会话日志/操作流水/碎片）
炼成**可被检索、可被直接调用**的干净知识。你不是摘要器，你是编辑。

铁律（违反即失败）：
1. 只保留有复用价值的内容：结论、依据、可操作步骤、坑与边界条件、决策与理由。
2. 删掉一切废话：寒暄、过程性冗余、重复表述、"我正在/接下来/好的"、无关的临时进程记录。
3. 逻辑必须清楚：把内容重排为 结论 → 依据 → 操作 → 坑与边界；同主题合并，不要流水账。
4. 绝不编造。原文没有的数字/命令/路径/版本，一个字都不许写。
5. 保留原文中所有关键标识符的**精确写法**（命令、路径、端口、版本号、时长、报错串），
   一个字都不要改写、不要翻译、不要四舍五入。
6. 如果原文确实没有可复用知识（只是过程记录），verdict 必须为 "raw-only"，不要硬凑。
7. 如果原文只是空壳/目录/纯指针（没有实质内容），verdict 为 "noise"。
8. 如果原文与提供的「已有正典」讲的是同一件事，verdict 为 "merge"，并给出 merge_into。

长度纪律（硬约束，输出一旦超长就会被截断而整篇作废）：
- sections 每节最多 3 条；每条 ≤ 40 字，只写要点，不复述原文、不写解释性从句。
- facts 最多 8 条，每条只填标识符本身（命令/路径/版本/端口/时长/报错串），不带句子。
- dropped 只写 1 条（一句话概括删了什么）。
- 全文 JSON 控制在 1200 字以内。

只输出一个 JSON 对象，不要任何解释文字。"""

BASELINE_USER = """【原文】ID: {sid}
路径: {rel}
正文：
<<<原文开始
{body}
原文结束>>>

【已有正典候选（可能同主题，用于判断 merge；没有则为空）】
{cands}

请按 schema 输出 JSON：
{{
  "verdict": "canon | raw-only | noise | merge",
  "title": "一句话标题（<=30 字，具体，不要泛泛）",
  "summary": "一句话摘要（<=60 字）",
  "sections": {{
    "结论": ["..."],
    "依据": ["..."],
    "操作": ["..."],
    "坑与边界": ["..."]
  }},
  "facts": [{{"kind": "cmd|path|version|duration|port|number|error", "value": "原文中出现过的精确串"}}],
  "sources": ["{sid}"],
  "dropped": ["你删掉的都是什么类型的内容（一句话）"],
  "confidence": 0.0,
  "merge_into": null
}}
sections 里没有内容的小节请省略。长度纪律：每节最多 3 条、每条 ≤40 字；facts ≤8 条
（只填标识符本身）；dropped 只 1 条；整个 JSON ≤1200 字 —— 超长会被截断而整篇作废。"""

def build_messages_baseline(body, fm=None, sid="", rel="", page_type=None,
                            k_examples=0, max_body=12000, merge_text="（无）"):
    """baseline 提示词（与 P0 之前生产环境一致；额外把页型说清楚）。"""
    fm = fm or {}
    pt = page_type or guess_page_type(body, fm)
    user = BASELINE_USER.format(sid=sid, rel=rel, body=body[:max_body], cands=merge_text)
    user = user + ("\n\n【页型】" + pt
                   + "（按 reference/kb-schema.json；没有内容的小节省略）")
    return [{"role": "system", "content": BASELINE_SYSTEM},
            {"role": "user", "content": user}]
