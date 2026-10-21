#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_common — 兼容 shim（P1-1 / 收尾 ① 拆分后）。

本模块原为 1236 行的 god-module，已按职责拆为六个模块：
  - `kb_io.py`       文件 I/O（atomic_write_text / parse_frontmatter / load_meta /
                     load_note / iter_notes / safe_*）
  - `kb_text.py`     文本/向量（tokenize / count_tokens / content_fingerprint /
                     norm / cos / char_vec / 倒排索引）
  - `kb_metautil.py` 领域元数据（kb_layer_of / root_of / parent_of / is_stale /
                     retrievable_status / record_lineage / parse_date / parse_importance）
  - `kb_config.py`   配置加载（model_config / load_config / taxonomy / dedup 阈值）
  - `kb_domain.py`   domain 词表与两级解析（DOMAIN_MAP / parse_domain / add_relation）
  - `kb_infer.py`    笔记字段推断（infer_status / infer_tags / infer_domain /
                     load_note_full / aggregate_bucket）

本文件保留全部 re-export，使既有 `from kb_common import X` 调用者零改动可用。
**新代码请直接 import 上述具体模块**，不要经本 shim —— 它会在迁移完成后删除
（见 ARCHITECTURE-REVIEW.md §九 ①）。

依赖方向（无环）：
    kb_config → kb_domain（延迟）
    kb_domain → kb_metautil（延迟，RELATION_FIELDS）
    kb_infer  → kb_config + kb_domain + kb_text
    kb_common → 以上全部（仅 re-export）
"""
from kb_io import *        # noqa: F401,F403  I/O 层
from kb_text import *      # noqa: F401,F403  文本/向量层
from kb_metautil import *  # noqa: F401,F403  领域元数据层
from kb_config import *    # noqa: F401,F403  配置/模型端点层
from kb_domain import *    # noqa: F401,F403  domain 词表层
from kb_infer import *     # noqa: F401,F403  字段推断层

# 私有名不被 `import *` 带出，显式补齐（有测试与调用方使用）
from kb_infer import (      # noqa: F401
    _tokens, _split_bracket_aware, _FM_RE, _LAT_RE,
)
from kb_config import _MODEL_CFG_CACHE  # noqa: F401
