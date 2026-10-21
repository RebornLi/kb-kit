#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_types.py — 兼容垫片（P2-2 研究层移包后保留）。

实现已移到 `pipeline/_research/kb_types.py`。本文件只做 re-export，
使既有调用方零改动：
  · 测试里的 `import kb_types` / `from kb_types import ...`；
  · 部署到旧 vault 时按旧路径 exec_module 的外部脚本。

纯类型定义库，**没有 CLI**（原文件也没有 `__main__`），故不需要 CLI 转发。

新代码请直接 `from _research.kb_types import ...`。
"""
from _research.kb_types import *  # noqa: F401,F403
