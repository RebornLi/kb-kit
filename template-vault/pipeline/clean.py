#!/usr/bin/env python3
# 注意：精确去重/结构化归一已部分由 RSI 引擎（kb_rsi/kb_engine，内部研究层）接管；
#   但 chunk（分块）与清洗仍是 `kb clean` 的正式入口，文档将其列为治理命令。
#   本模块部分函数（aggregate_bucket/fmt_value）仍被 memory_sync/memory_ingest 复用。
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
import argparse, os, re, sys, json, subprocess, datetime, hashlib, unicodedata
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, Union
from kb_common import (ROOT_DEFAULT, EXCLUDE, DOMAIN_WHITELIST, iter_notes,
                       aggregate_bucket, fmt_value, infer_domain, infer_tags,
                       list_of, infer_status, infer_summary, base_fm,
                       FOLDER_DOMAIN, DOMAIN_MAP, KEYWORD_DOMAIN,
                       AGGREGATE_DIR, AGGREGATE_LIMIT, AGGREGATE_IMPORTANCE,
                       is_generated_report, content_fingerprint, is_source_note, FM,
                       add_alias_text, record_lineage,
                       is_index_stub, is_pointer_page, is_raw_path)
from kb_common import parse_date_str as parse_date
from kb_common import load_note_full as load

BODY_LIMIT = 2000
ARCHIVE_DAYS = 90  # SOP-02 流转归档：updated 超过此天数且非 archived/legacy → 建议 retire
STATUS = {"draft", "active", "stable", "legacy", "archived"}
REQUIRED = ["tags", "status", "domain", "created", "updated",
            "importance", "kb_target", "kb_action", "kb_summary"]


def _norm_body(body: str) -> str:
    """正文归一（NFKC + 压缩空白）用于去重指纹，不改原文件。"""
    return re.sub(r"[ \t\r\f\v]+", " ", unicodedata.normalize("NFKC", body or "")).strip()


def _rewrite_tags(text: str, tags: List[str]) -> Optional[str]:
    """仅替换/插入 frontmatter 的 tags 行，保留其余内容。无 frontmatter 返回 None。"""
    m = FM.search(text)
    if not m:
        return None
    end = FM.search(text, m.end())
    if not end:
        return None
    block = text[m.end():end.start()]
    body = text[end.end():]
    tagline = "tags: [" + ", ".join(f'"{t}"' for t in tags) + "]"
    if re.search(r"^tags:.*$", block, re.M):
        block = re.sub(r"^tags:.*$", tagline, block, count=1, flags=re.M)
    else:
        block = tagline + "\n" + block.lstrip("\n")
    return "---\n" + block + "\n---\n" + body


def do_taxonomy(root: Union[str, Path], dry: bool) -> Tuple[List, List[str]]:
    """按受控词表归一 tags（别名→规范、扁平→层级、去重）。返回 (changes, touched)。"""
    import taxonomy
    tax = taxonomy.load_taxonomy(root)
    changed, touched = [], []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel) or _is_raw_rel(rel):
            continue
        fm, _text, _block, _body = load(p)
        if is_source_note(fm):
            continue
        old = taxonomy.parse_tags(fm.get("tags"))
        new = taxonomy.normalize_tags(old, tax)
        if new != old:
            changed.append((rel, old, new))
            if not dry:
                nt = _rewrite_tags(p.read_text(encoding="utf-8"), new)
                if nt:
                    p.write_text(nt, encoding="utf-8")
                    touched.append(rel)
    return changed, touched


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


