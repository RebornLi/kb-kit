#!/usr/bin/env python3
# ============================================================
# intake_triage.py —— 摄入 triage 引擎(成长 引擎①)
#   对收件箱每篇按四维打分(信号密度/结构/唯一/价值信号),路由为:
#     keep  保留   merge 合并进更高价值的现注   trash 归档垃圾
#     refine 补写   route 按 kb_target 挪到对应 PARA 区
#   review : 输出 triage 清单(默认,不改文件)
#   apply move : 按 kb_target 路由/挪到对应 bucket(写前 checkpoint, 可回滚)
#   apply trash: 低价值 + 合并项移到 90-归档/_trash(可回滚)
# 用法:
#   python3 pipeline/intake_triage.py review [--root R] [--thr T]
#   python3 pipeline/intake_triage.py apply  [--root R] [--thr T] --move --trash
# ============================================================
import argparse, os, re, sys, math, datetime
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import (ROOT_DEFAULT, EXCLUDE, parse_frontmatter, tokenize, CJK,
                       iter_notes, load_note, norm, cos, load_config,
                       parse_importance as _parse_importance, is_generated_report,
                       add_alias_text, record_lineage)

INBOX = "00-收件箱 Inbox"
TRASH = "90-归档 Archive/_trash"
REQUIRED = ["tags", "status", "domain", "created", "updated",
            "importance", "kb_target", "kb_action", "kb_summary"]
VALUE_KW = ["根因", "复盘", "决定", "决策", "为什么", "方案", "结论", "教训",
            "经验", "TODO", "下一步", "建议", "坑", "踩坑", "原则", "机制",
            "清洗", "路由", "入库", "管道", "治理", "知识"]

# D2: 路由规则配置化
BUCKET_PREFIXES = {"00", "10", "20", "30", "40", "50", "60", "70", "90"}
# 评分阈值 (min_score, action) — 默认值，实际从 triage-config.json 加载
TRIAGE_THRESHOLDS = [
    (70, "route"),   # >= 70 且有 route_to → route
    (40, "refine"),  # 40-69 → refine
    (0,  "trash"),   # < 40 → trash
]
MERGE_SIM_THRESHOLD = 0.85  # vsim >= 此值 → merge


def _load_triage_config(root=None):
    """加载 triage-config.json，缺失时回退默认值。

    Returns:
        dict: {
            "weights": {"structure": 25, "density": 25, "uniqueness": 25, "value": 25},
            "thresholds": {"route": 70, "refine": 40, "trash": 0},
            "merge_sim_threshold": 0.85,
            "value_keywords": [...]
        }
    """
    config = load_config("triage-config.json", root)
    defaults = {
        "weights": {"structure": 25, "density": 25, "uniqueness": 25, "value": 25},
        "thresholds": {"route": 70, "refine": 40, "trash": 0},
        "merge_sim_threshold": 0.85,
        "value_keywords": VALUE_KW,
    }
    if not config:
        return defaults
    for k in defaults:
        if k not in config:
            config[k] = defaults[k]
    return config


def density(text: str) -> float:
    signal = sum(1 for ch in text if ch.isalnum() or CJK.match(ch))
    return signal / max(1, len(text))


