#!/usr/bin/env python3
# ============================================================
# kb_schema_migrate.py —— 知识库分类体系细化迁移
# ------------------------------------------------------------
# 目的：把"一维泛化 domain"升级成"domain(领域) × category(类型)二维分类"。
#   ① 新增 category：由顶级分区客观推导（不主观臆断），供 T1 收件箱路由 + T2 分类加权。
#   ② 收敛 domain=综合：对信号明确者细化（project/reference/experience→开发；
#      meta/sop→管理）；模板/纯元认知保留综合（本跨领域），靠 category 区分。
#   ③ 补齐缺失必填（domain/status/category），缺则补默认并标注 category 待人工。
# 铁律：--dry 默认只读；--apply 才写，且写前 git checkpoint、可回滚、写进决策日志。
# 用法:
#   python3 pipeline/kb_schema_migrate.py [--root R] [--dry] [--apply]
# ============================================================
import argparse, os, re, sys, json
from pathlib import Path
from datetime import datetime

# 顶级分区前缀 → category（客观映射）
FOLDER_CATEGORY = {
    "00": "inbox", "01": "knowledge", "10": "project", "20": "experience",
    "30": "decision", "40": "resource", "50": "template", "60": "knowledge",
    "70": "meta", "90": "archive",
}
ROOT_CATEGORY = {
    "🏠-知识库首页.md": "knowledge", "01-如何开始.md": "knowledge",
    "📖-知识库管理方案.md": "meta",
}
# category → 当 domain==综合 时建议收到的领域
DOMAIN_BY_CATEGORY = {
    "project": "开发", "experience": "开发", "reference": "开发",
    "sop": "管理", "meta": "管理",
    # template/knowledge/decision/inbox/archive → 保持原 domain（跨领域/沉淀/暂存）
}


def category_for(rel):
    parts = rel.split(os.sep)
    if len(parts) == 1:                      # 根目录文件
        return ROOT_CATEGORY.get(parts[0], "knowledge")
    if parts[0] == "reference":              # reference/ 子树
        return "reference"
    m = re.match(r"(\d+)", parts[0])
    return FOLDER_CATEGORY.get(m.group(1)) if m else "knowledge"


def domain_refine(current, category):
    """综合 → 明确领域；否则不改（返回 None 表示保持现状）。"""
    if current == "综合" and category in DOMAIN_BY_CATEGORY:
        return DOMAIN_BY_CATEGORY[category]
    return None


def read_note(p):
    text = p.read_text(encoding="utf-8")
    m0 = re.match(r"^\s*---\s*$", text, re.M)
    if not m0:
        return {}, text, ""
    rest = text[m0.end():]
    m1 = re.search(r"^\s*---\s*$", rest, re.M)   # 闭括号不在 rest 起点，必须用 search
    if not m1:
        return {}, text, rest.strip()
    fm_txt, body = rest[:m1.start()], rest[m1.end():]
    fm = {}
    for ln in fm_txt.splitlines():
        mm = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", ln)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip()
    return fm, text, body


def set_fm(p, key, value):
    """在 frontmatter 写入字段（缺失则追加到 firstmatter 末尾）。不改正文语义。"""
    text = p.read_text(encoding="utf-8")
    m0 = re.match(r"^\s*---\s*$", text, re.M)
    if not m0:
        return False
    rest = text[m0.end():]
    m1 = re.search(r"^\s*---\s*$", rest, re.M)   # 闭括号必须用 search
    if not m1:
        return False
    fm_txt, body = rest[:m1.start()], rest[m1.end():]
    line = f"{key}: {value}"
    if re.search(rf"^{re.escape(key)}\s*:", fm_txt, re.M):
        new_fm = re.sub(rf"^{re.escape(key)}\s*:.*$", line, fm_txt, count=1, flags=re.M)
    else:
        new_fm = fm_txt.rstrip("\n") + "\n" + line + "\n"
    p.write_text("---\n" + new_fm + "\n---\n" + body, encoding="utf-8")
    return True


# 注：iter_notes 保留本地实现——与 kb_common.iter_notes 排除集不同：
#   本地版仅排除 (.git, .obsidian, vector, .kb)，不排除 backups/logs/pipeline 等；
#   schema migrate 需扫描更广范围（含 pipeline/ 下的模板笔记），故保持独立。
def iter_notes(root):
    for p in Path(root).rglob("*.md"):
        if any(d in p.parts for d in (".git", ".obsidian", "vector", ".kb")):
            continue
        yield p


def analyze(root):
    plan = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        try:
            fm, text, body = read_note(p)
        except (OSError, UnicodeDecodeError):
            continue
        cat = category_for(rel)
        changes = {}
        # category
        old_cat = fm.get("category")
        if old_cat != cat:
            changes["category"] = (old_cat, cat)
        # domain 收敛（仅综合→明确领域）
        old_dom = fm.get("domain")
        new_dom = domain_refine(old_dom, cat)
        if old_dom and new_dom and new_dom != old_dom:
            changes["domain"] = (old_dom, new_dom)
        # 必填补齐
        if not old_dom:
            changes.setdefault("domain", (None, "综合"))
        if not fm.get("status"):
            changes.setdefault("status", (None, "active"))
        if changes:
            plan.append({"rel": rel, "category": cat, "changes": changes})
    return plan


def git_backup(root, msg):
    import subprocess
    subprocess.run(["git", "-C", str(root), "add", "-u"], capture_output=True)
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg],
                       capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)

    plan = analyze(root)
    total = len(plan)
    n_cat = sum(1 for x in plan if "category" in x["changes"])
    n_dom = sum(1 for x in plan if "domain" in x["changes"])
    n_extra = sum(len(x["changes"]) - (1 if "category" in x["changes"] else 0)
                  - (1 if "domain" in x["changes"] else 0) for x in plan)

    print(f"🗂️ 分类迁移分析：笔记 {total} · 待改 category {n_cat} · domain 收敛 {n_dom} · 其余补齐 {n_extra}")
    print("--- 预览 ---")
    for x in plan:
        parts = [f"{k}:{v[0]!r}→{v[1]!r}" for k, v in x["changes"].items()]
        print(f"  [{x['category']}] {x['rel']}  {'  '.join(parts)}")

    if not args.apply:
        print("\nℹ️ --dry 模式（未写任何文件）。加 --apply 执行，写前 git checkpoint 可回滚。")
        return 0

    git_backup(root, "pre-schema-migrate checkpoint")
    applied = 0
    logp = Path(root) / "pipeline" / ".kb_schema_migrate.jsonl"
    logp.parent.mkdir(parents=True, exist_ok=True)
    for x in plan:
        p = Path(root) / x["rel"]
        for k, (old, new) in x["changes"].items():
            if set_fm(p, k, new):
                applied += 1
                with open(logp, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                        "rel": x["rel"], "field": k,
                                        "from": old, "to": new}, ensure_ascii=False) + "\n")
    # 写后提交（审计）
    import subprocess
    subprocess.run(["git", "-C", str(root), "add", "-u"], capture_output=True)
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(root), "commit", "-q",
                        "-m", f"kb: 分类体系细化 category+domain 收敛 {applied} 处(可回滚)"],
                       capture_output=True)
    print(f"\n✅ 已应用 {applied} 处变更（+ 决策日志 {logp}）。git 可回滚。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
