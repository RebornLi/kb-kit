#!/usr/bin/env python3
# ============================================================
# kb_contradiction.py —— 矛盾检测 lint（P1-C：知识质量信号补全）
# ------------------------------------------------------------
# 命题：今天 kb-kit 完全缺失"知识质量"信号——两篇笔记谈同一主题、
#   却给出相反的断言（一篇 heavily 否定、一篇肯定），长期并存会让库
#   自相矛盾而无人察觉。这是模型塌缩的温床（系统在自己跟自己打架）。
#
# 做法（启发式、纯本地、零依赖，与 kb_rsi/kb_health 同向量风格）：
#   1. 对编译笔记（排除 raw/ 不可变层、归档、收件箱、MOC）建 token 向量；
#   2. 高语义相似度（同一主题）的笔记对，若"否定密度"差异很大
#      （一篇大量否定/Contrast，一篇近乎肯定）→ 判为**陈述相反**的矛盾候选；
#   3. 只**产出清单交人工复核**，绝不自动改/删笔记（lint 定位，同 kb_rsi 只读）。
#
# 局限（诚实）：否定密度是否定词/转折词的代理，非真正的语义极性解析；
#   可能漏报（同主题双方都否定）或误报（主题相关但并非对立）。故只供人审，
#   且被人工采纳前不会进入 T1/T2 的自动流程（也就无法被"删一篇来藏矛盾"刷爆）。
#
# 用法:
#   python3 pipeline/kb_contradiction.py [--root R] [--json] [--verbose]
# ============================================================
import argparse, os, re, sys, json, math
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi  # 复用 collect/metrics/load_note（向量由本地 _vec/_cos，处理中文）
from kb_common import norm as _norm, cos as _cos, build_inverted_index, candidate_pairs
from kb_constants import CONTRA_SIM, MAX_NOTES_CONTRA as MAX_NOTES

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)

# 中文按字符单字建向量（kb_rsi.tokenize 把整段中文当单一 token，致中文笔记间余弦恒近 0），
# 英文按词。这样"系统已经拥有意识"与"系统没有意识"能通过共有字(系统意识...)产生余弦相似。
_CN_CHARS = re.compile(r"[\u4e00-\u9fff]")
_LAT = re.compile(r"[A-Za-z0-9]+")


def _vec(s):
    # 注意：此 _vec 与 kb_common.char_vec 不同。
    # 本版本：CJK 按单字 + 英文按词；kb_common.char_vec 用 tokenize（CJK unigram+bigram）。
    # 保留本地版本以维持矛盾检测原有相似度行为不变；归一化复用 kb_common.norm。
    c = Counter()
    for w in _LAT.findall(s or ""):
        c[w.lower()] += 1
    for ch in _CN_CHARS.findall(s or ""):
        c[ch] += 1
    return _norm(dict(c))

# 阈值（保守：宁可多报让人审，也不要漏报真矛盾）
MIN_BODY_CHARS = 40      # 正文过短不计极性
MIN_NEG_DENSE = 0.020    # "否定方"至少要有的否定密度
MAX_NEG_DENSE = 0.015    # "肯定方"不能超过的否定密度

# 否定/转折词库（中文单字也可匹配 + 英文单词）
# 含 没/没有（最常见的中文否定），避免真矛盾漏报
# FR-6 扩展：新增避免/切忌/慎用/不宜/不应/不可/无法/失败/崩溃/异常/错误/拒绝/否定/推翻/反驳/质疑
_NEG_CN = re.compile(
    r"不|没|没有|非|无|未|从不|从未|绝不|禁止|并非|未必|反而|相反|却|否则|绝非"
    r"|避免|切忌|慎用|不宜|不应|不可|无法|失败|崩溃|异常|错误|拒绝|否定|推翻|反驳|质疑"
)
# FR-6 扩展：新增 avoid/prevent/reject/refuse/unable/crash/wrong/incorrect/invalid/
#           disagree/dispute/however/but/yet/although/despite/unlike
_NEG_EN = re.compile(
    r"\b(not|never|cannot|can't|won't|doesn't|don't|isn't|aren't|no|none|null"
    r"|false|falsely|opposite|rarely|barely|hardly|neither|nor|fail|fails|failed"
    r"|avoid|prevent|reject|refuse|unable|crash|wrong|incorrect|invalid"
    r"|disagree|dispute|however|but|yet|although|despite|unlike)\b",
    re.I)
_CN_TOKENS = re.compile(r"[\u4e00-\u9fff]")
_ALL_TOKENS = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def neg_density(text: str) -> float:
    """正文否定密度 = 否定/转折词数 / 字符数。代理"陈述 polarity 的负面程度"。"""
    n = len(_NEG_CN.findall(text or "")) + len(_NEG_EN.findall(text or ""))
    return n / (len(text or "") or 1)


