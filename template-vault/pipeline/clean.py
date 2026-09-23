#!/usr/bin/env python3
# ⚠️ DEPRECATED — 本模块已由 RSI 引擎替代
# 替代方案: kb clean → kb_engine.py / kb_rsi.py (RSI CLI)
# 保留原因: 向后兼容旧 CLI 调用和 sync.py 内部 import
# 迁移指引: 使用 `kb engine t1` 命令（通过 kb_launcher.py 路由到 RSI 引擎）
# ============================================================
# clean.py —— 知识库清洗（方案 v4.0 §3 + §5 最佳实践落地）
#   dry-run : 输出拟改动清单，不写
#   apply   : 写前 checkpoint → 结构化 + 标准化 + 精确去重 → 提交（§2.1 写前 checkpoint）
#   chunk   : 对正文 >阈值的笔记按 ## 标题分块（内容不丢失，仅拆分+父索引）
#   report  : 扫描并输出超长/待清洗清单
# 清洗项（结构化+标准化）：
#   status 归一白名单 / 路径或内容推断 domain / tags 补全 /
#   created+updated(优先原日期/回退ctime) / importance(默认0.5)/保留原附加字段 /
#   kb_target(当前分区) / kb_action(归档→retire 否则 new) / kb_summary(首句)
# 建议顺序：先 --apply（结构化）再 --chunk（分块）。
# ── SUPERSEDED（2026-09-20 用户决定）──
#   本文件已被 RSI 引擎取代：精确去重/结构化归一由 kb_rsi（dups 指标 + T1 收敛提议）/
#   kb_engine --run t1 接管。今后 kb-kit 清洗/收敛动作默认走 RSI CLI，不再直接调本文件 CLI。
#   保留原因：aggregate_bucket/fmt_value 等函数仍被 memory_sync.py / memory_ingest.py 摄入路径
#   import 复用，故不删，仅标 superseded 停用。要彻底移除需先解耦上述 import。
# 用法:
#   python3 pipeline/clean.py --dry-run
#   python3 pipeline/clean.py --apply
#   python3 pipeline/clean.py --chunk [--threshold N]
#   python3 pipeline/clean.py --report
# ============================================================
import argparse, os, re, sys, json, subprocess, datetime, hashlib
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import (ROOT_DEFAULT, EXCLUDE, DOMAIN_WHITELIST, iter_notes,
                       aggregate_bucket, fmt_value, infer_domain, infer_tags,
                       list_of, infer_status, infer_summary, base_fm,
                       FOLDER_DOMAIN, DOMAIN_MAP, KEYWORD_DOMAIN,
                       AGGREGATE_DIR, AGGREGATE_LIMIT, AGGREGATE_IMPORTANCE)
from kb_common import parse_date_str as parse_date
from kb_common import load_note_full as load

BODY_LIMIT = 2000
ARCHIVE_DAYS = 90  # SOP-02 流转归档：updated 超过此天数且非 archived/legacy → 建议 retire
STATUS = {"draft", "active", "stable", "legacy", "archived"}
REQUIRED = ["tags", "status", "domain", "created", "updated",
            "importance", "kb_target", "kb_action", "kb_summary"]


def route_chunk_strategy(body: str) -> str:
    """大小感知策略路由：按字符数选择处理策略。

    Returns:
        "aggregate"        - <200 字，聚合桶（P1 完整实现）
        "direct"           - 200-2000 字，直接建笔记
        "chunk_by_heading" - 2000-10000 字，按 ## 标题分块
        "semantic_chunk"   - >10000 字，语义分块
    """
    nchars = len(body.replace("\n", "").replace(" ", ""))
    if nchars < 200:
        return "aggregate"
    if nchars <= 2000:
        return "direct"
    if nchars <= 10000:
        return "chunk_by_heading"
    return "semantic_chunk"


FM_RE = re.compile(r"^---\s*$", re.M)
CJK = re.compile(r"[\u4e00-\u9fff]")
LAT = re.compile(r"[A-Za-z0-9]+")


