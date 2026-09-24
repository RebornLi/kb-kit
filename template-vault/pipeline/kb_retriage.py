#!/usr/bin/env python3
# ============================================================
# kb_retriage.py —— L0 检索表层修正（反馈阶梯 · 检索纠错）
# ------------------------------------------------------------
# 使命：让"检索漏检"反过来修补检索表层——
#   把真实查询信号（L1 漏检缺口 (query, note)）喂回 L0 检索表层，只产“只增型”
#   检索元数据修复提议（tags + kb_summary），绝不删改内容、绝不自动改库。
#
#   反馈阶梯 Rungs：
#     L0 检索表层修正（本模块 · 最底层·只增·有界） ← 本文件
#     L1 查询即反馈（kb_adaptretrieve，漏检信号来源）← M1 已完成
#     L2 自我加权（kb_selfweight，importance 只升） ← M2a 已完成
#     L3 元调参 / L4 宪法
#
#   为什么只修 tags + kb_summary（L0 表层，而非 body 内容）：
#     rag.py 检索打分**只用 token**（body+tags 的 TF-IDF 余弦 + 倒排 tag /
#     kb_summary_token 的 INVERTED_HIT_BOOST）—— wikilinks/入链不影响 rag.py 打分。
#     故“漏检”根因 = query 的 token 不在笔记检索表层（tags/summary/body），而非无入链。
#     body 属内容（归 L3/元数据质量），本 L0 层可修杠杆只剩“只增的 tags + kb_summary”，
#     二者都进 rag.py 索引（tags 同时进 TF-IDF 向量与倒排 tag 域；kb_summary 进倒排）。
#
#   铁律（与 kb_adaptretrieve / kb_selfweight / feedback_loop 完全一致）：
#     · 只升不降：tags 只加（去重·单篇封顶 L0_NOTE_MAX_TAGS）；kb_summary 只在缺失时
#         充实（绝不覆盖已有正文摘要）。绝不删/改既有 token。
#     · 外部锚最硬 + 接地阀（P0-1）：知识库疑似自闭（新鲜外部源<5% 且跨域<15%）时，
#         冻结“内部检索表层自改”（避免给回声室加词），只产解释性零提议。
#     · 有界：缺口 token idf 下限（L0_MIN_IDF，取自 rag 词表）+ 单篇 tag 上限 + 总数上限
#         （L0_TOTAL_PROPOSALS，接 L1 MAX_PROPOSALS）+ (query,note) 去重。
#     · 人工在环：默认只写 proposals 到 jsonl，绝不自动改库；apply（显式 --id/--all）
#         确认后只增写回 frontmatter，写前做一次（原子）git checkpoint，可回滚。
#     · raw/ 不改：不可变外部源硬隔离守卫（复用 kb_rsi.is_raw/is_archive/in_inbox）。
#     · best-effort：无 L1 信号/接地不足 → 静默降级（0 提议 + 解释），绝不锁死流水线。
#
#   用法:
#     python3 pipeline/kb_retriage.py report --root R        # 只读：接地阀 + L1 漏检信号汇总
#     python3 pipeline/kb_retriage.py propose --root R        # 只产提议（写 jsonl）
#     python3 pipeline/kb_retriage.py apply  --root R [--id N.. | --all]
#     python3 pipeline/kb_retriage.py status --root R [--json]
# ============================================================
import argparse, json, re, subprocess, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import kb_rsi            # 复用 collect/metrics/tokenize/is_raw/is_archive/in_inbox 守卫
from kb_common import tokenize
from kb_constants import (
    EXTERNAL_MIN_CROSS, EXTERNAL_FRESH_MIN_PCT,
    L0_GAP_TOKEN_MIN_LEN, L0_MIN_IDF, L0_NOTE_MAX_TAGS,
    L0_SUMMARY_TOKENS, L0_SUMMARY_MIN_LEN, L0_TOTAL_PROPOSALS,
)

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)  # template-vault 根

