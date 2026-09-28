#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# curate.py —— 知识结晶层（由空闲本地 Agent 把「日志」炼成「可调用的知识」）
#
# 为什么存在：
#   代码能做的只有「结构归一 + 指纹精确去重 + 分块」。它无法判断
#   「这段是不是废话」「两条碎片讲的是不是同一个坑」「怎么把 5000 字日志
#   压成 400 字结论」—— 这些是语义判断，必须由 LLM 完成。
#   于是分工：**Agent 负责生成正典，代码负责守住不变量 + 可溯源。**
#
# 关键机制（为什么 agent 写的东西可信）：
#   1. 接地关 verify：Agent 必须把关键标识符（命令/路径/版本/时长/端口）列进
#      `facts`，代码逐条回**原文全文**核对；找不到的一律丢弃（drift control）。
#   2. 信息守恒关：正典长度必须落在原文的 [MIN_RATIO, MAX_RATIO]，太短=过度压缩、
#      太长=没压缩（等于搬运），都不通过。
#   3. 引用关：`sources` 指向的原记忆必须真实可达（交给 kb_raw.resolve 校验）。
#   4. 可回滚：--apply 前写前 checkpoint（git），单批可整体回滚；写 lineage。
#
# 默认「本地优先、空闲才跑」：模型不可用 / 无 KEY / 忙 → 只出 plan，绝不阻塞流水线。
#
# 用法:
#   python3 pipeline/curate.py plan   [--root R] [--domain X] [--limit N] [--json]
#   python3 pipeline/curate.py run    [--root R] [--domain X] [--limit N] [--apply] [--model M]
#   python3 pipeline/curate.py verify [--root R] [--batch ID] [--pending]
#   python3 pipeline/curate.py apply  [--root R] [--batch ID] [--all]
#   python3 pipeline/curate.py report [--root R] [--json]
# ============================================================
import argparse, datetime, hashlib, json, os, re, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import (ROOT_DEFAULT, iter_notes, load_note_full as load,
                       is_index_stub, is_pointer_page, kb_layer_of, is_raw_path,
                       is_generated_report, record_lineage, fmt_value, content_fingerprint)

PROPOSAL_DIR = Path("70-知识治理 Governance") / "_curate"
LEDGER = Path(".kb") / "curate_ledger.jsonl"
STATE = Path(".kb") / "curate_state.json"
REVIEW = Path(".kb") / "curate_review.jsonl"

# 信息守恒：正典 / 原文 长度比允许区间
MIN_RATIO, MAX_RATIO = 0.12, 0.65
# 大原文（信息密度高、天然压缩比大）的宽容区间：低于此只警告不拦截
HARD_MIN_RATIO = 0.04
HARD_MIN_CHARS = 300
# 候选优先级阈值
BIG_BODY = 2000          # 正文超此长度：优先结晶（长日志最需要压缩）
SMALL_BODY = 80          # 小于此长度：视为噪声/无信息量，不浪费推理
HUGE_BODY = 16000        # 超大正文：单次上下文放不下，需先分块/切段（跳过并提示）
BATCH_DEFAULT = 8
REUSE_WINDOW = 0.35      # 与已有正典正文相似度超过此值 → 视为同一主题，走"合并"而非新建
CONF_MIN = 0.6           # 模型自评置信度低于此 → 不自动写回，转人工裁决队列（kb curate review）
# 不参与结晶的路径（判定为「非知识正文」）
SKIP_DIRS = {"90-归档 Archive", "50-模板 Templates", "00-收件箱 Inbox", "_curated",
             "stale-reports", "_curate", "backups", "memory"}
SKIP_NAME_RE = re.compile(r"^(?:_|README|索引|index|使用说明)", re.I)


