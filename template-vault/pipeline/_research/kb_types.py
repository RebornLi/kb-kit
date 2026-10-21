#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_types.py — 跨模块共享的类型定义中心。

为 frontmatter、笔记记录、相似度结果等数据结构提供 TypedDict 和 dataclass 定义，
消除 dict 无类型标注问题。所有类型使用 Python 3.8+ 兼容语法。

使用方式:
    from kb_types import Frontmatter, NoteInfo, NoteRecord, SimResult, Vec
"""

# ── 路径自举（P2-2 移包）────────────────────────────────────
# 本文件被**直接** `python3 pipeline/_research/<name>.py` 运行时，sys.path[0]
# 是 _research/ 而不是 pipeline/，裸 import 生产层会失败。走垫片
# （`python3 pipeline/<name>.py`）或经 `import _research` 则没有这个问题，
# 但补上这段让两种跑法都成立。
import os as _os
import sys as _sys
_PIPE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PIPE not in _sys.path:
    _sys.path.insert(0, _PIPE)
from typing import TypedDict, Optional, List, Dict, Any
from dataclasses import dataclass


class Frontmatter(TypedDict, total=False):
    """Markdown frontmatter 字段定义。

    total=False 允许字段缺失，匹配现有 frontmatter 可选字段语义。
    """
    domain: str
    category: str
    importance: str
    created: str
    updated: str
    tags: str
    kb_summary: str
    content_type: str
    related_to: str
    prerequisite: str
    supersedes: str
    superseded_by: str


class NoteInfo(TypedDict):
    """笔记信息（TypedDict 形式，兼容现有 dict 返回值）。"""
    rel: str
    fm: Frontmatter
    text: str
    body: str


@dataclass
class NoteRecord:
    """笔记记录（dataclass 形式，用于需要字段访问的场景）。"""
    rel: str
    fm: Dict[str, Any]
    text: str
    body: str
    domain: str
    tags: List[str]


@dataclass
class SimResult:
    """相似度结果（用于 link_engine / kb_health / kb_contradiction 输出）。"""
    score: float
    from_rel: str
    to_rel: str
    cross_domain: bool
    shared_tags: List[str]


# 向量类型别名：稀疏 dict 形式 {token: weight}
Vec = Dict[str, float]
