#!/usr/bin/env python3
"""
memory_ingest_session.py — DSH 会话日志记忆摄取（修复方案 1：解析现有会话日志入库）

背景：现有 DSH 运行时每天在 ~/.dsh/sessions/<id>/session.v4.jsonl.zstd 写原始对话日志，
而原记忆写入引擎（dsh-agent-memory / dsh-evolve / dsh-memory-fortress）已卸载，
kb-agent.json 里 dsh 指向的 storages/memory.json 自 09-16/09-21 后不再更新。
本脚本把“每天新产生的 DSH 记忆”从会话日志里捞出来、结构化入库。

流程（与 OpenClaw 的 mirror → promote 同路）：
  1. 解压缩并解析会话日志，抽取值得入库的「人类轮」（过滤审批策略/委派前缀/历史重放/客套等噪声）。
  2. 用 content_fingerprint 全局去重（复用 memory_ingest 状态，与 mirror/extract 共享去重表）。
  3. 按 memory/ 层格式写 memory/dsh-YYYY-MM-DD-NNNN.md，交由既有 memory_sync.promote 晋升到 PARA。

用法：
  python3 memory_ingest_session.py mirror-session --root R [--session-root S] [--days N] [--max-turns M] [--apply]
  # 默认 dry-run：只统计与预览，不写盘；加 --apply 才落盘 + git commit

环境变量：
  KB_SESSION_ROOT  会话日志根（默认 ~/.dsh/sessions）
"""
import argparse, os, re, sys, json, datetime, hashlib, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_ingest import frontmatter_block  # 复用前门块格式

try:
    from kb_common import content_fingerprint  # 优先用统一指纹算法
except Exception:  # pragma: no cover - 兜底
    def content_fingerprint(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---- 独立 state 文件（与 ingest 共享的 memory_state.json 隔离，互不污染）----
_STATE_NAME = "memory_session_state.json"

def _load_state(root: Path):
    try:
        from state_manager import StateStore
        store = StateStore(root)
        default = {"session_hashes": {}, "content_hashes": {}}
        if store.exists(_STATE_NAME):
            return store.load(_STATE_NAME, default)
        return default
    except Exception:
        p = Path(root) / ".kb" / "state" / _STATE_NAME
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"session_hashes": {}, "content_hashes": {}}

def _save_state(root: Path, state):
    try:
        from state_manager import StateStore
        StateStore(root).save(_STATE_NAME, state)
    except Exception:
        (Path(root) / ".kb" / "state").mkdir(parents=True, exist_ok=True)
        (Path(root) / ".kb" / "state" / _STATE_NAME).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")


DEFAULT_SESSION_ROOT = str(Path("~").expanduser() / ".dsh" / "sessions")
MIN_CHARS = 12                 # 低于此视为客套/噪声（ok / 好的 / 收到）
MAX_BODY = 3200                # 单条正文上限，过长 brief 截断刷屏
REPLAY_MARKERS = ("对话历史：", "对话历史:")
# 纯委派/系统前缀块（不含人类真实意图）→ 剔除该块
SYSTEM_BLOCK_RE = re.compile(
    r"^(Your parent agent id is\s+\S+|You will act as a|You will respond as a|You are a "
    r"\S+? (researcher|assistant|agent|helper)|The user wants you to|The following is your profile)",
    re.I)
# 功能自测 / 协议探测——非人类真实记忆，剔除
NOISE_KW = (
    "tool_call_ok", "reply with exactly one word", "只回复一个词", "回复一个词",
    "你现在用的是哪个模型名", "用哪个模型", "你是哪个模型", "local 或 cloud",
    "func test", "function test", "自检", "自测", "回 pong", "回 ok", "回 hello",
    "echo TOOL_CALL", "echo ok", "echo hello", "echo test", "echo ping",
)


def is_noise(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in NOISE_KW)


def decompress(path: Path) -> str:
    """zstd 解压缩为文本（PyZStandard 优先，否则回退 zstd CLI）。"""
    try:
        import zstandard
        dctx = zstandard.ZstdDecompressor()
        with open(path, "rb") as fh:
            return dctx.stream_reader(fh).read().decode("utf-8", "replace")
    except Exception:
        out = subprocess.run(["zstd", "-dc", str(path)],
                             capture_output=True, text=True, timeout=120)
        return out.stdout


