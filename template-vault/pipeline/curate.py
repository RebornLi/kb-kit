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
PROPS = Path(".kb") / "curate_proposals.jsonl"   # 完整提案（含 canon 草稿），供 recheck/人工复核

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


def _props_append(root: Path, rec: Dict[str, Any]) -> None:
    """把完整提案（含 canon 草稿）写入旁路 jsonl：账本保持精简，复核/重审读这里。"""
    p = root / PROPS
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: rec.get(k) for k in ("rel", "sid", "ts", "batch", "verdict", "action", "model", "confidence")}
    keep["proposal"] = rec.get("proposal")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(keep, ensure_ascii=False) + "\n")


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
        """要一份 JSON 对象；容忍 ```json 包裹、前后废话、以及被截断的尾部。
        返回 (对象, 说明)。说明会显式区分「截断」与「格式不对」，便于上层决定是否重试。
        """
        text, note = self.chat(messages, max_tokens=max_tokens)
        if text is None:
            return None, note
        truncated = str(note).startswith("truncated")
        obj = _extract_json(text)
        if obj is None:
            tail = text.strip()[-160:].replace("\n", " ")
            why = "输出被 max_tokens 截断且无法修复" if truncated else "未返回合法 JSON"
            return None, f"{why}（finish={note}；尾部：…{tail}）"
        if truncated:
            note = "truncated-repaired"   # 补齐括号抢救成功，但仍提示上层可紧凑重试
        return obj, note


