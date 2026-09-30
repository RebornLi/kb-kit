#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_contradiction2.py — 矛盾在环（P3）。

问题：现在**新结论直接覆盖旧结论**，冲突不留痕。时间一长，库里同时存在
「X 是这样」和「X 已经改成那样」两条正典，检索时各说各话（这才是知识库"死掉"的样子）。

做法（确定性、可解释、零模型成本）：
  1. **主题重合**：两篇正典的 section 文本 Jaccard 重叠 ≥ 阈值（默认 0.25）→ 同一主题；
  2. **变更信号**：至少一方含"变更词"（改为/不再/废弃/换掉/修正/错了/停用/失效/替代…）；
  3. 判定 `conflicts_with`：有变更信号 + 主题重合 → 冲突候选；
     再看**时间先后**：较新的一方若含变更词 → 方向为「新取代旧」（supersede 候选）。

产出与纪律：
  · 只**标注**，绝不自动删改：写 `conflicts_with` frontmatter + 正文 `> ⚠️ 与 [[X]] 冲突` callout；
  · 同时进人工队列 `.kb/contradiction_queue.jsonl`（与 kb_curate review 同一套人工在环机制）；
  · 默认 dry-run；`--apply` 才写。可 `--root` 指定库。

用法:
  python3 pipeline/kb_contradiction2.py scan  [--root R] [--threshold 0.25] [--json]
  python3 pipeline/kb_contradiction2.py apply [--root R] [--limit N] [--dry-run]
