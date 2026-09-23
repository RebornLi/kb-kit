#!/usr/bin/env python3
# ============================================================
# kb_usage.py —— 真实使用结果捕获（方案 C / Layer 2 · 只读 PoC）
# ------------------------------------------------------------
# 目标：把“现实怎么用这份知识”变成可测信号 —— 查询成功率 / 再问率。
#
#  适应度闸（Layer 1）问的是“声明有没有被现实推翻”；
#  Layer 2 问的是“知识流进库后，真有人问到它、并且答上了吗”。
#  真实信号藏在 query 日志里：一次“相似 query 短期重问”几乎等价于
#  “前一次回答没满足用户”——零用户成本就能挖到的负反馈。
#
#  数据源：feedback_state.json 的 per-query 事件 agent_hits：
#     { "<session>": {"query":..., "hits":[{"path":...}], "timestamp":..., "weight":0.3}}
#  由 rag.py:_record_hits 在 --session 非空时写入。若无 agent_hits，
#  退化为 hits/run_queries 的基础计数（能数“多少查询、命中多少篇”，
#  但拿得到 query 文本才能算再问率）。
#
#  铁律（照搬 Layer 1，见 kb_claim.py）：
#    ① 默认只读 —— analyze/report/stats 只读 feedback_state，绝不改 rag.py 检索路径；
#    ② best-effort —— 无事件 → status "no_events"，绝不锁死/报错；
#    ③ embedding 可选 —— 默认精确匹配；embedding 可用才叠加余弦相似（>=阈值）。
#
#  ⚠️ 唯一写入：`mark` 是 opt-in（不自动跑），把“窗口内被重复询问”的首次命中笔记
#    回写 record_hit(useful=True) 提升 importance。语义是“用户反复回来问=高相关刚需”，
#    不是“前次答得好”（re-query 其实更可能是前次没答上），false-positive 风险 → 手动确认再用。
#
#  用法:
#    python3 pipeline/kb_usage.py analyze --root R [--window 3600] [--sim 0.55]   # 只读
#    python3 pipeline/kb_usage.py report --root R                                 # 只读
#    python3 pipeline/kb_usage.py stats --root R                                  # 只读
#    python3 pipeline/kb_usage.py mark --root R                                   # [opt-in 写] 提升被反复问中的命中
# ============================================================
import argparse, json, re, sys, threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_embed

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)
STATE_FILE = "feedback_state.json"

# 分析参数（均为只读分析的启发阈值，非调优目标）
_WINDOW_SECONDS = 3600          # 判定“重问”的时间窗口（1 小时）
_SIM_THRESHOLD = 0.55           # embedding 余弦相似判定“同义重问”的阈值
_MAX_EVENTS = 200               # 单份状态最多分析的事件数（有界）

_Q_NORM = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)   # 归一：去标点/空白
_TS_FMTS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M")


# ── 加载事件 ────────────────────────────────────────────────
STATE_FILE = "feedback_state.json"         # 存于 .kb/state/feedback_state.json（StateStore）


def _load_state(root):
    """读 feedback_state（StateStore，缺失降级直读）。返回 (dict, exists)。"""
    try:
        from state_manager import StateStore
        ss = StateStore(root)
        if ss.exists(STATE_FILE):
            try:
                return ss.load(STATE_FILE, {}), True
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
    except (ImportError, OSError, TypeError):
        pass
    sp = Path(root) / ".kb" / "state" / STATE_FILE
    try:
        if not sp.exists():
            return {}, False
        return json.loads(sp.read_text(encoding="utf-8")), True
    except (json.JSONDecodeError, OSError):
        return {}, False


