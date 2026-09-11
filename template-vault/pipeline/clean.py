#!/usr/bin/env python3
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
# 用法:
#   python3 pipeline/clean.py --dry-run
#   python3 pipeline/clean.py --apply
#   python3 pipeline/clean.py --chunk [--threshold N]
#   python3 pipeline/clean.py --report
# ============================================================
import argparse, os, re, sys, json, subprocess, datetime, hashlib
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT, EXCLUDE, DOMAIN_WHITELIST

BODY_LIMIT = 2000
ARCHIVE_DAYS = 90  # SOP-02 流转归档：updated 超过此天数且非 archived/legacy → 建议 retire
STATUS = {"draft", "active", "stable", "legacy", "archived"}
REQUIRED = ["tags", "status", "domain", "created", "updated",
            "importance", "kb_target", "kb_action", "kb_summary"]


def route_chunk_strategy(body):
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


# ── 聚合桶机制（FR-3.0.15）──────────────────────────────────
AGGREGATE_DIR = "00-收件箱 Inbox/_aggregated"
AGGREGATE_LIMIT = 2000
AGGREGATE_IMPORTANCE = 0.3


def aggregate_bucket(root, body, content_type, author=None):
    """将简短内容追加到月度聚合桶。

    Args:
        root: vault 根路径
        body: 简短内容正文
        content_type: chat / chat_todo
        author: 贡献者（可选）

    Returns:
        聚合桶相对路径
    """
    bucket_type = "chat" if content_type in ("chat", "chat_decision", "chat_knowledge") else "todo"
    month = datetime.date.today().strftime("%Y-%m")
    bucket_path = Path(root) / AGGREGATE_DIR / f"{month}-{bucket_type}.md"

    timestamp = datetime.datetime.now().strftime("%H:%M")
    author_prefix = f" {author}:" if author else ""
    entry = f"\n---\n\n**[{timestamp}]{author_prefix}** {body}\n"

    if bucket_path.exists():
        existing = bucket_path.read_text(encoding="utf-8")
        bucket_path.write_text(existing + entry, encoding="utf-8")
    else:
        fm = {
            "tags": [bucket_type, "aggregated"],
            "status": "active", "domain": "综合",
            "created": datetime.date.today().strftime("%F"),
            "updated": datetime.date.today().strftime("%F"),
            "importance": AGGREGATE_IMPORTANCE,
            "kb_target": AGGREGATE_DIR, "kb_action": "new",
            "kb_summary": f"{month} {bucket_type} 汇总",
            "content_type": content_type,
        }
        header = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm.items()) + "\n---\n\n"
        bucket_path.parent.mkdir(parents=True, exist_ok=True)
        bucket_path.write_text(header + f"# {month} {bucket_type} 汇总\n" + entry, encoding="utf-8")

    return str(bucket_path.relative_to(root))


FOLDER_DOMAIN = {
    "AI与LLM AI": "开发", "AI与LLM": "开发", "后端开发": "开发", "前端开发": "开发",
    "开发": "开发", "安全 Security": "安全", "安全": "安全", "测试 Testing": "开发", "测试": "开发",
    "基础设施 Infra": "运维", "基础设施": "运维", "部署运维": "运维", "部署": "运维", "运维": "运维",
    "数据库 Database": "数据", "数据库": "数据", "项目管理 PM": "管理", "项目管理": "管理", "PM": "管理",
    "智能客服 Smart-Customer": "开发", "AI测试求职 AI-Testing-Port": "综合",
    "面试准备": "综合", "光钟圆环模型 Light-Clock": "综合",
    "记忆维护 Memory": "综合", "Career-Pivot": "综合",
}
DOMAIN_MAP = {
    "技术": "开发", "AI": "开发", "知识管理": "管理", "综合": "综合", "其他": "综合",
    "决策": "管理", "项目": "综合", "职业": "综合", "记忆维护": "综合",
    "运维": "运维", "开发": "开发", "安全": "安全", "产品": "产品", "数据": "数据", "管理": "管理",
}
# 仅拉丁词（tokenizer 产出拉丁词/单字 CJK，中文双字词会误匹配），避免如 部署→运维 误判
KEYWORD_DOMAIN = {"test": "开发", "deploy": "运维", "database": "数据",
                  "prompt": "产品", "career": "综合", "security": "安全"}