def _extract_json(text: str) -> Optional[dict]:
    """从模型输出里取 JSON 对象：容忍 ```json 包裹、前后废话、以及**被截断**的尾部。

    截断修复：逐步回退到最后一个完整元素边界并补齐括号。理由：「输出被 max_tokens
    截断」时前面 90% 的内容通常完全可用，直接丢弃等于浪费一整次推理。
    """
    s = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    if start < 0:
        return None
    frag = s[start:]
    # 1) 截取到最后一个平衡的 '}' 再试
    depth = 0
    for i, ch in enumerate(frag):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(frag[:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    break
    # 2) 截断修复：只在「元素边界」回退（逗号/数组尾/对象尾），最多试 400 个候选
    cuts = [m.start() for m in re.finditer(r'[,}\]]', frag)]
    for idx in reversed(cuts[-400:]):
        head = frag[:idx + 1].rstrip()
        head = re.sub(r",$", "", head).rstrip()
        opens = head.count("{") - head.count("}")
        arrs = head.count("[") - head.count("]")
        if opens < 0 or arrs < 0:
            continue
        candidate = head + ("]" * arrs) + ("}" * opens)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and ("verdict" in obj or "sections" in obj):
                return obj
        except json.JSONDecodeError:
            continue
    return None


# ── 候选选批 ────────────────────────────────────────────────
def _already_done(st: Dict[str, Any], rel: str, body: str) -> bool:
    """已结晶过且原文未变（按内容指纹判断）。"""
    rec = (st.get("done") or {}).get(rel)
    if not rec:
        return False
    return rec.get("fp") == content_fingerprint(body)


MAX_ATTEMPTS = 2      # 一篇最多尝试几次（propose 未过校验允许重试 1 次；error 不无限重跑）


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
        if layer in ("canon", "noise"):
            continue                       # 已是正典 / 已判为噪声，不再重复结晶
        # 已判定过（raw-only / noise 写回了 curate_verdict）的笔记不再重复送模型：
        #   否则每轮都会重炼同一批，命中率被稀释（实测第三批 250 篇里 223 篇是重复劳动）
        if str(fm.get("curate_verdict", "") or "").strip().lower() in ("raw-only", "noise"):
            continue
        if str(fm.get("kb_index", "")).strip().lower() in ("false", "no", "0"):
            continue                       # 显式退出检索的笔记不结晶
        n = len(body.replace("\n", "").strip())
        if n < SMALL_BODY:
            continue                       # 空壳/无信息量
        base = Path(rel).name.split(".")[0]
        if domain and str(fm.get("domain", "")).strip() != domain:
            continue
        if not include_done:
            if _already_done(st, rel, body):
                continue
            # 未通过校验（propose/error）的笔记：允许再试，但不超过 MAX_ATTEMPTS
            prior = (st.get("done") or {}).get(rel) or {}
            if int(prior.get("attempts") or 0) >= MAX_ATTEMPTS:
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


# ── 提示词：由提示词层（curate_prompt）组装四层结构 ──────────────
#   L1 契约（reference/kb-schema.json）· L2 规则 · L3 任务 · L4 范文
#   旧版把这几层揉成一段硬编码文本；现在契约改了提示词自动跟着改。
from curate_prompt import (build_messages as _build_layered,
                           build_messages_baseline as _build_baseline)

PROMPT_STYLE = "baseline"      # baseline（实测更稳）| layered（四层契约结构，A/B 未胜出前不默认）


def _build_prompt_messages(root: Path, body: str, fm, sid: str, rel: str):
    """按档位组装提示词。baseline 与 P0 前生产环境一致；layered 为契约驱动四层。"""
    if PROMPT_STYLE == "layered":
        return _build_layered(root, body, fm, sid, rel)
    try:
        from curate_prompt import _merge_candidates
        merge_text = _merge_candidates(root, body)
    except Exception:
        merge_text = "（无）"
    return _build_baseline(body, fm, sid, rel, merge_text=merge_text)


# P3：写回前的"近重复正典"闸门阈值——同内容被结晶两遍的直接原因就是缺这道闸
DUP_CANON_SIM = 0.90


def find_dup_canon(root: Path, body: str, exclude: str = "") -> Optional[Dict[str, Any]]:
    """检查是否已有内容近乎相同的正典（≥DUP_CANON_SIM）。

    为什么需要：同一份内容会被不同来源碎片各结晶一次，产出两个文件名不同的正典，
    既浪费又会在检索里互相稀释（实测发现 22 对 99%+ 重叠的"正典双胞胎"）。
    命中时应改为 merge/跳过，而不是再新建一篇。
    """
    best = None
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel) or is_generated_report(rel):
            continue
        fm, _t, _b, b = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        if exclude and rel == exclude:
            continue                      # 别和自己比（否则恒 sim=1.0 自己挡自己）
        sim = _jaccard(_emphasis_of(b), _emphasis_of(body))
        if sim >= DUP_CANON_SIM and (best is None or sim > best["sim"]):
            best = {"rel": rel, "sim": round(sim, 3)}
    return best


def _emphasis_of(body: str) -> str:
    """取结论性部分（结论/决定/要点/现象/根因）做比对。"""
    out, cur = [], None
    keep = ("结论", "决定", "要点", "现象", "根因")
    for line in str(body or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1).strip()
        elif cur and any(cur.startswith(k) for k in keep):
            out.append(line)
    return "\n".join(out).strip() or str(body or "")[:1500]


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
_ID_PATTERNS = (
    re.compile(r"`([^`\n]{2,60})`"),                       # 反引号包裹的命令/路径/参数
    re.compile(r"(?:[A-Za-z]:)?(?:/[\w@+-]+){2,6}(?:\.[A-Za-z0-9]{1,6})?"),  # 路径（≥2 段）
    re.compile(r"\b\d+(?:\.\d+){1,3}\b"),                # 版本号
    re.compile(r"\b(?:localhost|127\.0\.0\.1|\d{1,3}(?:\.\d{1,3}){3}):\d{2,5}\b"),  # 主机:端口
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:GB|MB|KB|GiB|MiB|TB|ms|分钟|小时|天)\b"),         # 量纲
    re.compile(r"\b[a-z][a-z0-9-]{2,30}\.(?:service|py|json|md|sh|sqlite|db|ya?ml)\b"),  # 服务/文件名
)

# 明确不是"标识符"的东西：表格分隔、列表符号、纯标点、软性短语
_NOT_ID = re.compile(r"^[\s|\-:+#*>·。，、；：！？()\[\]{}]+$|^(?:默认|可选|必填|无|空|同上|见上)$")