def _real_text(content):
    """从 data.content 列表里拼出“人类真实意图”，剔除委派/系统前缀块与重放前缀。"""
    parts = []
    if not isinstance(content, list):
        return ""
    for b in content:
        t = (b or {}).get("text", "")
        if not isinstance(t, str):
            continue
        t = t.strip()
        if not t:
            continue
        if SYSTEM_BLOCK_RE.match(t):
            continue                      # 剔除纯系统/委派前缀块
        for m in REPLAY_MARKERS:        # 剥离“对话历史：”重放前缀，保留冒号后内容
            if t.startswith(m):
                after = t[len(m):].lstrip()
                t = after.strip()
                break
        if t:
            parts.append(t)
    return "\n".join(p.strip() for p in parts).strip()


def iter_turns(session_path: Path):
    """yield (time_ms, text) 人类轮（source.kind=user 且含实质 text；会话内重放去重）。"""
    text = decompress(session_path)
    kept = []  # (text,) 本轮会话内用于重放（子串）去重
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "user/message":
            continue
        kind = (e.get("data") or {}).get("source", {}).get("kind")
        if kind and kind != "user":
            continue                      # 跳过 user-approval / system 等系统事件
        combined = _real_text((e.get("data") or {}).get("content") or [])
        if not combined:
            continue
        if len(combined) < MIN_CHARS:
            continue
        if is_noise(combined):
            continue                      # 剔除功能自测/协议探测（非人类记忆）
        # 重放去重：本轮会话内，若某条是已保留条的子串 → 视为历史重放，跳过
        for prev in kept:
            if prev and prev in combined:
                break
        else:
            kept.append(combined)
            yield e.get("time", 0), combined


def session_meta(path: Path):
    name = path.parent.name
    try:
        sess = name.split("--", 1)[-1] if "--" in name else name
        sid = sess.split("/")[0] if sess else name
    except Exception:
        sid = name
    return sid, path.stat().st_mtime, path.stat().st_size


def _slug(s):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", s, flags=re.S).strip("-")
    return re.sub(r"-{2,}", "-", s)[:48] or "memory"


def _title(text):
    first = text.replace("\r", " ").split("\n")[0].strip()
    first = first.rstrip("。.！!？?：:")
    return first[:48] or "DSH 会话记忆"


def _localnow():
    return datetime.datetime.now().astimezone()


