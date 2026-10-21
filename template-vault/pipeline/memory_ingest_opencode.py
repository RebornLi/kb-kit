#!/usr/bin/env python3
# ============================================================
# memory_ingest_opencode.py —— SQLite 记忆源 adapter（OpenCode）
#
# OpenCode 记忆是单个 SQLite 数据库（~/.local/share/opencode/opencode.db），
# 核心表：
#   session : id, title, directory, time_created, time_updated, time_archived
#   message : id, session_id, time_created, data(JSON: {role: user|assistant})
#   part    : id, message_id, session_id, data(JSON: {type: text|reasoning|tool, ...})
#
# 本模块按 session（对话）聚合 user/assistant 消息，把每条完整交互整理成
# “agent 交互记录”（带 kb_target/kb_summary/kb_action frontmatter），喂给 sync/mirror。
#
# 用法: ingest(root, db_path, dry_run=False) -> 摄取条数
# ============================================================
import sqlite3, re, os, json, hashlib, datetime, glob
from pathlib import Path
from kb_io import ROOT_DEFAULT
from memory_ingest_sqlite import (_safe_kb_path, _fm_block, _domain, _kb_target,
                                  _load_state, _save_state)

TEXT_LIMIT = 400


def _message_texts(con, session_id):
    """返回 {message_id: {"text": str, "reasoning": str}}（只取文本/思考 part）。"""
    out = {}
    try:
        rows = con.execute("SELECT message_id, data FROM part WHERE session_id=? "
                           "ORDER BY time_created ASC, id ASC", (session_id,)).fetchall()
    except sqlite3.Error:
        return out
    for message_id, pdata in rows:
        try:
            p = json.loads(pdata)
        except (json.JSONDecodeError, TypeError):
            continue
        cur = out.setdefault(message_id, {"text": "", "reasoning": ""})
        ptype = p.get("type")
        if ptype == "text":
            cur["text"] += str(p.get("text", ""))
        elif ptype == "reasoning":
            cur["reasoning"] += str(p.get("text", "") or p.get("reasoning", ""))
    return out


