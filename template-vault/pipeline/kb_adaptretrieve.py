#!/usr/bin/env python3
# ============================================================
# kb_adaptretrieve.py —— L1 查询即反馈（反馈阶梯 · 实时回路）
# ------------------------------------------------------------
# 使命：让"每一次查询"都变成一次对现实的评估，并据此*只提议*修正检索——
#   把真实的"答不上/答错"信号喂回检索，而**擅自不改笔记**。这是反馈阶梯 Rungs
#   的第一级（最紧的实时回路 L1）。
#
#  三级能力：
#    record_query  —— 一次 query → 信号落 feedback_state（与 rag.py agent_hits 同格式）
#    propose       —— 从真实信号挖"检索缺口"：高相似却未命中的笔记 → 只产 *提议*
#    apply         —— 人工确认后落 .kb_retrieval_adjust.json（检索修正契约），git checkpoint 可回滚
#
#  铁律（与 kb_usage / kb_claim / rag.py 完全一致）：
#    · 默认只提议：proposals 只写 jsonl，绝不自动改检索/笔记
#    · 外部锚：信号=真实查询(agent_hits)，非自指打分
#    · 确定优先：核心相似度走离线 char-gram 余弦，不连 vLLM；embedding 可用才叠加
#    · 有界：相似度下限 + 总数上限 + (query,note) 去重
#    · raw/ 不改：提议只针对编译态笔记
#    · best-effort：无信号/无 LLM → 静默降级，绝不锁死流水线
#
#  用法:
#    python3 pipeline/kb_adaptretrieve.py capture --root R --query "..." [--hits a.md b.md] [--session s]
#    python3 pipeline/kb_adaptretrieve.py propose --root R [--sim 0.5] [--max 50]        # 只提议
#    python3 pipeline/kb_adaptretrieve.py apply   --root R [--id 1 2] [--all] [--force]   # 人工后写契约
#    python3 pipeline/kb_adaptretrieve.py status  --root R
# ============================================================
import argparse, json, os, re, sys, uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi  # 复用 collect/is_raw（编译态笔记）+ tokenize

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)

PROP_FILE = ".kb_adaptretrieve_proposals.jsonl"        # 瞬态提议（gitignore）
ADJUST_FILE = ".kb_retrieval_adjust.json"              # 检索修正契约（人工 apply 写，gitignore）

SIM_MIN = 0.50        # "高相似却未命中"的余弦下限（char-gram，与 kb_rsi 去重尺度同档）
MAX_PROPOSALS = 50    # 单次 propose 最多产出提议数（有界）
REQUERY_WINDOW = 3600  # 重问窗口（秒），判定"同一 query 短期再问"=前次没答上
_MAX_EVENTS = 500      # 状态内最多分析的事件数（有界）

_NORM = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)  # 归一：去标点/空白


# ── 工具：确定性离线 char-gram 余弦（不连 vLLM）───────────────────
def _ngrams(text: str, n: int = 2) -> List[str]:
    t = re.sub(r"\s+", " ", (text or "").lower())
    return [t[i:i + n] for i in range(len(t) - n + 1)] if len(t) >= n else []