def _sample_with_neg(text):
    """取正文里含否定词的一句作为"该笔记立场"的样本。"""
    for ln in (text or "").splitlines():
        if _NEG_CN.search(ln) or _NEG_EN.search(ln):
            return ln.strip(" \t>*#")[:160]
    # 无否定句则回退首句
    for ln in (text or "").splitlines():
        ln = ln.strip(" \t>*#")
        if ln and not ln.startswith("#"):
            return ln[:160]
    return ""


def collect(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """编译笔记（可被自改进、排除 raw/ 不可变层）。"""
    out = []
    for p in Path(root).rglob("*.md"):
        if any(d in p.parts for d in kb_rsi.EXCLUDE_DIRS) or ".git" in p.parts:
            continue
        rel = str(p.relative_to(root))
        if kb_rsi.is_raw(rel) or kb_rsi.is_archive(rel) or kb_rsi.in_inbox(rel):
            continue
        stem = Path(rel).stem.lower()
        if any(k in stem for k in ("首页", "索引", "治理总方案")) or "toc" in rel.lower():
            continue
        try:
            fm, text, body = kb_rsi.load_note(p)
        except (OSError, UnicodeDecodeError):
            continue
        if not body.strip() or len(body) < MIN_BODY_CHARS:
            continue
        fm = kb_rsi.load_note(p)[0]
        out.append({"rel": rel, "text": text, "body": body,
                    "neg": neg_density(body),
                    "vec": _vec(body + " " + " ".join(fm.get("tags", [])))})
    return out


def detect(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """返回矛盾候选对列表，每项 {a, b, sim, neg_a, neg_b, sample_a, sample_b}。"""
    notes = collect(root)
    pairs = []
    n = len(notes)
    if n < 2:
        return pairs

    def cmp_key(it):
        # 默认按 importance 降序（重要笔记优先比对）
        try:
            return -kb_rsi.parse_f(kb_rsi.load_note(Path(root) / it["rel"])[0].get("importance"))
        except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, IndexError):
            return 0.0

    # 倒排索引预过滤：仅对共享至少 1 个 token 的笔记对计算余弦相似度。
    # 共享 token 为空 → 余弦必为 0 → 低于 CONTRA_SIM(0.40) 可安全跳过。
    vecs_for_index = {str(i): notes[i]["vec"] for i in range(n)}
    inv_index = build_inverted_index(vecs_for_index)

    idx = list(range(n))
    if n > MAX_NOTES:
        # 大范围：仅域内全比对 + 每域取前 40 篇做跨域受限比较
        import collections
        buckets = collections.defaultdict(list)
        dom_by = {}
        for i, it in enumerate(notes):
            _, text, _ = kb_rsi.load_note(Path(root) / it["rel"])
            dom = kb_rsi.load_note(Path(root) / it["rel"])[0].get("domain", "-")
            dom_by[i] = dom
            buckets[dom].append(i)
        doms = list(buckets.keys())
        # 域内全比对：使用 candidate_pairs 预过滤
        for di in doms:
            members = sorted(buckets[di], key=cmp_key)[:60]
            member_keys = [str(idx[m]) for m in members]
            for a_key, b_key in candidate_pairs(inv_index, member_keys):
                _maybe(notes, int(a_key), int(b_key), pairs, root=root)
        # 跨域受限比较：每域取前 40 篇，使用 candidate_pairs 预过滤
        for xi in range(len(doms)):
            for yi in range(xi + 1, len(doms)):
                da, db = doms[xi], doms[yi]
                a_list = sorted(buckets[da], key=cmp_key)[:40]
                b_list = sorted(buckets[db], key=cmp_key)[:40]
                combined_keys = [str(idx[ai]) for ai in a_list] + [str(idx[bi]) for bi in b_list]
                dom_of = {str(idx[ai]): da for ai in a_list}
                dom_of.update({str(idx[bi]): db for bi in b_list})
                for a_key, b_key in candidate_pairs(inv_index, combined_keys):
                    if dom_of[a_key] == dom_of[b_key]:
                        continue
                    _maybe(notes, int(a_key), int(b_key), pairs, root=root)
    else:
        # 小范围：全量比较改为 candidate_pairs 预过滤
        all_keys = [str(i) for i in range(n)]
        for a_key, b_key in candidate_pairs(inv_index, all_keys):
            _maybe(notes, int(a_key), int(b_key), pairs, root=root)

    pairs.sort(key=lambda p: p["sim"], reverse=True)
    return pairs


def _maybe(notes, i, j, pairs, root=None):
    a, b = notes[i], notes[j]
    s = _cos(a["vec"], b["vec"])
    if s < CONTRA_SIM:
        return
    na, nb = a["neg"], b["neg"]
    hi, lo = (na, nb) if na >= nb else (nb, na)
    method = "heuristic"
    is_contra = False
    # 一对一中：否定方足够否定、肯定方几乎肯定，且主题高度相似 → 陈述相反
    if hi >= MIN_NEG_DENSE and lo <= MAX_NEG_DENSE:
        is_contra = True
    # FR-6: LLM 极性解析（可选，当启发式未判定且 ORNITH_API_KEY 已设置时）
    if not is_contra and root is not None:
        llm_result = _llm_polarity(root, a["body"], b["body"])
        if llm_result is True:
            is_contra = True
            method = "llm"
    if is_contra:
        ia, ib = (i, j) if na >= nb else (j, i)
        pairs.append({
            "sim": round(s, 3),
            "a": notes[ia]["rel"], "b": notes[ib]["rel"],
            "neg_a": round(notes[ia]["neg"], 4),
            "neg_b": round(notes[ib]["neg"], 4),
            "sample_a": _sample_with_neg(notes[ia]["body"]),
            "sample_b": _sample_with_neg(notes[ib]["body"]),
            "method": method,
        })


def _llm_polarity(root: Union[str, Path], text_a: str, text_b: str) -> Optional[bool]:
    """LLM 极性解析：判断两段文本是否陈述相反结论。

    Args:
        root: vault 根路径
        text_a: 文本 A
        text_b: 文本 B

    Returns:
        True（矛盾）/ False（不矛盾）/ None（无法判断或 LLM 不可用）
    """
    if not os.environ.get("ORNITH_API_KEY"):
        return None
    try:
        import rag
        prompt = (
            "判断以下两段文本是否陈述相反的结论。只回答'是'或'否'。\n"
            f"文本A: {text_a[:500]}\n\n"
            f"文本B: {text_b[:500]}\n\n"
            "回答:"
        )
        ans = rag.llm_answer(root, prompt, [])
        if ans is None:
            return None
        ans = ans.strip().lower()
        if ans.startswith("是") or ans.startswith("yes"):
            return True
        if ans.startswith("否") or ans.startswith("no"):
            return False
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def contradiction_zero_score(n_pairs: int) -> float:
    """矛盾零分：无矛盾=1.0，矛盾越多越趋 0。P1-A 的 fitness 分量之一。"""
    return round(1.0 / (1.0 + n_pairs), 4)


def render(pairs: List[Dict[str, Any]]) -> str:
    L = ["# 🚫 矛盾检测 lint（只读 · 交人工复核）", "",
         f"> 扫描编译笔记，语义相似但陈述相反的候选对：**{len(pairs)}** 对。"
         "每对需人工确认是否真矛盾，再决定如何调和/ retire。"]
    if not pairs:
        L += ["\n✅ 未发现自相矛盾陈述（在当前阈值下）。"]
        return "\n".join(L) + "\n"
    L += ["", "## ⚠️ 待复核矛盾对（按相似度排序）", ""]
    for i, p in enumerate(pairs, 1):
        method_label = p.get("method", "heuristic")
        method_tag = f" [LLM]" if method_label == "llm" else ""
        L += [f"### {i}. `{p['a']}` ↔ `{p['b']}`  (相似度 {p['sim']}){method_tag}",
              f"   - 否定密度: {p['a']}={p['neg_a']}  {p['b']}={p['neg_b']}",
              f"   - {p['a']}: {p['sample_a']}",
              f"   - {p['b']}: {p['sample_b']}",
              "   - 动作: 人工确认是否真矛盾 → 调和/退役其一（**勿自动删**）", ""]
    L += ["> 注：否定密度是否定/转折词代理，非严格极性解析；供人审，非自动决断。"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)

    pairs = detect(root)
    score = contradiction_zero_score(len(pairs))
    snap = {"n_contradictions": len(pairs), "zero_score": score, "pairs": pairs}
    # P2-2: lint 事件追加到统一演进日志
    try:
        import evolution_log
        evolution_log.append(root, "LINT",
                             f"矛盾检测 {len(pairs)} 对（只读交人工）",
                             detail=f"零分 {score} · 余弦≥0.40 且否定密度差双闸")
    except (ImportError, OSError, AttributeError, TypeError):
        pass

    logdir = Path(root) / "pipeline"
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / ".kb_contradictions.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")  # 瞬态快照（gitignore）
    (logdir / "kb_contradictions_report.md").write_text(render(pairs), encoding="utf-8")

    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(render(pairs))
        print(f"📝 矛盾快照 → {logdir / '.kb_contradictions.json'}  报告 → {logdir / 'kb_contradictions_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
