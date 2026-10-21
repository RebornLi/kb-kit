#!/usr/bin/env python3
"""
memory_distill_session.py —— 用本地 LLM 把 DSH 会话「蒸馏」成声明式决定/经验

为什么需要它：
  DSH 运行时每天在 ~/.dsh/sessions/ 写原始对话日志（session.v4.jsonl.zstd），
  其中人类轮是任务请求（"检查一下…"），密度低、无复用价值。要让它够格成为知识库
  正典，必须先蒸馏成声明式结论（决定/规则/经验/教训）。这一步用本地模型（ornith）
  完成，不依赖外部 API。

  原始人类轮原样落盘（memory/dsh-*.md）的 mirror 流程已于 2026-10-19 永久停用；
  本模块直接读会话日志、解析人类轮、蒸馏后落 memory/dshd-*.md。

分工与状态隔离：
  - 蒸馏结论：  本模块 → memory/dshd-<date>-<hash8>.md（status: active，可晋升）
  - 独立 state：memory_distill_state.json

  解析工具（解压、人类轮抽取、噪声过滤、会话元信息）复用 memory_ingest_session。

用法：
  python3 memory_distill_session.py distill-session --root R \
      [--session-root S] [--days N] [--limit N] [--apply]
  # 默认 dry-run：只统计与预览，不写盘；--apply 才落盘 + git commit

环境变量：
  KB_SESSION_ROOT   会话日志根（默认 ~/.dsh/sessions）
  ORNITH_BASE_URL   本地 vLLM 地址（如 http://127.0.0.1:8000/v1）
  ORNITH_API_KEY    本地 vLLM 令牌
  ORNITH_CHAT_MODEL 模型名（默认 ornith1.5-35b）
"""
import argparse, datetime, json, os, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_ingest import frontmatter_block
from memory_ingest_session import (
    DEFAULT_SESSION_ROOT, content_fingerprint, decompress, is_noise, _real_text,
    session_meta,
)

_STATE_NAME = "memory_distill_state.json"
MAX_DIGEST = 6000          # 送进模型的会话文本上限（head+tail 采样）
MAX_STMTS = 12             # 单会话最多产出多少条结论
MIN_DIGEST = 200           # 文本太短不值得蒸馏
MAX_STMT_CHARS = 160       # 单条结论上限
CHAT_TIMEOUT = 300

SYSTEM_PROMPT = (
    "你是知识提炼助手。从 agent 会话中抽取可长期复用的「决定/规则/经验/教训」，"
    "只输出结论本身，不要解释、不要复述任务、不要输出过程。"
)
USER_PROMPT = """从下面这段 agent 会话中，抽取值得长期记住的「声明式结论」：
- 取：做出的决定、确立的规则/约定、踩坑教训、可复用的做法
- 弃：任务请求、寒暄、过程流水、代码/日志片段
- 每条一行，以「- 」开头；至多 %d 条；按重要性排序
- 若确无可提炼结论，只输出：NONE

会话：
%s
""" % (MAX_STMTS, "%s")


# ------------------------------------------------------------
# 独立 state（与 memory_session_state.json / memory_state.json 隔离）
# ------------------------------------------------------------
def _load_state(root: Path):
    default = {"session_hashes": {}, "note_hashes": {}}
    try:
        from state_manager import StateStore
        store = StateStore(root)
        return store.load(_STATE_NAME, default) if store.exists(_STATE_NAME) else default
    except Exception:
        p = Path(root) / ".kb" / "state" / _STATE_NAME
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return default


def _save_state(root: Path, state):
    try:
        from state_manager import StateStore
        StateStore(root).save(_STATE_NAME, state)
    except Exception:
        (Path(root) / ".kb" / "state").mkdir(parents=True, exist_ok=True)
        (Path(root) / ".kb" / "state" / _STATE_NAME).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------
# 本地模型调用（best-effort：缺配置/不可达 → 返回 (None, 原因)）
# ------------------------------------------------------------
def llm_ready():
    return bool((os.environ.get("ORNITH_API_KEY") or "").strip()
                and (os.environ.get("ORNITH_BASE_URL") or "").strip())