def evaluate(rel: str, path: Path, fm: Dict[str, Any], text: str, body: str,
             vsim: Optional[float], target: str,
             config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # Task 4: 评分权重配置化
    if config is None:
        config = _load_triage_config()
    w = config["weights"]
    thr = config["thresholds"]
    merge_sim = config["merge_sim_threshold"]
    value_kw = config["value_keywords"]
    # 内容闸门:有意义字符过少 → 强制 trash(不论结构/唯一性)
    signal_chars = sum(1 for ch in text if ch.isalnum() or CJK.match(ch))
    if signal_chars < 15:
        return {"rel": rel, "score": 0, "density": round(density(text), 3),
                "action": "trash", "reasons": [f"内容过少({signal_chars} 字符) → 垃圾"],
                "route_to": "", "sim_to": target, "importance": fm.get("importance", "-")}
    score, reasons = 0, []
    # 结构分 0-w.structure
    w_struct = w["structure"]
    present = sum(1 for f in REQUIRED if fm.get(f))
    score += int(w_struct * present / len(REQUIRED))
    if present < len(REQUIRED) - 2:
        reasons.append(f"缺{len(REQUIRED) - present} 必填字段")
    # 密度分 0-w.density
    w_dens = w["density"]
    d = density(text)
    score += w_dens if d >= 0.15 else int(w_dens * 0.72) if d >= 0.10 else int(w_dens * 0.40) if d >= 0.05 else int(w_dens * 0.12)
    if d < 0.15:
        reasons.append(f"信号密度 {d*100:.0f}%(<15%)")
    # 唯一性 0-w.uniqueness
    w_uniq = w["uniqueness"]
    if vsim is None:
        score += w_uniq
    elif vsim >= 0.85:
        reasons.append(f"与{target} 相似( sim{vsim:.2f})→ 应合并")
    elif vsim >= 0.70:
        score += int(w_uniq * 0.40)
        reasons.append(f"与{target} 较相似")
    else:
        score += int(w_uniq * 0.80)
    # 价值信号 0-w.value
    w_val = w["value"]
    hits = sum(1 for kw in value_kw if kw in text)
    score += min(w_val, int(w_val * 0.20) + int(w_val * 0.12) * hits)
    # 路由判定
    tgt = fm.get("kb_target", "")
    route_to = tgt if _is_bucket(tgt) else ""
    action = "route" if route_to else "keep"
    if vsim and vsim >= merge_sim:
        action = "merge"
    elif score < thr["refine"]:
        action = "trash"
    elif score < thr["route"]:
        action = "refine"
    elif not route_to:
        action = "keep"
    return {"rel": rel, "score": score, "density": round(d, 3),
            "action": action, "reasons": reasons, "route_to": route_to,
            "sim_to": target, "importance": fm.get("importance", "-")}


def _is_bucket(v):
    if not v:
        return False
    head = re.split(r"\s", v.strip(), 1)[0]
    return head.split("-")[0].strip() in BUCKET_PREFIXES


def triage(root: Union[str, Path], threshold: float) -> List[Dict[str, Any]]:
    # Task 4: 加载 triage 配置
    config = _load_triage_config(root)
    vault = {}      # rel -> vector
    meta = {}       # rel -> dict(fm,text,body,importance)
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, _, body = load_note(p)
        text = p.read_text(encoding="utf-8")
        vault[rel] = norm(Counter(tokenize(body + " " + text)))
        meta[rel] = {"fm": fm, "text": text, "body": body,
                     "importance": _parse_importance(fm.get("importance"))}
    inbox_dir = os.path.join(root, INBOX)
    results = []
    for p in sorted(Path(inbox_dir).glob("*.md")) if os.path.isdir(inbox_dir) else []:
        rel = str(p.relative_to(root))
        fm, _, body = load_note(p)
        text = p.read_text(encoding="utf-8")
        # 在库内找最相似的一条(排除自己)
        best_v, best_rel = None, None
        own = vault[rel]
        for r, v in vault.items():
            if r == rel:
                continue
            sc = cos(v, own)
            if sc > (best_v or 0):
                best_v, best_rel = sc, r
        if best_v is not None and best_v >= threshold:
            res = evaluate(rel, p, fm, text, body, round(best_v, 3), best_rel, config)
        else:
            res = evaluate(rel, p, fm, text, body, None, "", config)
        results.append(res)
    return results


def build_report(results: List[Dict[str, Any]]) -> str:
    from collections import defaultdict
    by = defaultdict(list)
    for r in results:
        by[r["action"]].append(r)
    order = ["merge", "trash", "refine", "route", "keep"]
    labels = {"merge": "🔗 合并(进更高价值现注)", "trash": "🗑️ 归档垃圾",
              "refine": "⏳ 待审队列(✏️ 需补写)", "route": "🚚 路由到 PARA 区", "keep": "📌 保留"}
    out = [f"# 📥 摄入 triage 清单(共 {len(results)} 篇)", "",
           f"> keep {len(by['keep'])} · route {len(by['route'])} · refine {len(by['refine'])} · "
           f"merge {len(by['merge'])} · trash {len(by['trash'])}"]
    for a in order:
        if not by[a]:
            continue
        out += [f"\n## {labels[a]}  ({len(by[a])})"]
        for r in sorted(by[a], key=lambda x: x["score"]):
            score = "⭐" * max(1, min(5, int(r["score"] / 20)))
            tail = ("  → " + r["route_to"]) if r["action"] == "route" else ""
            out += [f"- **{r['rel']}**  [{r['score']}/100 · 密度{r['density']:.2f}] {score}{tail}"]
            for rr in r["reasons"]:
                out += [f"    - {rr}"]
    if not any(by[a] for a in order):
        out += ["\n✅ 无待 triage 项"]
    return "\n".join(out) + "\n"


def _unique_path(path: Path) -> Path:
    """返回不与现有文件冲突的路径（追加 -1/-2…），绝不覆盖。"""
    base, ext = path.stem, path.suffix
    cand, i = path, 1
    while cand.exists():
        cand = path.with_name(f"{base}-{i}{ext}")
        i += 1
    return cand


def _safe_place(dest_dir: Path, dest_name: str, content: str) -> Tuple[Path, str]:
    """把 content 放到 dest_dir/dest_name，避免覆盖已有文件。

    目标不存在 → 写入（'new'）；已存在且内容相同 → 判重不写（'dup'）；
    已存在但内容不同 → 改名避让（'renamed'）。返回 (最终路径, 结果)。
    """
    dest = dest_dir / dest_name
    if dest.exists():
        try:
            if dest.read_text(encoding="utf-8") == content:
                return dest, "dup"
        except OSError:
            pass
        dest = _unique_path(dest)   # 同名不同内容：改名，绝不覆盖
    dest.write_text(content, encoding="utf-8")
    return dest, ("renamed" if dest.name != dest_name else "new")


def _is_raw_rel(rel: str) -> bool:
    """rel 是否在 raw/ 不可变层内。"""
    try:
        return "raw" in [str(x).lower() for x in Path(rel).parts[:-1]]
    except (TypeError, ValueError, AttributeError):
        return False


def _git_stage(root: Union[str, Path], paths: List[str]) -> List[str]:
    """暂存变更，容忍被移动后已不存在的源路径。

    - 存在的路径（目标/新建/修改）→ `git add -A`
    - 不存在但确为已跟踪文件的路径 → 单独 `git add -A`(staged 删除)
    - 不存在且未跟踪（新笔记被移走的源）→ 跳过（无需暂存）
    返回存在路径列表，供 `git commit -- <paths>` 使用（避免 pathspec 报错）。
    """
    import subprocess
    existing = [p for p in paths if (Path(root) / p).exists()]
    if existing:
        subprocess.run(["git", "-C", str(root), "add", "-A", "--", *existing],
                       capture_output=True)
    for p in paths:
        if (Path(root) / p).exists():
            continue
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", p],
                                 capture_output=True)
        if tracked.returncode == 0:
            subprocess.run(["git", "-C", str(root), "add", "-A", "--", p], capture_output=True)
    return existing