def tokens(s: str) -> List[str]:
    return [m.lower() for m in LAT.findall(s)] + CJK.findall(s)


# 容错解析 frontmatter importance，避免裸 float() 把占位符(区间/空/双冒号)
# 误解析成崩溃或静默回退默认值。与 intake_triage/feedback_loop/recall_schedule
# 里的 _parse_importance 同义，保持 across 模块一致。
# 注：_parse_importance 保留本地实现——默认值 0.5（kb_common.parse_importance 默认 0.0）。
def _parse_importance(v):
    """robustly parse frontmatter 'importance' to float, default 0.5.
    Handles numeric ("0.8"), range/placeholder ("0.0-1.0"), wikilink-ish
    ("importance:: 0.8"), empty/missing -> float or 0.5 on failure.
    """
    try:
        m = re.search(r"[-+]?\d*\.?\d+", str(v))
    except TypeError:
        return 0.5
    return float(m.group()) if m else 0.5


def git(args: List[str], cwd: str) -> Any:
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


def checkpoint(root: Union[str, Path]) -> str:
    # 用 -u 仅暂存已跟踪文件的修改，不 add 新的未跟踪文件(如 .obsidian 运行时态)
    git(["add", "-u"], root)
    if git(["status", "--porcelain"], root).stdout.strip():
        git(["commit", "-q", "-m", f"pre-write checkpoint @ {datetime.datetime.now():%F %T}"], root)
    return git(["rev-parse", "HEAD"], root).stdout.strip()


# iter_notes 已迁移至 kb_common.iter_notes（单一权威源）
# aggregate_bucket / fmt_value / infer_domain / infer_tags / list_of /
# infer_status / infer_summary / base_fm / parse_date / load 已迁移至
# kb_common（Task 1 解耦），此处通过 re-export 保持向后兼容。


def clean_note(root: Union[str, Path], p: Path) -> Tuple[str, str]:
    rel = str(p.relative_to(root))
    folder = rel.split(os.sep)[0]
    fm, text, block, body = load(p)
    mtime = p.stat().st_mtime
    created = parse_date(fm.get("created"), datetime.date.fromtimestamp(mtime))
    updated = parse_date(fm.get("updated"), datetime.date.today())
    if block:
        title = ""
        for ln in body.splitlines():
            ln = ln.strip().lstrip("#").strip()
            if ln:
                title = ln; break
    else:
        title = text.split("\n", 1)[0].lstrip("#").strip()
    title = title or p.stem
    domain = infer_domain(rel, fm, title, body)
    status = infer_status(rel, fm, mtime)
    importance = _parse_importance(fm.get("importance"))
    is_archive = ("归档" in rel) or ("trash" in rel.lower())
    extras = {k: v for k, v in fm.items() if k not in REQUIRED and v is not None}
    fm2 = {
        "tags": list_of(fm) or infer_tags(folder),
        "status": status, "domain": domain,
        "created": created, "updated": updated,
        "importance": round(float(importance), 2),
        "kb_target": rel.split(os.sep)[0],
        "kb_action": "retire" if is_archive else "new",
        "kb_summary": infer_summary(body),
    }
    for k, v in extras.items():
        if k not in REQUIRED and k not in fm2 and v is not None:
            fm2[k] = v
    new = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2.items()) + "\n---\n\n" + body.lstrip("\n")
    return rel, new


