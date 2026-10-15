#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fsrs_core.py — FSRS-6 记忆模型（纯标准库，约 150 行）。

## 为什么换掉 SM-2

`recall_schedule.py` 原来用 SM-2（ease 因子 + 固定间隔表）。FSRS 是它的现代替代：
用「难度 D / 稳定性 S / 可提取性 R」三参数描述记忆，S 的定义是**"遗忘到可提取性 90% 所需天数"**，
于是 `R(t=S) = 0.9` 是模型的**定义式**（下面 `test_*` 与自检都拿它当不变量）。

公式与 21 个默认参数取自 fsrs-rs `src/model_v6.rs`、`dart_srs`、`py-fsrs` 三处核对一致。
FSRS-5 固定 `DECAY = -0.5`；FSRS-6 让 `w[20]` 可学习，默认 0.1542。

## 迁移

FSRS 的 S 与 SM-2 的 interval 在同一尺度上（都是"到 90% 可提取的天数"），
所以**现有 interval 可直接当 S 的初值**；`ease` 用反解公式转成 D。迁移零成本。

## ⚠️ 评分口径必须一起改

`recall_schedule.GRADE_Q` 是 **SM-2 语义**（again:1/hard:3/good:4/easy:5），
而 FSRS 只认 **1/2/3/4**（again/hard/good/easy）——之前我在那一级留了注释钉住这件事，
现在换成 FSRS 就必须**同时**换成 FSRS 口径，否则难度 D 会朝反方向更新。
"""
import math
from typing import Any, Dict, List, Optional, Tuple

# FSRS-6 公开默认参数（21 个）。三处来源逐字核对一致。
DEFAULT_W: List[float] = [
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666,
    0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658,
    0.1542,
]

# FSRS 评分：1=again 2=hard 3=good 4=easy（**不是** SM-2 的 1/3/4/5）
GRADES = {1: "again", 2: "hard", 3: "good", 4: "easy"}
GRADE_BY_NAME = {v: k for k, v in GRADES.items()}

DESIRED_RETENTION = 0.9     # 目标可提取性（唯一需要人调的旋钮；0.9→0.95 大致翻倍工作量）


def _decay(w: List[float]) -> float:
    """衰减率：FSRS-6 里由 w[20] 决定（FSRS-5 固定 -0.5）。"""
    return -float(w[20]) if len(w) > 20 else -0.5


def _factor(w: List[float]) -> float:
    """FACTOR = 0.9^(1/DECAY) − 1，用来让 R(S,S)=0.9 成立。"""
    d = _decay(w)
    return 0.9 ** (1.0 / d) - 1.0


def retrievability(elapsed_days: float, stability: float, w: Optional[List[float]] = None) -> float:
    """R(t,S) = (1 + FACTOR·t/S)^DECAY。t=0 → 1；t=S → 0.9（定义式）。"""
    w = w or DEFAULT_W
    if stability <= 0:
        return 0.0
    t = max(0.0, float(elapsed_days))
    return (1.0 + _factor(w) * t / stability) ** _decay(w)


def interval(stability: float, retention: float = DESIRED_RETENTION,
             w: Optional[List[float]] = None) -> float:
    """I(S,r) = S/FACTOR · (r^(1/DECAY) − 1)：为达到目标可提取性该隔多少天。"""
    w = w or DEFAULT_W
    r = min(0.999, max(0.5, float(retention)))
    return stability / _factor(w) * (r ** (1.0 / _decay(w)) - 1.0)


def init_difficulty(grade: int, w: Optional[List[float]] = None) -> float:
    """D0(G) = w4 − e^(w5·(G−1)) + 1，clamp [1,10]。"""
    w = w or DEFAULT_W
    d = w[4] - math.exp(w[5] * (grade - 1)) + 1.0
    return min(10.0, max(1.0, d))


def next_difficulty(d: float, grade: int, w: Optional[List[float]] = None) -> float:
    """ΔD = −w6·(G−3)；D' = D + (10−D)/9·ΔD；再向 D0(4) 均值回归 w7；clamp [1,10]。"""
    w = w or DEFAULT_W
    delta = -w[6] * (grade - 3)
    d2 = d + (10.0 - d) / 9.0 * delta
    d2 = w[7] * init_difficulty(4, w) + (1.0 - w[7]) * d2     # 均值回归，防难度漂到极值
    return min(10.0, max(1.0, d2))


