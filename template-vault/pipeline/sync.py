#!/usr/bin/env python3
# ============================================================
# sync.py —— 知识同步管道（方案 v4.0 §5）幂等 / dry-run / 回滚 / 统一摄取
#   流程: 写前 checkpoint commit → 校验(硬错误即中止) → 路由写入 → 提交
# 用法:
#   python3 pipeline/sync.py dry-run    [--root R] [--allow-existing-issues]
#   python3 pipeline/sync.py apply      [--root R] <note.md> [--allow-existing-issues]
#   python3 pipeline/sync.py rollback   [--root R] [--no-purge]
#   python3 pipeline/sync.py history    [--root R]
#   python3 pipeline/sync.py ingest-any [--root R] <file> [--author A]
# ============================================================
# ── 状态说明（2026-10-22 P2-4 修正）──
#   本文件**曾**同时标着两处互相矛盾的"已废弃"标记：
#     · 顶部说本模块已被 RSI 引擎替代，并让用户改用某个**根本不存在的**
#       `engine` 子命令；
#     · 中段说本文件 CLI 已被取代、不应再直接调用。
#   两者都与事实不符，已删除，理由是实测核对：
#     1. **`kb engine` 命令根本不存在** —— COMPAT_MAP 里没有任何 `engine` 条目，
#        敲 `kb engine` 得到的是「❓ 未知命令」。让用户迁移到一个不存在的命令，
#        比没有迁移指引更糟。
#     2. `kb sync` 的 5 个动作（dry-run / apply / rollback / history / ingest-any）
#        全部经 `ops_plugin._ROUTES` 主动路由到本文件，是**活跃生产入口**。
#     3. `kb ingest-any <文件>` 是 README「统一摄取入口」，实现在本文件
#        （`cmd_ingest_any`：归一化 → 分类 → 质量门 → 路由 → 写入 → 标相关）。
#
#   与 RSI 的真实关系：`_research/kb_engine.py` 是**编排实验代码**（T1/T2/T3，
#   默认只提议、需 --auto 才写），没有对用户暴露的 CLI 入口；本文件是
#   **生产同步与摄取管道**。两者不是替代关系。
#
#   若将来真要让 kb_engine 接管，正确顺序是：
#     ① 先给 kb_engine 加 COMPAT_MAP 入口并跑通；
#     ② 再把本文件的 CLI 标为废弃并给出**可执行**的迁移指引。
#   ——反过来先标废弃，等于凭空制造一个"用户没法照做"的指引。
# ============================================================
import argparse, os, sys, json, shutil, subprocess, datetime
from pathlib import Path

from kb_infer import fmt_value
from kb_io import ROOT_DEFAULT, parse_frontmatter
# domain → (PARA 区, 子目录)，与 memory_sync.py FOLDER_TARGET 一致
DOMAIN_TARGET = {
    "运维": ("20-技术 Technology", "基础设施"),
    "开发": ("20-技术 Technology", "后端开发"),
    "安全": ("20-技术 Technology", "安全"),
    "数据": ("20-技术 Technology", "数据库"),
    "产品": ("40-资源库 Resources", "项目管理"),
    "管理": ("10-项目 Projects", "项目管理"),
    "综合": ("40-资源库 Resources", "综合"),
}
CKPT_FILE = "logs/.last-checkpoint"          # gitignore


def sub(args, cwd):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


def run_validate(root):
    p = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "validate.py"),
                        "--root", root, "--json"], capture_output=True, text=True)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"hard_issues": p.returncode, "issues": []}


def checkpoint(root):
    # 用 -u 仅暂存已跟踪文件的修改，不 add 新的未跟踪文件(如 .obsidian 运行时态)
    sub(["add", "-u"], root)
    sub(["commit", "-q", "-m", f"pre-write checkpoint @ {datetime.datetime.now():%F %T}"], root)
    ck = sub(["rev-parse", "HEAD"], root).stdout.strip()
    # 新库可能还没有 logs/（安装只建 .kb/ 等运行态目录）；这里补建，
    # 否则 `kb sync apply` 会在写 checkpoint 时 FileNotFoundError 崩溃。
    ckpt = Path(root) / CKPT_FILE
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(ck, encoding="utf-8")
    return ck