def do_clean(root: Union[str, Path], dry: bool) -> Tuple[List[str], int, Counter, List[str]]:
    changed, dup_archived, stats = [], 0, Counter()
    touched = []  # 所有被改/新建/删除的文件 rel，用于精确 git add
    for p in iter_notes(root):
        rel, new = clean_note(root, p)
        cur = p.read_text(encoding="utf-8")
        if new != cur:
            changed.append(rel)
            touched.append(rel)
            if not dry:
                p.write_text(new, encoding="utf-8")
    # 精确去重：正文（含 frontmatter）完全一致 → 保留最新，旧者归档
    idx = {}
    for p in iter_notes(root):
        idx.setdefault(hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest(), []).append(p)
    for group in idx.values():
        if len(group) > 1:
            group.sort(key=lambda pp: pp.stat().st_mtime)
            keep, *dups = group
            for dp in dups:
                target = Path(root) / "90-归档 Archive" / "trash" / f"{dp.stem}-dup-{datetime.date.today()}.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                header = f"# 重复归档：{dp.name}\n\n> 由 clean.py 去重归档，正文已并入 {keep.name}。\n"
                target.write_text(header + "\n" + dp.read_text(encoding="utf-8"), encoding="utf-8")
                dp.unlink()
                dup_archived += 1
                touched.append(str(target.relative_to(root)))
                touched.append(str(dp.relative_to(root)))
    for p in iter_notes(root):
        fm, _, _, _ = load(p)
        stats["status:" + str(fm.get("status", "?"))] += 1
    return changed, dup_archived, stats, touched


def report(root: Union[str, Path]) -> int:
    over, dirty, stale = [], [], []
    now = datetime.datetime.now()
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, text, block, body = load(p)
        if len(body.replace("\n", "")) > BODY_LIMIT:
            over.append((len(body.replace("\n", "")), rel))
        missing = [f for f in REQUIRED if not fm.get(f)]
        if missing or fm.get("domain") not in DOMAIN_WHITELIST or fm.get("status") not in STATUS:
            dirty.append(rel)
        # 过期笔记：updated 超过 ARCHIVE_DAYS 且 status 非 archived（SOP-02 流转归档）
        upd = fm.get("updated", "")
        st = fm.get("status", "")
        if isinstance(upd, str) and len(upd) >= 10 and upd[4] == "-" and upd[7] == "-":
            try:
                age = (now - datetime.datetime.fromisoformat(upd[:10])).days
                if age > ARCHIVE_DAYS and st not in ("archived", "legacy"):
                    stale.append((age, rel))
            except ValueError:
                pass
    over.sort(reverse=True)
    stale.sort(reverse=True)
    print(f"📋 笔记总数 {sum(1 for _ in iter_notes(root))}")
    print(f"📋 待清洗(缺字段/域或状态非法) {len(dirty)} 条")
    print(f"📋 超长(>{BODY_LIMIT}字) {len(over)} 条，建议分块（前10）：")
    for n, rel in over[:10]:
        print(f"   - {n} 字  {rel}")
    print(f"📋 过期(>{ARCHIVE_DAYS}天未更新且非 archived/legacy) {len(stale)} 条，建议 retire（前10）：")
    for age, rel in stale[:10]:
        print(f"   - {age}天  {rel}")
    return 0


