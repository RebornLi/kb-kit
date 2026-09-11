#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
kb-healthcheck.py — 知识库健康巡检脚本（知识库治理层 v4 配套）
用法:
  python3 kb-healthcheck.py deadlinks    死链检测
  python3 kb-healthcheck.py skeletons    骨架笔记检测
  python3 kb-healthcheck.py tags         标签越界检测
  python3 kb-healthcheck.py empty        空目录检测
  python3 kb-healthcheck.py frontmatter  frontmatter 缺失检测
  python3 kb-healthcheck.py discipline   顶层目录纪律
  python3 kb-healthcheck.py orphans     孤儿笔记检测（无入链）
  python3 kb-healthcheck.py overlong    超长笔记检测
  python3 kb-healthcheck.py all          全巡检（默认）

设计要点（贴合 Obsidian 链接语义）：
  - 处理转义管道 \[|]  [[path|alias]]  [[path#anchor]]  裸名跨命名空间  [[../..]]
  - 系统目录链接（memory/reference/main/.git/.obsidian/...）不计为知识库死链
  - 骨架/纪律检测自动排除系统目录与模板目录
仅检测 + 报告，不自动修改。
"""
import os, re, sys
from pathlib import Path
from datetime import datetime, timezone

# 默认 vault 根：脚本位于 pipeline/，parents[1] 即 vault 根（可用 KB_ROOT 覆盖）
from kb_common import ROOT_DEFAULT as _ROOT_DEFAULT_STR, EXCLUDE
# kb-healthcheck 用 Path 对象
ROOT_DEFAULT = Path(_ROOT_DEFAULT_STR)
# 全名分区（与脚手架目录名一致）
VAULT_DIRS = ["00-收件箱 Inbox", "10-项目 Projects", "20-技术 Technology",
              "30-决策日志 Decisions", "40-资源库 Resources", "50-模板 Templates",
              "60-运营 Operations", "70-知识治理 Governance", "90-归档 Archive"]
SYSTEM_HINTS = ("memory/", "reference/", "main/", ".git/", ".obsidian/",
                ".openclaw/", ".venvo", ".trash", "scripts/", "media/")
FRONTMATTER_FIELDS = {"tags", "status", "domain", "importance"}

# 标签白名单：系统层 + 主题白名单 + 历史沿用通用标签
# TAG_WHITELIST_SYS：系统/类型标签（moc/daily/template/decision 等）
# TAG_WHITELIST_TOPIC：主题/领域标签（后端/前端/数据库 等，是 DOMAIN_WHITELIST 的超集）
TAG_WHITELIST_SYS = {"system", "moc", "daily", "decision-log", "cleanup", "kb-sync",
             "kb-clean", "weekly", "template", "meta", "governance", "ops",
             "metric", "taxonomy", "kb-manage", "journal", "decision", "lesson",
             "projects", "resources", "guide", "pipeline", "tech", "dashboard",
             "v4", "solution", "knowledge-management", "bucket", "sop", "toc", "portal", "moc-system", "inbox", "project", "review"}
TAG_WHITELIST_TOPIC = {"后端", "前端", "数据库", "微服务", "架构", "部署", "vllm",
               "推理", "嵌入服务", "gpu", "渗透", "rbac", "xss", "审计",
               "pytest", "ragas", "llm-as-judge", "鲁棒性", "模型选型",
               "记忆系统", "提示词", "量化", "面试", "作品集", "jd分析",
               "职业转型", "知识管理", "方法论", "效率工具", "chunk", "40-资源库 Resources", "40资源库Resources", "10-项目 Projects", "10项目Projects", "90-归档 Archive", "90归档Archive", "20-技术 Technology", "20技术Technology", "testing", "运维", "llm", "infrastructure", "AI与LLM", "deployment", "AI", "视频生成", "prompt", "MiniMaxH3", "security", "robustness", "LLM", "30-决策日志 Decisions", "30决策日志Decisions", "基础设施", "管理", "full-run", "model-routing", "测试", "模型部署", "安全", "70-知识治理 Governance", "70知识治理Governance", "obsidian", "知识体系", "求职", "故障排查", "远程集群", "模型对比", "性能基准", "视频模型", "工具部署", "memory-audit", "context-management", "frontend", "gpu-memory", "dgx-spark", "best-practice", "综合", "dsh", "career", "ai-testing", "active", "玩模型", "数学模型", "memory", "august-2026", "summary", "interview", "AI Agent", "test engineer", "面试准备",
 "开发", "产品", "数据"
}
NOISE_TAGS = {"", "-", "ai", "gent", "2", "august-", "a", "an"}

# 成长引擎产生的瞬时报表（已 gitignore，不是笔记）：巡检时不计入，
# 否则它们的自引用名字（如 #link_suggestions.md）会被当自由 tag 误报越界。
REPORT_NAMES = {"intake_triage.md", "link_suggestions.md", "recall_deck.md",
                "feedback_hits.md", "recall_schedule.md", "_INDEX.md"}


def collect_md(root):
    """用 os.walk + EXCLUDE 遍历，避免 glob(**) 递归过深且无法排除目录。"""
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE]
        for f in fn:
            if f.endswith(".md") and f not in REPORT_NAMES:
                yield Path(dp) / f


def collect_titles(root):
    """收集所有笔记可被链接的标题(贴近 Obsidian 语义):
    aliases → kb_title → 标题(H1 或 filename stem)。供裸名/标题匹配兜底。"""
    titles = set()
    for fp in collect_md(root):
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        m = re.search(r"^aliases:\s*\[(.*)\]", text, re.M)
        if m:
            for a in m.group(1).split(","):
                a = a.strip().strip('"').strip("'")
                if a:
                    titles.add(a)
        # filename stem 也默认可被链接
        titles.add(fp.stem)
        # H1 标题 (# xxx) 也是可链接标题
        hm = re.search(r"^#\s+(.+)$", text, re.M)
        if hm:
            titles.add(hm.group(1).strip())
    return titles


def cached_stems(root):
    """内容层 stem 集合（排除系统目录），供裸名匹配。"""
    stems = set()
    for fp in collect_md(root):
        s = str(fp.relative_to(root))
        if any(h in s for h in SYSTEM_HINTS):
            continue
        if "00-收件箱 Inbox" not in s and "10-项目 Projects" not in s and \
           "20-技术" not in s and "30-决策日志" not in s and \
           "40-资源库" not in s and "50-模板" not in s and \
           "60-运营" not in s and "70-知识治理" not in s and "90-归档" not in s:
            continue
        stems.add(fp.stem)
    return stems


def link_path(m):
    r"""[[path|alias]]  →  只取 path 段（处理转义管道与锚点）。
    转义管道 \[|] 表示 pipe 是路径一部分，非别名分隔符，用占位 split。
    """
    inner = m.group(1)
    pipe = "\x00PIPE\x00"
    # 先把转义管道 \| 替换占位（它不是别名分隔符）
    inner = inner.replace("\\|", pipe)
    if pipe in inner:
        inner = inner.split(pipe, 1)[0]
    elif "|" in inner:
        inner = inner.split("|", 1)[0]
    path = inner.strip()
    # 去掉 #锚点
    path = path.split("#", 1)[0].strip()
    return path


def is_absent(raw_path, stems, titles, root):
    """判断链接路径是否指向真实存在文件（贴近 Obsidian 语义）。
    覆盖四种形式：
      1) 指向 .md 文件：10-项目/…/company-site  → 10-项目/…/company-site.md
      2) 指向目录（MOC）：20-技术/数据库 → 20-技术/数据库/
      3) 裸名跨命名空间：MEMORY → 按标题/基名匹配
      4) Obsidian 标题匹配：[[MEMORY|...]] → 全文任意可链接标题
    """
    if not raw_path or raw_path.endswith(".git"):
        return False
    # 标题匹配兜底(Obsidian 语义): 取路径最后一段(去掉锚点后)做标题/别名匹配
    bare_title = raw_path.split("#", 1)[0].strip()
    if bare_title:
        if bare_title in titles:
            return False
        basename = bare_title.split("/")[-1].strip()
        if basename and basename in titles:
            return False
    # 过滤系统目录链接
    for h in SYSTEM_HINTS:
        if raw_path.startswith(h) or "/" + h in raw_path:
            return False
    # 归一化 ../
    resolved = []
    for seg in raw_path.split("/"):
        if seg == "..":
            if resolved:
                resolved.pop()
        elif seg in (".", ""):
            continue
        else:
            resolved.append(seg)
    rel = "/".join(resolved)
    if not rel:
        return False
    full = Path(root) / rel
    # 1) 精确 .md 文件
    if full.with_suffix(".md").exists():
        return False
    # 2) 无扩展名直接存在（罕见）
    if full.with_suffix("").exists():
        return False
    # 3) 作为目录（MOC）
    if full.is_dir():
        return False
    # 4) 裸名跨命名空间（无 /）→ 按基名在内容层匹配
    if "/" not in raw_path:
        return raw_path not in stems
    return True


def check_deadlinks(root):
    dead, total = [], 0
    stems = cached_stems(root)
    titles = collect_titles(root)
    # 噪声链接: 文档里的示例/占位/锚点 + 会话 reply_to 元数据, 不是真死链
    NOISE_LINKS = ("reply_to:", "双向链接", "双链", "双链接", "wikilink", "…", "...", "$# -gt", "[[#", "[[...")
    for fp in collect_md(root):
        rel = str(fp.relative_to(root))
        if "50-模板" in rel or "/kb-kit/" in rel:        # 模板是蓝本，占位符链接不参与死链统计
            continue
        content = fp.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\[\[([^]]+)\]\]", content):
            raw = m.group(1)
            if raw.strip() == "link":                  # 文档里的 [[link]] 占位示例
                continue
            if any(w in raw for w in NOISE_LINKS):
                continue
            path = link_path(m)
            if not path:
                continue
            total += 1
            if is_absent(path, stems, titles, root):
                dead.append((rel, m.group(0)))
    rate = 100 * len(dead) / max(total, 1)
    print(f"🔗 死链检测: {len(dead)}/{total} 死链 ({rate:.1f}%)\n   阈值: <2%")
    for rel, ln in dead[:20]:
        print(f"   ✗ {rel} :: {ln}")
    if not dead:
        print("   ✅ 0 死链")
    return len(dead)


def check_skeletons(root):
    skels, now = [], datetime.now(timezone.utc)
    for fp in collect_md(root):
        s = str(fp.relative_to(root))
        if any(h in s for h in SYSTEM_HINTS) or s.startswith("50-模板"):
            continue
        if fp.stat().st_size < 500 and (now - datetime.fromtimestamp(fp.stat().st_ctime, tz=timezone.utc)).days > 7:
            skels.append((s, fp.stat().st_size))
    print(f"🦴 骨架笔记: {len(skels)} 个(<500B 且 >7天，排除系统目录/模板)\n   阈值: 0")
    for s, sz in skels[:20]:
        print(f"   ⚠ {s} ({sz}B)")
    return len(skels)


def check_tags(root):
    violations, seen = [], set()
    # P2: 从 tag-taxonomy.json 加载白名单（FR-3.3.5）
    try:
        from kb_common import load_tag_taxonomy
        tag_taxonomy = load_tag_taxonomy(root)
        external_whitelist = set(tag_taxonomy.keys())
        for subs in tag_taxonomy.values():
            external_whitelist.update(subs)
    except ImportError:
        external_whitelist = set()
    for fp in collect_md(root):
        rel = str(fp.relative_to(root))
        if any(h in rel for h in SYSTEM_HINTS):
            continue
        m = re.search(r"^tags:\s*\[(.*)\]", fp.read_text(encoding="utf-8", errors="replace"), re.M)
        if not m:
            continue
        for t in [x.strip().strip('`"') for x in m.group(1).split(",")]:
            if not t:
                continue
            seen.add(t)
            if t in NOISE_TAGS:
                violations.append((rel, t, "无意义"))
            elif t in TAG_WHITELIST_SYS or t in TAG_WHITELIST_TOPIC or t in external_whitelist:
                continue
            else:
                violations.append((rel, t, "待归入白名单"))
    print(f"🏷  标签越界(内容层): {len(violations)} 个 | 全部自由 tag: {len(seen)} 种\n   阈值: 0")
    for rel, t, why in violations[:20]:
        print(f"   ✗ {rel} :: #{t} ({why})")
    if len(seen) > 20:
        print(f"   — 全部自由 tag 清单: {', '.join(sorted(x for x in seen if x not in NOISE_TAGS and x not in TAG_WHITELIST_SYS and x not in TAG_WHITELIST_TOPIC and x not in external_whitelist))}")
    return len(violations)


def check_empty(root):
    empty = []
    # 需排除的系统/工具目录(含隐藏目录), 避免把 .git/.deepeval/代码缓存等算作空目录
    system_dir_parts = {".git", ".deepeval", ".benchmarks", ".codeartsdoer",
                        ".venvo", ".trash", ".trash-old", ".obsidian",
                        ".venv", "__pycache__", "vector index"}
    for vdir in VAULT_DIRS:
        base = Path(root) / vdir
        if not base.is_dir():
            continue
        # 用 os.walk 替换 rglob("*")，可排除系统目录、限制遍历
        for dp, dn, fn in os.walk(base):
            # 剪枝：跳过系统/工具子目录
            dn[:] = [d for d in dn if not d.startswith(".")
                     and d not in system_dir_parts]
            for d in dn:
                sub = Path(dp) / d
                try:
                    if not sub.is_dir():
                        continue
                except OSError:
                    continue
                if not any(sub.iterdir()):
                    empty.append(str(sub.relative_to(root)))
    print(f"🕳  空目录: {len(empty)} 个\n   阈值: 0")
    for e in empty[:20]:
        print(f"   ✗ {e}")
    return len(empty)


def check_frontmatter(root):
    missing = []
    for fp in collect_md(root):
        rel = str(fp.relative_to(root))
        if any(h in rel for h in SYSTEM_HINTS) or rel.startswith("."):
            continue
        if not fp.read_text(encoding="utf-8", errors="replace").startswith("---"):
            continue
        block = fp.read_text(encoding="utf-8", errors="replace").split("---", 2)[1]
        miss = [f for f in FRONTMATTER_FIELDS if f not in block]
        if miss:
            missing.append((rel, ",".join(miss)))
    print(f"📋  frontmatter 缺失: {len(missing)} 个（排除系统目录）\n   阈值: 0")
    for rel, fields in missing[:20]:
        print(f"   ⚠ {rel} :: 缺 {fields}")
    return len(missing)


def check_discipline(root):
    # 套件适配：把工程目录(脚本/索引/启动器)也视为系统层，不计为纪律违规
    sys_dirs = {"memory", "reference", "scripts", "media", "main", "backups",
                "reports", "tmp", "plans", "jd_fetch", "career-ai-test",
                ".venvo", ".trash", ".trash-old", "pipeline", "vector index",
                "logs", "kb", "kb.cmd"}
    viol = []
    for entry in Path(root).iterdir():
        n = entry.name
        if n.startswith(".") or n.startswith("_") or n in sys_dirs:
            continue
        if entry.is_dir() and not re.match(r"^\d{2}-", n):
            viol.append(n)
    print(f"🗂  顶层目录纪律(排除系统目录): {len(viol)} 处\n   阈值: 0")
    for v in viol[:20]:
        print(f"   ✗ {v} （非 NN- 编号格式）")
    return len(viol)


def check_orphans(root):
    """孤儿笔记（无 [[...]] 出链，SOP-03 巡检项）。
    排除系统目录/模板；MOC/索引页不视为孤儿（它们是导航节点）。"""
    orphans = []
    for fp in collect_md(root):
        rel = str(fp.relative_to(root))
        if any(h in rel for h in SYSTEM_HINTS) or rel.startswith("50-模板"):
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        # 有 [[...]] 出链 → 非孤儿
        if re.search(r"\[\[([^\]]+)\]\]", text):
            continue
        # MOC/索引/首页 不视为孤儿
        if any(k in fp.stem for k in ("首页", "索引", "INDEX", "toc")):
            continue
        orphans.append(rel)
    print(f"🏝  孤儿笔记: {len(orphans)} 个(无出链，排除系统/模板/MOC)\n   阈值: 0")
    for rel in orphans[:20]:
        print(f"   ⚠ {rel}")
    return len(orphans)


def check_overlong(root):
    """超长未分块笔记（正文 >2000 字，§3.3 颗粒度建议上限）。
    与 clean.py/validate.py 的 BODY_LIMIT=2000 一致。"""
    BODY_LIMIT = 2000
    over = []
    for fp in collect_md(root):
        rel = str(fp.relative_to(root))
        if any(h in rel for h in SYSTEM_HINTS) or rel.startswith("50-模板"):
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        # 去掉 frontmatter 取正文
        if text.startswith("---"):
            end = text.find("\n---\n", 3)
            body = text[end + 4:] if end != -1 else text
        else:
            body = text
        n = len(body.replace("\n", ""))
        if n > BODY_LIMIT:
            over.append((n, rel))
    over.sort(reverse=True)
    print(f"📏 超长未分块: {len(over)} 个(>{BODY_LIMIT}字，建议 kb clean --chunk)\n   阈值: 0")
    for n, rel in over[:20]:
        print(f"   ⚠ {n} 字  {rel}")
    return len(over)


def main():
    import argparse, contextlib, io
    parser = argparse.ArgumentParser(
        prog="kb-healthcheck.py",
        description="知识库健康巡检（仅检测+报告，不自动修改笔记）",
    )
    check_names = ["deadlinks", "skeletons", "tags", "empty", "frontmatter", "discipline", "overlong", "orphans"]
    parser.add_argument("check", nargs="?", default="all",
                        choices=["all"] + check_names,
                        help="巡检项：all（默认）或死链/skeletons/tags/empty/frontmatter/discipline/overlong/orphans")
    parser.add_argument("--root", default=None, help="vault 根（默认取脚本所在 vault）")
    parser.add_argument("--log", action="store_true",
                        help="结果同时写入 logs/health-<date>.log（契约：SOP-03 结果写日志）")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve() if args.root else ROOT_DEFAULT

    checks = {"deadlinks": check_deadlinks, "skeletons": check_skeletons,
              "tags": check_tags, "empty": check_empty,
              "frontmatter": check_frontmatter, "discipline": check_discipline,
              "overlong": check_overlong, "orphans": check_orphans}

    total = 0
    logfh = None
    if args.log:
        (root / "logs").mkdir(exist_ok=True)
        logfh = open(root / f"logs/health-{datetime.now():%F}.log", "a", encoding="utf-8")

    def emit(title, text):
        # 控制台：标题 + 内容
        print(title)
        print(text, end="")
        # 契约：结果同时写 logs/health-<date>.log
        if logfh:
            logfh.write(f"{title}\n{text}")

    try:
        if args.check == "all":
            emit(f"===== 巡检: all · {datetime.now().strftime('%F %T')} =====\n", "")
            for name, fn in checks.items():
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    total += fn(root)
                emit(f"===== 巡检: {name} =====", buf.getvalue())
            summary = f"\n✅ 巡检完成 · 问题合计: {total}"
            print(summary)
            if logfh:
                logfh.write(f"{summary}\n")
        elif args.check in checks:
            emit(f"===== 巡检: {args.check} =====\n", "")
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                pass
            text = io.StringIO()
            with contextlib.redirect_stdout(text):
                total = checks[args.check](root)
            emit(f"===== 巡检: {args.check} =====", text.getvalue())
            verdict = f"\n{'✅ OK' if total == 0 else f'⚠ 发现 {total} 个问题'}"
            print(verdict)
            if logfh:
                logfh.write(f"{verdict}\n")
        else:  # 不会走到（choices 已限制），兜底
            print(f"用法: {sys.argv[0]} {'|'.join(checks)} | all（加 --log 写日志）")
            sys.exit(2)
    finally:
        if logfh:
            logfh.close()
    sys.exit(1 if total > 0 else 0)


if __name__ == "__main__":
    main()