def read_note(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"✗ 笔记不存在: {p}")
    text = p.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    return fm, body


def route_domain(fm):
    """domain → (PARA 区, 子目录)，与 memory_sync.py FOLDER_TARGET 一致。"""
    d = fm.get("domain") or "综合"
    return DOMAIN_TARGET.get(d, ("40-资源库 Resources", d))


def cmd_dry(args):
    rep = run_validate(args.root)
    hard = rep.get("hard_issues", 0)
    print(f"🔍 dry-run  笔记总数 {rep.get('total')}  硬错误 {hard}  软告警 {rep.get('warn_issues', 0)}")
    if hard and not args.allow_existing_issues:
        print("  ⏸ 存在硬错误 —— dry-run 中止，未写入任何内容（安全闸）。")
        print("     加 --allow-existing-issues 可绕过；或先用 validate.py 治理后再同步。")
        return 0
    if hard and args.allow_existing_issues:
        print("  ⚠ 存在硬错误但已 --allow-existing-issues，将照常 checkpoint + 路由。")
    print("  拟执行（未实际写入）:")
    print("    (1) git add -u && commit 'pre-write checkpoint'")
    print("    (2) 路由 <待同步笔记> 至 <PARA 区>/<子域>/")
    print("    (3) git commit 'kb: sync <笔记>'")
    return 0


def cmd_apply(args, note_path):
    note = Path(note_path).resolve()
    fm, body = read_note(note)
    para_area, subdir = route_domain(fm)
    target = Path(args.root) / para_area / subdir
    # (1) 写前 checkpoint
    ck = checkpoint(args.root)
    # (2) 校验整仓（硬错误 → 回滚，除非显式 --allow-existing-issues）
    rep = run_validate(args.root)
    if rep.get("hard_issues") and not args.allow_existing_issues:
        print(f"✗ 校验发现 {rep['hard_issues']} 硬错误，回滚写前 checkpoint（安全闸）")
        sub(["reset", "--hard", ck], args.root)
        (Path(args.root) / CKPT_FILE).unlink(missing_ok=True)
        return 1
    # (3) 路由写入
    target.mkdir(parents=True, exist_ok=True)
    body = body or f"# {fm.get('title', note.stem)}\n\n{fm.get('kb_summary', note.name)}"
    header = f"# {fm.get('title', note.stem)}\n\n> 同步自 {note.name} @ {datetime.datetime.now():%F %T}\n"
    fname = f"{datetime.date.today()}-{note.stem}.md"
    # 必须写 frontmatter：以前只写 header+body，产物当场被自家 validate 判「缺少 frontmatter」
    # 硬错误，后续 sync/dry-run 会被安全闸拦死。
    fm_out = {
        "tags": fm.get("tags") or "综合",
        "status": fm.get("status") or "active",
        "domain": fm.get("domain") or "综合",
        "category": fm.get("category") or "reference",
        "created": str(fm.get("created") or datetime.date.today()),
        "updated": datetime.date.today().isoformat(),
        "importance": fm.get("importance") or 0.5,
        "kb_target": f"{para_area}/{subdir}",
        "kb_action": "new",
        "kb_summary": str(fm.get("kb_summary") or f"同步自 {note.name}"),
    }
    block = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm_out.items()) + "\n---\n\n"
    out_path = _unique_path(target / fname)   # 同日重跑不再同名静默覆盖
    out_path.write_text(block + header + body, encoding="utf-8")
    # 只 add 刚写入的笔记文件，不 sweep Obsidian 运行时态
    rel_out = str(out_path.relative_to(args.root))
    sub(["add", "--", rel_out], args.root)
    sub(["commit", "-q", "-m", f"kb: sync {note.stem} (domain={fm.get('domain', '综合')}) via sync.py"], args.root)
    print(f"✅ 已写入 {out_path}  提交: {sub(['rev-parse', '--short', 'HEAD'], args.root).stdout.strip()}")
    return 0