def _cosine(a_text: str, b_text: str) -> float:
    """字符 bigram 归一化余弦（离线、确定、不连模型）。任一为空 → 0。0-1。"""
    if not a_text or not b_text:
        return 0.0
    ga, gb = Counter(_ngrams(a_text)), Counter(_ngrams(b_text))
    if not ga or not gb:
        return 0.0
    small, large = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
    dot = sum(c * large.get(k, 0) for k, c in small.items())
    na = sum(v * v for v in ga.values()) ** 0.5
    nb = sum(v * v for v in gb.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _norm_q(q: str) -> str:
    return _NORM.sub("", (q or "").lower())


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── ① record_query：一次 query → 信号落 feedback_state─────────────
def record_query(root: Union[str, Path], query: str, hits: Optional[List[str]] = None,
                 session: Optional[str] = None, satisfied: Optional[bool] = None
                 ) -> Dict[str, Any]:
    """把一次查询记为 per-query 事件 agent_hits（与 rag.py._record_hits 同格式）。
    命中空也记（供"无命中"检测）。best-effort：StateStore 失败 → 返回 {"recorded": False,...}。"""
    try:
        from state_manager import StateStore
    except ImportError:
        return {"recorded": False, "reason": "state_manager 导入失败"}
    if not (query and query.strip()):
        return {"recorded": False, "reason": "query 为空"}
    sp = StateStore(root)
    try:
        state = sp.load("feedback_state.json", {})
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        state = {}
    session = (session or ("q-" + uuid.uuid4().hex[:12]))[:24]
    ev = {"query": query.strip(),
          "hits": [str(h) for h in (hits or [])],
          "timestamp": _now_iso(), "weight": 0.3}
    if satisfied is not None:
        ev["satisfied"] = bool(satisfied)
    state.setdefault("agent_hits", {})[session] = ev
    try:
        sp.save("feedback_state.json", state)
        return {"recorded": True, "session": session,
                "n_agent_events": len(state.get("agent_hits") or {})}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {"recorded": False, "reason": "写入 feedback_state 失败"}


# ── 读状态里的 agent_hits 事件──────────────────────────────────
def _agent_events(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """读 feedback_state 的 agent_hits，退化为 {}（best-effort）。"""
    try:
        from state_manager import StateStore
        st = StateStore(root).load("feedback_state.json", {})
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ImportError):
        st = {}
    events = []
    for sess, ev in (st.get("agent_hits") or {}).items():
        if not isinstance(ev, dict):
            continue
        q = ev.get("query") or ""
        paths = [str(h) for h in (ev.get("hits") or []) if h]
        evs = {"session": str(sess), "query": q, "paths": paths,
               "satisfied": ev.get("satisfied"), "ts": ev.get("timestamp")}
        if q:
            events.append(evs)
    return events[:_MAX_EVENTS]


# ── ② propose：挖检索缺口 → 只产提议─────────────────────────────
def _suspect_failed_queries(root: Union[str, Path]) -> set:
    """复用 kb_usage.analyze 的 suspect_failed（再问=前次可能没答上）；退化空集。"""
    try:
        import kb_usage
        a = kb_usage.analyze(root, use_embedding=False)
    except (ImportError, OSError, ValueError):
        return set()
    return {_norm_q(s.get("query", "")) for s in a.get("suspect_failed", []) if s.get("query")}


def _index_notes(root: Union[str, Path]) -> List[Tuple[str, str]]:
    """编译态笔记 (rel, body) 索引，排除 raw/归档收件箱MOC。"""
    out = []
    for n in kb_rsi.collect(root):
        rel = n.get("rel")
        if not rel or kb_rsi.is_raw(rel) or kb_rsi.is_archive(rel) or kb_rsi.in_inbox(rel):
            continue
        body = (n.get("body") or "").strip()
        if body:
            out.append((rel, body))
    return out


def propose(root: Union[str, Path], sim_min: float = SIM_MIN,
            max_proposals: int = MAX_PROPOSALS) -> Dict[str, Any]:
    """从真实使用信号挖"检索缺口"，只产提议（写 jsonl，不碰笔记/检索）。返回汇总。"""
    events = _agent_events(root)
    suspect_q = _suspect_failed_queries(root)
    notes = _index_notes(root)

    proposals: List[Dict[str, Any]] = []
    seen = set()
    for ev in events:
        q = ev.get("query", "")
        nq = _norm_q(q)
        if not q:
            continue
        hits_set = {str(h) for h in ev.get("paths", [])}
        stop = False
        for rel, body in notes:
            if rel in hits_set:
                continue  # 已命中，不算缺口
            s = _cosine(q, body)
            if s < sim_min:
                continue
            key = (nq, rel)
            if key in seen:
                continue
            seen.add(key)
            is_req = nq in suspect_q
            issue = "requery_failure_missed" if is_req else "missed_retrieval"
            proposals.append({
                "query": q, "note": rel, "sim": round(s, 3), "issue": issue,
                "suggested": "boost",
                "reason": (f"笔记内容与 query 高相似({s:.2f}) 但检索未命中"
                           + (" · 该 query 窗口内被重问(前次疑未答上)" if is_req else "")),
            })
            if len(proposals) >= max_proposals:
                stop = True
                break  # 有界：累计达 max 立即停止（内层）
        if stop:
            break  # 已达上限 → 停止扫描后续事件

    # 确定性排序(先重问失败、再 sim 降序) → id 稳定
    proposals.sort(key=lambda p: (0 if p["issue"].startswith("requery") else 1, -p["sim"]))
    for i, p in enumerate(proposals, 1):
        p["id"] = i
        p["severity"] = "high" if p["issue"].startswith("requery") else "medium"

    # 写提案文件（全量重写，使 apply 看到最新）
    logdir = Path(root) / "pipeline"
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / PROP_FILE).write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in proposals) + ("\n" if proposals else ""),
        encoding="utf-8")

    req = sum(1 for p in proposals if p["issue"].startswith("requery"))
    return {"n_events": len(events), "n_notes": len(notes),
            "n_proposals": len(proposals), "n_requery": req,
            "proposals": proposals[:max_proposals]}