PROPOSAL_FILE = ".kb_retriage_proposals.jsonl"          # 瞬态提议（gitignore）
APPLIED_FILE = ".kb_retriage_applied.json"              # 累计写回账本（gitignore，写前 force-add 作回滚锚）
PROP_L1_FILE = ".kb_adaptretrieve_proposals.jsonl"      # L1 漏检缺口（kb_adaptretrieve 产出）

# 中英高频停词：缺口 token 过滤，避免给检索表层灌噪声（单字 CJK/短拉丁已由 len≥2 兜底）
_L0_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "is", "are", "was", "were",
    "be", "by", "with", "as", "at", "this", "that", "these", "those", "it", "its", "do", "does",
    "did", "will", "would", "can", "could", "has", "have", "had", "not", "no", "but", "if", "then",
    "than", "which", "while", "about", "into", "over", "under",
    "的", "了", "和", "与", "或", "及", "而", "其", "此", "该", "之", "用", "以", "被", "把", "从",
    "向", "于", "由", "为", "自", "至", "在", "也", "都", "有",
}


# ── 知识层守卫：仅 PARA 编号域笔记（剔除私人记忆 / 插件参考 / 代码 / 不可变源）────────
# 非知识层路径段：.agents/私人记忆 · reference/插件参考 · pipeline/代码 · memory/私人 ·
#   raw/不可变源 · backups/logs/向量索引 等。L0 只增强真实知识笔记的检索表层。
_NON_KB_SEGMENTS = (".agents", ".kb", ".obsidian", "reference", "pipeline", "memory",
                    "raw", "backups", "logs", "vector index", "_aggregated", ".github", "scripts")


def _is_knowledge_note(rel: str) -> bool:
    """仅 PARA 知识层笔记（编号域 00-90，剔除 raw/reference/pipeline/memory/.agents 等）。
    与 kb_adaptretrieve 的 raw/归档/收件箱 守卫互补——绝不碰私人记忆与插件参考文档。"""
    return not any(seg in _NON_KB_SEGMENTS for seg in Path(rel).parts)


# ── 状态 / 文件路径 ──────────────────────────────────────────
def _prop_path(root: Union[str, Path]) -> Path:
    return Path(root) / "pipeline" / PROPOSAL_FILE


def _applied_path(root: Union[str, Path]) -> Path:
    return Path(root) / "pipeline" / APPLIED_FILE


def _read_applied(root: Union[str, Path]) -> Dict[str, Any]:
    p = _applied_path(root)
    if not p.exists():
        return {"applied": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"applied": {}}


# ── 接地阀（P0-1，公式复用 kb_selfweight.grounding·源自 kb_rsi）──────────
def grounding(root: Union[str, Path]) -> Tuple[bool, str, Dict[str, Any]]:
    """返回 (grounded, note, metrics)。grounded = 新鲜外部源≥5% 且 跨域≥15%。
    与 kb_selfweight.grounding 同公式，直接源自 kb_rsi.metrics（L0 轻依赖，不引 L2）。"""
    m = kb_rsi.metrics(kb_rsi.collect(root), root=root)
    if not m:
        return False, "库为空或无编译笔记，无法评估接地", {}
    grounded = m["external_inflow"] >= EXTERNAL_FRESH_MIN_PCT and \
        m["cross_ratio"] >= EXTERNAL_MIN_CROSS
    if grounded:
        note = f"外部接地正常（新鲜外部源 {m['external_inflow']}%≥{EXTERNAL_FRESH_MIN_PCT}% · 跨域 {m['cross_ratio']}≥{EXTERNAL_MIN_CROSS}）"
    else:
        note = (f"⚠️ 外部接地不足（新鲜外部源 {m['external_inflow']}%<{EXTERNAL_FRESH_MIN_PCT}% "
                f"或 跨域 {m['cross_ratio']}<{EXTERNAL_MIN_CROSS}）→ 知识库疑似自闭，"
                f"冻结内部检索表层自改（避免给回声室加词）")
    return grounded, note, m


