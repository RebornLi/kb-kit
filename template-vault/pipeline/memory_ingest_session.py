#!/usr/bin/env python3
"""
memory_ingest_session.py — DSH 会话日志解析工具集（供 memory_distill_session 蒸馏复用）

背景：原 mirror_session 入库流程（把人类轮原样落 memory/dsh-*.md）已于 2026-10-19
永久停用——原样落盘的「任务请求」密度低、无复用价值，改由 memory_distill_session
用本地 LLM 蒸馏成声明式结论（memory/dshd-*.md）入库。本模块现在只保留蒸馏所需的
共享工具：会话日志解压、人类轮抽取与噪声过滤、内容指纹、会话元信息。

被 memory_distill_session 复用的符号：
  DEFAULT_SESSION_ROOT, content_fingerprint, decompress, is_noise, _real_text, session_meta
"""
import re, sys, hashlib, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from kb_text import content_fingerprint
except Exception:  # pragma: no cover - 兜底
    def content_fingerprint(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()


DEFAULT_SESSION_ROOT = str(Path("~").expanduser() / ".dsh" / "sessions")
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


def session_meta(path: Path):
    name = path.parent.name
    try:
        sess = name.split("--", 1)[-1] if "--" in name else name
        sid = sess.split("/")[0] if sess else name
    except Exception:
        sid = name
    return sid, path.stat().st_mtime, path.stat().st_size
