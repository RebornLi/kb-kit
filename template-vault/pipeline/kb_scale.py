#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_scale.py — 兼容垫片（P2-2 研究层移包后保留）。

实现已移到 `pipeline/_research/kb_scale.py`。本文件只做 re-export，
使既有调用方零改动：
  · `ladder.py` / `adjudicate.py` 的 `__import__("kb_xxx")` 裸模块名；
  · 测试里的 `import kb_engine` / `from kb_fitness import ...`；
  · 部署到旧 vault 时按旧路径 exec_module 的外部脚本。

新代码请直接 `from _research.<name> import ...`。
"""
from _research.kb_scale import *  # noqa: F401,F403
from _research.kb_scale import (  # noqa: F401
    _doc_hash_set,
    _index_path,
    _load_index,
)


# ── CLI 转发 ────────────────────────────────────────────────
# 原文件是可直接运行的脚本（`python3 pipeline/kb_l4.py docket --root R`）。
# 移包后若不加这段，跑原路径会静默退出 0（什么都不做）——
# scripts/verify_curate.py 的 P3 检查就是这么坏的（stdout 为空 → 判 FAIL）。
if __name__ == "__main__":
    import sys as _sys
    from _research.kb_scale import main as _main
    _sys.exit(_main())