# ── rag 词表 idf（缺口 token 区分度滤波的权威来源，与 rag.py 同构）──────────
def _rag_idf(root: Union[str, Path]) -> Optional[Dict[str, float]]:
    p = Path(root) / "vector index" / "df_idf.json"
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload.get("idf") if isinstance(payload.get("idf"), dict) else None


# ── 缺口 token 计算：query 有 · 笔记检索表层(body+tags) 无 · 有区分度──────────
def _gap_tokens(query: str, surface_tokens: Set[str],
                idf: Optional[Dict[str, float]] = None) -> List[str]:
    """返回缺口 token 有序去重列表：len≥2、非停词、不在检索表层、且“有区分度”。
    有区分度 = token 不在语料词表（全新专属词，必留）或 idf≥L0_MIN_IDF（语料里够冷）。
    语料里过宽泛的高频词（低 idf）才drop——避免给检索表层灌噪声。
    idf 缺失（rag 索引未建）则仅按 len/停词/表层过滤（best-effort，质量降级但不错误）。"""
    q = set(tokenize(query))
    gaps = [t for t in q
            if len(t) >= L0_GAP_TOKEN_MIN_LEN and t not in _L0_STOP and t not in surface_tokens]
    if idf is not None:
        gaps = [t for t in gaps if t not in idf or idf[t] >= L0_MIN_IDF]
    return sorted(set(gaps))