def _plausible_id(v: str) -> bool:
    """判断抽取到的串是否真是"不能丢的标识符"（不是表格行、不是纯标点）。"""
    v = re.sub(r"^[|\s`]+|[|\s`]+$", "", v.strip())
    if len(v) < 2 or _NOT_ID.match(v):
        return False
    if v.count("|") >= 2:                 # 表格行残留（`| a | b |`）
        return False
    if v in ("--apply", "--dry-run"):     # 单独出现的开关太弱，交给命令模式捕获
        return False
    if v.startswith("|") or v.endswith("|"):
        return False
    return True


def _key_identifiers(body: str, cap: int = 40) -> List[str]:
    """抽取原文里"不能丢"的关键标识符（命令/路径/版本/端口/量纲/常量），去重限量。"""
    found, seen = [], set()
    for pat in _ID_PATTERNS:
        for m in pat.finditer(body or ""):
            v = (m.group(1) if m.groups() else m.group(0)).strip().strip("`").strip()
            if not _plausible_id(v) or v in seen:
                continue
            seen.add(v)
            found.append(v)
            if len(found) >= cap:
                return found
    return found


_FILLER_WORD = re.compile(r"好的?|收到|明白|稍等|嗯+|继续|没问题|哈哈+|谢谢|OK|okay|got it|好嘞", re.I)


def is_filler_line(line: str) -> bool:
    """是否纯寒暄行：由寒暄词/标点/空白组成，且不含实义内容。"""
    if "：" in (line or "") or ":" in (line or ""):   # 带冒号 = 有内容标注，不是寒暄
        return False
    t = re.sub(r"[\s，。！？、,.!?~;；\-—…]", "", line or "")
    # 寒暄行必须"几乎全是寒暄词"：去掉寒暄词后所剩无几
    residue = _FILLER_WORD.sub("", t)
    return bool(t) and len(t) <= 12 and len(residue) <= 2
IMPERATIVE_PAT = re.compile(r"^\s*(?:我(?:们)?(?:现在|接下来|先|再)|让我|下面我|接下来我|现在开始)")


def _boilerplate_ratio(body: str) -> Dict[str, Any]:
    """测"废话/冗余"占比：寒暄行 + 指令性开头 + 重复行（≥3 次的短行）。

    这是对"没有废话"这一目标的**直接度量**——比字符压缩比更贴近目标。
    """
    lines = [l.strip() for l in str(body or "").splitlines() if l.strip()]
    if not lines:
        return {"boilerplate_ratio": 0.0, "boiler_lines": 0, "lines": 0, "dup_lines": 0}
    from collections import Counter
    cnt = Counter(l for l in lines if len(l) <= 40)
    boiler = 0
    dup = 0
    for l in lines:
        if is_filler_line(l) or IMPERATIVE_PAT.match(l):
            boiler += 1
        elif len(l) <= 60 and cnt[l] >= 3:
            dup += 1
    return {"boilerplate_ratio": round((boiler + dup) / len(lines), 3),
            "boiler_lines": boiler, "dup_lines": dup, "lines": len(lines)}