def _stage_paths(root: Union[str, Path], paths: List[str]) -> List[str]:
    """暂存变更，容忍已删除且未跟踪的路径（如归档掉的重复文件）。
    返回存在的路径列表，供 `git commit -- <paths>` 使用。"""
    existing = [p for p in paths if (Path(root) / p).exists()]
    if existing:
        git(["add", "-A", "--", *existing], root)
    for p in paths:
        if (Path(root) / p).exists():
            continue
        r = git(["ls-files", "--error-unmatch", "--", p], root)
        if r.returncode == 0:
            git(["add", "-A", "--", p], root)
    return existing


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
    # P0：分块索引页显式打标（无正文、只指向子块）→ RAG 不入索引，防"空壳顶替真内容"
    if is_index_stub(fm):
        fm2["kb_layer"] = "index"
        fm2["is_chunk_index"] = True
    # P0：历史分块 bug 残留的字面占位符摘要（`{name} · 块{i}`）就地纠正
    if "{name}" in str(fm2["kb_summary"]) or "{i}" in str(fm2["kb_summary"]):
        fm2["kb_summary"] = f"{title}（分块 {len(body.replace(chr(10), ''))} 字）"
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
    # 精确去重：正文指纹（NFKC 归一，忽略 frontmatter 差异）一致 → 保留最新，旧者归档 _trash/
    idx = {}
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel) or _is_raw_rel(rel):
            continue  # 报告产物 / raw 不可变层不参与去重
        fm, _text, _block, body = load(p)
        if is_source_note(fm):
            continue
        idx.setdefault(content_fingerprint(_norm_body(body)), []).append(p)
    trash_dir = Path(root) / "90-归档 Archive" / "_trash"
    for group in idx.values():
        if len(group) > 1:
            group.sort(key=lambda pp: pp.stat().st_mtime)
            keep, *dups = group
            for dp in dups:
                trash_dir.mkdir(parents=True, exist_ok=True)
                target = trash_dir / f"{dp.stem}-dup-{datetime.date.today()}.md"
                i = 1
                while target.exists():  # 不覆盖既有归档
                    target = trash_dir / f"{dp.stem}-dup-{datetime.date.today()}-{i}.md"
                    i += 1
                header = (f"# 重复归档：{dp.name}\n\n"
                          f"> 由 clean.py 去重归档（正文指纹一致），保留 {keep.name}。\n")
                target.write_text(header + "\n" + dp.read_text(encoding="utf-8"), encoding="utf-8")
                # 旧链接重定向：保留笔记声明被归档项 stem 别名 + lineage
                aliased = add_alias_text(keep.read_text(encoding="utf-8"), dp.stem)
                if aliased:
                    keep.write_text(aliased, encoding="utf-8")
                    touched.append(str(keep.relative_to(root)))
                record_lineage(root, str(dp.relative_to(root)),
                               str(keep.relative_to(root)), "dedup")
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
    # TTL 归档候选：过期且低 importance
    ttl = []
    for age, rel in stale:
        fm, _t, _b, _body = load(Path(root) / rel)
        if _parse_importance(fm.get("importance")) < 0.4:
            ttl.append((age, rel))
    print(f"📋 建议归档(TTL：过期且 importance<0.4) {len(ttl)} 条（前10）：")
    for age, rel in ttl[:10]:
        print(f"   - {age}天  {rel}")
    # 近重复候选（MinHash+LSH，仅提示不删；确认后 kb ingest merge）
    import dedup
    docs = {}
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel) or _is_raw_rel(rel):
            continue
        fm, _text, _block, body = load(p)
        if is_source_note(fm):
            continue
        if len(body.strip()) >= 200:
            docs[rel] = body
    ndp = dedup.near_duplicate_pairs(docs, threshold=0.7)
    print(f"📋 近重复候选(相似度≥0.7) {len(ndp)} 对（仅提示，确认后 kb ingest move 无损合并，前10）：")
    for sim, a, b in ndp[:10]:
        print(f"   - {sim}  {a}  ↔  {b}")
    if ndp:
        lines = ["# 🔁 近重复候选（MinHash+LSH）", "",
                 "> 仅提示、不自动删除；确认后 `kb ingest move`（merge）无损合并。", ""]
        lines += [f"- {sim}  `{a}` ↔ `{b}`" for sim, a, b in ndp[:50]]
        (Path(root) / "clean_suggestions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def _is_raw_rel(rel: str) -> bool:
    """rel 是否在 raw/ 不可变层内（分块产物绝不写入 raw/）。"""
    return is_raw_path(rel)


# ── P0 修复：历史分块 bug 的字面占位符残留 ──────────────────────
PLACEHOLDER_RE = re.compile(r"\{name\}\s*·\s*块\{i\}")


def do_repair(root: Union[str, Path], dry: bool = True) -> Tuple[int, int, List[str]]:
    """修历史分块 bug 残留：

      1. 正文里未插值的 `## {name} · 块{i}` → `## <父页名> · 块<N>`（从文件名/`chunk_of` 推断）
      2. frontmatter 里同一占位符的 `kb_summary` → 具体标题
      3. 分块索引页（`chunk_of` 或文件名 `-index.md`）补 `is_chunk_index: true` + `kb_layer: index`
         → 让 RAG 不再索引空壳目录页

    默认 dry-run（只报数）。返回 (占位符修复数, 索引页打标数, 改动文件列表)。
    """
    fixed_ph, marked, touched = 0, 0, []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue
        if _is_raw_rel(rel):
            continue  # raw/ 不可变证据层：绝不改写（占位符残留也不动，保持原文保真）
        fm, text, block, body = load(p)
        new = text
        hit_ph = bool(PLACEHOLDER_RE.search(new))
        if hit_ph:
            # 父页名：优先 chunk_of / 文件名去掉 -pN 后缀
            base = str(fm.get("chunk_of") or "").strip()
            if not base:
                base = re.sub(r"-(p|c)\d+(-\w+)?$", "", p.stem)
            idx = str(fm.get("chunk") or "").strip()
            if not idx:
                m = re.search(r"-p(\d+)", p.stem)
                idx = m.group(1) if m else "1"
            label = f"## {base} · 块{idx}"
            new = PLACEHOLDER_RE.sub(label, new)
            if "{name}" in str(fm.get("kb_summary", "")) or "{i}" in str(fm.get("kb_summary", "")):
                new = re.sub(r"^kb_summary:.*$", f"kb_summary: {base}（块{idx}）",
                             new, count=1, flags=re.M)
            fixed_ph += 1
        need_mark = (is_index_stub(fm)
                     and str(fm.get("is_chunk_index", "")).strip().lower() not in ("true", "yes", "1")
                     and str(fm.get("kb_layer", "")).strip().lower() != "index")
        if need_mark and new.startswith("---"):
            adds = []
            if not re.search(r"^is_chunk_index:", new, re.M):
                adds.append("is_chunk_index: true")
            if not re.search(r"^kb_layer:", new, re.M):
                adds.append("kb_layer: index")
            if adds:
                # 只插到开头那个 '---' 之后（count=1）
                new = re.sub(r"^---\s*$", "---\n" + "\n".join(adds), new, count=1, flags=re.M)
            marked += 1
        if new != text:
            touched.append(rel)
            if not dry:
                p.write_text(new, encoding="utf-8")
    return fixed_ph, marked, touched


def do_chunk(root: Union[str, Path], limit: int) -> Tuple[int, List[str]]:
    handled, created = 0, 0
    touched = []  # 所有被改/新建的文件 rel，用于精确 git add
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue  # 运行期报告产物不分块
        if _is_raw_rel(rel):
            continue  # raw/ 不可变层，绝不写入分块产物
        fm, text, block, body = load(p)
        # 幂等：分块产物 / 父索引页不再分块（防重复运行产生级联膨胀）
        if fm.get("chunk_of") or fm.get("chunk") or fm.get("is_chunk_index"):
            continue
        if is_index_stub(fm) or str(fm.get("kb_layer", "")).strip().lower() == "index":
            continue  # P0：目录页/索引页绝不二次分块（-p2-p2 级联的来源）
        if re.search(r"-(p\d+|c\d+|index)\.md$", rel):
            continue
        if "§3.3 颗粒度分块为" in text and len(body.replace("\n", "")) <= limit:
            continue  # 已分块且未再膨胀；若被追加重又超限则允许再分块
        # 语义分块不改写父文档：同级已存在 <stem>-index.md 视为已分块
        if (p.parent / f"{p.stem}-index.md").exists():
            continue
        if len(body.replace("\n", "")) <= limit:
            continue
        # P0：正文本身是空壳/纯指针页（无知识可承载）→ 不分块，只提示交给 kb curate 判定
        if is_pointer_page(body, rel):
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
            fm2 = base_fm(str(p.relative_to(root)), fm, {"tags": list_of(fm, "toc"),
                                                         "is_chunk_index": True, "kb_layer": "index"})
            parent = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2.items()) + "\n---\n\n"
            parent += f"# {name}\n\n> 已按 §3.3 颗粒度分块为 {len(chunks)} 块，正文见下：\n\n"
            for i, ck in enumerate(chunks, 1):
                cf = p.with_name(f"{name}-p{i}.md")
                fm2c = base_fm(str(p.relative_to(root)), fm, {"tags": list_of(fm, "chunk"), "chunk": i, "chunk_of": name})
                chunk_text = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2c.items()) + "\n---\n\n" + f"## {name} · 块{i}\n\n" + "\n".join(ck)
                cf.write_text(chunk_text, encoding="utf-8")
                created += 1
                touched.append(str(cf.relative_to(root)))
                parent += f"- {name}·块{i} → {cf.name}\n"
            p.write_text(parent, encoding="utf-8")
            touched.append(str(p.relative_to(root)))
        else:
            # 无 ## 标题 → 语义分块（滑窗 + 递归兜底 + 父索引）
            import semantic_chunk
            rel = str(p.relative_to(root))
            chunk_notes = semantic_chunk.chunk(body, rel, chunk_size=limit,
                                               overlap=max(50, limit // 4))
            if not chunk_notes:
                continue  # 无需分块
            parent_title = fm.get("title") or p.stem
            parent_content_type = fm.get("content_type")
            for cn in chunk_notes:
                fm2c = base_fm(rel, fm, {"tags": list_of(fm, "chunk")})
                fm2c.update(cn.frontmatter)
                if parent_content_type and "content_type" not in fm2c:
                    fm2c["content_type"] = parent_content_type
                head = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2c.items()) + "\n---\n\n"
                if cn.is_parent_index:
                    # 父文档就地改写为索引页（消除「父原文 + 块」重复入库）
                    fm2c["is_chunk_index"] = True
                    fm2c["kb_layer"] = "index"
                    head = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2c.items()) + "\n---\n\n"
                    p.write_text(head + cn.body, encoding="utf-8")
                    touched.append(rel)
                    continue
                # 子块：contextual header（父标题）便于检索与追溯
                cf = p.parent / cn.filename
                cf.write_text(head + f"《{parent_title}》\n\n{cn.body}", encoding="utf-8")
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
    tx = sub.add_parser("taxonomy"); tx.add_argument("--root", default=ROOT_DEFAULT)
    tx.add_argument("--apply", action="store_true", help="应用标签归一（默认 dry-run）")
    rf = sub.add_parser("refine"); rf.add_argument("--root", default=ROOT_DEFAULT)
    rf.add_argument("--threshold", type=int, default=BODY_LIMIT)
    rp = sub.add_parser("repair"); rp.add_argument("--root", default=ROOT_DEFAULT)
    rp.add_argument("--apply", action="store_true", help="应用修复（默认 dry-run 只报数）")
    args = ap.parse_args()
    if args.cmd == "repair":
        fixed, marked, touched = do_repair(args.root, dry=not args.apply)
        mode = "✅ 已修复" if args.apply else "🔍 dry-run（未写入）"
        print(f"{mode}：占位符残留 {fixed} 条、分块索引页补打标 {marked} 条、涉及文件 {len(touched)} 个")
        if fixed or marked:
            print("   影响面示例：")
            for r in touched[:10]:
                print(f"   - {r}")
            if not args.apply:
                print("   应用：kb clean repair --apply   然后：kb rag index（重建索引使降级生效）")
            else:
                staged = _stage_paths(args.root, touched)
                git(["commit", "-q", "-m", f"kb: clean repair 占位符 {fixed} + 索引页打标 {marked}",
                     "--", *staged], args.root)
                print("   下一步：kb rag index（重建索引）")
        return 0
    if args.cmd == "dry-run":
        print("🔍 dry-run 将执行：给每条笔记补全 frontmatter + 归一 域/状态/标签 + 精确去重归档。")
        print("   建议先 `--report` 查看范围，再 `--apply`，最后可视情况 `--chunk`。")
        return 0
    if args.cmd == "report":
        return report(args.root)
    if args.cmd == "taxonomy":
        changed, touched = do_taxonomy(args.root, dry=not args.apply)
        for rel, old, new in changed[:30]:
            print(f"   {rel}: {old} → {new}")
        print(f"{'✅ 已应用' if args.apply else '🔍 dry-run'}："
              f"{'归一' if args.apply else '拟归一'} {len(changed)} 条标签")
        if args.apply and touched:
            staged = _stage_paths(args.root, touched)
            git(["commit", "-q", "-m", f"kb: taxonomy 归一标签 {len(touched)} 条",
                 "--", *staged], args.root)
        return 0
    if args.cmd == "refine":
        # 编排：checkpoint → 结构化+去重 → 分块 → 重建索引 → 校验
        print("⛳ refine：checkpoint → 结构化+去重 → 分块 → 重建索引 → 校验")
        checkpoint(args.root)
        changed, dup, _stats, touched = do_clean(args.root, dry=False)
        if touched:
            staged = _stage_paths(args.root, touched)
            git(["commit", "-q", "-m",
                 f"kb: refine 结构化 {len(changed)} + 归档重复 {dup}", "--", *staged], args.root)
        created, ctouched = do_chunk(args.root, args.threshold)
        if ctouched:
            cstaged = _stage_paths(args.root, ctouched)
            git(["commit", "-q", "-m", f"kb: refine 分块 {created}", "--", *cstaged], args.root)
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "rag.py"),
                        "index", "--root", args.root], capture_output=True)
        vp = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "validate.py"),
                             "--root", args.root, "--json"], capture_output=True, text=True)
        try:
            hard = json.loads(vp.stdout or "{}").get("hard_issues", 0)
        except json.JSONDecodeError:
            hard = "?"
        print(f"✅ refine 完成  结构化改动 {len(changed)}  去重归档 {dup}  分块新建 {created}"
              f"  索引已重建  validate 硬错误={hard}")
        return 0
    if args.cmd == "chunk":
        print("⛳ 写前 checkpoint")
        checkpoint(args.root)
        created, touched = do_chunk(args.root, args.threshold)
        # 精确 add 本次改/新建的笔记，不 sweep Obsidian 运行时态
        if touched:
            staged = _stage_paths(args.root, touched)
            git(["commit", "-q", "-m", f"kb: 颗粒度分块 新建 {created} 个分块文件",
                 "--", *staged], args.root)
        return 0
    print("⛳ 写前 checkpoint")
    checkpoint(args.root)
    changed, dup_archived, stats, touched = do_clean(args.root, dry=False)
    # 精确 add 本次改/新建/删除的笔记，不 sweep Obsidian 运行时态
    if touched:
        staged = _stage_paths(args.root, touched)
        git(["commit", "-q", "-m", f"kb: 清洗笔记 {len(changed)} 条 + 归档重复 {dup_archived} 条",
             "--", *staged], args.root)
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