# ── ③ apply：人工确认后落检索修正契约─────────────────────────────
def _git_checkpoint_force(root: Union[str, Path], rel: str, msg: str) -> None:
    """force-add（即便 gitignore）再 commit → 让合成配置也有可回滚锚点。"""
    import subprocess
    subprocess.run(["git", "-C", str(root), "add", "--force", "--", rel], capture_output=True)
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(root), "commit", "-m", msg], capture_output=True)


def apply(root: Union[str, Path], ids: Optional[List[str]] = None,
          force: bool = False) -> Dict[str, Any]:
    """人工确认后，把选定提案合并进 .kb_retrieval_adjust.json（检索修正契约）。
    跳过 raw/ 与不存在的笔记；整次 apply 只做一次（原子）git checkpoint，可回滚。"""
    p = Path(root) / "pipeline" / PROP_FILE
    if not p.exists():
        return {"written": [], "skipped": [], "note": "无待审提议；先 run propose"}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    want = {str(i) for i in (ids or set())}  # 提案 id 落盘为 int，CLI 传串 → 统一转串比
    pick = recs if (ids is None and force) else [r for r in recs if str(r.get("id")) in want]

    written, skipped = [], []
    apath = Path(root) / "pipeline" / ADJUST_FILE
    existing, seen_keys = [], set()
    if apath.exists():
        try:
            for a in json.loads(apath.read_text(encoding="utf-8")).get("adjustments", []):
                existing.append(a); seen_keys.add((a.get("action"), a.get("note")))
        except (OSError, json.JSONDecodeError):
            existing = []

    for r in pick:
        rel, q = r.get("note"), r.get("query", "")
        if not rel or not q:
            skipped.append({"id": r.get("id"), "reason": "缺 note/query"}); continue
        relp = Path(root) / rel
        if not relp.is_file() or kb_rsi.is_raw(rel):
            skipped.append({"id": r.get("id"), "note": rel, "reason": "raw/ 不可变/文件不存在"}); continue
        key = (r.get("suggested", "boost"), rel)
        if key in seen_keys:
            skipped.append({"id": r.get("id"), "note": rel, "reason": "契约已含该项"}); continue
        rec = {"query": q, "action": r.get("suggested", "boost"), "note": rel,
               "sim": r.get("sim"), "issue": r.get("issue"), "reason": r.get("reason"),
               "from_proposal_id": r.get("id"), "captured": _now_iso()}
        existing.append(rec); seen_keys.add(key)
        written.append({"id": r.get("id"), "note": rel})

    if written:
        # 契约本身 gitignore → force-add 以便 checkpoint 可回滚；整次 apply 一次提交
        apath.write_text(json.dumps({"adjustments": existing, "updated": _now_iso()},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
        _git_checkpoint_force(root, str(Path("pipeline") / ADJUST_FILE),
                              f"kb: 检索修正入库 {len(written)} 条 [{','.join(str(w['id']) for w in written)}] boost")

    try:
        import evolution_log
        if written:
            evolution_log.append(root, "QUERY",
                                 f"L1 检索修正入库 {len(written)} 条",
                                 detail="、".join(f"{w['id']}->{w['note']}" for w in written))
    except (ImportError, OSError, AttributeError, TypeError):
        pass
    return {"written": written, "skipped": skipped,
            "note": f"写入 {len(written)} 条 · 剩余 {len(skipped)} 待审/跳过"}


# ── status / render──────────────────────────────────────────────
def status(root: Union[str, Path]) -> Dict[str, Any]:
    p = Path(root) / "pipeline" / PROP_FILE
    if not p.exists():
        return {"count": 0, "proposals": []}
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"count": len(recs), "proposals": recs}


def render(root: Union[str, Path], summary: Optional[Dict[str, Any]] = None) -> str:
    st = status(root)
    L = ["# 🔎 L1 查询即反馈 —— 检索修正提议（只读 · 交人工）", "",
         f"> 基于真实查询信号(agent_hits)挖出的检索缺口：**{st['count']}** 条。"
         "`apply --id N` 或 `apply --all` 确认后落检索修正契约。"]
    if not st["proposals"]:
        L += ["", "✅ 无检索缺口提议（或暂无使用信号：先 capture 灌信号 / 跑 rag query）。"]
        return "\n".join(L) + "\n"
    L += ["", "## ⚠️ 待人工复核（按优先级：重问失败 > sim 降序）", ""]
    for p in st["proposals"]:
        tag = "重问" if p["issue"].startswith("requery") else "漏检"
        L += [f"### {p['id']}. [{tag}/{p.get('severity')}] sim={p['sim']} — `{p['note']}`",
              f"   - query: {p['query']}",
              f"   - 说明: {p['reason']}", ""]
    L += ["> 注：相似度为离线 char-gram 余弦，代理“该笔记是否该被此 query 命中”；供人审。"]
    return "\n".join(L) + "\n"


# ── CLI──────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="kb_adaptretrieve: L1 查询即反馈（检索修正）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture"); cap.add_argument("--root", default=ROOT_DEFAULT)
    cap.add_argument("--query", required=True); cap.add_argument("--hits", nargs="*", default=None)
    cap.add_argument("--session", default=None); cap.add_argument("--satisfied",
        type=lambda x: x.lower() in ("1", "true", "yes"), default=None)

    pro = sub.add_parser("propose"); pro.add_argument("--root", default=ROOT_DEFAULT)
    pro.add_argument("--sim", type=float, default=SIM_MIN); pro.add_argument("--max", type=int, default=MAX_PROPOSALS)
    pro.add_argument("--json", action="store_true")

    ap_ = sub.add_parser("apply"); ap_.add_argument("--root", default=ROOT_DEFAULT)
    ap_.add_argument("--id", nargs="*", default=None); ap_.add_argument("--all", action="store_true")
    ap_.add_argument("--force", action="store_true", help="写全部待审提议")
    ap_.add_argument("--json", action="store_true")

    st = sub.add_parser("status"); st.add_argument("--root", default=ROOT_DEFAULT)
    st.add_argument("--json", action="store_true")

    args = ap.parse_args()
    root = Path(args.root)

    if args.cmd == "capture":
        print(json.dumps(record_query(root, args.query, args.hits, args.session, args.satisfied),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "propose":
        summary = propose(root, sim_min=args.sim, max_proposals=args.max)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(render(root, summary))
            print(f"📝 检索修正提议 → {Path(root)/'pipeline'/PROP_FILE}  ·  "
                  f"应用: apply --id N | apply --all")
    elif args.cmd == "apply":
        res = apply(root, ids=args.id, force=getattr(args, "all", False) or args.force)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else f"✅ {res['note']}  → {Path(root)/'pipeline'/ADJUST_FILE}")
    else:
        stt = status(root)
        print(json.dumps(stt, ensure_ascii=False, indent=2) if args.json else render(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