def extract_sessions(db_path):
    """从 opencode.db 提取每个 session 的 user/assistant 交互对。
    返回 [{session_id, title, directory, ts, user, reply, reasoning}]。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return []
    try:
        sessions = con.execute(
            "SELECT id, title, directory, time_created FROM session "
            "ORDER BY time_created ASC").fetchall()
    except sqlite3.Error:
        return []
    turns = []
    for sid, title, directory, ts in sessions:
        try:
            msgs = con.execute(
                "SELECT id, time_created, data FROM message WHERE session_id=? "
                "ORDER BY time_created ASC, id ASC", (sid,)).fetchall()
        except sqlite3.Error:
            continue
        parts = _message_texts(con, sid)
        pending_user = None
        for mid, mts, mdata in msgs:
            try:
                md = json.loads(mdata)
            except (json.JSONDecodeError, TypeError):
                continue
            role = md.get("role")
            info = parts.get(mid, {})
            text = (info.get("text") or "").strip()
            if role == "user":
                pending_user = {"mid": mid, "ts": mts, "text": text}
            elif role == "assistant" and pending_user and text:
                turns.append({
                    "session_id": sid,
                    "title": (title or "").strip(),
                    "directory": directory,
                    "ts": pending_user["ts"] or mts or ts,
                    "user": pending_user["text"],
                    "reply": text,
                    "reasoning": (info.get("reasoning") or "").strip(),
                })
                pending_user = None
    con.close()
    return turns


def _iter_dbs(db_path):
    p = Path(db_path)
    if p.is_dir():
        return sorted(glob.glob(str(p / "*.db")))
    return [str(p)] if p.exists() else []


def ingest(root, db_path, dry_run=False):
    root = Path(root)
    state = _load_state(root)
    seen = state.get("opencode_seen", [])
    touched, written = [], 0

    for sqlite_path in _iter_dbs(db_path):
        for turn in extract_sessions(sqlite_path):
            user = turn.get("user", "").strip()[:TEXT_LIMIT]
            reply = turn.get("reply", "").strip()[:TEXT_LIMIT]
            if not user or not reply:
                continue  # 单条交互不完整，跳过
            title = user.split("\n", 1)[0].strip()[:60]
            if len(title) < 4:
                title = (turn.get("title") or "opencode 交互")[:60]
            domain = _domain(user + reply)
            full = f"{user}\n\n---\n\n{reply}\n\n---\n\n[agent reasoning] {turn.get('reasoning', '')}"
            date_str = datetime.datetime.fromtimestamp(
                turn["ts"] / 1000).strftime("%F") if turn.get("ts") else "2026-01-01"
            kb_target = _kb_target(full)
            # 去重键带内容指纹：只用 `opencode-{domain}-{title}` 会把
            # 「同标题、新内容」的会话永久丢弃（静默数据丢失）。
            # 原 tuple key 是死代码：seen 只存字符串，tuple 永不相等。
            fp = hashlib.sha1(full.encode("utf-8")).hexdigest()[:12]
            dedup_key = f"opencode|{domain}|{title}|{fp}"
            if dedup_key in seen:
                continue

            entry = (f"## {title}（{date_str}）\n\n"
                     f"**用户**：{user}\n\n"
                     f"**Agent**：{reply}\n\n"
                     f"> 来源：[[opencode]] · 同步自 OpenCode 会话（{Path(sqlite_path).name}）")
            kb_path = _safe_kb_path(root, kb_target)
            if kb_path is None:
                print(f"  ⚠️ 跳过越界 kb_target '{kb_target}'（不在 vault 内或为绝对路径）")
                continue
            if dry_run:
                seen.append(dedup_key)
                written += 1
                continue
            if kb_path.exists():
                text = kb_path.read_text(encoding="utf-8")
                # 内容级幂等：这一轮的原文已写入过才算重复（旧标题级判断会挡住同标题新内容）
                if user and user in text:
                    seen.append(dedup_key)
                    continue
                kb_path.write_text(text + "\n" + entry, encoding="utf-8")
            else:
                kb_path.parent.mkdir(parents=True, exist_ok=True)
                fm = {"tags": [domain], "importance": 0.4, "status": "active",
                      "domain": domain, "created": date_str, "updated": date_str,
                      "kb_target": kb_target, "kb_action": "new", "kb_summary": title,
                      "kb_source": "opencode", "kb_synced_from": Path(sqlite_path).name}
                kb_path.write_text(_fm_block(fm) + f"# {title}\n\n{entry}\n", encoding="utf-8")
            touched.append(str(kb_path))
            seen.append(dedup_key)
            written += 1

    if not dry_run:
        state["opencode_seen"] = seen
        _save_state(root, state)

    if touched and not dry_run:
        import subprocess
        # add 之后必须 commit：只 add 不 commit 会让结果长期停在暂存区，
        # 下次 `git commit` 被顺手打包（与 json 适配器行为不一致）。
        subprocess.run(["git", "-C", str(root), "add", "--", *touched],
                       capture_output=True, text=True)
        if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "-C", str(root), "commit", "-q",
                            "-m", f"kb: OpenCode 记忆摄取 {written} 条"],
                           capture_output=True, text=True)
    print(f"✅ OpenCode SQLite 摄取 {written} 条{'（dry-run，未落盘）' if dry_run else ''}")
    return written


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(description="OpenCode SQLite 记忆源 adapter")
    ap.add_argument("--root", default=ROOT_DEFAULT, help="vault 根目录")
    ap.add_argument("--db", required=True, help="opencode.db 路径（或所在目录）")
    ap.add_argument("--dry-run", action="store_true", help="不落盘")
    args = ap.parse_args()
    sys.exit(ingest(args.root, args.db, args.dry_run))
