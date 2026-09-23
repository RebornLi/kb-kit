#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_chat.py — 聊天记录解析器：聊天导出文件 → 结构化笔记。

支持 5 平台适配：企业微信/钉钉/飞书/Slack/通用。
按时间间隔 >30 分钟分段，每段调 classify.classify() 分类提取
chat_decision / chat_knowledge / chat_todo 笔记。

零依赖（只用 Python 标准库）。
"""
import csv, json, re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import classify


@dataclass
class Message:
    """单条聊天消息。"""
    timestamp: datetime
    sender: str
    content: str
    thread_ts: str | None = None  # Slack 回复关系


@dataclass
class ChatSegment:
    """对话段（按时间间隔切分）。"""
    messages: list
    start_time: datetime
    end_time: datetime
    participants: list


@dataclass
class ChatNote:
    """提取的聊天笔记。"""
    content_type: str           # chat_decision / chat_knowledge / chat_todo
    frontmatter: dict
    body: str


# ── 时间解析 ───────────────────────────────────────────────
def _parse_time(s):
    """解析多种时间格式，失败返回 None。"""
    s = s.strip()
    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # 尝试 fromisoformat
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except (ValueError, TypeError):
        return None


# ── 平台适配器 ─────────────────────────────────────────────
def adapt_wechat_work(path):
    """企业微信 CSV：时间/人/消息 三列。"""
    messages = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            ts = _parse_time(row[0])
            if ts is None:
                continue
            messages.append(Message(ts, row[1].strip(), row[2].strip()))
    return messages


def adapt_dingtalk(path):
    """钉钉 JSON：msgid/sender/content 数组。"""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    messages = []
    items = data if isinstance(data, list) else data.get("messages", data.get("records", []))
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = _parse_time(str(item.get("time", item.get("timestamp", item.get("sendTime", "")))))
        if ts is None:
            continue
        sender = item.get("sender", item.get("from", item.get("senderNick", "")))
        content = item.get("content", item.get("text", item.get("message", "")))
        messages.append(Message(ts, str(sender), str(content)))
    return messages


def adapt_feishu(path):
    """飞书 JSON：message 数组。"""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    messages = []
    items = data if isinstance(data, list) else data.get("messages", data.get("data", []))
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = _parse_time(str(item.get("create_time", item.get("timestamp", item.get("msg_time", "")))))
        if ts is None:
            # 飞书 create_time 可能是毫秒时间戳
            ct = item.get("create_time", item.get("timestamp", ""))
            try:
                ts = datetime.fromtimestamp(int(str(ct)) / 1000)
            except (ValueError, TypeError, OSError):
                continue
        sender = item.get("sender_id", item.get("from", item.get("user_id", "")))
        content = item.get("content", item.get("text", item.get("body", "")))
        messages.append(Message(ts, str(sender), str(content)))
    return messages


def adapt_slack(path):
    """Slack JSON：messages 数组（含 thread_ts 回复关系）。"""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    messages = []
    items = data if isinstance(data, list) else data.get("messages", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        # Slack ts 是浮点字符串（如 "1694350200.000100"）
        ts_str = str(item.get("ts", ""))
        try:
            ts = datetime.fromtimestamp(float(ts_str))
        except (ValueError, TypeError):
            ts = _parse_time(str(item.get("datetime", "")))
        if ts is None:
            continue
        sender = item.get("user", item.get("username", ""))
        content = item.get("text", "")
        thread_ts = item.get("thread_ts")
        messages.append(Message(ts, str(sender), str(content), thread_ts))
    return messages


# 通用 TXT：[时间] 人: 消息
GENERIC_LINE_RE = re.compile(r"\[(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?):\s*(.*)")


def adapt_generic(path):
    """通用 TXT：[时间] 人: 消息。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    messages = []
    for line in text.splitlines():
        m = GENERIC_LINE_RE.match(line)
        if not m:
            continue
        ts = _parse_time(m.group(1))
        if ts is None:
            continue
        messages.append(Message(ts, m.group(2).strip(), m.group(3).strip()))
    return messages


ADAPTERS = {
    "wechat_work": adapt_wechat_work,
    "dingtalk": adapt_dingtalk,
    "feishu": adapt_feishu,
    "slack": adapt_slack,
    "generic": adapt_generic,
}


def _detect_platform(file_path):
    """自动探测聊天平台（按文件名关键词）。"""
    name = file_path.name.lower()
    if "wechat" in name or "wework" in name or "企业微信" in name:
        return "wechat_work"
    if "dingtalk" in name or "ding" in name or "钉钉" in name:
        return "dingtalk"
    if "feishu" in name or "lark" in name or "飞书" in name:
        return "feishu"
    if "slack" in name:
        return "slack"
    # 按扩展名兜底
    ext = file_path.suffix.lower()
    if ext == ".csv":
        return "wechat_work"  # CSV 默认按企业微信
    if ext == ".json":
        return "slack"  # JSON 默认按 Slack
    return "generic"