def load_schema(root: Path) -> Optional[dict]:
    """读知识契约（P1 起用于页型校验）；缺失 → None（校验降级为不查页型）。"""
    try:
        return json.loads((root / "reference" / "kb-schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def verify_note(body: str, prop: Dict[str, Any], root: Path,
                sid: str) -> Dict[str, Any]:
    """对一份提案做确定性校验。返回 {ok, checks, issues, grounded_facts, dropped_facts}。"""
    issues: List[str] = []
    checks: Dict[str, Any] = {}
    canon = render_canon(prop)
    ratio = (len(canon) / len(body)) if body else 0.0
    checks["ratio"] = round(ratio, 3)
    checks["canon_chars"] = len(canon)
    # 尺寸感知的守恒判据：短原文（碎片）的正典天然短，不能套用长原文的绝对字数门槛。
    #   否则「原文 300 字 → 正典 150 字」会被误判为"过度压缩"，实测 92 条提案里大半如此。
    n_src = len(body)
    if n_src <= 600:
        min_ratio, min_chars = 0.03, max(60, int(n_src * 0.10))
    elif n_src <= 3000:
        min_ratio, min_chars = HARD_MIN_RATIO, min(300, int(n_src * 0.10))
    else:
        min_ratio, min_chars = HARD_MIN_RATIO, HARD_MIN_CHARS
    checks["min_ratio"] = min_ratio
    checks["min_chars"] = min_chars
    if ratio < min_ratio or len(canon) < min_chars:
        issues.append(f"过度压缩：原文 {n_src} 字 → 正典 {len(canon)} 字 = {ratio:.1%}"
                      f"（低于阈值 {min_ratio:.0%} 或正典不足 {min_chars} 字）→ 疑丢关键内容")
    elif ratio < MIN_RATIO:
        issues.append(f"⚠️压缩偏狠：{ratio:.1%}（目标 ≥{MIN_RATIO:.0%}；原文超长时属正常，请抽检事实完整性）")
    elif ratio > MAX_RATIO:
        issues.append(f"⚠️压缩偏轻：{ratio:.1%}（目标 ≤{MAX_RATIO:.0%}；若原文本身就是要点清单，"
                      f"保真优先可以接受——内容是否靠谱由接地关与覆盖关判定）")

    # 接地关：facts 逐条回原文核对（精确子串，允许空白归一），并按 kind 分型
    norm_body = re.sub(r"\s+", "", body)
    grounded, dropped, inferred = [], [], []
    for f in (prop.get("facts") or []):
        if isinstance(f, dict):
            v = str(f.get("value", "")).strip()
            kind = str(f.get("kind", "verbatim") or "verbatim").strip().lower()
        else:
            v, kind = str(f).strip(), "verbatim"
        if not v:
            continue
        # 溯源 ID 不是"事实"：模型偶尔把 source_ref 混进 facts，不该按未接地计罚
        if v.startswith(("vault:", "memory/", "openclaw/", "raw/")) or "::" in v:
            continue
        if kind == "inferred":
            inferred.append(v)          # 推断项：登记但不计入接地分母（必须在正文标 ^[推断]）
            continue
        if re.sub(r"\s+", "", v) in norm_body:
            grounded.append(v)
        else:
            dropped.append(v)
    checks["facts_total"] = len(grounded) + len(dropped)
    checks["facts_grounded"] = len(grounded)
    checks["facts_dropped"] = dropped
    checks["facts_inferred"] = inferred
    checks["ground_rate"] = round(len(grounded) / max(len(grounded) + len(dropped), 1), 3)
    if dropped:
        issues.append(f"未接地事实 {len(dropped)} 条已丢弃：{dropped[:5]}")
    if (len(grounded) + len(dropped)) >= 3 and not grounded:
        issues.append("全部 facts 都无法在原文中找到 → 疑似编造，建议人工复核")
    if inferred:
        # 推断项必须在正文打标记，否则读者无法区分「原文有」与「模型想」
        missing = [v for v in inferred if "^[推断]" not in canon]
        issues.append(f"⚠️含 {len(inferred)} 条推断事实（正文需带 ^[推断] 标记）"
                      + ("，当前正文缺少标记" if missing else ""))

    # 页型关：按契约检查必填节（缺必填节 → 硬失败；节名不在契约里 → 警告）
    pt = str(prop.get("page_type") or "").strip()
    if pt:
        schema = load_schema(root)
        pt_def = ((schema or {}).get("page_types") or {}).get(pt) or {}
        req = pt_def.get("required_sections") or []
        allowed = set((pt_def.get("required_sections") or []) + (pt_def.get("optional_sections") or []))
        secs = set((prop.get("sections") or {}).keys())
        checks["page_type"] = pt
        checks["missing_sections"] = [s for s in req if s not in secs]
        checks["extra_sections"] = sorted(secs - allowed) if allowed else []
        if checks["missing_sections"]:
            issues.append(f"缺页型必填节 {checks['missing_sections']}（契约页型 `{pt}`）")
        if checks["extra_sections"]:
            issues.append(f"⚠️节名不在契约内：{checks['extra_sections']}")

    # 蒸馏关（P1）：原文里的废话/冗余，被删掉多少？——直接对应"没有废话"这个目标
    bp = _boilerplate_ratio(body)
    checks.update({f"src_{k}": v for k, v in bp.items()})
    if bp["lines"] >= 8 and bp["boilerplate_ratio"] >= 0.05:
        keep = 1.0 - bp["boilerplate_ratio"]
        if keep > 1.0 - bp["boilerplate_ratio"] * 0.8:      # 几乎没删
            issues.append(f"⚠️废话未清：原文 {bp['boiler_lines']} 行寒暄/指令语 + "
                          f"{bp['dup_lines']} 行重复（占 {bp['boilerplate_ratio']:.0%}），正典应把这些删掉")

    # 覆盖关（P1）：用"原文关键标识符被正典覆盖的比例"代替"字符数守恒"。
    #   字符守恒 ≠ 信息守恒；标识符（命令/路径/端口/版本/数字/报错串）才是真正不能丢的东西。
    ids = _key_identifiers(body)
    if ids:
        covered = [x for x in ids if x in canon]
        checks["identifiers_total"] = len(ids)
        checks["identifiers_covered"] = len(covered)
        checks["coverage"] = round(len(covered) / len(ids), 3)
        missing = [x for x in ids if x not in covered]
        if len(ids) >= 8 and len(covered) / len(ids) < 0.6:
            issues.append(f"⚠️标识符覆盖偏低：{len(covered)}/{len(ids)}（<60%）漏掉：{missing[:6]}")
    else:
        checks["coverage"] = None

    # 引用关：sources 必须指向可达的原记忆
    srcs = [str(s).strip() for s in (prop.get("sources") or []) if str(s).strip()]
    checks["sources"] = srcs
    if not srcs:
        issues.append("未给出 sources（无法回溯原记忆）")

    hard_kw = ("过度压缩", "未给出 sources", "全部 facts", "缺页型必填节")
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


_DATE_IN_TEXT = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


def _event_at(fm: Dict[str, Any], body: str) -> str:
    """该正典描述的**事实何时为真**（双时态的事件时间）。

    取法（按可信度）：原文 frontmatter 的 event_at > created > updated > 正文里最早出现的日期。
    与 derived_at（何时学到）分开存，才能回答"当时知道什么"。
    """
    for k in ("event_at", "created", "updated"):
        v = str(fm.get(k) or "")[:10]
        if re.match(r"^20\d{2}-\d{2}-\d{2}$", v):
            return v
    ms = _DATE_IN_TEXT.findall(body[:3000])
    if ms:
        y, m, d = ms[0]
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return ""


def _composite_confidence(prop: Dict[str, Any], verified: Dict[str, Any]) -> float:
    """P3：写回时算复合置信分（四因子），而不是只信模型自评。

    与 kb_confidence.score() 同口径，但这里用的是**当下可得**的信号
    （接地事实数、标识符覆盖、页型、source_ref 数），避免写回后再扫库。
    """
    try:
        from kb_confidence import _basis_field, _corroboration, _recency, _verification, W
    except Exception:
        return 0.5
    fake_fm = {
        "source_ref": ["x"],                                   # 至少一条（刚结晶）
        "updated": datetime.date.today().isoformat(),
        "grounded_facts": (verified or {}).get("grounded_facts") or [],
        "coverage": ((verified or {}).get("checks") or {}).get("coverage"),
        "canon_type": prop.get("page_type"),
    }
    body_render = render_canon(prop)
    e, _ = _basis_field(body_render)
    c, _ = _corroboration(len(prop.get("sources") or ["x"]))
    r, _ = _recency(fake_fm["updated"])
    v, _ = _verification(fake_fm)
    return round(W["extraction"] * e + W["corroboration"] * c + W["recency"] * r + W["verification"] * v, 3)


def _canon_fm(rel: str, fm: Dict[str, Any], prop: Dict[str, Any],
              sid: str, verified: Dict[str, Any], model: str) -> Dict[str, Any]:
    """正典 frontmatter：继承原 frontmatter + 溯源字段（可一键回原记忆）。"""
    out = dict(fm)
    out.update({
        "kb_layer": "canon",
        "canon_type": str(prop.get("page_type") or "canon-generic"),
        "kb_summary": str(prop.get("summary") or prop.get("title") or "")[:160],
        "title": str(prop.get("title") or "")[:120] or fm.get("title", Path(rel).stem),
        "updated": datetime.date.today().isoformat(),
        "curated_by": model,
        "curated_at": _now(),
        "derived_at": _now(),                       # 双时态：何时学到
        "event_at": _event_at(fm, str(prop.get("summary") or "")),  # 双时态：事实何时为真
        "source_ref": list(dict.fromkeys([sid] + [str(s) for s in (verified.get("checks", {}).get("sources") or [])])),
        "source_chars": verified.get("checks", {}).get("orig_chars", 0),
        "canon_ratio": verified.get("checks", {}).get("ratio", 0),
        "grounded_facts": verified.get("grounded_facts", [])[:20],
        "curate_confidence": prop.get("confidence", None),   # 模型自评（原始信号）
        "confidence": _composite_confidence(prop, verified),  # P3 复合置信分（可审计四因子）
    })
    for junk in ("chunk_of", "chunk", "is_chunk_index", "related_to", "memory_source"):
        out.pop(junk, None)
    return out


# ── 单篇结晶 ────────────────────────────────────────────────
def curate_one(root: Path, rel: str, llm: LLM, apply: bool = False) -> Dict[str, Any]:
    p = root / rel
    fm, text, block, body = load(p)
    sid = str(fm.get("memory_source") or f"vault:{rel}")
    msgs = _build_prompt_messages(root, body, fm, sid, rel)
    payload = msgs[-1]["content"]
    prop, note = llm.ask_json(msgs, max_tokens=4096)
    # 被 max_tokens 截断（且括号补齐也没救回来）→ 就地重试一次，明确要求更紧凑
    if prop is None and "截断" in str(note):
        retry = (payload + "\n\n【重要】上次输出超长被截断而作废。本次必须极紧凑："
                 "sections 每节最多 2 条、每条不超过 30 字；facts 最多 6 条（只填标识符本身）；"
                 "dropped 只 1 条；整个 JSON 不超过 800 字。")
        prop2, note2 = llm.ask_json([{"role": "system", "content": msgs[0]["content"]},
                                     {"role": "user", "content": retry}], max_tokens=4096)
        if prop2 is not None:
            prop, note = prop2, note2
        else:
            note = f"{note}（紧凑重试仍失败：{str(note2)[:120]}）"
    rec: Dict[str, Any] = {"rel": rel, "sid": sid, "model": llm.model, "ts": _now(),
                           "body_chars": len(body), "ok": False, "note": note}
    if prop is None:
        rec["error"] = note
        return rec
    verdict = str(prop.get("verdict", "")).strip().lower() or "canon"
    rec["verdict"] = verdict
    rec["proposal"] = prop
    if verdict in ("noise", "raw-only"):
        # 不生成正典；把判定写回 frontmatter，让索引降级真正生效
        rec["ok"] = True
        rec["action"] = mark_evidence_verdict(root, rel, verdict, rec)
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
        # P3：写回前先查"是否已有近乎相同的正典"（同内容被结晶两遍的根治闸）
        dup = find_dup_canon(root, render_canon(prop), exclude=rel)
        if dup:
            rec["action"] = f"skip:duplicate-of({dup['rel']})"
            rec["dup_of"] = dup
            return rec
        rec["action"] = apply_canon(root, rel, fm, prop, sid, verified, llm.model)
    return rec


def l4_frozen(root: Path) -> bool:
    """L4 宪法是否冻结了知识结晶（rung=lc）。

    冻结 = 真人连续判错达阈值 → 停止**自动写回**，退化为只出提案（人工在环）。
    读不到配置/损坏 → 不冻结（best-effort，绝不因此卡住流水线）。
    """
    for name in (".kb_l4_config.json", ".kb/state/l4_config.json"):
        try:
            d = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        frz = (d.get("frozen") or {})
        if isinstance(frz, dict):
            return bool(frz.get("lc"))
    return False


def _upsert_frontmatter(text: str, updates: Dict[str, Any]) -> Optional[str]:
    """就地更新/插入 frontmatter 键（无 frontmatter 则返回 None，不动文件）。"""
    if not text.startswith("---"):
        return None
    m = re.search(r"^---\s*$", text, re.M)
    if not m:
        return None
    end = re.search(r"^---\s*$", text[m.end():], re.M)
    if not end:
        return None
    head = text[m.end():m.end() + end.start()]
    body = text[m.end() + end.end():]
    lines = head.strip("\n").splitlines()
    keys_done = set()
    for i, ln in enumerate(lines):
        for k, v in updates.items():
            if re.match(rf"^{re.escape(k)}\s*:", ln):
                lines[i] = f"{k}: {fmt_value(v)}"
                keys_done.add(k)
    for k, v in updates.items():
        if k not in keys_done:
            lines.append(f"{k}: {fmt_value(v)}")
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def mark_evidence_verdict(root: Path, rel: str, verdict: str, rec: Dict[str, Any]) -> str:
    """把 raw-only / noise 判定写回 frontmatter，让索引降级真正生效。

      · raw-only（只有过程价值、无结论）→ `kb_layer: raw`（排序降级，仍可检索可调用）
      · noise（寒暄/重复/纯指针/空壳）  → `kb_layer: noise` + `kb_index: false`（不入主检索）
    两者都记录判定来源与时间，便于审计与回滚（重新结晶或人工改回即可）。
    """
    p = root / rel
    text = _read(p)
    if verdict == "noise":
        updates = {"kb_layer": "noise", "kb_index": "false"}
    else:
        updates = {"kb_layer": "raw"}
    updates.update({"curate_verdict": verdict, "curated_by": rec.get("model", ""),
                    "curated_at": _now()})
    new = _upsert_frontmatter(text, updates)
    if new is None:
        return f"skip:{verdict}(no-frontmatter)"
    p.write_text(new, encoding="utf-8")
    return f"skip:{verdict}"


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
    if apply and l4_frozen(root):
        print("  ⚖️ L4 宪法已冻结 lc（知识结晶）→ 本轮只出提案，不自动写回；"
              "人工解冻：python3 pipeline/kb_l4.py unfreeze --root . --rung lc")
        apply = False
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
        prev = (st.get("done") or {}).get(c["rel"]) or {}
        st.setdefault("done", {})[c["rel"]] = {"fp": content_fingerprint(_read(root / c["rel"])),
                                              "ts": _now(), "batch": batch_id,
                                              "attempts": int(prev.get("attempts") or 0) + 1,
                                              "ok": rec.get("ok"), "action": rec.get("action")}
        recs.append(rec)
        _ledger_append(root, {k: v for k, v in rec.items() if k != "proposal"})
        if rec.get("proposal"):
            _props_append(root, rec)
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


def cmd_recheck(root: Path, apply_pass: bool, export: bool) -> int:
    """用**当前**校验规则重审历史提案（离线，不调模型）。

    用途：校验规则变严/变松后，把「当时没过、现在达标」的提案补写回；
    把仍未达标的导成 `_curate/REVIEW-NEEDED.md` 人工清单（附模型草稿与未过原因）。
    只对最终动作是 propose / review 的笔记动手（canon-written / skip:* 不动）。
    """
    src = root / PROPS
    if not src.exists():
        # 兼容：早期只有账本（proposal 已被剥离）；提示用 _curate/*.md 人工复核
        print("（无完整提案旁路 .kb/curate_proposals.jsonl；历史提案请看 "
              "70-知识治理 Governance/_curate/b*.md，本次运行起会自动记录）")
        return 3
    led = src
    final: Dict[str, Dict[str, Any]] = {}
    for line in led.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rel = r.get("rel")
        if not rel or not r.get("proposal"):
            continue
        prev = final.get(rel)
        if prev is None or str(r.get("ts", "")) >= str(prev.get("ts", "")):
            final[rel] = r

    pass_now, still, applied = [], [], 0
    for rel, r in final.items():
        if (r.get("action") or "") not in ("propose", "review:low-confidence"):
            continue
        p = root / rel
        if not p.exists():
            continue
        fm, _t, _b, body = load(p)
        v = verify_note(body, r["proposal"], root, r.get("sid", ""))
        # 低置信原样保留人工裁决（不因改规则就绕过置信闸门）
        if (r.get("action") or "") == "review:low-confidence":
            still.append((rel, "低置信（人工裁决闸门）", r))
            continue
        if v["ok"]:
            pass_now.append((rel, v))
        else:
            still.append((rel, "；".join(v["issues"][:2]) or "未过校验", r))

    print(f"🔁 重审历史提案：达标 {len(pass_now)} 篇 · 仍未达标 {len(still)} 篇")
    if apply_pass and pass_now:
        checkpoint(root)
        for rel, v in pass_now:
            fm, _t, _b, body = load(root / rel)
            sid = str(fm.get("memory_source") or f"vault:{rel}")
            v["checks"]["orig_chars"] = len(body)
            act = apply_canon(root, rel, fm, final[rel]["proposal"], sid, v, "recheck")
            applied += 1
            print(f"   ✅ {act}  {rel[:64]}")
        st = _load_state(root)
        for rel, _v in pass_now:
            st.setdefault("done", {})[rel] = {
                "fp": content_fingerprint(_read(root / rel)), "ts": _now(),
                "batch": "recheck", "ok": True, "action": "canon-written"}
        _save_state(root, st)
        print(f"   已写回 {applied} 篇；建议随后 kb rag index")

    if export:
        out = root / PROPOSAL_DIR / "REVIEW-NEEDED.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# 🧑⚖️ 待人工复核的结晶提案（{len(still)} 篇）", "",
                 f"> 生成 {_now()}　·　这些提案**未写回知识层**，仍在原处等待判断。",
                 "> 认可某篇：`kb curate judge` 或直接手工把下方草稿合进原笔记；",
                 "> 不认可：不必处理（下次不会再被自动重跑，见 MAX_ATTEMPTS）。", ""]
        for rel, why, r in still:
            prop = r.get("proposal") or {}
            lines += [f"## {rel}", "",
                      f"- 未过原因：{why}",
                      f"- 模型标题：{prop.get('title', '-')}",
                      f"- 摘要：{prop.get('summary', '-')}",
                      f"- 取原文：`kb raw show \"{r.get('sid', '')}\"`",
                      f"- 原记忆 ID：`{r.get('sid', '')}`", "",
                      "```markdown", render_canon(prop).strip(), "```", ""]
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"   人工清单：{out.relative_to(root)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "verify", "report", "review", "recheck"):
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
            s.add_argument("--prompt", choices=("baseline", "layered"), default="baseline",
                           help="提示词档位：baseline（默认，实测更稳）| layered（四层契约结构）")
        if name == "verify":
            s.add_argument("--batch", default=None)
            s.add_argument("--pending", action="store_true")
        if name == "review":
            s.add_argument("--limit", type=int, default=50)
        if name == "recheck":
            s.add_argument("--apply-pass", action="store_true", dest="apply_pass",
                           help="把「按新规则已达标」的提案补写回")
            s.add_argument("--export", action="store_true", help="导出 REVIEW-NEEDED.md 人工清单")
        s.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "plan":
        return cmd_plan(root, args.domain, args.limit, args.as_json)
    if args.cmd == "run":
        global PROMPT_STYLE
        PROMPT_STYLE = getattr(args, "prompt", "baseline")
        return cmd_run(root, args.domain, args.limit, args.apply, args.model, args.as_json,
                       max_seconds=getattr(args, "max_seconds", 0))
    if args.cmd == "verify":
        return cmd_verify(root, args.batch, args.pending)
    if args.cmd == "report":
        return cmd_report(root, args.as_json)
    if args.cmd == "review":
        return cmd_review(root, getattr(args, "limit", 50), args.as_json)
    if args.cmd == "recheck":
        return cmd_recheck(root, getattr(args, "apply_pass", False), getattr(args, "export", False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