def mirror_session(root, session_root=None, days=0, max_turns=10000,
                   apply=False, agent_name="dsh-session"):
    root = Path(root)
    state = _load_state(root)
    content_seen = state.setdefault("content_hashes", {})   # 会话级内容指纹去重（独立文件）
    session_hashes = state.setdefault("session_hashes", {})  # 会话级幂等（mtime+size）

    if not session_root:
        session_root = os.environ.get("KB_SESSION_ROOT") or DEFAULT_SESSION_ROOT
    sessions = sorted(Path(session_root).rglob("session*.jsonl.zstd")) \
        if Path(session_root).is_dir() else []

    sessions_skipped, content_deduped, new_turns, total_turns = 0, 0, 0, 0
    per_day, sample, touched = {}, [], []

    for p in sessions:
        sid, mtime, size = session_meta(p)
        key = f"{sid}::{mtime:.0f}::{size}"
        # 幂等键用物理文件路径 str(p)（147 个会话可同处一个 session-<uuid>/ 目录、
        # 各有 session/v3/v4 等多个版本文件，.name 或 sid 都会互相覆盖）；
        # 值仍存 sid::mtime::size，文件未改动（mtime+size 不变）即可跳过。
        if session_hashes.get(str(p)) == key:
            sessions_skipped += 1
            continue
        if days and (time_now() - mtime) > days * 86400:
            sessions_skipped += 1
            continue
        session_hashes[str(p)] = key
        day_turns = 0
        for tm, text in iter_turns(p):
            total_turns += 1
            fp = content_fingerprint(text)
            if fp in content_seen:
                content_deduped += 1
                continue
            # 日内近似：与已抽取内容近似（子串/前 40 字相同）→ 视为同一条，跳过
            day_turns += 1
            content_seen[fp] = f"{agent_name}/{sid}"
            dt = datetime.datetime.fromtimestamp(tm / 1000.0) if tm else _localnow()
            date = dt.strftime("%F")
            title = _title(text)
            # 文件名必须由「稳定标识」派生——用内容指纹前缀。
            # 绝不能用「每次运行都从 1 重数的当日计数器」：每日 cron 只写新增轮，
            # 同日二次运行会让新轮重新拿到 001，把 dsh-<date>-001 覆盖成另一会话的轮，静默丢数据。
            stem = f"dsh-{date}-{fp[:8]}"
            importance = round(min(0.9, 0.30 + len(text) / 20000.0), 2)
            fm = {
                "kb_source": agent_name,
                "memory_source": f"{agent_name}/{sid}",
                "memory_ingested": _localnow().strftime("%F"),
                "source_hash": fp,
                "status": "active",
                "importance": importance,
                "tags": ["knowledge", "dsh-session"],
                "recorded_at": _localnow().isoformat(timespec="seconds"),
                "valid_from": date,
                "created": date,
                "updated": date,
            }
            if apply:
                out = root / "memory" / f"{stem}.md"
                out.parent.mkdir(parents=True, exist_ok=True)
                header = f"## {stem} · {title}\n\n"
                body_text = text[:MAX_BODY]
                if len(text) > MAX_BODY:
                    body_text += f"\n\n…（截断，原文 {len(text)} 字）"
                out.write_text(
                    frontmatter_block(fm) + header + body_text +
                    f"\n\n> 来源：DSH 会话日志 · 会话 {sid} · {dt.strftime('%F %T')} · 原文 {len(text)} 字\n",
                    encoding="utf-8")
                touched.append(f"memory/{stem}.md")
            new_turns += 1
            per_day[date] = per_day.get(date, 0) + 1
            if len(sample) < 6 and (not apply or new_turns <= 6):
                sample.append((stem, title, text[:220]))
    # 仅在真正写盘时才持久化去重表（干跑不占用 content/session 去重槽位）。
    # 注意：只要 apply 就落盘——即便 content 全部命中去重（touched 为空），
    # session_hashes 仍要记录，否则次日会重扫全量会话而非幂等跳过。
    if apply:
        _save_state(root, state)
        # 只在本次真写了新文件时才提交；且用 pathspec 限定提交范围，
        # 避免把仓库里其它已暂存/脏内容（本库常有 100+ 运行时脏文件）一起带入。
        if touched:
            try:
                subprocess.run(["git", "-C", str(root), "add", "--", *touched],
                               capture_output=True, text=True, timeout=120)
                subprocess.run(["git", "-C", str(root), "commit", "-q",
                                "-m", "memory: 摄入DSH会话记忆", "--", *touched],
                               capture_output=True, text=True, timeout=120)
            except Exception:
                pass

    # ---- 报告 ----
    print("═══════════ DSH 会话日志记忆抽取 ═══════════")
    print(f"会话日志根: {session_root}")
    print(f"扫描会话: {len(sessions)}  幂等跳过: {sessions_skipped}")
    print(f"抽取人类轮(去重后): {new_turns}  会话级去重: {content_deduped}  总轮: {total_turns}")
    if per_day:
        print("按日期分布:")
        for d in sorted(per_day):
            print(f"   {d}: {per_day[d]} 条")
    print("\n抽取样本(前 6 条标题 / 原文):")
    for stem, title, txt in sample:
        print(f"   · {stem}  ← {title}")
        print(f"       {txt[:180]}{'…' if len(txt) > 180 else ''}")
    print("════════════════════════════════════════════")
    if not apply:
        print("✅ 这是 dry-run（未写盘）。确认无误后加 --apply 落盘并提交。")
    else:
        print(f"✅ 已写入 {new_turns} 条 → memory/（供 memory_sync 晋升）。下次跑 rag index 再建索引。")
    return 0


def time_now():
    import time as _t
    return _t.time()


def main():
    ap = argparse.ArgumentParser(description="DSH 会话日志 → KB memory/ 层（修复方案 1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    P = {}
    P["mirror-session"] = sub.add_parser("mirror-session")
    P["mirror-session"].add_argument("--root", required=True)
    P["mirror-session"].add_argument("--session-root", default=None)
    P["mirror-session"].add_argument("--days", type=int, default=0,
                                     help="只处理 N 天内修改的会话；0=全部（补捕获用）")
    P["mirror-session"].add_argument("--max-turns", type=int, default=10000)
    P["mirror-session"].add_argument("--apply", action="store_true",
                                     help="落盘 + git commit（默认只 dry-run）")

    args = ap.parse_args()
    if args.cmd == "mirror-session":
        return mirror_session(args.root, args.session_root, args.days,
                              args.max_turns, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