def _tombstone(rel: str, tgt_rel: str, fm_src: Dict[str, Any], body: str) -> str:
    """生成合并墓碑：可回溯原文 + redirect_to（归档层，不参与检索）。"""
    today = datetime.date.today().isoformat()
    fm = {
        "tags": '["knowledge"]', "status": "archived",
        "domain": fm_src.get("domain") or "综合",
        "created": today, "updated": today, "importance": 0.0,
        "kb_target": "90-归档 Archive/_trash", "kb_action": "retire",
        "kb_summary": f"已合并到 {tgt_rel}",
        "redirect_to": tgt_rel, "merged_from": rel,
    }
    head = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return ("---\n" + head + "\n---\n\n# 已合并：`" + rel + "`\n\n"
            f"> 已合并到 [[{Path(tgt_rel).stem}]]（`redirect_to`）。原正文保留于下，可回溯。\n\n"
            + body.strip() + "\n")


def apply_moves(root: Union[str, Path], results: List[Dict[str, Any]],
                move: bool, trash: bool, dry_run: bool = False) -> int:
    import subprocess
    moved, trashed, reviewed, merged, dups = 0, 0, 0, 0, 0
    touched = []  # 收集被改动的笔记路径，用于精确 git add
    REVIEW_DIR = "70-知识治理 Governance/_review"
    if dry_run:
        print("🔍 dry-run（只预览将路由/合并/归档/待审的笔记，不移动、不写盘、不 commit）")
    for r in results:
        rel = r["rel"]
        src = Path(root) / rel
        if move and r["action"] == "route":
            src_top = rel.split(os.sep)[0]
            if r["route_to"] == src_top:
                continue  # 已在目标区,跳过
            if dry_run:
                print(f"   · 路由 {rel} → {r['route_to']}/")
                moved += 1
                continue
            dest_dir = Path(root) / r["route_to"]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest, kind = _safe_place(dest_dir, rel.split("/")[-1], src.read_text(encoding="utf-8"))
            src.unlink()
            touched += [str(dest.relative_to(root)), rel]
            moved += 1
            if kind == "dup":
                dups += 1
        elif move and r["action"] == "merge":
            # 真合并：把源正文并入最相似的目标笔记（去重），再移除源
            tgt_rel = r.get("sim_to") or ""
            tgt = Path(root) / tgt_rel if tgt_rel else None
            if not (tgt and tgt.exists() and not _is_raw_rel(tgt_rel)
                    and not is_generated_report(tgt_rel)):
                continue  # 目标不可用：保留原件，下次再处理
            if dry_run:
                print(f"   · 合并 {rel} → {tgt_rel}（写入墓碑 + lineage）")
                merged += 1
                continue
            fm_src, _text, body = load_note(src)
            marker = f"<!-- merged:{rel} -->"
            tgt_text = tgt.read_text(encoding="utf-8")
            if marker not in tgt_text:
                tgt.write_text(
                    tgt_text.rstrip() + f"\n\n{marker}\n## 合并自 {rel}\n\n{body.strip()}\n",
                    encoding="utf-8")
                # 旧 [[source]] 链接重定向：目标声明 source stem 别名
                aliased = add_alias_text(tgt.read_text(encoding="utf-8"), Path(rel).stem)
                if aliased:
                    tgt.write_text(aliased, encoding="utf-8")
                touched.append(str(tgt.relative_to(root)))
            # 墓碑：归档可回溯 + redirect_to（不参与检索）
            trash_dir = Path(root) / TRASH
            trash_dir.mkdir(parents=True, exist_ok=True)
            tomb = trash_dir / f"{Path(rel).stem}-merged-{datetime.date.today()}.md"
            i = 1
            while tomb.exists():
                tomb = trash_dir / f"{Path(rel).stem}-merged-{datetime.date.today()}-{i}.md"
                i += 1
            tomb.write_text(_tombstone(rel, tgt_rel, fm_src, body), encoding="utf-8")
            touched.append(str(tomb.relative_to(root)))
            src.unlink()
            touched.append(rel)
            record_lineage(root, rel, tgt_rel, "merge")
            merged += 1
        elif trash and r["action"] == "trash":
            if dry_run:
                print(f"   · 归档 {rel} → {TRASH}/")
                trashed += 1
                continue
            dest_dir = Path(root) / TRASH
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest, kind = _safe_place(dest_dir, rel.split("/")[-1], src.read_text(encoding="utf-8"))
            src.unlink()
            touched += [str(dest.relative_to(root)), rel]
            trashed += 1
            if kind == "dup":
                dups += 1
        elif move and r["action"] == "refine":
            # 待审队列：移入 _review/（原件离开收件箱，避免每轮重复 triage）
            if dry_run:
                print(f"   · 待审 {rel} → {REVIEW_DIR}/")
                reviewed += 1
                continue
            dest_dir = Path(root) / REVIEW_DIR
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest, _kind = _safe_place(dest_dir, rel.split("/")[-1], src.read_text(encoding="utf-8"))
            src.unlink()
            touched += [str(dest.relative_to(root)), rel]
            reviewed += 1
    # 精确 add 被移动/合并/归档/待审的笔记，不 sweep Obsidian 运行时态
    staged_paths = _git_stage(root, touched) if touched else []
    if staged_paths and subprocess.run(["git", "-C", root, "status", "--porcelain"],
                                       capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", root, "commit", "-q",
                        "-m", f"kb: 摄入 triage 处理(moved{moved}/merged{merged}/trashed{trashed}/review{reviewed},可回滚)",
                        "--", *staged_paths],
                       capture_output=True)
    dup_msg = f" | 判重跳过 {dups} 篇" if dups else ""
    if dry_run:
        print(f"\n🔍 dry-run 汇总：拟路由 {moved} | 拟合并 {merged} | 拟归档 {trashed} | 拟待审 {reviewed}{dup_msg}")
        print("   应用：kb ingest move（或 kb ingest trash）")
        return 0
    print(f"✅ 路由 {moved} 篇 | 合并 {merged} 篇 | 归档 {trashed} 篇 | 待审 {reviewed} 篇{dup_msg}"
          f"(均在目标区/_trash/_review 可回滚；同名不同内容已改名避让，绝不覆盖)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("review"); d.add_argument("--root", default=ROOT_DEFAULT)
    d.add_argument("--thr", type=float, default=0.85)
    a = sub.add_parser("apply")
    a.add_argument("--root", default=ROOT_DEFAULT); a.add_argument("--thr", type=float, default=0.85)
    a.add_argument("--move", action="store_true"); a.add_argument("--trash", action="store_true")
    a.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="只预览将路由/合并/归档的笔记，不移动")
    args = ap.parse_args()
    results = triage(args.root, args.thr)
    if args.cmd == "review":
        report = build_report(results)
        (Path(args.root) / "intake_triage.md").write_text(report, encoding="utf-8")
        summary = next((ln.strip().lstrip("> ").strip() for ln in report.splitlines()
                        if ln.startswith(">")), "")
        print(f"📥 triage 完成: {summary}  已写入 intake_triage.md")
        print("\n" + report)
        return 0
    if not (args.move or args.trash):
        print("提示: 需指定 --move(路由) 和/或 --trash(归档),默认只 review")
        return 0
    return apply_moves(args.root, results, args.move, args.trash,
                       dry_run=getattr(args, "dry_run", False))


