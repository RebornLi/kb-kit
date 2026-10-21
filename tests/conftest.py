#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/conftest.py — 测试公共辅助（P2-2 研究层移包后新增）。

提供 `research_src(name)`：读研究层模块的**真实源码**。

为什么需要它：一批"源码守卫"测试（P0 系列）用
`(PIPELINE / "kb_contradiction.py").read_text()` 直接读文件来钉实现细节
（常量存在、取尾部而非头部、走原子写等）。P2-2 把研究层模块移进
`pipeline/_research/` 后，原路径只剩 re-export 垫片，直接读会读到垫片本身
而不是实现 → 守卫集体失效（"看起来绿了但什么都没验"）。

`research_src()` 的做法：若 `<name>.py` 是垫片（内容含
`from _research.<name> import`），就改读 `_research/<name>.py`。
这样守卫始终打在真实实现上，且将来删除垫片也无需再改测试。
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"


def research_src(name: str) -> str:
    """读研究层模块的真实源码（穿透 re-export 垫片）。"""
    direct = PIPELINE / f"{name}.py"
    real = PIPELINE / "_research" / f"{name}.py"
    if real.exists():
        return real.read_text(encoding="utf-8")
    return direct.read_text(encoding="utf-8")


def research_path(name: str) -> Path:
    """研究层模块的真实路径（穿透垫片）。"""
    real = PIPELINE / "_research" / f"{name}.py"
    return real if real.exists() else PIPELINE / f"{name}.py"


@pytest.fixture
def pipeline_dir() -> Path:
    return PIPELINE