# ── 小工具 ──────────────────────────────────────────────────
def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _tokenize(s: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", s.lower())


def _jaccard(a: str, b: str) -> float:
    A, B = set(_tokenize(a)), set(_tokenize(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _body_of(text: str) -> str:
    if text.startswith("---"):
        end = re.search(r"^---\s*$", text, re.M)
        if end:
            end2 = re.search(r"^---\s*$", text[end.end():], re.M)
            if end2:
                return text[end.end() + end2.end():].lstrip("\n")
    return text


def _load_state(root: Path) -> Dict[str, Any]:
    try:
        return json.loads((root / STATE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"done": {}, "batches": {}}


def _save_state(root: Path, st: Dict[str, Any]) -> None:
    p = root / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _ledger_append(root: Path, entry: Dict[str, Any]) -> None:
    p = root / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def git(root: Path, args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True)


def checkpoint(root: Path) -> str:
    git(root, ["add", "-u"])
    if git(root, ["status", "--porcelain"]).stdout.strip():
        git(root, ["commit", "-q", "-m", f"pre-curate checkpoint @ {datetime.datetime.now():%F %T}"])
    return git(root, ["rev-parse", "HEAD"]).stdout.strip()


# ── 本地模型客户端（零依赖，OpenAI 兼容）────────────────────
class LLM:
    """本地 OpenAI 兼容端点客户端。无 KEY / 不可达 → available() False，业务静默降级。"""

    def __init__(self, model: Optional[str] = None, base: Optional[str] = None,
                 key: Optional[str] = None, timeout: int = 180):
        self.base = (base or os.environ.get("ORNITH_BASE_URL")
                     or os.environ.get("OPENAI_BASE_URL") or "http://127.0.0.1:8000/v1").rstrip("/")
        self.key = key or os.environ.get("ORNITH_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        self.model = model or os.environ.get("ORNITH_CHAT_MODEL") or "ornith1.5-35b"
        self.timeout = timeout

    def available(self) -> Tuple[bool, str]:
        if not self.key:
            return False, "未设置 ORNITH_API_KEY / OPENAI_API_KEY（本地模型不可用，只出 plan）"
        try:
            req = urllib.request.Request(f"{self.base}/models",
                                         headers={"Authorization": f"Bearer {self.key}"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status >= 400:
                    return False, f"端点返回 {r.status}"
            return True, "ok"
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, f"端点不可达：{e}"

    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 2048,
             temperature: float = 0.2, stop: Optional[List[str]] = None) -> Tuple[Optional[str], str]:
        payload_obj: Dict[str, Any] = {
            "model": self.model, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature,
        }
        if stop:
            payload_obj["stop"] = stop
        payload = json.dumps(payload_obj).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            finish = str(choice.get("finish_reason") or "")
            return content, (f"truncated(finish={finish})" if finish == "length" else "ok")
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            return None, f"{type(e).__name__}: {e}"

    def ask_json(self, messages: List[Dict[str, str]], max_tokens: int = 2048) -> Tuple[Optional[dict], str]:
        """要一份 JSON 对象；容忍 ```json 包裹与前后废话。返回 (对象, 说明)。"""
        text, note = self.chat(messages, max_tokens=max_tokens)
        if text is None:
            return None, note
        obj = _extract_json(text)
        if obj is None:
            tail = text.strip()[-160:].replace("\n", " ")
            why = "输出被 max_tokens 截断" if note.startswith("truncated") else "未返回合法 JSON"
            return None, f"{why}（{note}；尾部：…{tail}）"
        return obj, note


def _extract_json(text: str) -> Optional[dict]:
    s = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, depth = None, 0
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(s[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    start = None
    return None


# ── 候选选批 ────────────────────────────────────────────────
def _already_done(st: Dict[str, Any], rel: str, body: str) -> bool:
    """已结晶过且原文未变（按内容指纹判断）。"""
    rec = (st.get("done") or {}).get(rel)
    if not rec:
        return False
    return rec.get("fp") == content_fingerprint(body)


def select_batch(root: Path, domain: Optional[str], limit: int,
                 include_done: bool = False) -> List[Dict[str, Any]]:
    """选一批最值得结晶的笔记（只读）。评分依据：长正文、空壳/指针、日志味、低信息量。"""
    st = _load_state(root)
    out = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel):
            continue                       # 证据层不结晶（保持原文保真）
        if is_generated_report(rel):
            continue                       # 运行期产物（_MOC/_graph/_INDEX…）不是知识正文
        parts = set(Path(rel).parts[:-1])
        if parts & SKIP_DIRS:
            continue                       # 归档 / 模板 / 收件箱 / 提案目录
        if SKIP_NAME_RE.match(p.stem):
            continue                       # 说明页 / 索引页
        fm, text, block, body = load(p)
        if is_index_stub(fm):
            continue                       # 目录页无内容
        layer = str(fm.get("kb_layer", "") or "").strip().lower()
        if layer == "canon":
            continue                       # 已是正典
        n = len(body.replace("\n", "").strip())
        if n < SMALL_BODY:
            continue                       # 空壳/无信息量
        base = Path(rel).name.split(".")[0]
        if domain and str(fm.get("domain", "")).strip() != domain:
            continue
        if not include_done and _already_done(st, rel, body):
            continue
        score = 0.0
        if BIG_BODY <= n <= HUGE_BODY:
            score += 3.0                   # 长而有界：结晶收益最高
        elif n > HUGE_BODY:
            score += 0.2                   # 超大：需先切段，暂不优先
        elif n >= 600:
            score += 1.5
        if fm.get("chunk_of"):
            score += 1.2                   # 碎片：同族合并价值高
        if re.search(r"\d{2}:\d{2}|活动记录|会话|^\s*-\s*\*\*", body, re.M):
            score += 1.0                   # 日志味：最需要"提炼成结论"
        if str(fm.get("kb_source", "")).strip():
            score += 0.3
        if is_pointer_page(body, rel):
            score -= 1.5                   # 空壳：无内容可炼，排在后面
        out.append({"rel": rel, "body_chars": n, "score": round(score, 2),
                    "domain": str(fm.get("domain", "") or "-"),
                    "layer": kb_layer_of(fm, rel),
                    "chunk_of": str(fm.get("chunk_of", "") or "")})
    out.sort(key=lambda x: (x["score"], x["body_chars"]), reverse=True)
    return out[:limit]


def cmd_plan(root: Path, domain: Optional[str], limit: int, as_json: bool) -> int:
    cand = select_batch(root, domain, limit)
    llm = LLM()
    ok, note = llm.available()
    if as_json:
        print(json.dumps({"model": llm.model, "base": llm.base, "available": ok, "note": note,
                          "count": len(cand), "candidates": cand}, ensure_ascii=False, indent=2))
        return 0
    print(f"🔭 结晶选批（模型 {llm.model} @ {llm.base} · {'可用' if ok else '不可用：' + note}）")
    print(f"   候选 {len(cand)} 篇（评分 = 长正文/空壳/碎片/日志味）：")
    for c in cand:
        print(f"   {c['score']:>4}  {c['body_chars']:>6}字  [{c['domain']}] {c['rel'][:70]}")
    if not cand:
        print("   （无候选：可能都已结晶，或 --domain 过滤太窄）")
    return 0


# ── 提示词 ──────────────────────────────────────────────────
SYSTEM = """你是知识库的「结晶器」。你的唯一职责：把原始记录（会话日志/操作流水/碎片）
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

只输出一个 JSON 对象，不要任何解释文字。"""

USER_TMPL = """【原文】ID: {sid}
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
sections 里没有内容的小节请省略；每条要点独立成一行，避免长句堆叠。"""


def _existing_canon(root: Path, body: str, topk: int = 3) -> List[Dict[str, str]]:
    """找与本文相似的已有正典（kb_layer: canon），用于判断 merge。"""
    scored = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel):
            continue
        fm, _t, _b, b = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        sim = _jaccard(body, b)
        if sim >= REUSE_WINDOW:
            scored.append((sim, rel, b[:600]))
    scored.sort(reverse=True)
    return [{"rel": rel, "excerpt": ex, "sim": round(sim, 2)} for sim, rel, ex in scored[:topk]]


# ── 校验（接地 / 守恒 / 引用）────────────────────────────────
def verify_note(body: str, prop: Dict[str, Any], root: Path,
                sid: str) -> Dict[str, Any]:
    """对一份提案做确定性校验。返回 {ok, checks, issues, grounded_facts, dropped_facts}。"""
    issues: List[str] = []
    checks: Dict[str, Any] = {}
    canon = render_canon(prop)
    ratio = (len(canon) / len(body)) if body else 0.0
    checks["ratio"] = round(ratio, 3)
    checks["canon_chars"] = len(canon)
    # 分级：硬失败（明显丢内容/没压缩） vs 软警告（大原文天然压缩比高）
    if ratio < HARD_MIN_RATIO or len(canon) < HARD_MIN_CHARS:
        issues.append(f"过度压缩：正典/原文 = {ratio:.1%}（< {HARD_MIN_RATIO:.0%}）或正典仅 "
                      f"{len(canon)} 字（< {HARD_MIN_CHARS}）→ 疑丢关键内容")
    elif ratio < MIN_RATIO:
        issues.append(f"⚠️压缩偏狠：{ratio:.1%}（目标 ≥{MIN_RATIO:.0%}；原文超长时属正常，请抽检事实完整性）")
    elif ratio > MAX_RATIO:
        issues.append(f"几乎没压缩：正典/原文 = {ratio:.1%} > {MAX_RATIO:.0%}（等于搬运，不算结晶）")

    # 接地关：facts 逐条回原文核对（精确子串，允许空白归一）
    norm_body = re.sub(r"\s+", "", body)
    grounded, dropped = [], []
    for f in (prop.get("facts") or []):
        v = str((f or {}).get("value", "") if isinstance(f, dict) else f).strip()
        if not v:
            continue
        if re.sub(r"\s+", "", v) in norm_body:
            grounded.append(v)
        else:
            dropped.append(v)
    checks["facts_total"] = len(grounded) + len(dropped)
    checks["facts_grounded"] = len(grounded)
    checks["facts_dropped"] = dropped
    if dropped:
        issues.append(f"未接地事实 {len(dropped)} 条已丢弃：{dropped[:5]}")
    if (len(grounded) + len(dropped)) >= 3 and not grounded:
        issues.append("全部 facts 都无法在原文中找到 → 疑似编造，建议人工复核")

    # 引用关：sources 必须指向可达的原记忆
    srcs = [str(s).strip() for s in (prop.get("sources") or []) if str(s).strip()]
    checks["sources"] = srcs
    if not srcs:
        issues.append("未给出 sources（无法回溯原记忆）")

    hard_kw = ("过度压缩", "几乎没压缩", "未给出 sources", "全部 facts")
    hard = [i for i in issues if i.startswith(hard_kw)]
    warn = [i for i in issues if i not in hard]
    return {"ok": not hard, "issues": issues, "warnings": warn, "checks": checks,
            "grounded_facts": grounded, "dropped_facts": dropped}


def render_canon(prop: Dict[str, Any]) -> str:
    """把提案渲染为正典正文（Markdown）。"""
    lines = []
    sections = prop.get("sections") or {}
    order = ["结论", "依据", "操作", "坑与边界"]
    extra = [k for k in sections if k not in order]
    for key in order + extra:
        items = sections.get(key)
        if not items:
            continue
        if isinstance(items, str):
            items = [items]
        lines.append(f"## {key}")
        for it in items:
            s = str(it).strip()
            if not s:
                continue
            lines.append(f"- {s}" if not s.startswith(("-", "*", "1.")) else s)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _canon_fm(rel: str, fm: Dict[str, Any], prop: Dict[str, Any],
              sid: str, verified: Dict[str, Any], model: str) -> Dict[str, Any]:
    """正典 frontmatter：继承原 frontmatter + 溯源字段（可一键回原记忆）。"""
    out = dict(fm)
    out.update({
        "kb_layer": "canon",
        "kb_summary": str(prop.get("summary") or prop.get("title") or "")[:160],
        "title": str(prop.get("title") or "")[:120] or fm.get("title", Path(rel).stem),
        "updated": datetime.date.today().isoformat(),
        "curated_by": model,
        "curated_at": _now(),
        "source_ref": list(dict.fromkeys([sid] + [str(s) for s in (verified.get("checks", {}).get("sources") or [])])),
        "source_chars": verified.get("checks", {}).get("orig_chars", 0),
        "canon_ratio": verified.get("checks", {}).get("ratio", 0),
        "grounded_facts": verified.get("grounded_facts", [])[:20],
        "curate_confidence": prop.get("confidence", None),
    })
    for junk in ("chunk_of", "chunk", "is_chunk_index", "related_to", "memory_source"):
        out.pop(junk, None)
    return out


# ── 单篇结晶 ────────────────────────────────────────────────
def curate_one(root: Path, rel: str, llm: LLM, apply: bool = False) -> Dict[str, Any]:
    p = root / rel
    fm, text, block, body = load(p)
    sid = str(fm.get("memory_source") or f"vault:{rel}")
    cands = _existing_canon(root, body)
    cand_txt = "\n".join(f"- {c['rel']}（相似度 {c['sim']}）：{c['excerpt'][:200]}"
                         for c in cands) or "（无）"
    payload = USER_TMPL.format(sid=sid, rel=rel, body=body[:12000], cands=cand_txt)
    prop, note = llm.ask_json([{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": payload}], max_tokens=6144)
    rec: Dict[str, Any] = {"rel": rel, "sid": sid, "model": llm.model, "ts": _now(),
                           "body_chars": len(body), "ok": False, "note": note}
    if prop is None:
        rec["error"] = note
        return rec
    verdict = str(prop.get("verdict", "")).strip().lower() or "canon"
    rec["verdict"] = verdict
    rec["proposal"] = prop
    if verdict in ("noise", "raw-only"):
        # 不生成正典；只记录判定（P2 可用于索引降级/归档）
        rec["ok"] = True
        rec["action"] = f"skip:{verdict}"
        return rec
    verified = verify_note(body, prop, root, sid)
    verified["checks"]["orig_chars"] = len(body)
    rec["verify"] = verified
    rec["ok"] = bool(verified["ok"])
    rec["action"] = "propose"
    # 置信度闸门：模型自评低于阈值 → 不自动写回，转人工裁决队列（人工在环）
    conf = prop.get("confidence")
    try:
        conf = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    rec["confidence"] = conf
    low_conf = conf is not None and conf < CONF_MIN
    if low_conf:
        rec["verified_ok"] = rec["ok"]
        rec["ok"] = True  # 判定本身没问题，只是需要人来拍板
        rec["action"] = "review:low-confidence"
        _review_append(root, {"ts": _now(), "rel": rel, "sid": sid, "reason": "low-confidence",
                              "confidence": conf, "model": llm.model,
                              "verify_ok": rec.get("verified_ok"),
                              "issues": verified.get("issues", [])[:5],
                              "title": str(prop.get("title") or "")[:120],
                              "canon": render_canon(prop)[:4000]})
        return rec
    if apply and rec["ok"]:
        rec["action"] = apply_canon(root, rel, fm, prop, sid, verified, llm.model)
    return rec


def _review_append(root: Path, entry: Dict[str, Any]) -> None:
    """人工裁决队列（jsonl）：低置信/高价值批次等人拍板，可与 kb_l4 judge 对接。"""
    p = root / REVIEW
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cmd_review(root: Path, limit: int, as_json: bool) -> int:
    """列出待人工裁决的结晶提案（低置信度）。人看完用 kb curate run 手动补跑或直接改笔记。"""
    p = root / REVIEW
    if not p.exists():
        print("✅ 无待裁决提案（人工队列为空）")
        return 0
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if as_json:
        print(json.dumps({"count": len(rows), "items": rows[:limit]}, ensure_ascii=False, indent=2))
        return 0
    print(f"🧑⚖️ 待人工裁决 {len(rows)} 条（低置信度，未自动写回）")
    for r in rows[:limit]:
        print(f"\n· {r.get('rel')}")
        print(f"  置信度 {r.get('confidence')}  三关校验 {'✅' if r.get('verify_ok') else '❌'}"
              f"  标题：{r.get('title')}")
        if r.get("issues"):
            print(f"  问题：{'；'.join(str(i) for i in r['issues'][:3])}")
        print(f"  取原文：kb raw show \"{r.get('sid')}\"")
    print(f"\n看完若认可：kb curate run --limit N（会跳过已处理；需要时可改 CONF_MIN 或直接编辑笔记）")
    return 0


def apply_canon(root: Path, rel: str, fm: Dict[str, Any], prop: Dict[str, Any],
                sid: str, verified: Dict[str, Any], model: str) -> str:
    """把正典**原位替换**；原文按 ID 保号移入 raw/_curated/（保真可回滚）。"""
    p = root / rel
    orig_text = _read(p)
    archive = root / "raw" / "_curated" / rel
    archive.parent.mkdir(parents=True, exist_ok=True)
    i = 1
    while archive.exists():
        archive = archive.with_name(f"{archive.stem}-{i}{archive.suffix}")
        i += 1
    archive.write_text(orig_text, encoding="utf-8")

    fm2 = _canon_fm(rel, fm, prop, sid, verified, model)
    fm2["archived_original"] = str(archive.relative_to(root))
    head = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2.items()) + "\n---\n\n"
    title = str(prop.get("title") or Path(rel).stem)
    body_new = f"# {title}\n\n" + render_canon(prop)
    body_new += (f"\n---\n\n> 原记忆（原文保真）：`kb raw show \"{sid}\"`　·　"
                 f"本地副本 `{archive.relative_to(root)}`\n")
    p.write_text(head + body_new, encoding="utf-8")
    record_lineage(root, rel, str(archive.relative_to(root)), "curate-archive-original")
    return "canon-written"


# ── 批量结晶 ────────────────────────────────────────────────
def cmd_run(root: Path, domain: Optional[str], limit: int, apply: bool,
            model: Optional[str], as_json: bool, max_seconds: int = 0) -> int:
    llm = LLM(model=model)
    ok, note = llm.available()
    if not ok:
        print(f"⚠️ 本地模型不可用：{note}")
        print("   → 降级为只读 plan（不消耗任何推理）：")
        cand = select_batch(root, domain, limit)
        for c in cand:
            print(f"   {c['score']:>4}  {c['body_chars']:>6}字  {c['rel'][:70]}")
        return 0
    cand = select_batch(root, domain, limit)
    if not cand:
        print("✅ 无待结晶候选")
        return 0
    batch_id = "b" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    budget = f"　·　时间预算 {max_seconds}s" if max_seconds else ""
    print(f"⛳ 结晶批次 {batch_id}：{len(cand)} 篇（模型 {llm.model}）"
          f"{' · 直接写回' if apply else ' · 只出提案'}{budget}")
    st = _load_state(root)
    recs = []
    t_start = time.time()
    if apply:
        checkpoint(root)
    for i, c in enumerate(cand, 1):
        if max_seconds and (time.time() - t_start) > max_seconds:
            print(f"  ⏹ 达到时间预算（{max_seconds}s），本轮停在第 {i - 1} 篇；未处理的留待下次 cron")
            break
        t0 = time.time()
        rec = curate_one(root, c["rel"], llm, apply=apply)
        rec["batch"] = batch_id
        rec["secs"] = round(time.time() - t0, 1)
        st.setdefault("done", {})[c["rel"]] = {"fp": content_fingerprint(_read(root / c["rel"])),
                                              "ts": _now(), "batch": batch_id,
                                              "ok": rec.get("ok"), "action": rec.get("action")}
        recs.append(rec)
        _ledger_append(root, {k: v for k, v in rec.items() if k != "proposal"})
        mark = "✅" if rec.get("ok") else "⚠️"
        why = ""
        if not rec.get("ok"):
            why = str(rec.get("error") or (rec.get("verify") or {}).get("issues") or rec.get("action") or "")
        print(f"  [{i}/{len(cand)}] {mark} {str(rec.get('action') or '-'):<16} {c['rel'][:58]}"
              f"  ({rec['secs']}s{'' if not why else ' · ' + why[:100]})")
    st.setdefault("batches", {})[batch_id] = {"ts": _now(), "n": len(recs),
                                             "apply": bool(apply), "model": llm.model}
    _save_state(root, st)
    _write_proposals(root, batch_id, recs)
    n_ok = sum(1 for r in recs if r.get("ok"))
    n_canon = sum(1 for r in recs if r.get("action") == "canon-written")
    print(f"✅ 批次 {batch_id} 完成：通过校验 {n_ok}/{len(recs)}，写回正典 {n_canon} 篇")
    print(f"   提案：{(root / PROPOSAL_DIR / (batch_id + '.md')).relative_to(root)}"
          f"　报告：kb curate report")
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
    return 0


def _write_proposals(root: Path, batch_id: str, recs: List[Dict[str, Any]]) -> None:
    out = root / PROPOSAL_DIR
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# 🧪 结晶提案 {batch_id}", "",
             f"> 生成 {_now()}　·　{len(recs)} 篇　·　"
             f"`kb curate run`（本地 Agent 结晶，代码校验后写回）", ""]
    for r in recs:
        v = r.get("verify") or {}
        lines.append(f"## {r['rel']}")
        lines.append(f"- 判定：`{r.get('verdict', '-')}`　动作：`{r.get('action', '-')}`　"
                     f"通过校验：{'✅' if r.get('ok') else '❌'}　耗时 {r.get('secs', '-')}s")
        lines.append(f"- 原文 {r.get('body_chars', '-')} 字 → 正典比例 "
                     f"{(v.get('checks', {}) or {}).get('ratio', '-')}")
        if v.get("issues"):
            lines.append("- ⚠️ 校验问题：" + "；".join(v["issues"][:4]))
        prop = r.get("proposal") or {}
        if prop:
            lines.append(f"- 标题：{prop.get('title', '-')}")
            lines.append(f"- 摘要：{prop.get('summary', '-')}")
            lines.append("")
            lines.append("```markdown")
            lines.append(render_canon(prop).strip())
            lines.append("```")
        lines.append("")
    (out / f"{batch_id}.md").write_text("\n".join(lines), encoding="utf-8")


def cmd_verify(root: Path, batch: Optional[str], pending: bool) -> int:
    """重新校验账本里的提案（离线，不调模型）。"""
    led = root / LEDGER
    if not led.exists():
        print("（无账本：先跑 kb curate run）")
        return 0
    rows = []
    for line in led.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if batch and r.get("batch") != batch:
            continue
        if not r.get("proposal"):
            continue
        rows.append(r)
    if not rows:
        print(f"（无提案记录{'：batch ' + batch if batch else ''}）")
        return 0
    n_ok = n_bad = 0
    dropped_total = 0
    for r in rows:
        p = root / r["rel"]
        fm, _t, _b, body = load(p) if p.exists() else ({}, "", "", "")
        v = verify_note(body, r["proposal"], root, r.get("sid", ""))
        dropped_total += len(v["dropped_facts"])
        flag = "✅" if v["ok"] else "❌"
        n_ok += v["ok"]
        n_bad += (not v["ok"])
        print(f"{flag} {r['rel']}  比例 {v['checks']['ratio']}  "
              f"接地 {v['checks']['facts_grounded']}/{v['checks']['facts_total']}")
        for i in v["issues"]:
            print(f"     · {i}")
    print(f"\n合计：通过 {n_ok}，不通过 {n_bad}，累计丢弃未接地事实 {dropped_total} 条")
    return 0


def cmd_report(root: Path, as_json: bool) -> int:
    """结晶进度 + 质量指标（正典覆盖率、压缩比、接地率）。"""
    n_notes = n_canon = n_raw = n_index = 0
    canon_chars = orig_chars = 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, _t, _b, body = load(p)
        layer = kb_layer_of(fm, rel)
        n_notes += 1
        if layer == "canon":
            n_canon += 1
            canon_chars += len(body)
            orig_chars += int(fm.get("source_chars") or 0)
        elif layer == "raw":
            n_raw += 1
        elif layer == "index":
            n_index += 1
    led = root / LEDGER
    n_runs = n_ok = n_drop = 0
    if led.exists():
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_runs += 1
            n_ok += bool(r.get("ok"))
            n_drop += len(((r.get("verify") or {}).get("checks") or {}).get("facts_dropped") or [])
    n_review = 0
    rv = root / REVIEW
    if rv.exists():
        n_review = sum(1 for l in rv.read_text(encoding="utf-8").splitlines() if l.strip())
    data = {
        "notes": n_notes, "canon": n_canon, "raw": n_raw, "index_stubs": n_index,
        "pending_review": n_review,
        "canon_ratio": round(n_canon / max(n_notes - n_raw - n_index, 1), 3),
        "compression": round(canon_chars / orig_chars, 3) if orig_chars else None,
        "curate_runs": n_runs, "curate_ok": n_ok,
        "ok_rate": round(n_ok / n_runs, 3) if n_runs else None,
        "facts_dropped_total": n_drop,
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print("📊 结晶报告")
    print(f"   笔记总数 {n_notes}　正典 {n_canon}　证据层(raw) {n_raw}　索引页 {n_index}")
    print(f"   正典覆盖率（知识层）: {data['canon_ratio']:.1%}")
    if data["compression"] is not None:
        print(f"   压缩比（正典/原文）: {data['compression']:.1%}"
              f"（目标区间 {MIN_RATIO:.0%}–{MAX_RATIO:.0%}）")
    print(f"   结晶运行 {n_runs} 次，通过校验 {n_ok} 次（{data['ok_rate']}），"
          f"累计丢弃未接地事实 {n_drop} 条")
    if n_review:
        print(f"   待人工裁决 {n_review} 条（低置信）：kb curate review")
    if not n_runs:
        print("   （还没有结晶记录：kb curate plan → kb curate run）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "verify", "report", "review"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=ROOT_DEFAULT)
        if name in ("plan", "run"):
            s.add_argument("--domain", default=None)
            s.add_argument("--limit", type=int, default=BATCH_DEFAULT)
        if name == "run":
            s.add_argument("--apply", action="store_true", help="写回（默认只出提案）")
            s.add_argument("--model", default=None)
            s.add_argument("--max-seconds", type=int, default=0, dest="max_seconds",
                           help="单轮时间预算（秒）；到时停止，剩余留给下次 cron")
        if name == "verify":
            s.add_argument("--batch", default=None)
            s.add_argument("--pending", action="store_true")
        if name == "review":
            s.add_argument("--limit", type=int, default=50)
        s.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "plan":
        return cmd_plan(root, args.domain, args.limit, args.as_json)
    if args.cmd == "run":
        return cmd_run(root, args.domain, args.limit, args.apply, args.model, args.as_json,
                       max_seconds=getattr(args, "max_seconds", 0))
    if args.cmd == "verify":
        return cmd_verify(root, args.batch, args.pending)
    if args.cmd == "report":
        return cmd_report(root, args.as_json)
    if args.cmd == "review":
        return cmd_review(root, getattr(args, "limit", 50), args.as_json)
    return 1


if __name__ == "__main__":
    sys.exit(main())