PURGE_AREAS = ("20-技术 Technology", "10-项目 Projects", "40-资源库 Resources")


def _unique_path(p: Path) -> Path:
    """同名撞车时避让（绝不静默覆盖既有笔记）。"""
    if not p.exists():
        return p
    for i in range(1, 100):
        cand = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not cand.exists():
            return cand
    return p


def _untracked_under(root, areas):
    """列出 `git clean -fd` 会删除的未跟踪文件（相对 root）。

    用 `ls-files --others -z` 而不是解析 `clean -n` 的输出，原因有两条：
      · `clean -n` 的文案随 git 语言变（本机中文输出「将删除 …」），无法稳定匹配；
      · 非 ASCII 路径会被引号 + 八进制转义，拿不到可直接操作的文件系统路径。
    `-z` 用 NUL 分隔且不做转义，是机器可读的稳定接口。
    """
    out = []
    for area in areas:
        r = sub(["ls-files", "--others", "--exclude-standard", "-z", "--", area], root)
        for rel in r.stdout.split("\0"):
            rel = rel.strip()
            if rel:
                out.append(rel)
    return out


def _backup_before_purge(root, rels):
    """删除前把即将被清理的未跟踪路径备份到 backups/rollback-<ts>/。

    返回 (备份目录, 份数)；任何一项备份失败即返回 None —— 宁可不清，也不丢数据。
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = Path(root) / "backups" / f"rollback-{stamp}"
    n = 0
    for rel in rels:
        src = Path(root) / rel.rstrip("/")
        if not src.exists():
            continue
        tgt = dst / rel.rstrip("/")
        try:
            if src.is_dir():
                shutil.copytree(src, tgt, dirs_exist_ok=True)
            else:
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, tgt)
            n += 1
        except OSError as e:
            print(f"  ⚠️ 备份失败：{rel}（{e}）")
            return None
    return dst, n


def cmd_rollback(args):
    ckpt = (Path(args.root) / CKPT_FILE).read_text(encoding="utf-8").strip() \
        if (Path(args.root) / CKPT_FILE).exists() else None
    if not ckpt:
        print("✗ 无 checkpoint 记录（未检测到 apply 痕迹）"); return 1
    before = sub(["rev-parse", "HEAD"], args.root).stdout.strip()
    sub(["reset", "--hard", ckpt], args.root)
    after = sub(["rev-parse", "HEAD"], args.root).stdout.strip()
    print(f"↩ 已回滚 HEAD {before[:8]} → {after[:8]}（回到写前 checkpoint）")
    if not args.no_purge:
        # sync 可能写到多个 PARA 区，清理所有同步目标下的未跟踪笔记。
        # 注意：`git clean -fd` 会连同「用户自己写了但还没提交的笔记」一起删，
        # 所以必须先列出清单并备份，备份不成功就整体放弃清理（P0-4）。
        victims = _untracked_under(args.root, PURGE_AREAS)
        if not victims:
            print("  🧹 无未跟踪文件需要清理")
            return 0
        res = _backup_before_purge(args.root, victims)
        if res is None:
            print("  ⛔ 备份不完整 → 已跳过清理，未删除任何未跟踪文件。"
                  "请先手工处理这些文件，或用 --no-purge 重新执行。")
            return 1
        dst, n = res
        print(f"  🧹 清理 {n} 个未跟踪路径（已先备份到 backups/{dst.name}/，可恢复）：")
        for v in victims[:20]:
            print(f"     - {v}")
        if len(victims) > 20:
            print(f"     … 其余 {len(victims) - 20} 项")
        for area in PURGE_AREAS:
            sub(["clean", "-fdq", "--", area], args.root)
    return 0


def cmd_history(args):
    """列出 sync.py 的同步提交历史（git log --grep="kb: sync"）。"""
    r = sub(["log", "--oneline", "--grep=^kb: sync"], args.root)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        print("📅 无同步历史（未跑过 sync.py apply）")
        return 0
    print(f"📅 同步历史（{len(lines)} 次）:")
    for ln in lines[:25]:
        print(f"  {ln}")
    return 0


# ── 统一摄取入口（FR-3.0.16）──────────────────────────────────
def cmd_ingest_any(args):
    """kb ingest-any <file> → 格式归一化 → 分类 → 质量门禁 → 分块 → 路由 → 写入"""
    root = Path(args.root)
    src = Path(args.file)
    if not src.exists():
        print(f"✗ 文件不存在: {src}")
        return 1

    # ① 格式归一化
    #    真实入口是 `ingest_convert.convert(file, mirror_dir) -> ConvertResult`；
    #    以前 import 的 `convert_to_markdown` 根本不存在 → ImportError 被吞 →
    #    归一化整体失效，且非文本文件走无 errors= 的 read_text 会 UnicodeDecodeError 崩溃。
    body = ""
    try:
        from ingest_convert import convert
        conv = convert(src, root / "raw" / "_mirror")
        if getattr(conv, "success", False) and getattr(conv, "markdown", None):
            body = conv.markdown
    except (ImportError, OSError, ValueError, UnicodeDecodeError) as e:
        print(f"⚠️ 格式归一化不可用（{e}），按纯文本读取", file=sys.stderr)
    if not body:
        body = src.read_text(encoding="utf-8", errors="replace")

    # ② 内容分类 + 质量门禁
    from classify import classify, classify_quality
    signals_path = root / "reference" / "content-type-signals.json"
    cr = classify(body, signals_path=signals_path)
    qr = classify_quality(body, signals_path=signals_path)

    if not qr.should_ingest:
        print(f"✗ 质量门禁未通过: {qr.reason}")
        return 0

    # ③ 路由决策
    from intake_triage import triage_by_content_type
    from kb_domain import domain_primary
    from kb_infer import infer_domain
    # ClassifyResult 没有 domain 字段；以前 getattr(cr, 'domain', '综合') 恒为「综合」，
    # 路由永远走兜底。这里用与其它模块一致的推断函数。
    raw_domain = infer_domain(src.name, {}, src.stem, body)
    fm = {"domain": domain_primary(raw_domain)}  # P2: 按一级 domain 路由
    route = triage_by_content_type(str(root), src.name, fm, body, qr.content_type)

    # ④ 写入
    from clean import aggregate_bucket, fmt_value
    from kb_text import content_fingerprint
    import datetime

    if route["action"] == "trash":
        print(f"✗ 丢弃: {route['reason']}")
        return 0

    # 写前 checkpoint（write-side-protocol 约定 3）：ingest-any 以前全程没有 git 锚，
    # 写错了无法回滚。非 git 仓库则跳过（best-effort，不阻断摄取）。
    in_git = sub(["rev-parse", "--is-inside-work-tree"], root).stdout.strip() == "true"
    if in_git:
        checkpoint(str(root))

    if route["action"] == "aggregate":
        rel = aggregate_bucket(str(root), body, qr.content_type, author=args.author)
        print(f"✅ 聚合桶: {rel}")
        if in_git and rel:
            sub(["add", "--", str(rel)], root)
            sub(["commit", "-q", "-m", f"kb: ingest-any 聚合 {rel}"], root)
        return 0

    # direct: 写入目标目录
    target_dir = route["target_dir"]
    target_path = root / target_dir
    target_path.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    out_path = target_path / f"{stem}.md"

    today = datetime.date.today().strftime("%F")
    fm2 = {
        "tags": [qr.content_type, "ingested"],
        "status": "active",
        "domain": fm.get("domain", "综合"),
        "created": today, "updated": today,
        "importance": qr.quality_score,
        "kb_target": target_dir, "kb_action": "new",
        "kb_summary": body[:60].replace("\n", " "),
        "content_type": qr.content_type,
        "source_hash": content_fingerprint(body),
    }
    if args.author:
        fm2["author"] = args.author

    header = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2.items()) + "\n---\n\n"
    out_path.write_text(header + f"# {stem}\n\n" + body, encoding="utf-8")
    if in_git:
        rel_out = str(out_path.relative_to(root))
        sub(["add", "--", rel_out], root)
        sub(["commit", "-q", "-m", f"kb: ingest-any {out_path.name}"], root)

    # P2: 自动标注 related_to（FR-3.3.1）
    try:
        from kb_domain import add_relation
        from kb_io import load_meta, iter_notes
        from kb_text import tokenize
        from collections import Counter
        import math
        new_toks = Counter(tokenize(body))
        if new_toks:
            new_vec = {t: c for t, c in new_toks.items()}
            new_norm = math.sqrt(sum(v * v for v in new_vec.values())) or 1.0
            new_vec = {k: v / new_norm for k, v in new_vec.items()}
            related = []
            for p in iter_notes(str(root)):
                if p == out_path:
                    continue
                try:
                    fm_ex, body_ex = load_meta(p)
                    if fm_ex.get("kb_action") == "retire":
                        continue
                    ex_toks = Counter(tokenize(body_ex))
                    if not ex_toks:
                        continue
                    ex_vec = {t: c for t, c in ex_toks.items()}
                    ex_norm = math.sqrt(sum(v * v for v in ex_vec.values())) or 1.0
                    ex_vec = {k: v / ex_norm for k, v in ex_vec.items()}
                    # 余弦相似度
                    dot = sum(new_vec.get(t, 0) * ex_vec.get(t, 0) for t in new_vec if t in ex_vec)
                    if 0.70 <= dot < 0.92:
                        related.append((str(p.relative_to(root)), dot))
                except (OSError, UnicodeDecodeError):
                    continue
            if related:
                related.sort(key=lambda x: x[1], reverse=True)
                for rel_path, _ in related[:3]:  # 最多标 3 个相关
                    add_relation(fm2, "related_to", rel_path)
                # 重写文件
                header = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm2.items()) + "\n---\n\n"
                out_path.write_text(header + f"# {stem}\n\n" + body, encoding="utf-8")
                print(f"   相关: {len(related[:3])} 篇")
    except ImportError:
        pass

    print(f"✅ 摄取成功: {out_path.relative_to(root)}")
    print(f"   分类: {qr.content_type}  质量: {qr.quality_score}  路由: {route['reason']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    subp = ap.add_subparsers(dest="cmd", required=True)
    d = subp.add_parser("dry-run"); d.add_argument("--root", default=ROOT_DEFAULT)
    d.add_argument("--allow-existing-issues", action="store_true")
    a = subp.add_parser("apply"); a.add_argument("--root", default=ROOT_DEFAULT)
    a.add_argument("note"); a.add_argument("--allow-existing-issues", action="store_true")
    r = subp.add_parser("rollback"); r.add_argument("--root", default=ROOT_DEFAULT)
    r.add_argument("--no-purge", action="store_true")
    h = subp.add_parser("history"); h.add_argument("--root", default=ROOT_DEFAULT)
    # 统一摄取入口
    ig = subp.add_parser("ingest-any"); ig.add_argument("--root", default=ROOT_DEFAULT)
    ig.add_argument("file", help="要摄取的文件路径")
    ig.add_argument("--author", default=None, help="贡献者")
    args = ap.parse_args()
    if args.cmd == "dry-run":   return cmd_dry(args)
    if args.cmd == "apply":     return cmd_apply(args, args.note)
    if args.cmd == "rollback":  return cmd_rollback(args)
    if args.cmd == "history":   return cmd_history(args)
    if args.cmd == "ingest-any": return cmd_ingest_any(args)


if __name__ == "__main__":
    sys.exit(main())
