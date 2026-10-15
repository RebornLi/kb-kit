#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_route_consistency.py — 「domain → PARA 顶层目录」路由一致性守卫。

同一个 domain 在不同模块里会被路由到不同分区，共 4 套路由表：
  · `memory_sync.FOLDER_TARGET`     —— 记忆晋升落盘
  · `sync.DOMAIN_TARGET`            —— sync.py 的路由
  · `intake_triage._domain_to_para` —— 收件箱 triage / ingest-any
  · `memory_ingest.DOMAIN_BUCKET`   —— agent 记忆摄取

**这四张表现在必须完全一致**（曾经 产品/管理 各有 2-3 个不同答案，会让同一篇笔记
「从收件箱进来」和「从记忆晋升进来」落到不同分区）。

对齐依据不是拍脑袋，而是现役库知识层的实测分布：
  · 产品  11/13 (85%) 落 40-资源库 Resources   → 取 40-资源库
  · 管理  25/52 (48%) 落 10-项目 Projects（60-运营 仅 3 篇 5.8%）→ 取 10-项目
  · 开发/运维/安全/数据 绝大多数落 20-技术 Technology
  · 综合  40-资源库 为众数（32.5%）
仓库文档只定义了 domain 词表与二级子域（`reference/domain-taxonomy.json`），
并未规定 domain→分区，所以以语料为准。

要改映射时：**同步改这 4 张表 + 本文件的 EXPECTED**，别只改一处。

运行：python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
TEMPLATE = REPO / "template-vault"
sys.path.insert(0, str(PIPELINE))

import intake_triage        # noqa: E402
import memory_ingest        # noqa: E402
import memory_sync          # noqa: E402
import sync as sync_mod     # noqa: E402

# 参与比对的是「domain → 顶层目录」这一类路由；适配器的 `_kb_target(content)` 是按
# **内容关键词**路由（项目/决策 → 固定聚合文件，其余进 20-技术 的子域），口径不同，不在此列。
ROUTERS = {
    "memory_sync.FOLDER_TARGET": lambda d: memory_sync.FOLDER_TARGET.get(d),
    "sync.DOMAIN_TARGET": lambda d: (sync_mod.DOMAIN_TARGET.get(d) or (None,))[0],
    "intake_triage": lambda d: intake_triage._domain_to_para(d),
    "memory_ingest.DOMAIN_BUCKET": lambda d: memory_ingest.DOMAIN_BUCKET.get(d),
}

# 唯一的期望映射（语料实测得出）。四张表都必须落到这个值。
EXPECTED = {
    "开发": "20-技术 Technology",
    "运维": "20-技术 Technology",
    "安全": "20-技术 Technology",
    "数据": "20-技术 Technology",
    "产品": "40-资源库 Resources",
    "管理": "10-项目 Projects",
    "综合": "40-资源库 Resources",
}   # 只列 DOMAIN_WHITELIST 的 7 个域；见 test_expected_covers_exactly_the_schema
# 未登记的 domain 走默认；这里只钉已知域，未知域只要四表一致即可
UNKNOWN_DOMAIN = "未知域xyz"


def _targets(domain):
    out = {}
    for name, fn in ROUTERS.items():
        try:
            v = fn(domain)
        except (KeyError, TypeError, AttributeError):
            v = None
        if v:
            out[name] = str(v).split("/")[0]
    return out


class RouteConsistencyTest(unittest.TestCase):
    def test_all_routers_agree_on_every_domain(self):
        """四张路由表必须全票一致（曾出现 产品/管理 各有 2-3 个答案）。"""
        disagreements = []
        for d in list(EXPECTED) + [UNKNOWN_DOMAIN]:
            got = _targets(d)
            if len(set(got.values())) > 1:
                disagreements.append(f"{d}: {got}")
        self.assertEqual(disagreements, [],
                         "路由表之间出现分歧（同一笔记会落到不同分区）：\n"
                         + "\n".join(disagreements))

    def test_expected_mapping_is_respected(self):
        """每张表都要落到语料实测出的期望值（防止四张表『一起漂』到同一个错值）。"""
        drifts = []
        for d, want in EXPECTED.items():
            for name, tgt in _targets(d).items():
                if tgt != want:
                    drifts.append(f"{d} → {name}: {tgt}（期望 {want}）")
        self.assertEqual(drifts, [], "路由映射偏离已实测确认的期望值：\n" + "\n".join(drifts))

    def test_all_route_targets_exist(self):
        """所有路由目标必须指向真实存在的顶层目录。"""
        bad = []
        for d in list(EXPECTED) + [UNKNOWN_DOMAIN]:
            for name, tgt in _targets(d).items():
                if not (TEMPLATE / tgt).is_dir():
                    bad.append(f"{d} → {name}: {tgt} 不存在")
        self.assertEqual(bad, [], "路由目标指向不存在的顶层目录：\n" + "\n".join(bad))

    def test_expected_covers_exactly_the_schema(self):
        """EXPECTED 必须恰好等于 `kb_common.DOMAIN_WHITELIST`（防止守卫与被测对象一起过期）。"""
        from kb_common import DOMAIN_WHITELIST
        self.assertEqual(set(EXPECTED), set(DOMAIN_WHITELIST),
                         "路由守卫的 EXPECTED 与 domain 白名单不一致")

    def test_every_router_covers_the_expected_domains(self):
        """四张表都要显式覆盖期望里的 domain（漏配会静默落到默认值，实测踩过）。"""
        missing = []
        for name, fn in ROUTERS.items():
            for d in EXPECTED:
                if not fn(d):
                    missing.append(f"{name} 缺 {d}")
        self.assertEqual(missing, [], "路由表漏配 domain（会静默走默认值）：\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
