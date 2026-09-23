#!/usr/bin/env python3
# ============================================================
# memory_ingest_sqlite.py —— SQLite 记忆源 adapter（OpenAI Codex）
#
# Codex 记忆是 SQLite 数据库（~/.codex/*.sqlite），核心表：
#   thread_items : thread_id, turn_id, item_id, item_type, created_at_ms, item_json
#   交互类型：userMessage(用户) / agentMessage(回复) / reasoning(思考)
#
# 本模块按 thread_id 聚合 turn，把每个 turn 整理成“agent 交互记录”
#（带 kb_target/kb_summary/kb_action frontmatter），喂给 sync/mirror。
#
# 用法: ingest(root, data_dir, dry_run=False) -> 摄取条数
# ============================================================
import sqlite3, re, os, json, hashlib, subprocess, datetime, glob
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT

STATE_FILE = ".memory_ingest_state.json"


def _domain(content):
    for d in ("AI与LLM", "运维", "安全", "开发", "测试", "产品", "数据", "基础设施"):
        if d in content:
            return d
    return "综合"


def _imp(_domain):
    """为生成的会话记录给一个中性默认 importance。会话记录多为轻量上下文，
    不追求检索权重，给一个中等偏低的固定值，保证 frontmatter 完整（巡检要求）。"""
    return 0.4


def _kb_target(content):
    """始终返回一个‘文件路径’，而非目录。"""
    dom = _domain(content)
    if "项目" in content or "project" in content.lower():
        return "10-项目 Projects/项目记忆项目记忆.md"
    if "决策" in content or "decision" in content.lower():
        return "30-决策日志 Decisions/决策记录决策记录.md"
    submap = {"运维": "部署运维", "安全": "安全", "开发": "AI与LLM AI",
              "测试": "测试", "基础设施": "基础设施", "数据": "数据库"}
    sub = submap.get(dom, "综合")
    return f"20-技术 Technology/{sub}/{sub}.md"


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


def _fm_block(fm):
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _iter_sqlite_dirs(data_dir):
    """返回该目录下所有 .sqlite 文件路径。"""
    return glob.glob(str(Path(data_dir) / "*.sqlite"))


def _extract_turns(sqlite_path):
    """从 thread_items 提取每个 turn 的用户/回复/思考文本。"""
    try:
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return []
    cur = con.cursor()
    try:
        cur.execute("""SELECT thread_id, turn_id, item_type, item_json, created_at_ms
                       FROM thread_items ORDER BY created_at_ms ASC""")
    except sqlite3.Error:
        return []
    turns = {}  # turn_key -> {thread_id, title, user, reply, reasoning, ts}
    for thread_id, turn_id, item_type, item_json, ts in cur.fetchall():
        try:
            d = json.loads(item_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if item_type == "userMessage":
            content = d.get("content", [])
            txt = "".join(c.get("text", "") for c in content if isinstance(c, dict) and "text" in c)
            if not txt.strip():
                continue
            key = (thread_id, turn_id)
            turns.setdefault(key, {"thread_id": thread_id, "turn_id": turn_id,
                                   "user": "", "reply": "",
                                   "reasoning": "", "ts": ts})
            turns[key]["user"] = txt.strip()[:300]
        elif item_type == "agentMessage":
            key = (thread_id, turn_id)
            if key in turns:
                turns[key]["reply"] = str(d.get("text", "")).strip()[:400]
        elif item_type == "reasoning":
            # reasoning 与 user/agent 共享同一 (thread_id, turn_id) 键
            content = d.get("content", [])
            if isinstance(content, list):
                key = (thread_id, turn_id)
                turns.setdefault(key, {"thread_id": thread_id, "turn_id": turn_id,
                                       "user": "", "reply": "", "reasoning": "", "ts": ts})
                turns[key]["reasoning"] = "".join(content).strip()[:200]
    return list(turns.values())


def ingest(root, data_dir, dry_run=False):
    root = Path(root)
    state = _load_state(root)
    seen = state.get("codex_seen", [])
    touched, written = [], 0

    for sqlite_path in _iter_sqlite_dirs(data_dir):
        turns = _extract_turns(sqlite_path)
        for turn in turns:
            key = (sqlite_path, turn.get("thread_id"), turn.get("turn_id"))
            if key in seen:
                continue
            user = turn.get("user", "").strip()
            reply = turn.get("reply", "").strip()
            if not user or not reply:
                continue  # 单条交互不完整，跳过
            title = user.split("\n", 1)[0].strip()
            if len(title) < 4:
                title = "codex 交互"
            domain = _domain(user + reply)
            full = f"{user}\n\n---\n\n{reply}\n\n---\n\n[agent reasoning] {turn.get('reasoning', '')}"
            date_str = datetime.datetime.fromtimestamp(
                turn.get("ts", 0) / 1000).strftime("%F") if turn.get("ts") else "2026-01-01"
            kb_target = _kb_target(full)
            summary = f"codex-{domain}-{title}"
            if summary in seen:
                continue  # 按"summary"去重，增量

            entry = (f"## {title}（{date_str}）\n\n"
                     f"**用户**：{user}\n\n"
                     f"**Agent**：{reply}\n\n"
                     f"> 来源：[[codex]] · 同步自 Codex 会话（{Path(sqlite_path).name}）")
            kb_path = _safe_kb_path(root, kb_target)
            if kb_path is None:
                print(f"  ⚠️ 跳过越界 kb_target '{kb_target}'（不在 vault 内或为绝对路径）")
                continue
            if kb_path.exists():
                text = kb_path.read_text(encoding="utf-8")
                if f"## {title}" in text:
                    seen.append(summary)
                    continue
                kb_path.write_text(text + "\n" + entry, encoding="utf-8")
            else:
                kb_path.parent.mkdir(parents=True, exist_ok=True)
                fm = {"tags": [domain], "importance": _imp(domain), "status": "active",
                      "domain": domain, "created": date_str, "updated": date_str,
                      "kb_target": kb_target, "kb_action": "new", "kb_summary": title[:60],
                      "kb_source": "codex", "kb_synced_from": Path(sqlite_path).name}
                kb_path.write_text(_fm_block(fm) + f"# {title}\n\n{entry}\n", encoding="utf-8")
            touched.append(str(kb_path))
            seen.append(summary)
            written += 1

    state["codex_seen"] = seen
    _save_state(root, state)

    if touched and not dry_run:
        subprocess.run(["git", "-C", root, "add", "--", *touched],
                       capture_output=True, text=True)
    print(f"✅ Codex SQLite 摄取 {written} 条{'（dry-run，未落盘）' if dry_run else ''}")
    return written


def _load_state(root):
    try:
        return json.loads((Path(root) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(root, state):
    (Path(root) / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(description="Codex SQLite 记忆源 adapter")
    ap.add_argument("--root", default=ROOT_DEFAULT, help="vault 根目录")
    ap.add_argument("--data-dir", required=True, help="Codex .sqlite 所在目录")
    ap.add_argument("--dry-run", action="store_true", help="不落盘")
    args = ap.parse_args()
    sys.exit(ingest(args.root, args.data_dir, args.dry_run))
