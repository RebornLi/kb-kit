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

# ── L2 自我加权（反馈阶梯·L2 代谢回路 · kb_selfweight）─────────
# L2 的锚 = 真实外部接地(P0-1 阀) + 真实使用信号，非自指人气打分。
#   接地不足时冻结“内部人气”提升，只允许“外部显式有用”反馈回写（它本身就是接地）。
L2_SIGNAL_MIN = 0.30            # 单篇需达的最小融合信号才提议提升
L2_LINK_BONUS = 0.10            # 每条入链折算的信号权重（结构/枢纽信号）
L2_L1_MISS_BONUS = 0.15         # L1 重问漏检折算信号（真实使用行为，接 L1 产出）

# ── L0 检索表层修正（反馈阶梯·L0 检索纠错 · kb_retriage）─────────
# L0 = 检索表层（tags + kb_summary 的 token）。rag.py 检索打分**只用 token**：
#   body+tags 的 TF-IDF 余弦 + 倒排 tag/kb_summary_token 的 INVERTED_HIT_BOOST；
#   wikilinks/入链不影响 rag.py 打分。故"漏检"根因 = query 的 token 不在笔记检索表层
#   （tags/summary/body），而非无入链。body 属内容（归 L3/元数据质量），可修杠杆只剩
#   "只增的 tags + kb_summary"（二者都进 rag.py 索引）。接 L1(kb_adaptretrieve) 漏检缺口。
L0_GAP_TOKEN_MIN_LEN = 2        # 缺口 token 最短长度（去单字/停词噪声，如"的"、"a"）
L0_MIN_IDF = 2.5                # 缺口 token 最低 idf（取自 rag 词表 idf；过滤过宽泛无区分度）
L0_NOTE_MAX_TAGS = 12           # 单篇笔记 tags 总数上限（只升·有界，接 BUMP_CAP 封顶思想）
L0_SUMMARY_TOKENS = 8           # 充实 kb_summary 时最多拼接的缺口 token 数
L0_SUMMARY_MIN_LEN = 4          # kb_summary 少于该字数视为"缺失"才充实（只升·不覆盖已有）
L0_TOTAL_PROPOSALS = 50         # 单次 propose 最多产出提议数（有界，接 L1 MAX_PROPOSALS）

# ── L3 元调参（反馈阶梯·L3 元调参 · kb_meta）─────────
# L3 观察 L0/L1/L2 的真实结果（kb_usage 漏检）来调它们的阈值旋钮，有界·接地阀冻结·
#   连续负 delta 回滚。旋钮方向：覆盖门(L0_MIN_IDF/L2_SIGNAL_MIN)越大越严→漏检多且该层未
#   动作下调松覆盖；加权(L2_L1_MISS_BONUS)越小忽视漏检→漏检多且 L2 未动作上调多加权。
L3_KNOB_MIN = {"L0_MIN_IDF": 0.5, "L2_SIGNAL_MIN": 0.10, "L2_L1_MISS_BONUS": 0.05}   # 旋钮下限
L3_KNOB_MAX = {"L0_MIN_IDF": 4.5, "L2_SIGNAL_MIN": 0.60, "L2_L1_MISS_BONUS": 0.40}   # 旋钮上限
L3_KNOB_STEP = {"L0_MIN_IDF": 0.5, "L2_SIGNAL_MIN": 0.05, "L2_L1_MISS_BONUS": 0.05}  # 单步步长
L3_ROLLBACK_CONSEC = 2        # 连续负 delta 回滚阈值（≥此连续次数 → 回滚该旋钮到 last_good）
L3_MISS_HIGH = 3              # suspect_failed ≥ 此值判"漏检偏多"（触发松覆盖/多加权的调参）

# ── L4 宪法（反馈阶梯·L4 人工裁判 · kb_l4）─────────
# L4 由真人逐条裁决 L0–L3 的自动调整；连续被裁决为错 → 冻结该 rung（宪法 veto，人工解冻）。
#   与 L3 同构：L3 在指标负 delta 回滚旋钮；L4 在裁决负冻结 rung。真人裁决=最硬外部锚。
L4_RUNGS = ("l0", "l1", "l2", "l3")              # 被宪法监管的 rung（其 auto 调整供真人裁决）
L4_FREEZE_CONSEC = 3        # 连续错裁决 >= 此数 → 冻结该 rung（宪法否决）
L4_MIN_SAMPLE = 5           # 至少如此多裁决才具统计意义（连续错且样本够才冻；避免 3 错就冻）
L4_TRUST_FLOOR = 0.5        # 信任度下限（仅陈述；仍由连续负裁决触发冻结）