def _chat(prompt):
    """返回 (content 或 None, 原因)。"""
    key = (os.environ.get("ORNITH_API_KEY") or "").strip()
    url = (os.environ.get("ORNITH_BASE_URL") or "").strip()
    model = (os.environ.get("ORNITH_CHAT_MODEL") or "ornith1.5-35b").strip()
    if not key or not url:
        return None, "未设 ORNITH_API_KEY/ORNITH_BASE_URL（蒸馏跳过，退回只落原始轮）"
    try:
        import urllib.request
        req = urllib.request.Request(
            url.rstrip("/") + "/chat/completions",
            data=json.dumps({
                "model": model,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}],
                "temperature": 0.2, "max_tokens": 2048,
            }).encode("utf-8"),
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"})
        raw = urllib.request.urlopen(req, timeout=CHAT_TIMEOUT).read().decode("utf-8", "replace")
        return (json.loads(raw)["choices"][0]["message"].get("content") or "").strip(), ""
    except Exception as e:  # 网络/解析/超时一律降级，不打断流水线
        return None, f"调用 {url} 失败({e})"


# ------------------------------------------------------------
# 会话 → 文本摘要（只取真实说话内容：用户轮 + 助手正式回复）
# ------------------------------------------------------------
def session_digest(path, budget=MAX_DIGEST):
    parts = []
    for line in decompress(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = e.get("type")
        if et == "user/message":
            kind = (e.get("data") or {}).get("source", {}).get("kind")
            if kind and kind != "user":
                continue
            t = _real_text((e.get("data") or {}).get("content") or [])
            if t and not is_noise(t):
                parts.append("用户：" + t)
        elif et == "assistant/message":
            msg = (e.get("data") or {}).get("message") or {}
            for b in (msg.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "text":
                    t = (b.get("text") or "").strip()
                    if t and not is_noise(t):
                        parts.append("助手：" + t)
    blob = "\n".join(parts)
    if len(blob) <= budget:
        return blob
    head = blob[: budget // 3]
    tail = blob[-(budget * 2 // 3):]
    return head + "\n…（中间略）…\n" + tail


def parse_statements(text):
    """把模型输出解析成结论列表（容忍 markdown 记号/编号）。"""
    if not text:
        return []
    out = []
    for line in text.splitlines():
        s = line.strip().lstrip("-*•").strip()
        s = s.lstrip("0123456789.、) ").strip()
        if not s or s.upper() == "NONE":
            continue
        if len(s) < 6:
            continue
        out.append(s[:MAX_STMT_CHARS])
    # 去重保序
    seen, uniq = set(), []
    for s in out:
        k = content_fingerprint(s)
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq[:MAX_STMTS]


def distill_session(root, session_root=None, days=0, limit=0, apply=False,
                    agent_name="dsh-distill"):
    root = Path(root)
    state = _load_state(root)
    session_hashes = state.setdefault("session_hashes", {})
    note_hashes = state.setdefault("note_hashes", {})
    # 抗状态丢失：把磁盘上已有的蒸馏笔记指纹并入去重表。
    # （回填中途中断/强杀时 state 可能落后于已写文件；并入后重跑不会产生重复笔记）
    for f in sorted((root / "memory").glob("dshd-*.md")):
        try:
            m = re.search(r"^source_hash:\s*(\S+)", f.read_text(encoding="utf-8"), re.M)
        except OSError:
            continue
        if m:
            note_hashes.setdefault(m.group(1), f"existing/{f.name}")

    if not session_root:
        session_root = os.environ.get("KB_SESSION_ROOT") or DEFAULT_SESSION_ROOT
    sessions = sorted(Path(session_root).rglob("session*.jsonl.zstd")) \
        if Path(session_root).is_dir() else []

    ready = llm_ready()
    skipped, processed, wrote, failed, deduped = 0, 0, 0, 0, 0
    per_day, sample, touched = {}, [], []

    for p in sessions:
        if limit and processed >= limit:
            break                                  # 分批回填：本次最多蒸馏 N 个会话
        sid, mtime, size = session_meta(p)
        key = f"{sid}::{mtime:.0f}::{size}"
        if session_hashes.get(str(p)) == key:
            skipped += 1
            continue
        digest = session_digest(p)
        if len(digest) < MIN_DIGEST:
            session_hashes[str(p)] = key          # 太短，不必蒸馏，记已处理
            skipped += 1
            continue
        if not ready:
            break                                  # 无模型：整体跳过（不写 state，下次再试）
        print(f"   [{processed + 1}] 蒸馏 {sid[:44]} (digest {len(digest)}) …", flush=True)
        stmts, err = _chat(USER_PROMPT % digest)
        processed += 1
        if err:
            failed += 1
            print(f"   ⚠️ {sid}: {err}")
            continue
        stmts = parse_statements(stmts)
        if not stmts:
            session_hashes[str(p)] = key
            continue
        body = "\n".join(f"- {s}" for s in stmts)
        fp = content_fingerprint(body)
        if fp in note_hashes:
            deduped += 1
            session_hashes[str(p)] = key
            continue
        dt = datetime.datetime.fromtimestamp(mtime) if mtime else datetime.datetime.now()
        date = dt.strftime("%F")
        stem = f"dshd-{date}-{fp[:8]}"
        note_hashes[fp] = f"{agent_name}/{sid}"
        fm = {
            "kb_source": agent_name,
            "memory_source": f"{agent_name}/{sid}",
            "memory_ingested": datetime.datetime.now().strftime("%F"),
            "source_hash": fp,
            "status": "active",
            "distilled": "true",
            "origin_session": sid,
            "importance": 0.6,
            "tags": ["knowledge", "dsh-distill"],
            "recorded_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "valid_from": date,
            "created": date,
            "updated": date,
        }
        title = stmts[0][:48]
        if apply:
            outp = root / "memory" / f"{stem}.md"
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(
                frontmatter_block(fm)
                + f"## {stem} · {title}\n\n{body}\n\n"
                + f"> 来源：DSH 会话蒸馏 · 会话 {sid} · {dt.strftime('%F %T')} · 本地模型提炼 {len(stmts)} 条\n",
                encoding="utf-8")
            touched.append(f"memory/{stem}.md")
        session_hashes[str(p)] = key               # 无条件记已蒸馏（否则重跑会重复产出）
        wrote += 1
        per_day[date] = per_day.get(date, 0) + 1
        if apply and wrote % 5 == 0:
            _save_state(root, state)               # 增量落盘：中断后可续、不重复
        if len(sample) < 6:
            sample.append((stem, stmts))

    if apply:
        _save_state(root, state)
        if touched:
            try:
                subprocess.run(["git", "-C", str(root), "add", "--", *touched],
                               capture_output=True, text=True, timeout=120)
                subprocess.run(["git", "-C", str(root), "commit", "-q",
                                "-m", "memory: 蒸馏DSH会话结论", "--", *touched],
                               capture_output=True, text=True, timeout=120)
            except Exception:
                pass

    # ---- 报告 ----
    print("═══════════ DSH 会话蒸馏（本地模型）═══════════")
    print(f"会话日志根: {session_root}   模型: {'可用' if ready else '未配置'}")
    print(f"扫描会话: {len(sessions)}  幂等跳过: {skipped}  已蒸馏: {processed}")
    print(f"产出结论笔记: {wrote}  跨会话重复: {deduped}  失败: {failed}")
    if per_day:
        print("按日期分布:")
        for d in sorted(per_day):
            print(f"   {d}: {per_day[d]} 篇")
    if sample:
        print("\n样本:")
        for stem, stmts in sample:
            print(f"   · {stem}")
            for s in stmts[:3]:
                print(f"       - {s}")
    print("════════════════════════════════════════════")
    if not apply:
        print("✅ 这是 dry-run（未写盘）。确认无误后加 --apply 落盘并提交。")
    else:
        print(f"✅ 已写入 {wrote} 篇 → memory/（status: active，可被 promote 晋升）。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="DSH 会话 → 本地 LLM 蒸馏 → memory/ 可晋升结论")
    sub = ap.add_subparsers(dest="cmd", required=True)
    P = sub.add_parser("distill-session")
    P.add_argument("--root", required=True)
    P.add_argument("--session-root", default=None)
    P.add_argument("--days", type=int, default=0, help="只处理 N 天内修改的会话；0=全部")
    P.add_argument("--limit", type=int, default=0, help="最多蒸馏 N 个会话（0=不限，便于分批回填）")
    P.add_argument("--apply", action="store_true", help="落盘 + git commit（默认 dry-run）")
    a = ap.parse_args()
    if a.cmd == "distill-session":
        return distill_session(a.root, a.session_root, a.days, a.limit, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