# ── 按内容类型分桶（FR-3.3.13）──────────────────────────────
CONTENT_TYPE_ROUTES = {
    "policy":     "30-资源 Resources/政策",
    "decision":   "10-项目 Projects/决策",
    "lesson":     "20-技术 Technology/教训",
    "knowledge":  "20-技术 Technology",
    "reference":  "30-资源 Resources",
    "chat":       None,  # 聚合桶
    "chat_todo":  None,  # 聚合桶
    "noise":      None,  # 丢弃
}


def _domain_to_para(domain):
    """domain → PARA 顶层目录映射。"""
    mapping = {
        "开发": "20-技术 Technology",
        "安全": "20-技术 Technology",
        "产品": "10-项目 Projects",
        "运营": "10-项目 Projects",
        "管理": "10-项目 Projects",
        "综合": "30-资源 Resources",
        "生活": "40-领域 Areas",
    }
    return mapping.get(domain, "30-资源 Resources")


def triage_by_content_type(root: Union[str, Path], rel: str, fm: Dict[str, Any],
                           body: str, content_type: str) -> Dict[str, Any]:
    """按内容类型决定路由目标。

    Returns:
        dict: {
            "action": "direct" | "aggregate" | "trash",
            "target_dir": str | None,
            "reason": str,
        }
    """
    route = CONTENT_TYPE_ROUTES.get(content_type)

    if content_type == "noise":
        return {"action": "trash", "target_dir": None, "reason": "噪声内容丢弃"}

    if route is None and content_type in ("chat", "chat_todo", "chat_decision", "chat_knowledge"):
        return {"action": "aggregate", "target_dir": None,
                "reason": f"简短对话 {content_type} 进聚合桶"}

    if route:
        return {"action": "direct", "target_dir": route,
                "reason": f"{content_type} 直达 {route}"}

    # 兜底：按 domain 路由
    domain = fm.get("domain", "")
    para_dir = _domain_to_para(domain)
    return {"action": "direct", "target_dir": para_dir,
            "reason": f"按 domain({domain}) 路由到 {para_dir}"}


if __name__ == "__main__":
    sys.exit(main())