# ── 对话分段 ───────────────────────────────────────────────
def _make_segment(messages):
    """构造 ChatSegment。"""
    participants = sorted(set(m.sender for m in messages if m.sender))
    return ChatSegment(
        messages=messages,
        start_time=messages[0].timestamp,
        end_time=messages[-1].timestamp,
        participants=participants,
    )


def _split_long_segment(segment, max_chars):
    """超长段按消息条数二次切分。"""
    result = []
    current = []
    current_chars = 0
    for msg in segment.messages:
        msg_chars = len(msg.content)
        if current and current_chars + msg_chars > max_chars:
            result.append(_make_segment(current))
            current = [msg]
            current_chars = msg_chars
        else:
            current.append(msg)
            current_chars += msg_chars
    if current:
        result.append(_make_segment(current))
    return result


def _segment(messages, gap_minutes=30, max_segment_chars=5000):
    """按时间间隔切分对话段。

    Args:
        messages: 消息列表
        gap_minutes: 相邻消息间隔超过此分钟数则切分（默认 30）
        max_segment_chars: 单段最大字符数，超过则二次切分（默认 5000）

    Returns:
        list[ChatSegment]
    """
    if not messages:
        return []
    messages = sorted(messages, key=lambda m: m.timestamp)
    segments = []
    current = [messages[0]]
    for prev, msg in zip(messages, messages[1:]):
        gap = (msg.timestamp - prev.timestamp).total_seconds() / 60
        if gap > gap_minutes:
            segments.append(_make_segment(current))
            current = [msg]
        else:
            current.append(msg)
    if current:
        segments.append(_make_segment(current))
    # 二次切分超长段
    result = []
    for seg in segments:
        total = sum(len(m.content) for m in seg.messages)
        if total > max_segment_chars:
            result.extend(_split_long_segment(seg, max_segment_chars))
        else:
            result.append(seg)
    return result


# ── 段分类提取 ─────────────────────────────────────────────
def _classify_segment(segment, channel="", source_format=""):
    """对段内消息分类，提取 chat_decision/chat_knowledge/chat_todo 笔记。

    chat 噪声丢弃（不提取）。
    """
    body = "\n".join(
        f"[{m.timestamp:%H:%M}] {m.sender}: {m.content}"
        for m in segment.messages
    )
    fm = {
        "participants": segment.participants,
        "chat_time": segment.start_time.isoformat(),
        "channel": channel,
        "source_format": source_format,
    }
    result = classify.classify(body, fm=fm)
    if result.content_type in ("chat_decision", "chat_knowledge", "chat_todo"):
        note_fm = dict(fm)
        note_fm["content_type"] = result.content_type
        return [ChatNote(result.content_type, note_fm, body)]
    return []  # chat 噪声丢弃


# ── 主入口 ─────────────────────────────────────────────────
def parse_chat(file_path, platform=None, channel=""):
    """聊天记录解析主入口。

    Args:
        file_path: 聊天导出文件路径
        platform: 平台名（None 自动探测）
        channel: 频道名（可选，写入 frontmatter）

    Returns:
        list[ChatNote]
    """
    path = Path(file_path)
    if platform is None:
        platform = _detect_platform(path)
    adapter = ADAPTERS.get(platform, adapt_generic)
    try:
        messages = adapter(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, KeyError, IndexError):
        # 解析失败，尝试通用适配
        messages = adapt_generic(path)
    if not messages:
        return []
    segments = _segment(messages)
    notes = []
    for seg in segments:
        notes.extend(_classify_segment(seg, channel=channel, source_format=platform))
    return notes


# ── CLI 入口 ───────────────────────────────────────────────
def main():
    import argparse, sys
    ap = argparse.ArgumentParser(description="聊天记录解析器")
    ap.add_argument("file", help="聊天导出文件路径")
    ap.add_argument("--platform", default=None, help="平台（wechat_work/dingtalk/feishu/slack/generic）")
    ap.add_argument("--channel", default="", help="频道名")
    args = ap.parse_args()
    notes = parse_chat(args.file, args.platform, args.channel)
    if not notes:
        print("无提取笔记（全部为噪声或解析失败）")
        return 0
    print(f"提取 {len(notes)} 条笔记：")
    for i, note in enumerate(notes, 1):
        print(f"\n--- 笔记 {i} ({note.content_type}) ---")
        print(f"frontmatter: {note.frontmatter}")
        print(f"body (前 200 字): {note.body[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
