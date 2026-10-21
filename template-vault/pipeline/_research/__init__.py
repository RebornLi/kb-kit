#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_research — 研究层（内部包，P2-2 边界固化）。

这里放的是**实验/研究性质**的模块：反馈阶梯 L0–L5、RSI 校准与执行、
适应度函数、漂移度量、矛盾检测、外部抓取等。它们的共同特征：

  · 没有稳定的 CLI 契约（不在 `COMPAT_MAP` 里，也没有 plugin wrapper）；
  · 由「研究层内部」或 CLI 入口的动态导入驱动（`ladder` / `adjudicate`）；
  · 只有测试直接引用，生产路径不依赖它们。

**边界规则**（为什么要有这个包）：这些模块与核心引擎共享
`kb_io` / `kb_text` / `kb_metautil` / `state_manager` 等公共层，但它们是
*可实验、可删除*的 —— 一旦实验结论被证伪，整个子包应该能整体摘掉而不
影响生产路径。放进 `_research/` 就是让这件事在**目录结构**上可见，
而不是靠 `tests/test_reachability.py` 里一份会腐化的手写清单。

依赖方向（允许）：
    _research.*  → 生产公共层（kb_io / kb_text / kb_metautil / state_manager …）
    _research.*  → _research.*（研究层内部互链，如 kb_usage）
    生产模块    → _research.*（**只允许** 经 ladder / adjudicate 的动态导入，
                            静态 import 视为边界破坏）

依赖方向（禁止）：
    生产公共层 → _research.*     （公共层绝不能被实验代码拖住）

`pipeline/` 根下的 `kb_l4.py` 等同名文件是**兼容垫片**（re-export 本包
模块），为的是让历史调用方零改动：
  · `ladder.py:56` / `adjudicate.py:337` 的 `__import__("kb_l4")` 裸模块名；
  · 测试里的 `import kb_engine` / `from kb_fitness import …`；
  · 部署到旧 vault 时按旧路径 `exec_module` 的外部脚本。
垫片会在调用方全部迁移后删除（同 §九 ① 的做法）。

注意 `kb_worker.py` **不在**本包：它由 `kb-context-dsh/src/worker.js`
按绝对路径 `${kbRoot}/pipeline/kb_worker.py` spawn 进来，移动路径会打断
外部进程。它由 `test_reachability.INTERNAL_MODULES` 单独声明为 internal。
"""
# 让 `_research/` 内的模块继续用裸模块名 import 生产层（kb_io / kb_text / …）
# 与研究层同伴（kb_usage / kb_engine …）—— 移包不改动 17 个文件的 import 语句。
#
# 两个来源分开处理：
#   1. 生产层（kb_io / kb_text / state_manager / rag …）
#      → 把 `pipeline/` 加进 sys.path，裸名自然解析到真实文件；
#   2. 研究层同伴（kb_usage / kb_scale / kb_fitness …，已搬进本包）
#      → 它们的真实路径在 `_research/` 下，裸名 import 找不到，
#        故在 sys.modules 里注册**别名**：`kb_usage` → `_research.kb_usage`。
#        这样 `import kb_fitness` 与 `import _research.kb_fitness` 是同一个
#        模块对象，`isinstance` / 单例缓存也不会分裂。
import importlib as _importlib
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PIPELINE = _os.path.dirname(_HERE)

# `python3 pipeline/_research/kb_l4.py …` 直接跑包内文件时，`sys.path[0]` 是
# `_research/` 而非 `pipeline/`，包 `__init__` 不会被执行 → 裸 import 全失败。
# 这里补一次路径注入，让"直接跑包内脚本"也能工作（垫片本来就支持，对齐行为）。
if _PIPELINE not in _sys.path:
    _sys.path.insert(0, _PIPELINE)

_RESEARCH_MODULES = tuple(
    _f[:-3] for _f in _os.listdir(_HERE)
    if _f.endswith(".py") and not _f.startswith("__")
)
for _name in _RESEARCH_MODULES:
    _sys.modules.setdefault(_name, _importlib.import_module(f"{__name__}.{_name}"))
del _name