def _to_epoch(iso):
    """宽松解析 ISO 时间戳 → datetime；失败返回 None。"""
    if not iso:
        return None
    # 剥时区后缀：'Z' 或 '+HH:MM'
    s = iso.replace("Z", "").split("+", 1)[0]
    for fmt in _TS_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _events_from_agent_hits(agent_hits):
    """agent_hits(dict session→event) → 事件列表（按时间升序）。"""
    evs = []
    for sess, ev in (agent_hits or {}).items():
        if not isinstance(ev, dict):
            continue
        q = ev.get("query") or ""
        # 兼容两种 hits 格式：字符串路径列表（rag.py 现状）或 {"path":...} 列表
        paths = []
        for h in (ev.get("hits") or []):
            if isinstance(h, str):
                if h:
                    paths.append(h)
            elif isinstance(h, dict) and h.get("path"):
                paths.append(h["path"])
        ts = _to_epoch(ev.get("timestamp"))
        if not q and not paths and ts is None:
            continue
        evs.append({"session": str(sess), "query": q, "paths": paths,
                    "n_hits": len(paths), "ts": ts,
                    "epoch": ts.timestamp() if ts else None})
    evs.sort(key=lambda e: (e["epoch"] is None, e["epoch"] or 0.0))
    return evs[:_MAX_EVENTS]


# ── embedding（best-effort）─────────────────────────────────
_EMB_CACHE = {}

# 注：_vec / _cos / _norm 保留本地实现——与 kb_common.char_vec/cos/norm 语义完全不同：
#   本地 _vec 返回 kb_embed embedding 稠密向量（非字符稀疏向量）；
#   本地 _cos 对 list 稠密向量算余弦（非 dict 稀疏向量点积）；
#   本地 _norm 做文本归一化去标点（非 L2 向量归一化）。


def _vec(text):
    """归一化向量；不可用返回 None（调用方据此退化为精确匹配）。"""
    if not text or not text.strip():
        return None
    k = text.strip()
    if k in _EMB_CACHE:
        return _EMB_CACHE[k]
    if not kb_embed.available():
        return None
    try:
        v = kb_embed.embed(k)
        if v:
            _EMB_CACHE[k] = v
            return v
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return None


def _cos(a, b):
    if a is None or b is None:
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return None
    return dot / (na * nb)


def _norm(q):
    return re.sub(_Q_NORM, "", q).lower()


_REQUERY_PROMPT = (
    "你是查询质量分析器。判断用户【重复提问】是否意味着上一次回答不满意/没答上。"
    "上一次问题：{prev_query}\n上一次给出的答案近似（命中笔记片段）：\n{prev_answer}\n"
    "用户接下来又问：{requery}\n"
    "前次与本次问题的语义相似度(sim)：{sim}（1.0=字面几乎相同）\n"
    "只返回 JSON：{{\"verdict\":\"failure|confirmation|补充\" , \"confidence\":0-1数字, "
    "\"reason\":\"一句话\"}}。"
    "failure=前次答得不满意所以重问；confirmation=只是换词确认/补充细节（不算失败）。"
)
_LLM_REQUERY_CACHE = {}
_LLM_REQUERY_LOCK = threading.Lock()  # 并发判定（见 analyze）保护缓存读写


def _load_snippet(root, rel, cap=200):
    """加载命中笔记开头片段，作为"前次回答近似"（供 LLM 判定）。失败返回 None。"""
    try:
        p = Path(root) / rel
        if not p.exists():
            return None
        txt = p.read_text(encoding="utf-8")[:cap]
        return re.sub(r"\s+", " ", txt).strip()
    except OSError:
        return None


def _claim_chat(messages):
    """复用 kb_claim._chat 接线（读 ORNITH_BASE_URL/KEY，/chat/completions，
    端点不可达静默降级）。返回 (content 或 None, reason)。"""
    try:
        import kb_claim
    except (ImportError, ModuleNotFoundError):
        return None, "kb_claim 导入失败"
    return kb_claim._chat(messages)