FM_RE = re.compile(r"^---\s*$", re.M)
CJK = re.compile(r"[\u4e00-\u9fff]")
LAT = re.compile(r"[A-Za-z0-9]+")


def tokens(s):
    return [m.lower() for m in LAT.findall(s)] + CJK.findall(s)


# 容错解析 frontmatter importance，避免裸 float() 把占位符(区间/空/双冒号)
# 误解析成崩溃或静默回退默认值。与 intake_triage/feedback_loop/recall_schedule
# 里的 _parse_importance 同义，保持 across 模块一致。
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


def git(args, cwd):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


def checkpoint(root):
    # 用 -u 仅暂存已跟踪文件的修改，不 add 新的未跟踪文件(如 .obsidian 运行时态)
    git(["add", "-u"], root)
    if git(["status", "--porcelain"], root).stdout.strip():
        git(["commit", "-q", "-m", f"pre-write checkpoint @ {datetime.datetime.now():%F %T}"], root)
    return git(["rev-parse", "HEAD"], root).stdout.strip()


def iter_notes(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE]
        # 跳过嵌套 git 仓库（子模块/独立子仓），但保留 root 本身
        if dp != root and os.path.exists(os.path.join(dp, ".git")):
            dn[:] = []
            continue
        for f in fn:
            if f.endswith(".md"):
                yield Path(dp) / f


def load(path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.search(text)
    if m:
        end = FM_RE.search(text, m.end())
        block = text[m.end():end.start()] if end else ""
        body = text[end.end():] if end else text[m.end():]
    else:
        block, body = "", text
    fm, cur = {}, None
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("{", "}"):
            continue
        if line.endswith(":"):
            cur = line[:-1].strip().strip('"\''); fm[cur] = None; continue
        if ":" in line:
            k, _, v = line.partition(":"); v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip().strip('"\'')
    return fm, text, block, body


def parse_date(v, default):
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    return default.strftime("%F")


def infer_domain(rel, fm, title, body):
    if fm.get("domain") in DOMAIN_WHITELIST:
        return fm["domain"]
    d = DOMAIN_MAP.get(str(fm.get("domain", "")), "")
    if d:
        return d
    for p in reversed(rel.split(os.sep)):
        base = p.split(" ")[0]
        if base in FOLDER_DOMAIN:
            return FOLDER_DOMAIN[base]
    tk = Counter(tokens(title + " " + body[:400]))
    for kw, dom in KEYWORD_DOMAIN.items():
        if kw in title or tk.get(kw):
            return dom
    return "综合"


def infer_status(rel, fm, mtime):
    if "归档" in rel or "trash" in rel.lower():
        return "archived"
    if fm.get("status") in STATUS:
        return fm["status"]
    age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(mtime)).days
    if age > 180: return "archived"
    if age > 90:  return "legacy"
    if age > 30:  return "stable"
    return "active"


def infer_tags(folder):
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", folder)
    if slug and folder != slug:
        return [folder, slug]
    return [folder] if folder else ["knowledge"]


def list_of(fm, *more):
    base = []
    tags = fm.get("tags")
    if isinstance(tags, list):
        base = [str(x).strip() for x in tags if str(x).strip()]
    elif isinstance(tags, str):
        base = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    return base + list(more)


def base_fm(rel, fm, extra):
    fm = dict(fm)
    fm.update(extra)
    if isinstance(fm.get("tags"), str):
        fm["tags"] = [t.strip() for t in re.split(r"[,\s]+", fm["tags"]) if t.strip()]
    return fm


def infer_summary(body):
    for line in body.splitlines():
        line = line.strip(" \t>*#")
        if line and not line.startswith("`"):
            return line[:120]
    return "（待补充摘要）"


def fmt_value(v):
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def clean_note(root, p):
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


def do_clean(root, dry):
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


def report(root):
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


def do_chunk(root, limit):
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


def main():
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
