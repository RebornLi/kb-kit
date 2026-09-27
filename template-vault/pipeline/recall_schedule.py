#!/usr/bin/env python3
# ============================================================
# recall_schedule.py —— 回忆调度引擎(成长 引擎④)
#   记忆间隔重复:按 importance 的衰减窗口(L2 14d / L3 30d / L4 180d)
#   + 递增稳定性(spaced repetition),算出今天"该回忆"的笔记,按
#   urgency = importance × (1 +  overdue/稳定性) 排序。
#   每条喂 rag.py query 做"检索浮层"核对(记忆是否还浮得上来)。
#   deck  :打印今日回忆 deck(默认,不改文件)
#   mark  :标记已回忆 → 推进稳定性(写前 checkpoint)
#   status: upcoming due 一览
# 状态: .kb/state/recall_state.json（运行时；兼容读取旧路径 pipeline/.recall_state.json）
# 用法:
#   python3 pipeline/recall_schedule.py deck      [--n 10] [--root R]
#   python3 pipeline/recall_schedule.py mark      [--deck N | --recalls "a,b"] [--root R]
#   python3 pipeline/recall_schedule.py status    [--root R]
# ============================================================
import argparse, os, re, sys, json, subprocess, datetime
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import (ROOT_DEFAULT, EXCLUDE, parse_frontmatter, tokenize,
                       iter_notes, parse_importance as _parse_importance)

STATE = ".recall_state.json"
IDX_DIR = "vector index"
IGNORE = EXCLUDE

# D3: 间隔策略配置化（importance → 衰减天数）
# (min_importance, interval_days, label)
INTERVAL_TABLE = [
    (0.9, 180, "L4 长期,稀疏重复"),
    (0.5, 30,  "L3 快取"),
    (0.3, 14,  "L2"),
    (0.0, 7,   "<0.3 频繁复核"),
]

# ── P1 Stage6: SM-2 式复习（interval/ease/reps）+ 负载均衡 ──
EASE_DEFAULT = 2.5
EASE_MIN = 1.3
MAX_INTERVAL = 365        # 间隔上限（天）
DAILY_CAP = 20            # 单日复习上限（防积压）
GRADE_Q = {"again": 1, "hard": 3, "good": 4, "easy": 5}


def review_update(interval, ease, reps, q):
    """SM-2 式更新：按评分调整 ease；间隔按 ease 增长，遗忘则缩短。

    首次回忆(reps 从 0 →1)保持种子间隔(来自 importance)；之后 interval *= ease。
    """
    ease = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ease = max(EASE_MIN, min(2.5, ease))
    if q < 3:
        reps = 0
        interval = max(1.0, interval * 0.5)   # 遗忘 → 缩短
    else:
        reps += 1
        if reps > 1:
            interval = interval * ease
    interval = max(1.0, min(interval, MAX_INTERVAL))
    return round(interval, 2), round(ease, 3), reps

try:
    import rag  # 复用 rag.py 的分词/_vec/_cos,避免每篇 shell out
except (ImportError, ModuleNotFoundError, OSError):
    rag = None


def load_meta(path: Path) -> Tuple[Dict[str, Any], str, str]:
    """返回 (fm, text, body) 三元组，兼容本模块原有调用。"""
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    return fm, text, body


# iter_notes 已迁移至 kb_common.iter_notes（单一权威源）


def base_interval(imp: float) -> int:
    # importance → 衰减窗口(与 memory-management.md 一致)，由 INTERVAL_TABLE 配置
    for threshold, days, _label in INTERVAL_TABLE:
        if imp >= threshold:
            return days
    return INTERVAL_TABLE[-1][1]


def load_state(root: Union[str, Path]) -> Dict[str, Any]:
    """加载回忆状态：优先 StateStore(.kb/state/，原子写)，回退旧路径 pipeline/.recall_state.json。"""
    try:
        from state_manager import StateStore
        store = StateStore(root)
        if store.exists("recall_state.json"):
            return store.load("recall_state.json", {})
        legacy = Path(root) / "pipeline" / STATE
        if legacy.exists():
            return json.loads(legacy.read_text(encoding="utf-8"))
        return {}
    except ImportError:
        sp = Path(root) / "pipeline" / STATE
        return json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}


