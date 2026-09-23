#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""classify.py — 内容分类器：12 种 content_type，规则 + 信号词 + 结构特征。

零依赖（只用 Python 标准库），不依赖 LLM。
信号词从 reference/content-type-signals.json 加载，配置缺失时用内置默认值降级。

12 种 content_type：
  - chat:          简短对话（<200字，含问句/应答词）
  - chat_decision: 聊天中的决策
  - chat_knowledge:聊天中的知识
  - chat_todo:     聊天中的待办
  - decision:      决策记录
  - lesson:        经验教训
  - case:          案例/案综
  - policy:        制度/纪律
  - reference:     参考资料
  - knowledge:     知识沉淀
  - todo:          待办/计划
  - noise:         噪声
"""
import json, re, sys
from dataclasses import dataclass, field
from pathlib import Path


# ── 默认信号词（配置文件缺失时降级用）────────────────────────
DEFAULT_SIGNALS = {
    "chat_decision": ["决定", "方案", "确认", "就这么定", "同意", "通过", "定了", "敲定", "拍板"],
    "chat_knowledge": ["原理", "规则", "流程", "注意", "机制", "原因", "因为", "说明"],
    "chat_todo": ["TODO", "下次", "安排", "负责", "待办", "跟进", "要做", "需要"],
    "decision": ["决定", "决策", "选择", "方案", "敲定", "拍板", "确定", "决议"],
    "lesson": ["复盘", "教训", "根因", "踩坑", "经验", "反思", "总结", "改进"],
    "case": ["案", "判决", "裁定", "当事人", "原告", "被告", "案综", "案号", "审理"],
    "policy": ["必须", "禁止", "规范", "要求", "时效", "不得", "严禁", "应当", "需要"],
    "knowledge": ["原理", "机制", "规则", "流程", "定义", "概念", "说明", "结构"],
    "todo": ["TODO", "计划", "下一步", "安排", "待办", "跟进", "要做", "需要"],
}


@dataclass
class ClassifyResult:
    """分类结果。"""
    content_type: str           # 12 种之一
    signal_density: float       # 信号密度（命中信号词字符数 / 正文总字符数）
    confidence: float           # 置信度 0-1
    matched_signals: list       # 命中的信号词列表


# ── 聊天上下文检测正则 ──────────────────────────────────────
# 匹配 [2026-09-10 14:30] 张三: 消息内容
CHAT_LINE_RE = re.compile(r"\[\d{4}[-/]\d{2}[-/]\d{2}[^\]]*\]\s*\S+:\s")
# 匹配 14:30 张三: 消息
CHAT_LINE_RE2 = re.compile(r"\d{1,2}:\d{2}\s+\S+:\s")


def _load_signals(signals_path):
    """从 JSON 加载信号词，缺失/损坏时降级用 DEFAULT_SIGNALS。"""
    if signals_path is None:
        return DEFAULT_SIGNALS
    p = Path(signals_path)
    if not p.exists():
        return DEFAULT_SIGNALS
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return DEFAULT_SIGNALS
        # 合并：用户配置覆盖默认值
        merged = dict(DEFAULT_SIGNALS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return DEFAULT_SIGNALS


def _signal_density(body, all_signals):
    """信号密度 = 命中信号词字符数 / 正文总字符数。"""
    if not body.strip():
        return 0.0
    nchars = len(body.replace("\n", "").replace(" ", ""))
    if nchars == 0:
        return 0.0
    matched_chars = sum(len(sig) for sig in all_signals if sig in body)
    return matched_chars / nchars


def _detect_chat_context(fm, body):
    """检测是否聊天上下文。

    1. frontmatter 含 participants/chat_time/channel → True
    2. body 匹配 [时间] 人: 消息 模式（≥3 行）→ True
    3. 否则 → False
    """
    if fm:
        if any(fm.get(k) for k in ("participants", "chat_time", "channel")):
            return True
        # frontmatter source_format 含 chat 关键字
        sf = str(fm.get("source_format", "")).lower()
        if "chat" in sf or "wechat" in sf or "dingtalk" in sf or "feishu" in sf or "slack" in sf:
            return True
    # 检测聊天行模式（至少 3 行匹配才算聊天上下文）
    lines = body.splitlines()
    chat_lines = sum(1 for ln in lines if CHAT_LINE_RE.search(ln) or CHAT_LINE_RE2.search(ln))
    return chat_lines >= 3


def classify(body, fm=None, signals_path=None):
    """内容分类主入口。

    Args:
        body: 正文文本
        fm: frontmatter 字典（可选，用于聊天上下文检测）
        signals_path: 信号词配置文件路径（可选，None 用默认值）

    Returns:
        ClassifyResult
    """
    signals = _load_signals(signals_path)
    nchars = len(body.replace("\n", "").replace(" ", ""))

    # ① 基础过滤：噪声
    if nchars < 15:
        return ClassifyResult("noise", 0.0, 1.0, [])

    all_signals = set()
    for sigs in signals.values():
        all_signals.update(sigs)
    density = _signal_density(body, all_signals)
    if density < 0.05 and nchars < 200:
        return ClassifyResult("noise", density, 0.9, [])

    # ② 聊天上下文检测
    is_chat = _detect_chat_context(fm, body)

    # ③/④ 信号词匹配（按优先级）
    if is_chat:
        priority = ["chat_decision", "chat_knowledge", "chat_todo"]
        fallback = "chat"
    else:
        priority = ["policy", "case", "lesson", "decision", "knowledge", "todo"]
        fallback = "reference"

    best_match = None
    best_count = 0
    for ct in priority:
        matched = [s for s in signals.get(ct, []) if s in body]
        if len(matched) > best_count:
            best_match = (ct, matched)
            best_count = len(matched)

    if best_match:
        ct, matched = best_match
        confidence = min(0.5 + 0.1 * best_count, 0.95)
        return ClassifyResult(ct, density, confidence, matched)

    # 兜底
    return ClassifyResult(fallback, density, 0.5, [])


# ── 质量门禁（FR-3.0.13 + FR-3.0.14）────────────────────────
@dataclass
class QualityResult:
    """质量门禁结果。"""
    content_type: str
    quality_score: float
    should_ingest: bool
    reason: str


# 聊天样内容标记词
CHAT_MARKERS = {"嗯", "好的", "收到", "ok", "OK", "嗯嗯", "好", "行", "明白", "了解"}


def _is_chat_like(body):
    """检测是否聊天样内容：含应答词/问句/短行。"""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return False
    short_lines = sum(1 for l in lines if len(l) < 30)
    has_marker = any(m in body for m in CHAT_MARKERS)
    return has_marker or (short_lines > len(lines) * 0.7 and len(lines) > 2)


def classify_quality(body, fm=None, signals_path=None):
    """三维度质量门禁：信息熵 + 信号密度 + 长度。

    Returns:
        QualityResult(content_type, quality_score, should_ingest, reason)
    """
    from kb_common import text_entropy
    signals = _load_signals(signals_path)
    nchars = len(body.replace("\n", "").replace(" ", ""))

    all_signals = set()
    for sigs in signals.values():
        all_signals.update(sigs)
    density = _signal_density(body, all_signals)
    entropy = text_entropy(body)

    # ① 噪声过滤
    if nchars < 15:
        return QualityResult("noise", 0.0, False, "字数<15")
    if density < 0.05 and nchars < 200:
        return QualityResult("noise", 0.0, False, "信号密度<0.05且短文本")

    # ② 简短对话聚合
    if nchars < 200 and _is_chat_like(body):
        return QualityResult("chat", 0.3, True, "简短对话进聚合桶")

    # ③ 高质量知识
    if density >= 0.15 and entropy >= 0.7:
        return QualityResult("knowledge", 0.8, True, "高质量知识")

    # ④ 其他
    return QualityResult("reference", 0.5, True, "参考资料")


# ── CLI 入口（可独立测试）──────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="内容分类器")
    ap.add_argument("text", nargs="?", help="要分类的文本（缺省从 stdin 读）")
    ap.add_argument("--signals", default=None, help="信号词配置文件路径")
    ap.add_argument("--quality", action="store_true", help="运行质量门禁")
    args = ap.parse_args()
    text = args.text if args.text else sys.stdin.read()
    signals_path = args.signals or Path(__file__).resolve().parents[1] / "reference" / "content-type-signals.json"
    if args.quality:
        r = classify_quality(text, signals_path=signals_path)
        print(f"content_type: {r.content_type}")
        print(f"quality_score: {r.quality_score}")
        print(f"should_ingest: {r.should_ingest}")
        print(f"reason: {r.reason}")
    else:
        r = classify(text, signals_path=signals_path)
        print(f"content_type: {r.content_type}")
        print(f"signal_density: {r.signal_density:.4f}")
        print(f"confidence: {r.confidence:.2f}")
        print(f"matched_signals: {r.matched_signals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