def do_chunk(root: Union[str, Path], limit: int) -> Tuple[int, List[str]]:
    handled, created = 0, 0
    touched = []  # 所有被改/新建的文件 rel，用于精确 git add
    for p in iter_notes(root):
        fm, text, block, body = load(p)
        if len(body.replace("\n", "")) <= limit:
            continue
        handled += 1
        # 检测是否有 ## 标题
        has_headings = bool(re.search(r"^## ", body, re.M))
        if has_headings:
            # 现有逻辑：按 ## 标题分块（保持不变）
            segs = [s for s in re.split(r"(?=^## )", body, flags=re.M) if s.strip()]
            chunks, cur, cur_n = [], [], 0
            for s in segs:
                if cur_n + len(s) > limit and cur:
                    chunks.append(cur); cur, cur_n = [s], len(s)
                else:
                    cur.append(s); cur_n += len(s)
            if cur:
                chunks.append(cur)
            name = p.stem
            fm2 = base_fm(str(p.relative_to(root)), fm, {"tags": list_of(fm, "toc")})
            parent = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2.items()) + "\n---\n\n"
            parent += f"# {name}\n\n> 已按 §3.3 颗粒度分块为 {len(chunks)} 块，正文见下：\n\n"
            for i, ck in enumerate(chunks, 1):
                cf = p.with_name(f"{name}-p{i}.md")
                fm2c = base_fm(str(p.relative_to(root)), fm, {"tags": list_of(fm, "chunk"), "chunk": i, "chunk_of": name})
                chunk_text = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2c.items()) + "\n---\n\n## {name} · 块{i}\n\n" + "\n".join(ck)
                cf.write_text(chunk_text, encoding="utf-8")
                created += 1
                touched.append(str(cf.relative_to(root)))
                parent += f"- {name}·块{i} → {cf.name}\n"
            p.write_text(parent, encoding="utf-8")
            touched.append(str(p.relative_to(root)))
        else:
            # 新增：无 ## 标题 → 语义分块（滑动窗口 + 父索引）
            import semantic_chunk
            rel = str(p.relative_to(root))
            chunk_notes = semantic_chunk.chunk(body, rel, chunk_size=2000, overlap=500)
            if not chunk_notes:
                continue  # 无需分块
            # 继承父文档 content_type
            parent_content_type = fm.get("content_type")
            for cn in chunk_notes:
                cf = p.parent / cn.filename
                # 合并父 frontmatter + 分块 frontmatter
                fm2c = base_fm(rel, fm, {"tags": list_of(fm, "chunk")})
                fm2c.update(cn.frontmatter)
                if parent_content_type and "content_type" not in fm2c:
                    fm2c["content_type"] = parent_content_type
                if cn.is_parent_index:
                    chunk_text = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2c.items()) + "\n---\n\n" + cn.body
                else:
                    chunk_text = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2c.items()) + "\n---\n\n" + cn.body
                cf.write_text(chunk_text, encoding="utf-8")
                created += 1
                touched.append(str(cf.relative_to(root)))
    print(f"✅ 分块完成  处理 {handled} 条笔记，新建 {created} 个分块文件")
    return created, touched


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dry-run"); d.add_argument("--root", default=ROOT_DEFAULT)
    a = sub.add_parser("apply"); a.add_argument("--root", default=ROOT_DEFAULT)
    c = sub.add_parser("chunk"); c.add_argument("--root", default=ROOT_DEFAULT)
    c.add_argument("--threshold", type=int, default=BODY_LIMIT)
    r = sub.add_parser("report"); r.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()
    if args.cmd == "dry-run":
        print("🔍 dry-run 将执行：给每条笔记补全 frontmatter + 归一 域/状态/标签 + 精确去重归档。")
        print("   建议先 `--report` 查看范围，再 `--apply`，最后可视情况 `--chunk`。")
        return 0
    if args.cmd == "report":
        return report(args.root)
    if args.cmd == "chunk":
        print("⛳ 写前 checkpoint")
        checkpoint(args.root)
        created, touched = do_chunk(args.root, args.threshold)
        # 精确 add 本次改/新建的笔记，不 sweep Obsidian 运行时态
        if touched:
            git(["add", "--", *touched], args.root)
        git(["commit", "-q", "-m", f"kb: 颗粒度分块 新建 {created} 个分块文件"], args.root)
        return 0
    print("⛳ 写前 checkpoint")
    checkpoint(args.root)
    changed, dup_archived, stats, touched = do_clean(args.root, dry=False)
    # 精确 add 本次改/新建/删除的笔记，不 sweep Obsidian 运行时态
    if touched:
        git(["add", "--", *touched], args.root)
    git(["commit", "-q", "-m", f"kb: 清洗笔记 {len(changed)} 条 + 归档重复 {dup_archived} 条"], args.root)
    print(f"✅ 清洗完成  改动 {len(changed)} 条  归档重复 {dup_archived} 条")
    for k, v in sorted(stats.items()):
        print(f"   {k}: {v}")
    # 写后校验：调 validate 检查写入结果，有硬错误则警告（不回滚，因已 commit）
    vp = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "validate.py"),
                         "--root", args.root, "--json"], capture_output=True, text=True)
    try:
        vr = json.loads(vp.stdout or "{}")
        hard = vr.get("hard_issues", 0)
        if hard:
            print(f"⚠️ 校验发现 {hard} 条硬错误（清洗写入的 frontmatter 可能不合法），请跑 kb validate 查看")
    except json.JSONDecodeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