def save_state(root: Union[str, Path], st: Dict[str, Any]) -> None:
    try:
        from state_manager import StateStore
        StateStore(root).save("recall_state.json", st)
    except ImportError:
        sp = Path(root) / "pipeline" / STATE
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def load_index(root: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """加载 rag 索引(rel2vec + vocab/idf);无索引返回 None。
    复用 rag.load_index 兼容旧索引（无 version 字段），版本不匹配时打印告警。"""
    idx = Path(root) / IDX_DIR / "df_idf.json"
    if rag is None:
        return None
    payload, ver = rag.load_index(idx)
    if payload is None:
        return None
    if ver < rag.IDX_VERSION:
        print(f"⚠️ 索引格式旧（version {ver} < {rag.IDX_VERSION}），建议跑 `rag.py index` 重建；当前仍可回忆。", file=sys.stderr)
    elif ver > rag.IDX_VERSION:
        print(f"⚠️ 索引版本 {ver} 高于当前代码支持 {rag.IDX_VERSION}，建议升级 kb-kit 或跑 `rag.py index` 重建；当前仍可回忆。", file=sys.stderr)
    rel2vec = {r: rag._vec(payload["tf"][r], payload["vocab"], payload["idf"])
               for r in payload["tf"]}
    return {"rel2vec": rel2vec, "vocab": payload["vocab"], "idf": payload["idf"]}


def retrievable(idx: Optional[Dict[str, Any]], rel: str, summary: str) -> bool:
    """进程内检索核对:以 summary 为查询,笔记是否命中顶层。"""
    if idx is None or not summary or rel not in idx["rel2vec"]:
        return False
    qv = rag._vec(Counter(rag.tokenize(summary)), idx["vocab"], idx["idf"])
    best_rel, best = None, -1.0
    for r, v in idx["rel2vec"].items():
        sc = rag._cos(v, qv)
        if sc > best:
            best, best_rel = sc, r
    return best_rel == rel


def build(root: Union[str, Path], deck_n: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    idx = load_index(root)
    state = load_state(root)
    now = datetime.date.today()
    notes, due_list = [], []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, text, body = load_meta(p)
        if fm.get("kb_action") == "retire":
            continue
        imp = _parse_importance(fm.get("importance"))
        if imp < 0.1:
            continue  # 无 importance 的报表/产物不参与回忆(由 intake triage 处理)
        base = base_interval(imp)
        st = state.get(rel) or {}
        last = datetime.date.fromisoformat(
            st.get("last_seen") or fm.get("created", "1970-01-01")[:10])
        interval = float(st.get("interval", st.get("stability", base)))
        ease = float(st.get("ease", EASE_DEFAULT))
        reps = int(st.get("reps", st.get("recalls", 0)))
        due = last + datetime.timedelta(days=max(1, int(round(interval))))
        overdue = (now - due).days
        urgency = imp * (1 + max(0, overdue) / max(interval, 1))
        summary = fm.get("kb_summary") or body.strip().splitlines()[0].strip()[:60]
        notes.append({"rel": rel, "imp": imp, "base": base, "interval": interval,
                      "ease": ease, "reps": reps, "due": due.isoformat(),
                      "overdue": overdue, "urgency": round(urgency, 3),
                      "summary": summary, "retrievable": retrievable(idx, rel, summary),
                      "domain": fm.get("domain", "-")})
    # deck = 已到期(overdue≥0),按 urgency; status = 全部到期按 due 升序
    due_list = sorted(notes, key=lambda n: (n["due"], -n["urgency"]))
    deck = sorted([n for n in notes if n["overdue"] >= 0],
                  key=lambda n: (-n["urgency"], n["rel"]))[:max(0, min(deck_n, DAILY_CAP))]
    return notes, deck, due_list


def report_deck(deck: List[Dict[str, Any]]) -> str:
    if not deck:
        return "🎴 今日回忆 deck 为空(无到期笔记)\n"
    out = ["# 🎴 今日回忆 Deck(间隔重复)", "",
           f"> {len(deck)} 篇到期 · 按 urgency=importance×(1+逾期/稳定性) 排序",
           "> 先凭记忆复述要点,再与 rag 检索核对(浮到顶层=记忆仍强)"]
    for i, n in enumerate(deck, 1):
        flag = "✅ 检索浮层✓" if n["retrievable"] else "⚠️ 检索未置顶"
        overdue = f"逾期{n['overdue']}天" if n["overdue"] > 0 else f"到期{1+n['overdue']}天"
        out += [f"\n### {i}. {n['rel']}  [{n['imp']:.2f} · {flag}]",
                f"- {overdue} · 间隔{n['interval']:.0f}天 · ease{n['ease']:.2f} · 已复习{n['reps']}次",
                f"- 🧠 回忆测试:{n['summary']}",
                f"- 📎 提示:回忆本笔记要点后 `rag.py query \"{n['summary']}\"` 核对"]
    return "\n".join(out) + "\n"


def report_status(due_list: List[Dict[str, Any]]) -> str:
    if not due_list:
        return "📅 无到期笔记\n"
    out = ["# 📅 到期排期(按 due 升序)", "", f"> {len(due_list)} 篇待回忆"]
    for n in due_list[:25]:
        tag = "已逾期" if n["overdue"] > 0 else "将到期"
        out += [f"- `{n['rel']}` [{n['imp']:.2f}] · {n['due']} · {tag}"
                 + (f" · 逾期{n['overdue']}天" if n['overdue'] > 0 else "")]
    return "\n".join(out) + "\n"


def mark(root: Union[str, Path], deck_n: int, rels: Optional[str], grade: str = "good") -> int:
    """标记已复习（SM-2 式）：按评分调整 ease，更新间隔与复习次数。

    grade ∈ again/hard/good/easy（映射 SM-2 q=1/3/4/5）。
    """
    state = load_state(root)
    _, deck, _ = build(root, 999)
    picked = [r for r in rels.split(",")] if rels else [n["rel"] for n in deck[:deck_n]]
    q = GRADE_Q.get(grade, 4)
    today = datetime.date.today().isoformat()
    advanced = []
    for rel in picked:
        st = state.setdefault(rel, {})
        imp = 0.5
        try:
            fm, _text, _body = load_meta(Path(root) / rel)
            imp = _parse_importance(fm.get("importance"))
        except OSError:
            pass
        interval = float(st.get("interval", st.get("stability", base_interval(imp))))
        ease = float(st.get("ease", EASE_DEFAULT))
        reps = int(st.get("reps", st.get("recalls", 0)))
        new_interval, new_ease, new_reps = review_update(interval, ease, reps, q)
        st.pop("stability", None)   # 迁移旧字段
        st.pop("recalls", None)
        st.update({"last_seen": today, "interval": new_interval,
                   "ease": new_ease, "reps": new_reps})
        if q < 3:
            st["lapses"] = int(st.get("lapses", 0)) + 1
        advanced.append((rel, new_interval, new_ease))
    save_state(root, state)
    print(f"✅ 已复习 {len(advanced)} 篇（grade={grade}）→ 间隔/ease 更新：\n" +
          "\n".join(f"- {r}  间隔{iv:.0f}天 ease{e:.2f}" for r, iv, e in advanced[:20]))
    print("   说明:复习状态存于 .kb/state/recall_state.json(运行时,可手改/删除)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    dd = sub.add_parser("deck"); dd.add_argument("--n", type=int, default=10); dd.add_argument("--root", default=ROOT_DEFAULT)
    mk = sub.add_parser("mark"); mk.add_argument("--deck", type=int, default=10)
    mk.add_argument("--recalls", default=None); mk.add_argument("--root", default=ROOT_DEFAULT)
    mk.add_argument("--grade", choices=list(GRADE_Q), default="good",
                    help="复习评分（SM-2）：again/hard/good/easy")
    su = sub.add_parser("status"); su.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()
    if args.cmd == "deck":
        _, deck, _ = build(args.root, args.n)
        rep = report_deck(deck)
        (Path(args.root) / "recall_deck.md").write_text(rep, encoding="utf-8")
        print(f"🎴  Recall deck: {len(deck)} 篇  已写入 recall_deck.md")
        print("\n" + rep)
        return 0
    if args.cmd == "mark":
        return mark(args.root, args.deck, args.recalls, args.grade)
    if args.cmd == "status":
        _, _, due = build(args.root, 0)
        rep = report_status(due)
        (Path(args.root) / "recall_schedule.md").write_text(rep, encoding="utf-8")
        print("📅  Recall 排期已写入 recall_schedule.md")
        print("\n" + rep)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
