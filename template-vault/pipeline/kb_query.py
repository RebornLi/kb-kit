# kb_query.py — P2-1 闭合"查询→知识"正回路（Karpathy 正增长回路的 RSI 版）
#
# 用户查询综合出"耐久性答案"时，**提议**存为一条编译笔记（query → synthesize → persist）。
# 关键约束：人工在环 + 有界，避免合成数据膨胀 / Model Collapse：
#   • 人工在环：只写提案到 `.kb_query_proposals.jsonl`，绝不自动写库；
#             由 `apply`（显式 --id/--all）在人工确认后才真正入库。
#   • 有界（五道刹车，任一不过关即不提议）：
#       1. 长度门    ≥MIN_ANSWER_CHARS（剔碎片闲聊）
#       2. 多样性门  TTR≥MIN_TTR（剔车轱辘话）
#       3. 新异度门  与已有编译笔记最大余弦 < NOVELTY_MIN_SIM（剔对现有笔记的复述）
#       4. 接地门    复用 kb_rsi 外接阀（fresh raw ≥5% & cross_ratio ≥15%）→ 自闭库不提议
#       5. 频率门    同一天 ≤ MAX_PROPOSALS_PER_DAY
#
# 中文字面向量用本地"按字/按词"方案（同 kb_contradiction），避免 kb_rsi.vec 把整段中文
# 当单 token 导致余弦恒近 0 的旧坑。
#
# 用法：
#   python3 pipeline/kb_query.py query  --root R --query "用户查询" [--answer "已综合答案"]
#   python3 pipeline/kb_query.py apply  --root R [--id N | --all] [--auto]
#   python3 pipeline/kb_query.py status --root R [--json]
import argparse, json, re, subprocess, sys, math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi            # 复用 collect/compiled_notes/metrics/proposals/load_note
from kb_common import cos as _cos  # 余弦相似度（行为等价：非空向量点积）

# ── 有界门控常量 ──────────────────────────────────────────────
MIN_ANSWER_CHARS = 60      # 答案太短 = 一次性闲聊，不提议
MIN_TTR = 0.45             # 词汇多样性下限（unique/total token）
NOVELTY_MIN_SIM = 0.90     # 答案与已有编译笔记的最大余弦低于此才可能"新知识"；
                           # 跨多篇综合天然与单篇高度相似(组合新≠复制)，故阈值设高，
                           # 仅当近乎完整复制单篇(合成膨胀)才拒绝。
MAX_PROPOSALS_PER_DAY = 10 # 频率门：同一天最多提议条数
# ── 本地字符向量（中文字 / 英文词，避免整段中文单 token）────────
_CN = re.compile(r"[\u4e00-\u9fff]")          # 中文字（按字切，避免整段中文当单 token）
_LAT = re.compile(r"[A-Za-z0-9]+")            # 英文词（按词切）
_DATE = datetime.now().strftime("%Y-%m-%d")


# ── 字符向量 ──────────────────────────────────────────────────
def _tokenize(s):
    # 中文字各计一 token、英文按词；避免"整段中文当单 token"使余弦恒近 0（P1 旧坑）
    return _CN.findall(s or "") + _LAT.findall(s or "")


def _vec(s):
    """L2-归一化字符向量：相同文本余弦≈1.0、无关≈0。
    词频归一(sum=1)会使相同文本余弦被长度稀释至~0.01，无法判重复——故用 L2 范数。"""
    c = Counter(_tokenize(s or ""))
    n = math.sqrt(sum(v * v for v in c.values())) or 1
    return {k: v / n for k, v in c.items()}


# 注：_tokenize / _vec 保留本地实现——与 kb_common.tokenize/char_vec 不同：
#   本地 _tokenize 无 lower()、无 CJK bigram，刻意保持以不改变检索排序行为。


def _today():
    return _DATE


def _slug(q):
    toks = _tokenize(q)[:6]
    slug = "".join(t for t in toks).replace(" ", "-")[:30]
    return slug or "query"