def _judge_requery(root, prev_query, prev_paths, requery, sim):
    """LLM 判定「本次重问是否意味着前次没答上」。
    返回 (verdict, confidence, detail)：verdict ∈ {failure, confirmation, 补充, None}。
    无 ORNITH / 端点不可达 → 静默 (None, None, reason)（best-effort 降级）。"""
    snippets = [s for s in (_load_snippet(root, p) for p in prev_paths) if s]
    prev_answer = "\n".join(snippets[:2]) if snippets else "（无命中笔记内容）"
    key = (prev_query, tuple(prev_paths), requery)
    with _LLM_REQUERY_LOCK:
        hit = _LLM_REQUERY_CACHE.get(key)
    if hit is not None:
        return hit
    messages = [
        {"role": "system", "content": "你是查询质量分析器，只输出 JSON。"},
        {"role": "user", "content": _REQUERY_PROMPT.format(
            prev_query=prev_query, prev_answer=prev_answer[:800],
            requery=requery, sim=sim)},
    ]
    content, reason = _claim_chat(messages)
    if content is None:
        result = (None, None, reason)
    else:
        try:
            j = _parse_json(content)
        except (json.JSONDecodeError, ValueError, TypeError):
            j = {}
        if j is None:
            # _parse_json 对空串/无法解析内容返回 None（非异常）；LLM 吐非 JSON 时
            # 视为"无 verdict"，静默降级为 doubt，绝不让真路径崩在这里（best-effort 铁律）。
            j = {}
        verdict = str(j.get("verdict", "")).strip()
        if verdict not in ("failure", "confirmation", "补充"):
            verdict = "doubtful"
        try:
            conf = float(j.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        result = (verdict, round(conf, 3), j.get("reason", ""))
    with _LLM_REQUERY_LOCK:
        if len(_LLM_REQUERY_CACHE) < 128:
            _LLM_REQUERY_CACHE[key] = result
    return result


def _parse_json(s):
    """tolerant JSON 解析（允许 markdown fence，照搬 kb_claim 的容错）。"""
    if not s:
        return None
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\n?", "", s)
        s = re.sub(r"\n?```+\s*$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i = min([k for k in (s.find("{"), s.find("[")) if k >= 0], default=-1)
        if i < 0:
            return None
        try:
            return json.loads(s[i:])
        except json.JSONDecodeError:
            return None


def _llm_available():
    """vLLM 是否接通（chat/embed 共用 ORNITH_BASE_URL，用 embedding 探测作代理预检）。
    无 ORNITH 时 kb_embed.available() 安全返回 False → LLM 层整体跳过。"""
    try:
        return bool(kb_embed.available())
    except (OSError, ValueError, TypeError, AttributeError):
        return False


# ── 分析核心 ────────────────────────────────────────────────
def analyze(root: Union[str, Path], window: int = _WINDOW_SECONDS,
            sim_threshold: float = _SIM_THRESHOLD,
            use_embedding: bool = True, use_llm: bool = True) -> Dict[str, Any]:
    """分析真实使用信号。返回扁平 dict（供 report / 他处 / fitness 复用）。
    use_embedding=False → 仅精确匹配（确定、快、不连 vLLM，供 fitness 用）。
    use_llm=False → 跳过 LLM「前次失败」判定（确定路径，供 fitness 用）；
      接通时用 kb_claim._chat 判「重问是因前次没答上还是仅换词确认」。"""
    st, exists = _load_state(root)
    events = _events_from_agent_hits(st.get("agent_hits"))

    if not events:
        # 退化：hits/run_queries 仍在，只能给基础计数
        nq = st.get("run_queries")
        n_hits_notes = len(st.get("hits") or {})
        return {
            "status": "no_events",
            "note": ("无 per-query 事件(agent_hits 空)。命中计数: "
                     f"{n_hits_notes} 篇笔记被命中, run_queries={nq}。"
                     "启用 rag.py query --session 后本工具可算再问率。"),
            "has_agent_events": False, "n_queries": nq, "n_notes_hit": n_hits_notes,
            "requery_rate": None, "query_success_proxy": None, "use_embedding": False,
            "suspect_failed_count": 0, "suspect_failed": [],
        }

    use_emb = kb_embed.available() if use_embedding else False
    queries = [_norm(e["query"]) for e in events if e["query"]]
    distinct = len(set(queries)) if queries else 0

    suspect = []
    n_requeries = 0
    for i, e in enumerate(events):
        if e["epoch"] is None:
            continue
        for j in range(i + 1, len(events)):
            o = events[j]["epoch"]
            if o is None or o - e["epoch"] > window:
                break
            same_q = bool(e["query"]) and _norm(e["query"]) == _norm(events[j]["query"])
            sim = None
            if not same_q and use_emb:
                sim = _cos(_vec(e["query"]), _vec(events[j]["query"]))
            if same_q or (sim is not None and sim >= sim_threshold):
                n_requeries += 1
                at = e["ts"].isoformat() if isinstance(e["ts"], datetime) else None
                suspect.append({"query": e["query"], "paths": e["paths"], "at": at,
                                "by": events[j]["session"],
                                "by_query": events[j]["query"],
                                "sim": round(sim, 3) if sim is not None else 1.0})
                break

    # LLM 判定「前次失败」（best-effort；vLLM 未接通/不可用时整体跳过，退回启发式）
    use_llm_out = False
    llm_verdicts = Counter()
    if use_llm:
        use_llm_out = _llm_available()
        if use_llm_out:
            # 联网确认解法：逐个串行调真 chat LLM 过慢（~40s/次，本仓库 5 个 suspect≈200s→首次超时）。
            # 判定彼此独立，改为有界并发（不 flood vLLM）+ 复用 _LLM_REQUERY_CACHE 缓存；
            # 单次判定仍走 kb_claim._chat 原接线，失败静默降级为 None（非崩溃）。
            workers = min(8, max(2, len(suspect)))
            with ThreadPoolExecutor(max_workers=workers) as _ex:
                _results = list(_ex.map(
                    lambda s: _judge_requery(root, s["query"], s["paths"],
                                             s.get("by_query", ""), s["sim"]),
                    suspect))
            for s, (v, conf, _res) in zip(suspect, _results):
                s["llm_verdict"] = v
                s["llm_conf"] = conf
                if v:
                    llm_verdicts[v] += 1

    n = len(events)
    requery_rate = round(n_requeries / n, 3) if n else None
    return {
        "status": "ok",
        "has_agent_events": True,
        "n_queries": n,
        "distinct_queries": distinct,
        "requery_rate": requery_rate,
        "query_success_proxy": round(1.0 - requery_rate, 3) if requery_rate is not None else None,
        "use_embedding": use_emb,
        "use_llm": use_llm_out,
        "llm_verdicts": dict(llm_verdicts),
        "window_seconds": window,
        "sim_threshold": sim_threshold,
        "suspect_failed_count": len(suspect),
        "suspect_failed": suspect[:20],
    }


# ── 展示 ────────────────────────────────────────────────────
def report(root: Union[str, Path]) -> str:
    a = analyze(root)
    if a["status"] == "no_events":
        return ("# 🔍 RSI 真实使用结果（方案 C / Layer 2 · 只读）\n\n"
                f"⚠️ {a['note']}\n"
                "- 解读：需 rag.py query --session 写入 per-query 事件后，方可算再问率。")
    a_s = a["suspect_failed"]
    L = ["# 🔍 RSI 真实使用结果（方案 C / Layer 2 · 只读）", "",
         f"总查询事件 {a['n_queries']} · 去重不同查询 {a['distinct_queries']} · "
         f"{'embedding' if a['use_embedding'] else '精确匹配(embedding未接通)'}",
         f"- 再问率(requery_rate)：{a['requery_rate']}  （越接近0越好）",
         f"- 查询成功率代理(query_success_proxy)：{a['query_success_proxy']}  "
         "(≈ 1 − 再问率，粗略）",
         f"- 疑似失败查询（窗口内被重问，前次可能没答上）：{a['suspect_failed_count']}"]
    if a_s:
        L.append("- 样例：")
        for s in a_s[:8]:
            llm = f" · LLM判:{s.get('llm_verdict')}" if s.get("llm_verdict") else ""
            L.append(f"    • {s['query']!r}  ← {s['by']} ({s['sim']}){llm}")
    if a.get("use_llm"):
        vd = a.get("llm_verdicts", {})
        L.append(f"- LLM 判定「重问=前次失败」分布：{vd or '（暂无 suspect）'}"
                 "（failure=前次没答上；confirmation=仅换词确认；补充=追加细节）")
    L += ["- 解读：再问率持续走高 = 知识没真答上用户的问题，适应度闸应打折（呼应 P0-1 接地阀）。"
          "此信号只读、best-effort，不进 fitness，仅作审计。"]
    return "\n".join(L) + "\n"


def stats(root: Union[str, Path]) -> str:
    a = analyze(root)
    if a["status"] == "no_events":
        nq = a.get("n_queries")
        n_notes = a.get("n_notes_hit", 0)
        return (f"usage: {nq or 0} 次查询 · {n_notes} 篇命中；"
                "无 per-query 事件，再问率待 --session 接入")
    return (f"usage: {a['n_queries']} 查询事件 · 再问率 {a['requery_rate']} · "
            f"成功率代理 {a['query_success_proxy']} · 疑似失败 {a['suspect_failed_count']}")


def _already_useful(root):
    """已显式 useful 的命中路径集合（去重用，避免重复回写）。"""
    try:
        st, _ = _load_state(root)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        st = {}
    return {fb.get("hit") for fb in st.get("explicit_feedback", [])
            if fb.get("useful") and fb.get("hit")}


def mark(root: Union[str, Path], window: int = _WINDOW_SECONDS) -> Dict[str, Any]:
    """自动 useful（opt-in，非自动跑）：窗口内被重复询问的首次命中笔记，记为
    record_hit(useful=True)。语义：用户反复回来问同一主题 = 高相关/刚需，命中笔记
    值得提升 importance（注意：这是“再互动=相关”启发式，非“前次答得好”；false-positive
    风险，故只 opt-in）。跳过 raw/（record_hit 隔）与已标记项。返回汇总 dict。"""
    import feedback_loop as fl
    already = _already_useful(root)
    a = analyze(root, window=window, use_embedding=False)
    marked = already_n = skipped = 0
    for s in a.get("suspect_failed", []):
        for p in s.get("paths", []):
            if not p:
                continue
            if p in already:
                already_n += 1
                continue
            try:
                delta = fl.record_hit(root, s["query"], p, True)
            except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError):
                skipped += 1
                continue
            if delta > 0:
                marked += 1
                already.add(p)
            else:
                skipped += 1
    return {"marked": marked, "already_marked": already_n, "skipped": skipped,
            "requeries": a.get("suspect_failed_count", 0),
            "note": "opt-in：把“反复被问”的主题命中提升 importance；re-query≠前次答好的反向证据，谨慎用。"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="kb_usage: 真实使用结果捕获（方案 C/Layer 2）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "report", "stats", "mark"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=ROOT_DEFAULT)
        p.add_argument("--window", type=int, default=_WINDOW_SECONDS)
        p.add_argument("--sim", type=float, default=_SIM_THRESHOLD)
    args = ap.parse_args()

    if args.cmd == "analyze":
        print(json.dumps(analyze(args.root, args.window, args.sim), ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print(report(args.root))
    elif args.cmd == "mark":
        print(json.dumps(mark(args.root), ensure_ascii=False, indent=2))
    else:
        print(stats(args.root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
