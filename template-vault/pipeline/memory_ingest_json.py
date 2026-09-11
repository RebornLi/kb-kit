#!/usr/bin/env python3
# ============================================================
# memory_ingest_json.py —— JSON 记忆源 adapter（DSH 的 agent-memory）
#
# DSH 的记忆是纯 JSON：$DSH_HOME/storages/memory.json，结构：
#   { "unit": {...}, "global": null, "tables": {
#       "records": { <id>: {id, content, kind, tags, scope, project,
#                            importance, createdAt, ...}, ... } } }
# kind: fact/decision/lesson/preference；importance: 0/1/2；scope: project/global
#
# 本模块把记录整理成 kb 可喂的“agent 日报”格式（带 kb_target/kb_summary/kb_action
# frontmatter），喂给 memory_ingest 的 sync/mirror，并只新增被改动的 KB 文件。
#
# 用法: ingest(root, json_path, dry_run=False) -> 摄取条数
# ============================================================
import os, json, hashlib, subprocess, datetime
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT

STATE_FILE = ".memory_ingest_state.json"


def _domain(content):
    for d in ("AI与LLM", "运维", "安全", "开发", "测试", "产品", "数据", "基础设施"):
        if d in content:
            return d
    return "综合"


def _kb_target(content):
    """始终返回一个‘文件路径’，而非目录。"""
    dom = _domain(content)
    # 项目/decision 类 -> 10-项目 Projects / 30-决策日志（补 .md 变文件路径）
    if "项目" in content or "project" in content.lower():
        return "10-项目 Projects/项目记忆项目记忆.md"
    if "决策" in content or "decision" in content.lower():
        return "30-决策日志 Decisions/决策记录决策记录.md"
    # 技术域 -> 20-技术 Technology/<子域>/<子域>.md
    submap = {"运维": "部署运维", "安全": "安全", "开发": "AI与LLM AI",
              "测试": "测试", "基础设施": "基础设施", "数据": "数据库"}
    sub = submap.get(dom, "综合")
    return f"20-技术 Technology/{sub}/{sub}.md"


def _folder(d):
    return {"运维": "部署运维", "安全": "安全", "开发": "AI与LLM AI",
            "测试": "测试", "基础设施": "基础设施"}.get(d, "综合")


def _safe_kb_path(root, target):
    """把 kb_target 解析为 root 内的绝对路径，拒绝路径穿越。
    防止恶意/失误的 kb_target（如 "../etc/cron.d/x" 或绝对路径）写到 vault 之外。
    返回 Path 对象；若越界则返回 None。"""
    if not target:
        return None
    root_resolved = Path(root).resolve()
    if Path(target).is_absolute():
        return None
    candidate = (root_resolved / target).resolve()
    try:
        if candidate == root_resolved or root_resolved in candidate.parents:
            return candidate
    except (OSError, ValueError):
        pass
    return None


def sha(path):
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def ingest(root, json_path, dry_run=False):
    root = Path(root)
    json_path = Path(json_path)
    state = _load_state(root)
    seen = state.get(f"dsj_seen_{Path(json_path).name}", [])
    touched, written = [], 0
    if not json_path.exists():
        print(f"✗ JSON 源不存在: {json_path}")
        return 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    records = (data.get("tables", {}).get("records")) or {}

    for _rid, rec in records.items():
        if _rid in seen:
            continue
        content = str(rec.get("content", "")).strip()
        if not content:
            continue
        title = content.split("：", 1)[0].split("\n", 1)[0].strip()
        title = title.lstrip('「』】"').rstrip('「『[(')
        if len(title) < 4:
            title = rec.get("kind", "fact")[:6]
        domain = _domain(content)
        key = f"{_kb_target(content)}::{title}"
        if key in seen:
            continue  # 按“目标::标题”去重，增量

        date = rec.get("updatedAt") or rec.get("createdAt")
        date_str = date[:10] if date else datetime.date.today().strftime("%F")
        fm = {
            "tags": [domain],
            "status": "active", "domain": domain,
            "created": date_str, "updated": date_str,
            "importance": _imp(rec.get("importance")),
            "kb_target": _kb_target(content),
            "kb_action": "new",
            "kb_summary": title[:60],
            "kb_source": "dsh",
            "kb_synced_from": Path(json_path).name,
        }
        entry = (f"## {title}（{date_str}）\n\n{content}\n\n"
                 f"> 来源：[[dsh]] · 同步自 DSH agent-memory（{Path(json_path).name}）")
        kb_path = _safe_kb_path(root, fm["kb_target"])
        if kb_path is None:
            print(f"  ⚠️ 跳过越界 kb_target '{fm['kb_target']}'（不在 vault 内或为绝对路径）")
            continue
        if kb_path.exists():
            text = kb_path.read_text(encoding="utf-8")
            if f"## {title}" in text:
                seen.append(key)
                continue
            kb_path.write_text(text + "\n" + entry, encoding="utf-8")
        else:
            kb_path.parent.mkdir(parents=True, exist_ok=True)
            kb_path.write_text(_fm_block(fm) + f"# {title}\n\n{entry}\n", encoding="utf-8")
        touched.append(str(kb_path))
        seen.append(key)
        written += 1

    state[f"dsj_seen_{Path(json_path).name}"] = seen
    _save_state(root, state)

    if touched and not dry_run:
        _git_add_commit(root, touched)
    print(f"✅ DSH JSON 摄取 {written} 条{'（dry-run，未落盘）' if dry_run else ''}")
    return written


def _imp(v):
    try:
        return round(float(v) / 2.0, 2)  # DSH 的 0/1/2 -> 0.0/0.5/1.0
    except (TypeError, ValueError):
        return 0.6


def _fm_block(fm):
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _load_state(root):
    try:
        return json.loads((Path(root) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(root, state):
    (Path(root) / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _git_add_commit(root, rels):
    """只 add 本次新建/被改的 KB 文件(绝不 git add -A)，有变化才 commit。"""
    subprocess.run(["git", "-C", root, "add", "--", *rels], capture_output=True, text=True)
    if subprocess.run(["git", "-C", root, "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", root, "commit", "-q",
                        "-m", f"kb: DSH JSON 记忆摄取 {len(rels)} 条"], capture_output=True)


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(description="DSH JSON 记忆源 adapter")
    ap.add_argument("--root", default=ROOT_DEFAULT, help="vault 根目录")
    ap.add_argument("--json", required=True, help="DSH memory.json 路径")
    ap.add_argument("--dry-run", action="store_true", help="不落盘")
    args = ap.parse_args()
    sys.exit(ingest(args.root, args.json, args.dry_run))