"""
import argparse, json, re, sys
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import (ROOT_DEFAULT, iter_notes, load_note_full as load,
                       is_raw_path, is_generated_report, tokenize, root_of)

# 变更信号词：出现即说明"这事变了/被推翻了"
CHANGE_KW = [
    "改为", "改成", "换成", "替换为", "不再是", "已经不用", "停止使用", "改为使用",
    "修正为", "更正为", "已废弃", "已停用", "弃用", "作废", "废弃", "停用", "推翻",
    "取代", "纠正", "误判", "有误", "错了", "过时",
    "superseded", "deprecated", "no longer", "replaced by", "changed to",
]
# 判定门槛：同族（同一父文档/同一根文档）不算冲突——那是碎片自己跟自己
OVERLAP_STRICT = 0.35

QUEUE = Path(".kb") / "contradiction_queue.jsonl"
DUP_QUEUE = Path(".kb") / "duplicate_canon_queue.jsonl"
OVERLAP_MIN = 0.25
DUP_LIKE = 0.85          # 近似重复：结论区几乎一致 → 是"同一内容结晶两遍"，不是矛盾
SECTIONS = ("结论", "决定", "要点", "现象", "根因", "操作", "步骤", "坑与边界")


def _sections(body: str) -> Dict[str, str]:
    out, cur = {}, None
    for line in str(body or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _emphasis(body: str) -> str:
    """取"结论性"部分做比较：结论/决定/要点/现象/根因；都没有就用全文。"""
    secs = _sections(body)
    picked = [v for k, v in secs.items() if any(k.startswith(s) for s in SECTIONS[:5])]
    return "\n".join(picked).strip() or str(body or "")[:2000]


def _jacc(a: str, b: str) -> float:
    A, B = set(tokenize(a)), set(tokenize(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _change_hits(text: str) -> List[str]:
    t = str(text or "")
    return [k for k in CHANGE_KW if k in t]


def _day(fm: Dict[str, Any]) -> str:
    return str(fm.get("updated") or fm.get("created") or "")[:10]


def scan(root: Path, threshold: float = OVERLAP_MIN, limit: int = 0) -> Dict[str, Any]:
    """扫描全库正典，产出冲突对清单（只读）。"""
    rows = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel) or is_generated_report(rel):
            continue
        fm, _t, _b, body = load(p)
        if str(fm.get("kb_layer", "")).strip().lower() != "canon":
            continue
        # 同族标识：根文档 / 父文档（同族碎片高度重叠，属"自我重复"而非"矛盾"）
        fam = root_of(rel, fm, root)
        rows.append({"rel": rel, "fm": fm, "emp": _emphasis(body),
                     "chg": _change_hits(_emphasis(body)),   # 只认"结论性部分"里的变更词
                     "day": _day(fm), "fam": fam,
                     "src": str(fm.get("source_ref") or ""),
                     "domain": str(fm.get("domain", "") or "")})
    pairs = []
    thr = max(threshold, OVERLAP_STRICT)
    for a, b in combinations(rows, 2):
        if a["domain"] and b["domain"] and a["domain"] != b["domain"]:
            continue
        if a["fam"] == b["fam"]:
            continue                       # 同族碎片：跳过
        if a["src"] and a["src"] == b["src"]:
            continue                       # 同一原记忆：跳过
        ov = _jacc(a["emp"], b["emp"])
        if ov < thr:
            continue
        if not (a["chg"] or b["chg"]):
            continue
        newer, older = (a, b) if a["day"] >= b["day"] else (b, a)
        direction = ("newer-supersedes" if newer["chg"] and newer["day"] > older["day"]
                     else "unclear")
        kind = "duplicate-like" if ov >= DUP_LIKE else "conflict"
        pairs.append({
            "kind": kind,
            "a": a["rel"], "b": b["rel"], "overlap": round(ov, 3),
            "change_a": a["chg"][:4], "change_b": b["chg"][:4],
            "day_a": a["day"], "day_b": b["day"], "direction": direction,
            "suspect_newer": newer["rel"], "suspect_older": older["rel"],
        })
        if limit and len(pairs) >= limit:
            break
    pairs.sort(key=lambda x: (-x["overlap"], x["kind"]))
    dups = [p for p in pairs if p["kind"] == "duplicate-like"]
    conf = [p for p in pairs if p["kind"] == "conflict"]
    return {"canon": len(rows), "pairs": pairs,
            "conflicts": conf, "duplicates": dups}


def _callout_text(other_rel: str, day: str) -> str:
    return (f"\n> ⚠️ **与 [[{Path(other_rel).stem}]] 存在冲突**（{day} 记录）："
            f"两条正典在讲同一主题但结论不同，请人工确认哪条为准。\n")


def _upsert_fm_simple(text: str, key: str, value: str) -> Optional[str]:
    if not text.startswith("---"):
        return None
    m = re.search(r"^---\s*$", text, re.M)
    if not m:
        return None
    end = re.search(r"^---\s*$", text[m.end():], re.M)
    if not end:
        return None
    head = text[m.end():m.end() + end.start()]
    rest = text[m.end() + end.end():]
    lines = head.strip("\n").splitlines()
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:", ln):
            lines[i] = f"{key}: {value}"
            return "---\n" + "\n".join(lines) + "\n---\n" + rest
    lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + rest


def apply_marks(root: Path, pairs: List[Dict[str, Any]], dry: bool, limit: int) -> int:
    """把冲突标注写进两篇（frontmatter + callout），并进人工队列。默认干跑。"""
    done, today = 0, date.today().isoformat()
    touched: List[str] = []
    for pr in pairs[:limit] if limit else pairs:
        for me, other in ((pr["a"], pr["b"]), (pr["b"], pr["a"])):
            p = root / me
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8")
            if f"[[{Path(other).stem}]]" in text and "存在冲突" in text:
                continue                                  # 幂等
            new = _upsert_fm_simple(text, "conflicts_with", f'["{other}"]')
            if new is None:
                continue
            new = new.rstrip() + "\n" + _callout_text(other, today)
            if not dry:
                p.write_text(new, encoding="utf-8")
                touched.append(me)
            done += 1
    if not dry:
        q = root / QUEUE
        q.parent.mkdir(parents=True, exist_ok=True)
        with q.open("a", encoding="utf-8") as f:
            for pr in pairs:
                f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **pr},
                                   ensure_ascii=False) + "\n")
        if touched:
            import subprocess
            subprocess.run(["git", "-C", str(root), "add", "--", *touched], capture_output=True)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="矛盾在环（P3）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--root", default=ROOT_DEFAULT)
    s.add_argument("--threshold", type=float, default=OVERLAP_MIN)
    s.add_argument("--json", action="store_true", dest="as_json")
    a = sub.add_parser("apply"); a.add_argument("--root", default=ROOT_DEFAULT)
    a.add_argument("--threshold", type=float, default=OVERLAP_MIN)
    a.add_argument("--dry-run", action="store_true", dest="dry")
    a.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    root = Path(args.root)
    res = scan(root, args.threshold)
    if args.cmd == "scan":
        if args.as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2)); return 0
        print(f"⚖️ 矛盾扫描：{res['canon']} 篇正典")
        print(f"   真冲突候选 {len(res['conflicts'])} 对 · "
              f"近似重复(同内容结晶两遍) {len(res['duplicates'])} 对")
        if res["duplicates"]:
            print("   ⚠️ 近似重复应走合并（不是标冲突）——见 .kb/duplicate_canon_queue.jsonl")
        for pr in res["conflicts"][:12]:
            print(f"   重叠 {pr['overlap']:.2f} [{pr['direction']}]")
            print(f"     {pr['a'][:56]}")
            print(f"     {pr['b'][:56]}   变更词: {pr['change_a'] or pr['change_b']}")
        if not res["pairs"]:
            print("   （无冲突候选：没有任何一对正典同时满足「主题重合 + 含变更词」）")
        return 0
    n = apply_marks(root, res["conflicts"], args.dry, args.limit)
    if not args.dry and res["duplicates"]:
        dq = root / DUP_QUEUE
        dq.parent.mkdir(parents=True, exist_ok=True)
        with dq.open("a", encoding="utf-8") as f:
            for pr in res["duplicates"]:
                f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **pr},
                                   ensure_ascii=False) + "\n")
    print(f"{'🔍 dry-run' if args.dry else '✅ 已标注'}：{n} 处"
          f"（真冲突 {len(res['conflicts'])} 对 · 近似重复 {len(res['duplicates'])} 对已入去重队列）")
    if args.dry and n:
        print("   应用：kb contradict apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
