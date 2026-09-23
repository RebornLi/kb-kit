#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_constants.py — 跨模块共享的常量定义中心。

收敛散布在各模块中的硬编码阈值和魔法数字，提供单一权威源。

使用方式:
    from kb_constants import DUP_SIM, MERGE_MIN_SIM, ...
"""
# ── 去重阈值 ──────────────────────────────────────────────────
DUP_SIM = 0.85              # 去重相似度阈值（原 kb_rsi.py）
DUP_SIM_HEALTH = 0.90       # 健康检查去重阈值（原 kb_health.py）
MERGE_MIN_SIM = 0.97        # 自动合并相似度门槛（原 kb_engine.py）
CONTRA_SIM = 0.40           # 矛盾检测相似度下限（原 kb_contradiction.py）

# ── 连接引擎 ──────────────────────────────────────────────────
LINK_THRESHOLD = 0.75       # 补链建议阈值（原 link_engine.py）
MIN_HITS = 3                # 反馈命中次数下限（原 feedback_loop.py）
BUMP_CAP = 0.30             # 重要性上调上限（原 feedback_loop.py）
MIN_BUMP = 0.05             # 重要性最小上调步长（原 feedback_loop.py）

# ── 时效参数 ──────────────────────────────────────────────────
ARCHIVE_DAYS = 90           # 归档天数阈值（原 kb_health.py）
RETIRE_AGE_DAYS = 180       # 退役年龄天数（原 kb_engine.py）
DECAY_DAYS = 30             # 衰减周期天数（原 kb_health.py）

# ── 性能参数 ──────────────────────────────────────────────────
PERF_WARN_THRESHOLD = 400   # O(n²) 告警阈值（原 link_engine.py / kb_health.py）
MAX_NOTES_CONTRA = 400      # 矛盾检测全量比较上限（原 kb_contradiction.py）

# ── 外部接地参数 ──────────────────────────────────────────────
EXTERNAL_MIN_CROSS = 0.15       # 外部最小交叉度（原 kb_rsi.py）
EXTERNAL_FRESH_MIN_PCT = 5.0    # 外部新鲜度最小百分比（原 kb_rsi.py）
