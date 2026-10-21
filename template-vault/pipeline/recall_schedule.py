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
from kb_io import ROOT_DEFAULT, EXCLUDE, parse_frontmatter, iter_notes
from kb_metautil import parse_importance as _parse_importance
from kb_text import tokenize

# 旧位置（只读回退）；规范位置是 <vault>/.kb/state/recall_state.json
_LEGACY_PATHS = ("pipeline/.recall_state.json", ".recall_state.json")
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
# ⚠️ 评分口径（**已从 SM-2 切到 FSRS**，2026-10-16）：
#   SM-2 用 q=1/3/4/5；FSRS 只认 **1/2/3/4**（again/hard/good/easy）。之前这里留过注释
#   钉住"迁移 FSRS 时必须同时改这里"—— 现在改了模型，所以一并改口径。
#   不一起改的后果：难度 D 会朝**反方向**更新（hard(2) 被当成 SM-2 的 3 反而算"及格"）。
GRADE_Q = {"again": 1, "hard": 2, "good": 3, "easy": 4}
GRADE_Q_SM2_LEGACY = {"again": 1, "hard": 3, "good": 4, "easy": 5}   # 仅供历史数据对账


def review_update(interval, ease, reps, q, elapsed_days=None):
    """**FSRS-6** 更新（原 SM-2 版保留在下方 `_review_update_sm2` 供对账）。

    返回值签名不变 `(interval, ease, reps)`，但语义映射为：
      · interval ← FSRS 的 `due_in_days`（都是"到 90% 可提取的天数"，同尺度）
      · ease     ← 由难度 D 反推的等效易度（便于旧字段与旧报表继续可读）
      · reps     ← 复习次数（内部维护 D/S）

    FSRS 的稳定性 S **直接就是**现有 interval（r=0.9 时 I(S,0.9)=S），所以**无迁移成本**。
    """
    import fsrs_core as F
    state = {"stability": float(interval or 0.0), "reps": int(reps or 0)}
    # 难度从 ease 反解（首次无历史时用 SM-2 反解公式；有历史则由 FSRS 自己维护）
    _s, d0 = F.memory_state_from_sm2(float(interval or 1.0), float(ease or EASE_DEFAULT))
    state["difficulty"] = d0
    elapsed = float(elapsed_days) if elapsed_days is not None else float(interval or 0.0)
    out = F.review(state, int(q), elapsed)
    # 等效 ease：D 越小越"容易"（1↔2.9, 10↔1.3 线性映射，仅为兼容旧报表）
    ease_eq = round(2.9 - (float(out["difficulty"]) - 1.0) * (1.6 / 9.0), 3)
    interval_out = max(1.0, min(float(out["due_in_days"]), float(MAX_INTERVAL)))
    return round(interval_out, 2), ease_eq, int(out["reps"])


def _review_update_sm2(interval, ease, reps, q):
    """旧 SM-2 实现（留档：换 FSRS 前的行为，便于对照与回滚判断）。"""
    ease = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ease = max(EASE_MIN, min(2.5, ease))
    if q < 3:
        reps = 0
        interval = max(1.0, interval * 0.5)
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
        from state_manager import read_state_path
        lg = read_state_path(root, "recall_state.json", legacy=_LEGACY_PATHS)
        return json.loads(lg.read_text(encoding="utf-8")) if lg else {}
    except ImportError:
        from state_manager import read_json
        return read_json(root, "recall_state.json", legacy=_LEGACY_PATHS, default={})


def save_state(root: Union[str, Path], st: Dict[str, Any]) -> None:
    try:
        from state_manager import StateStore
        StateStore(root).save("recall_state.json", st)
    except ImportError:
        from state_manager import write_json
        write_json(root, "recall_state.json", st)


def load_index(root: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """加载 rag 索引(rel2vec + vocab/idf);无索引返回 None。
    复用 rag.load_index 兼容旧索引（无 version 字段），版本不匹配时打印告警。"""
    if rag is None:
        return None
    # 传目录让 rag.load_index 自己解析（pickle 优先、回退旧 json）
    payload, ver = rag.load_index(Path(root) / IDX_DIR)
    if payload is None:
        return None
    if ver < rag.IDX_VERSION:
        print(f"⚠️ 索引格式旧（version {ver} < {rag.IDX_VERSION}），建议跑 `rag.py index` 重建；当前仍可回忆。", file=sys.stderr)
    elif ver > rag.IDX_VERSION:
        print(f"⚠️ 索引版本 {ver} 高于当前代码支持 {rag.IDX_VERSION}，建议升级 kb-kit 或跑 `rag.py index` 重建；当前仍可回忆。", file=sys.stderr)
    # v7 起索引不再写 tf → 由 rag 统一提供文档向量（postings 一趟构建）
    rel2vec = rag.all_doc_vectors(payload)
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
    """标记已复习（**FSRS-6**）：更新稳定性 S、难度 D 与下次间隔。

    grade ∈ again/hard/good/easy → FSRS 的 1/2/3/4。
    状态里 `stability`/`difficulty` 是**权威字段**；`interval`/`ease` 是同一事实的
    兼容投影（旧报表与旧代码仍可读），不要反过来用它们推 FSRS 状态。
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
        import fsrs_core as F
        # ── 读状态：S/D 是权威字段；首次（或旧 SM-2 数据）用反解公式初始化 ──
        reps = int(st.get("reps", st.get("recalls", 0)))
        sm2_iv = float(st.get("interval", base_interval(imp)))
        if st.get("stability"):
            s0 = float(st["stability"])
            d0 = float(st.get("difficulty") or 5.0)
        else:
            s0, d0 = F.memory_state_from_sm2(sm2_iv, float(st.get("ease", EASE_DEFAULT)))
        # FSRS 用「上次复习到现在过了几天」算可提取性 R(t,S)
        _elapsed = 0.0
        _last = st.get("last_seen")
        if _last:
            try:
                _elapsed = max(0.0, (datetime.date.today()
                                     - datetime.date.fromisoformat(str(_last)[:10])).days)
            except (ValueError, TypeError):
                _elapsed = 0.0
        out = F.review({"stability": s0, "difficulty": d0, "reps": reps,
                        "lapses": int(st.get("lapses", 0))}, int(q), _elapsed)
        new_interval = max(1.0, min(float(out["due_in_days"]), float(MAX_INTERVAL)))
        # 等效 ease：仅作旧字段/旧报表的兼容投影（D 越小越容易）
        new_ease = round(2.9 - (float(out["difficulty"]) - 1.0) * (1.6 / 9.0), 3)
        st.pop("recalls", None)
        st.update({"last_seen": today, "interval": new_interval, "ease": new_ease,
                   "reps": int(out["reps"]), "lapses": int(out["lapses"]),
                   "stability": float(out["stability"]),
                   "difficulty": float(out["difficulty"])})
        # 旧实现写的是 `q < 3`（SM-2 口径：1/2 都算没回忆起来）；FSRS 只有 again(1) 是遗忘。
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