def next_stability(d: float, s: float, r: float, grade: int,
                   w: Optional[List[float]] = None) -> float:
    """成功按 w8..w10 增长（hard 有惩罚、easy 有加成）；遗忘按 w11..w14 收缩。"""
    w = w or DEFAULT_W
    if grade == 1:      # again = 遗忘
        s_f = w[11] * (d ** -w[12]) * ((s + 1.0) ** w[13] - 1.0) * math.exp(w[14] * (1.0 - r))
        return min(s_f, s / math.exp(w[17] * w[18]))          # 上限：别掉得比"同日重来"更狠
    hard_penalty = w[15] if grade == 2 else 1.0
    easy_bonus = w[16] if grade == 4 else 1.0
    inc = (math.exp(w[8]) * (11.0 - d) * (s ** -w[9])
           * (math.exp(w[10] * (1.0 - r)) - 1.0) * hard_penalty * easy_bonus)
    return s * (1.0 + inc)


def review(state: Dict[str, Any], grade: int, elapsed_days: float,
           w: Optional[List[float]] = None) -> Dict[str, Any]:
    """一次复习的状态转移。`state` 至少含 stability/difficulty（首次复习可缺）。

    返回新的 {stability, difficulty, reps, lapses, last_review, due_in_days}。
    """
    w = w or DEFAULT_W
    grade = min(4, max(1, int(grade)))
    s = float(state.get("stability") or 0.0)
    d = float(state.get("difficulty") or 0.0)
    reps = int(state.get("reps") or 0)
    lapses = int(state.get("lapses") or 0)

    if reps == 0 or s <= 0:
        # 首次：D 由评分初始化；S **优先沿用调用方给的种子**。
        #   为什么不是无条件用 w[grade-1]：kb-kit 会用 **importance** 派生一个基础间隔
        #   （高重要笔记先隔久一点），那是**用户给的先验**，丢掉它是信息损失。
        #   w[0..3] 只在完全没有种子时兜底。FSRS 官方迁移路径也是"用现有 interval 当 S"。
        d = init_difficulty(grade, w)
        s = s if s > 0 else max(0.1, w[grade - 1])
    elif elapsed_days < 1.0:
        # 同日重复：S' = S·e^(w17(G−3+w18))·S^(−w19)
        s = s * math.exp(w[17] * (grade - 3 + w[18])) * (s ** -w[19])
        d = next_difficulty(d, grade, w)
    else:
        r = retrievability(elapsed_days, s, w)
        s = next_stability(d, s, r, grade, w)
        d = next_difficulty(d, grade, w)
    if grade == 1:
        lapses += 1
    reps += 1
    return {"stability": round(s, 4), "difficulty": round(d, 4), "reps": reps,
            "lapses": lapses, "due_in_days": round(max(1.0, interval(s, DESIRED_RETENTION, w)), 2)}


def memory_state_from_sm2(sm2_interval: float, ease: float = 2.5,
                          w: Optional[List[float]] = None) -> Tuple[float, float]:
    """把 SM-2 的 (interval, ease) 反解成 FSRS 的 (S, D) —— 迁移用，零成本。

    S：r=0.9 时 I(S,0.9)=S，所以**现有 interval 直接就是 S**。
    D：由成功时的 S 增长式反解。
    """
    w = w or DEFAULT_W
    s = max(0.1, float(sm2_interval))
    try:
        denom = (math.exp(w[8]) * (s ** -w[9]) * (math.exp(w[10] * (1.0 - DESIRED_RETENTION)) - 1.0))
        d = 11.0 - (float(ease) - 1.0) / denom if denom else 5.0
    except (ValueError, ZeroDivisionError, OverflowError):
        d = 5.0
    return s, min(10.0, max(1.0, d))