# ── 消费 L1 漏检缺口 (query, note) —— 上一 rung 产出 = 下一 rung 反馈源──────────
def _l1_missed(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """读取 kb_adaptretrieve 漏检缺口文件 (query/note/sim/issue)；缺失则触发一次
    L1.propose 取最新（best-effort，绝报错）。"""
    p = Path(root) / "pipeline" / PROP_L1_FILE
    recs: List[Dict[str, Any]] = []
    if p.exists():
        try:
            recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            recs = []
    if not recs:
        try:
            import kb_adaptretrieve as a1
            recs = a1.propose(root).get("proposals", [])
        except (ImportError, OSError, AttributeError, TypeError, KeyError):
            recs = []
    # 仅保留带 note/query 的有效缺口
    return [r for r in recs if r.get("note") and r.get("query")]


# ── frontmatter 只增写回（tags 合并·仅充实 kb_summary，绝不覆盖已有）──────────
def _fmt_tag_item(t: str) -> str:
    """YAML flow 单项：含特殊字符则双引号包裹，否则裸写（兼容 Obsidian 标签）。"""
    if re.search(r"[\s,\"\[\]:#&*!|>'%@`]", t):
        return '"' + t.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return t


def _fmt_tags_line(tags: List[str]) -> str:
    return "tags: [" + ", ".join(_fmt_tag_item(t) for t in tags) + "]"


def _fmt_summary_value(v: str) -> str:
    """kb_summary 值：含 `:` 或 `"` 或首尾空白则双引号包裹，否则裸写。"""
    s = v.strip()
    if re.search(r"[\"']", s) or re.search(r":\s", s) or s != v.strip():
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def _rewrite_tags_block(block: str, new_tags: List[str]) -> Tuple[str, bool]:
    """在 frontmatter 块内，把 tags 合并为“既有 ∪ 新增（去重·单篇封顶）”的 flow-list。
    兼容 flow-list（tags: [a, b]）、引号/逗号、以及块列表（tags:\n  - a）。
    写回时把整段 tags（键 + 块续行）统一收敛为单行 flow-list，避免残留悬空 `- ` 项。
    返回 (新块, 是否变更)。"""
    lines = block.splitlines()
    tag_idx = next((i for i, ln in enumerate(lines)
                    if re.match(r"^\s*tags\s*:", ln, re.I)), None)
    if tag_idx is None:
        block = _fmt_tags_line(new_tags) + "\n" + block
        return block, True
    # 解析既有 tags：行内取值（flow/逗号）+ 块列表续行（^\s*-\s）
    existing = _parse_tags_value(lines[tag_idx])
    j = tag_idx + 1
    while j < len(lines) and re.match(r"^\s*-\s", lines[j]):
        existing.extend(_parse_block_item(lines[j]))
        j += 1
    merged = list(existing)
    for t in new_tags:                       # 只增·去重
        if t not in merged:
            merged.append(t)
    merged = merged[:L0_NOTE_MAX_TAGS]       # 单篇封顶
    new_lines = lines[:tag_idx] + [_fmt_tags_line(merged)] + lines[j:]
    return "\n".join(new_lines), (merged != existing)


def _parse_block_item(line: str) -> List[str]:
    """解析块列表项 `  - value`（去引号、去行尾注释）。"""
    m = re.match(r"^\s*-\s*(.*)$", line)
    if not m:
        return []
    raw = m.group(1).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return [raw[1:-1]]
    raw = raw.split("#", 1)[0].strip()
    return [raw] if raw else []


def _parse_tags_value(line: str) -> List[str]:
    """解析 tags 行的既有取值（flow-list / 引号 / 逗号分隔 均有容错）。"""
    m = re.match(r"^\s*tags\s*:\s*(.*)$", line, re.I)
    if not m:
        return []
    raw = m.group(1).strip()
    if raw.startswith("["):
        raw = raw[1:raw.rfind("]") + 1] if "]" in raw else raw[1:]
    items = re.findall(r'"([^"]*)"|\'([^\']*)\'|([^\s,\]]+)', raw)
    out = []
    for a, b, c in items:
        v = a or b or c
        if v:
            out.append(v)
    return out


def _rewrite_summary_block(block: str, summary: str) -> Tuple[str, bool]:
    """仅当 kb_summary 缺失/过短时充实（只升·不覆盖已有）。返回 (新块, 是否变更)。"""
    lines = block.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*kb_summary\s*:", ln, re.I):
            existing = re.match(r"^\s*kb_summary\s*:\s*(.*)$", ln, re.I).group(1).strip()
            if len(existing) >= L0_SUMMARY_MIN_LEN:
                return "\n".join(lines), False   # 已有摘要 → 不覆盖（只升）
            lines[i] = "kb_summary: " + _fmt_summary_value(summary)
            return "\n".join(lines), True
    # 无 kb_summary 键 → 追加（只增）
    block = block + "\nkb_summary: " + _fmt_summary_value(summary)
    return block, True


def _write_back(path: Path, new_tags: List[str], summary: Optional[str]) -> bool:
    """只增写回 frontmatter：合并 tags（封顶）+ 充实 kb_summary（缺失时才）。
    无任何可写字段 → 返回 False（不动文件）。写前由调用方负责 git checkpoint。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    fm_m = re.compile(r"^---\s*$", re.M)
    m = fm_m.search(text)
    if not m:
        return False
    rest = text[m.end():]
    e = fm_m.search(rest)
    block = rest[:e.start()] if e else rest
    body = (rest[e.end():] if e else "")
    block, c1 = _rewrite_tags_block(block, new_tags)
    block, c2 = _rewrite_summary_block(block, summary) if summary else (block, False)
    if not (c1 or c2):
        return False
    new_text = "---\n" + block + ("\n---\n" if e else "\n") + body
    path.write_text(new_text, encoding="utf-8")
    return True


# ── 提议生成（接 L1 漏检缺口 → 只增检索表层修复）─────────────────────
def propose(root: Union[str, Path]) -> Dict[str, Any]:
    """消费 L1 漏检缺口，只产“只增型”检索表层修复提议（写 jsonl，不碰笔记）。
    排序（确定性）：重问失败 > sim 降序 > note 路径；id 跨运行稳定。"""
    idf = _rag_idf(root)
    grounded, ground_note, metric = grounding(root)

    l1 = _l1_missed(root)
    out: Dict[str, Any] = {"grounded": grounded, "ground_note": ground_note,
                           "n_l1_gaps": len(l1), "n_proposals": 0, "proposals": []}
    # 接地阀：自闭库冻结内部检索表层自改（避免给回声室加词）
    if not grounded:
        out["note"] = f"外部接地不足 → 冻结 L0 检索表层自改：{ground_note}"
        _write_proposals(root, [])
        return out

    proposals: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, Set[str]]] = set()
    for r in l1:
        rel, query = r.get("note"), r.get("query", "")
        sim = r.get("sim", 0.0)
        is_requery = str(r.get("issue", "")).startswith("requery")
        if not _is_knowledge_note(rel) or kb_rsi.is_archive(rel) or kb_rsi.in_inbox(rel):
            continue
        try:
            fm, _, body = kb_rsi.load_note(Path(root) / rel)
        except (OSError, UnicodeDecodeError):
            continue
        if (fm.get("kb_action") or "") == "retire":
            continue
        current_tags = _parse_current_tags(fm)
        surface_tokens: Set[str] = set(tokenize(body + " " + " ".join(current_tags)))
        gaps = _gap_tokens(query, surface_tokens, idf)
        avail = L0_NOTE_MAX_TAGS - len(current_tags)
        new_tags = gaps[:max(avail, 0)]
        # 充实 kb_summary：仅在缺失时才（只升·不覆盖已有）
        summary_text = ""
        if gaps and (not str(fm.get("kb_summary", "")).strip()
                     or len(fm.get("kb_summary", "")) < L0_SUMMARY_MIN_LEN):
            summary_text = ", ".join(gaps[:L0_SUMMARY_TOKENS])
        if not new_tags and not summary_text:
            continue
        key = (rel, frozenset(new_tags), bool(summary_text))
        if key in seen:
            continue
        seen.add(key)
        proposals.append({
            "note": rel, "query": query, "sim": sim,
            "severity": "high" if is_requery else "medium",
            "new_tags": new_tags, "summary_enrich": bool(summary_text),
            "summary_text": summary_text, "gap_tokens": gaps,
            "reason": (f"笔记检索表层缺词（高相似{sim:.2f}却漏检）：缺口 {len(gaps)} 个 token，"
                       f"补 tag {len(new_tags)}·{'充实摘要' if summary_text else '无'}"),
        })
        if len(proposals) >= L0_TOTAL_PROPOSALS:
            break

    proposals.sort(key=lambda p: (0 if p["severity"] == "high" else 1, -p["sim"], p["note"]))
    for i, p in enumerate(proposals, 1):
        p["id"] = i
    _write_proposals(root, proposals)
    out["n_proposals"] = len(proposals)
    out["proposals"] = proposals
    out["note"] = (f"接地{'正常' if grounded else '不足'} · 缺词修复提议 {len(proposals)} 条"
                   "（只增·人工在环，未改库）")
    _set_evolution(root, len(proposals), grounded)
    return out


def _parse_current_tags(fm: Dict[str, Any]) -> List[str]:
    """从 fm.tags 提取既有 tag 列表（YAML-list / 逗号 / 空格 均有容错）。"""
    t = fm.get("tags")
    if isinstance(t, list):
        return [str(x).strip() for x in t if str(x).strip()]
    if isinstance(t, str):
        return _parse_tags_value("tags: " + t)
    return []


def _set_evolution(root: Union[str, Path], n: int, grounded: bool) -> None:
    try:
        import evolution_log
        # 复用 QUERY 事件前缀（与 L1 kb_adaptretrieve 同属检索回路；detail 区分 rung）
        evolution_log.append(root, "QUERY",
                             f"L0 检索表层修复提议 {n} 条 · 接地{'OK' if grounded else '不足'}",
                             detail="接地阀 P0-1 通过后才允许内部表层自改")
    except (ImportError, OSError, AttributeError, TypeError, ValueError, KeyError):
        pass


def _write_proposals(root: Union[str, Path], proposals: List[Dict[str, Any]]) -> None:
    p = _prop_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in proposals)
        + ("\n" if proposals else ""),
        encoding="utf-8")


# ── apply：人工确认后只增写回 frontmatter（单次原子 checkpoint，可回滚）──────────
def apply(root: Union[str, Path], ids: Optional[List[str]] = None,
          force: bool = False) -> Dict[str, Any]:
    """人工确认后，把选定提议的 tags/kb_summary 只增写回 frontmatter。
    只升不降、单篇 tags 封顶 L0_NOTE_MAX_TAGS、raw/ 不改、写前一次（原子）checkpoint。
    id 归一化：提案存 int、CLI 传串 → 统一转 int 比对。"""
    p = _prop_path(root)
    if not p.exists():
        return {"written": [], "skipped": [], "applied_ledger": 0,
                "note": "无待审提议；先 run propose"}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if ids is None and force:
        pick = recs
    else:
        want = set()
        for x in (ids or []):
            try:
                want.add(int(x))
            except (TypeError, ValueError):
                pass
        pick = [r for r in recs if r.get("id") in want]

    ledger = _read_applied(root)
    applied: Dict[str, Any] = ledger.get("applied", {})
    applied_ids: Set = set(ledger.get("ids", []))
    written, skipped = [], []
    changed_rel: List[str] = []
    for r in pick:
        rid = r.get("id")
        if rid in applied_ids:
            skipped.append({"id": rid, "note": r.get("note"), "reason": "已写回(幂等)"}); continue
        rel = r.get("note")
        if not rel or (not r.get("new_tags") and not r.get("summary_enrich")):
            skipped.append({"id": rid, "reason": "缺 rel/修复项"}); continue
        if not _is_knowledge_note(rel) or kb_rsi.is_archive(rel) or kb_rsi.in_inbox(rel):
            skipped.append({"id": rid, "note": rel, "reason": "非知识层/raw/归档/收件箱 不可变"}); continue
        p_path = Path(root) / rel
        if not p_path.is_file():
            skipped.append({"id": rid, "note": rel, "reason": "文件不存在"}); continue
        # 幂等：该 note 已在上次写回中被标记 → 跳过（避免重复叠加）
        if applied.get(rel):
            applied_ids.add(rid); skipped.append({"id": rid, "note": rel, "reason": "已写回(幂等)"}); continue
        fm, _, body = kb_rsi.load_note(p_path)
        current_tags = _parse_current_tags(fm)
        new_tags = [t for t in current_tags if t not in r.get("new_tags", [])]
        for t in r.get("new_tags", []):
            if t not in new_tags:
                new_tags.append(t)
        new_tags = new_tags[:L0_NOTE_MAX_TAGS]
        summary = r.get("summary_text") if r.get("summary_enrich") else None
        ok = _write_back(p_path, new_tags, summary)
        if not ok:
            skipped.append({"id": rid, "note": rel, "reason": "无可写变更"}); continue
        applied[rel] = {"new_tags": r.get("new_tags", []),
                        "summary_enrich": bool(summary), "updated": datetime.now().isoformat(timespec="seconds")}
        applied_ids.add(rid)
        changed_rel.append(rel)
        written.append({"id": rid, "rel": rel, "added_tags": r.get("new_tags", []),
                        "summary_enrich": bool(summary)})

    # 写累计账本（transient，force-add 作回滚锚点）+ 单次原子 checkpoint
    ap_path = _applied_path(root)
    ap_path.parent.mkdir(parents=True, exist_ok=True)
    ap_path.write_text(json.dumps({"applied": applied, "ids": sorted(applied_ids),
                                   "updated": datetime.now().isoformat(timespec="seconds")},
                                  ensure_ascii=False, indent=2), encoding="utf-8")

    if written:
        for rel in changed_rel:
            subprocess.run(["git", "-C", str(root), "add", "--", rel], capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "--force", "--", str(ap_path)], capture_output=True)
        if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "-C", str(root), "commit", "-q",
                            "-m", f"kb: L0 检索表层只增修复 {len(written)} 篇（tags/kb_summary·接地门·可回滚）"],
                           capture_output=True)
    try:
        import evolution_log
        if written:
            evolution_log.append(root, "QUERY",
                                 f"L0 检索表层写回 {len(written)} 篇",
                                 detail="、".join(f"{w['id']} {w['rel'].split('/')[-1][:20]}…" for w in written))
    except (ImportError, OSError, AttributeError, TypeError, ValueError, KeyError):
        pass
    return {"written": written, "skipped": skipped,
            "applied_ledger": len(applied),
            "note": f"写入 {len(written)} 条 · 跳过 {len(skipped)} 条"}


def status(root: Union[str, Path]) -> Dict[str, Any]:
    p = _prop_path(root)
    if not p.exists():
        return {"count": 0, "proposals": [], "grounded": None}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    try:
        g, _, _ = grounding(root)
    except Exception:
        g = None
    return {"count": len(recs), "proposals": recs, "grounded": g}


# ── 渲染 / report ────────────────────────────────────────────
def render(root: Union[str, Path], summary: Optional[Dict[str, Any]] = None) -> str:
    s = grounding(root)
    st = status(root)
    L = ["# 🔎 L0 检索表层修正（反馈阶梯 · 检索纠错）", "",
         f"> 接地阀: **{'✅ OK' if s[0] else '⚠️ 不足（冻结内部表层自改）'}** — {s[1]}",
         "> 缺词修复 = 只增 tags + 充实 kb_summary（body 内容归 L3，本层不碰）；人工在环，未改库"]
    if not st["proposals"]:
        L += ["", "✅ 无缺词修复提议（或暂无 L1 漏检信号：先 capture/跑 rag query；或接地不足已冻结）。"]
        return "\n".join(L) + "\n"
    L += ["", "## ⚠️ 待人工复核（按优先级：重问 > sim 降序）", ""]
    for p in st["proposals"]:
        tags = ",".join(p.get("new_tags", [])) or "—"
        sm = "充实摘要" if p.get("summary_enrich") else "—"
        L += [f"### {p['id']}. [{p['severity']}·重问{'是' if p['severity']=='high' else '否'}] sim={p['sim']} — `{p['note']}`",
              f"   - query: {p['query']}",
              f"   - 补 tag: {tags} · {sm}（缺口 token {len(p.get('gap_tokens', []))} 个）",
              f"   - 说明: {p['reason']}", ""]
    L += ["> 铁律：tags 只升不降、单篇封顶 L0_NOTE_MAX_TAGS、raw/ 不改、写前 checkpoint 可回滚。"]
    return "\n".join(L) + "\n"


# ── CLI──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="kb_retriage: L0 检索表层修正（只增·检索纠错）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("report", "propose", "apply", "status"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=ROOT_DEFAULT)
        if name in ("apply", "status"):
            p.add_argument("--json", action="store_true")
        if name == "apply":
            p.add_argument("--id", nargs="*", default=None, help="指定提议ID")
            p.add_argument("--all", action="store_true", help="写回全部待审提议")
    args = ap.parse_args()

    if args.cmd == "report":
        g, note, m = grounding(args.root)
        st = status(args.root)
        n_l1 = len(_l1_missed(args.root))
        print(f"🧭 接地阀: {'✅ OK' if g else '⚠️ 不足（冻结内部表层自改）'} — {note}")
        print(f"🔢 L1 漏检缺口 {n_l1} 条 · 待缺词修复提议 {st['count']} 条（含本层筛选后）")
        if not g:
            print("⏹️  L0 检索表层自改已冻结（接地不足）；仅当新鲜外部源≥5% 且跨域≥15% 才允许加词。")
        return 0
    if args.cmd == "propose":
        r = propose(args.root)
        print(f"🧭 接地{r['grounded']} · L1 漏检缺口 {r['n_l1_gaps']} 条 · 缺词修复提议 {r['n_proposals']} 条")
        print(f"   {r['note']}  → {_prop_path(args.root).name}")
        return 0
    if args.cmd == "apply":
        force = bool(args.all)
        res = apply(args.root, ids=None if force else args.id, force=force)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"✅ 已写回 {len(res['written'])} 篇 · 跳过 {len(res['skipped'])} 条")
        return 0
    if args.cmd == "status":
        st = status(args.root)
        print(json.dumps(st, ensure_ascii=False, indent=2) if args.json else render(args.root))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