# ── 检索：query → top-k 相关编译笔记 ──────────────────────────
def search(root: Union[str, Path], query: str, topk: int = 6) -> List[Tuple[float, Dict[str, Any]]]:
    qv = _vec(query)
    scored = []
    for n in kb_rsi.compiled_notes(root):
        body = (n.get("body") or "").strip()
        if not body:
            continue
        s = _cos(qv, _vec(body))
        if s > 0:
            scored.append((s, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:topk]


# ── 规则综合（零 LLM）：抽取命中篇关键句拼接 ──────────────────
def synthesize(scored: List[Tuple[float, Dict[str, Any]]], query: str) -> str:
    if not scored:
        return ""
    bits = []
    seen = 0
    for _s, n in scored:
        lines = [ln.strip() for ln in (n.get("body") or "").splitlines() if ln.strip()]
        head = " ".join(lines[:2]) if lines else (n.get("body") or "")[:120].strip()
        if not head:
            continue
        bits.append(f"- [{Path(n['rel']).name}] {head}")
        seen += len(head)
        if seen >= 700:
            break
    return "[综合自 %d 篇命中]\n" % len(bits) + "\n".join(bits)


# ── 接地阀（复用 kb_rsi 外接阀）───────────────────────────────
def _grounded(root):
    m = kb_rsi.metrics(kb_rsi.collect(root))
    if not m:
        return False, None, ""
    _props, note, grounded = kb_rsi.proposals(m)
    return grounded, m, note


# ── 耐久性评分 ────────────────────────────────────────────────
def durability(answer: str, root: Union[str, Path]) -> Tuple[Dict[str, Any], bool, List[str]]:
    """返回 (score_dict, pass_bool, reason)。pass 需同时过 长度/多样性/新异度。"""
    chars = len(answer)
    tokens = _tokenize(answer)
    ttr = (len(set(tokens)) / len(tokens)) if tokens else 0.0
    av = _vec(answer)
    novelty = max((_cos(av, _vec((n.get("body") or "").strip()))
                   for n in kb_rsi.compiled_notes(root) if (n.get("body") or "").strip()),
                  default=0.0)
    reasons = []
    ok = True
    if chars < MIN_ANSWER_CHARS:
        ok = False; reasons.append(f"过短({chars}<{MIN_ANSWER_CHARS}字)")
    if ttr < MIN_TTR:
        ok = False; reasons.append(f"词汇重复(TTR {ttr:.2f}< {MIN_TTR})")
    if novelty >= NOVELTY_MIN_SIM:
        ok = False; reasons.append(f"对现有笔记复述(sim {novelty:.2f}≥{NOVELTY_MIN_SIM})")
    return {"chars": chars, "ttr": round(ttr, 2), "novelty": round(novelty, 2),
            "pass": ok, "reasons": reasons}, ok, reasons


# ── 提案文件 I/O ──────────────────────────────────────────────
def _prop_path(root):
    return Path(root) / "pipeline" / ".kb_query_proposals.jsonl"


def _state_path(root):
    return Path(root) / "pipeline" / ".kb_query_state.json"


def _daily_count(root):
    st = _state_path(root)
    if not st.exists():
        return 0
    try:
        return json.loads(st.read_text(encoding="utf-8")).get("per_day", {}).get(_DATE, 0)
    except (json.JSONDecodeError, OSError):
        return 0


def _inc_daily(root):
    st = _state_path(root)
    st.parent.mkdir(parents=True, exist_ok=True)
    try:
        st_obj = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
    except (json.JSONDecodeError, OSError):
        st_obj = {}
    per = st_obj.setdefault("per_day", {})
    per[_DATE] = per.get(_DATE, 0) + 1
    st.write_text(json.dumps(st_obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id(root):
    p = _prop_path(root)
    n = (len(p.read_text(encoding="utf-8").splitlines())) if p.exists() else 0
    return f"q-{_DATE}-{n + 1:03d}"


def query(root: Union[str, Path], q: str, answer: Optional[str] = None) -> Dict[str, Any]:
    """处理一条查询：检索→综合→耐久性评估→(若达标)写提案。返回结果 dict。"""
    kb_rsi.root = root  # kb_rsi.metrics 内部引用全局 root（与 kb_engine/kb_fitness 同约定）
    scored = search(root, q)
    answer = answer or synthesize(scored, q)
    dur, dur_ok, reasons = durability(answer, root)
    grounded, m, gnote = _grounded(root)

    result = {"query": q, "sources": [n["rel"] for _s, n in scored],
              "n_sources": len(scored), "answer": answer,
              "durability": dur, "grounded": grounded, "grounding_note": gnote}

    if not dur_ok:
        result["verdict"] = "REJECT"
        result["why"] = "durability: " + "; ".join(reasons)
        return result
    if not grounded:
        result["verdict"] = "REJECT"
        result["why"] = "外接阀未通过(疑似自闭库，需注入真实外部知识后重试)"
        return result
    if _daily_count(root) >= MAX_PROPOSALS_PER_DAY:
        result["verdict"] = "REJECT"
        result["why"] = f"频率门: 今日已提议 {MAX_PROPOSALS_PER_DAY} 条上限"
        return result

    # 达标 → 写提案（不写库，人工在环）
    rec = {
        "id": _next_id(root), "ts": datetime.now().isoformat(timespec="seconds"),
        "query": q, "answer": answer, "sources": [n["rel"] for _s, n in scored],
        "importance": 0.5, "kind": "compiled",
        "scores": {"chars": dur["chars"], "ttr": dur["ttr"], "novelty": dur["novelty"],
                   "grounded": grounded},
    }
    _prop_path(root).parent.mkdir(parents=True, exist_ok=True)
    with open(_prop_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _inc_daily(root)
    result["verdict"] = "PROPOSE"
    result["id"] = rec["id"]
    # P2-2: 记演进日志
    try:
        import evolution_log
        evolution_log.append(root, "QUERY",
                             f"查询\"{q[:40]}\" → 提议存 1 条编译笔记",
                             detail=f"密度TTR {dur['ttr']} · 新异 {dur['novelty']:.2f} · 来源 {len(scored)} 篇 · 接地{'OK' if grounded else '否'}")
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return result


def _fm_block(fm):
    # 裸值写回：kb_rsi.load_note 解析 frontmatter 不去引号（与 kb_common 不同），
    # 给字符串加 json.dumps 引号会破坏 parse_date(created) 等下游解析，须与真实笔记同格式。
    def fmt(v):
        if isinstance(v, list):
            return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
        if isinstance(v, str):
            return v.replace("\n", " ")  # 换行会破坏 frontmatter 行结构
        return v
    items = "".join(f"{k}: {fmt(v)}\n" for k, v in fm.items())
    return "---\n" + items + "---\n"


def _write_note(root, rec):
    slug = _slug(rec.get("query", ""))
    rel = f"reference/query-{slug}.md"
    p = Path(root) / rel
    i = 2
    while p.exists():
        rel = f"reference/query-{slug}-{i}.md"
        p = Path(root) / rel
        i += 1
    Path(p).parent.mkdir(parents=True, exist_ok=True)  # reference/ 目录不存在则建
    today = _today()
    fm = {
        "domain": "查询综合", "category": "insight",
        "importance": rec.get("importance", 0.5),
        "created": today, "updated": today,
        "source": "query", "query": rec.get("query", ""),
        "sources": rec.get("sources", []), "kb_action": "keep",
    }
    body = f"# {rec.get('query', '')}\n\n{rec.get('answer', '')}\n\n"
    body += (f"> ⏺ 由 `kb_query` 从 {len(rec.get('sources', []))} 篇命中综合 "
             f"（{rec.get('ts', '')[:10]}）\n")
    body += "> 来源笔记：" + "、".join(f"`{s}`" for s in rec.get("sources", [])) + "\n"
    p.write_text(_fm_block(fm) + body, encoding="utf-8")
    return rel


def _git_commit(root, rel, msg):
    subprocess.run(["git", "-C", str(root), "add", "--", rel], capture_output=True)
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(root), "commit", "-m", msg], capture_output=True)


def apply(root: Union[str, Path], ids: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
    """人工确认后，把选定提案真正写为编译笔记（带 git checkpoint，可回滚）。"""
    p = _prop_path(root)
    if not p.exists():
        return {"written": [], "skipped": [], "note": "无待审提案"}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    pick = recs if (ids is None and force) else [r for r in recs if r.get("id") in (ids or set())]
    written, skipped, meta = [], [], {}
    for r in pick:
        rel = _write_note(root, r)
        _git_commit(root, rel, f"kb: 查询综合入库[{r.get('id')}] 「{str(r.get('query',''))[:24]}…」")
        written.append(r["id"]); meta[r["id"]] = rel
        # 从提案清单移除
    for r in recs:
        if r.get("id") in set(written):
            continue
        skipped.append(r["id"])
    # 重写提案文件（仅保留未写入的）
    open(_prop_path(root), "w", encoding="utf-8").close()
    try:
        import evolution_log
        evolution_log.append(root, "QUERY",
                             f"人工 apply 写入 {len(written)} 条综合笔记",
                             detail="、".join(f"{i}->{meta.get(i,'')}" for i in written) or "-")
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return {"written": written, "skipped": skipped, "note": f"写入 {len(written)} 条 · 剩余 {len(skipped)} 条待审"}


def status(root: Union[str, Path]) -> Dict[str, Any]:
    p = _prop_path(root)
    if not p.exists():
        return {"count": 0, "proposals": [], "daily_used": _daily_count(root)}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"count": len(recs), "daily_used": _daily_count(root), "proposals": recs}


def _render_status(root):
    st = status(root)
    L = ["# 🔎 查询→知识 回路状态", "",
         f"> 待审提案 **{st['count']}** 条 · 今日已用频率额度 {st['daily_used']}/{MAX_PROPOSALS_PER_DAY}", ""]
    if not st["proposals"]:
        L.append("✅ 无待审提案。")
        return "\n".join(L) + "\n"
    L += ["## ⏳ 待人工复核（`apply --id N` 或 `apply --all`）", ""]
    for r in st["proposals"]:
        L += [f"### `{r['id']}` — {r.get('query','')}",
              f"   - 来源 {len(r.get('sources',[]))} 篇 · 评分 密度{r['scores']['ttr']} 新异{r['scores']['novelty']}",
              f"   - 答案片段: {str(r.get('answer',''))[:160]}", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query"); q.add_argument("--root", default=kb_rsi.ROOT_DEFAULT)
    q.add_argument("--query", required=True)
    q.add_argument("--answer", default=None, help="已综合答案(省略则规则综合)")
    q.add_argument("--json", action="store_true")
    ap_ = sub.add_parser("apply"); ap_.add_argument("--root", default=kb_rsi.ROOT_DEFAULT)
    ap_.add_argument("--id", nargs="*", default=None, help="指定提案ID")
    ap_.add_argument("--all", action="store_true", help="写入全部待审提案")
    ap_.add_argument("--json", action="store_true")
    asu = sub.add_parser("status"); asu.add_argument("--root", default=kb_rsi.ROOT_DEFAULT)
    asu.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "query":
        r = query(Path(a.root), a.query, a.answer)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"🔎 查询\"{a.query[:40]}\"  | 命中 {r['n_sources']} 篇 · **{r['verdict']}**")
            if r["verdict"] == "PROPOSE":
                print(f"✅ 已提案 {r['id']}（人工在环，未写库）。→ apply --id {r['id']} 入库")
            else:
                print(f"⏭️ {r['why']}")
            print(f"📝 待审清单 → pipeline/.kb_query_proposals.jsonl  ({'apply --all' if r['verdict']!='PROPOSE' else ''})")
        return 0
    if a.cmd == "apply":
        force = bool(a.all)
        res = apply(Path(a.root), ids=None if force else a.id, force=force)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"📝 已写入 {len(res['written'])} 条综合笔记 → 剩余 {len(res['skipped'])} 条待审")
        return 0
    if a.cmd == "status":
        st = status(Path(a.root))
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(_render_status(Path(a.root)))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
