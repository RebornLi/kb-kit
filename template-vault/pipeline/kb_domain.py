#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_domain.py — domain 词表 + 两级解析 + 关系字段操作（从 kb_common.py 拆出）。

职责：
  - domain 两级解析（"开发/后端" → ("开发", "后端")）
  - domain 推断词表（FOLDER_DOMAIN / DOMAIN_MAP / KEYWORD_DOMAIN）
  - frontmatter 关系型字段读写（related_to / prerequisite / supersedes / …）

依赖方向：kb_domain → kb_config（is_valid_domain 延迟引用，避免环）；
kb_infer → kb_domain（infer_domain 用词表）。无反向依赖。
"""

# ── domain 推断常量（从 clean.py 迁入，Task 1 解耦）──────────────
FOLDER_DOMAIN = {
    "AI与LLM AI": "开发", "AI与LLM": "开发", "后端开发": "开发", "前端开发": "开发",
    "开发": "开发", "安全 Security": "安全", "安全": "安全", "测试 Testing": "开发", "测试": "开发",
    "基础设施 Infra": "运维", "基础设施": "运维", "部署运维": "运维", "部署": "运维", "运维": "运维",
    "数据库 Database": "数据", "数据库": "数据", "项目管理 PM": "管理", "项目管理": "管理", "PM": "管理",
    "智能客服 Smart-Customer": "开发", "AI测试求职 AI-Testing-Port": "综合",
    "面试准备": "综合", "光钟圆环模型 Light-Clock": "综合",
    "记忆维护 Memory": "综合", "Career-Pivot": "综合",
}
DOMAIN_MAP = {
    "技术": "开发", "AI": "开发", "知识管理": "管理", "综合": "综合", "其他": "综合",
    "决策": "管理", "项目": "综合", "职业": "综合", "记忆维护": "综合",
    "运维": "运维", "开发": "开发", "安全": "安全", "产品": "产品", "数据": "数据", "管理": "管理",
}
KEYWORD_DOMAIN = {"test": "开发", "deploy": "运维", "database": "数据",
                  "prompt": "产品", "career": "综合", "security": "安全"}

# status 白单词表
STATUS_SET = {"draft", "active", "stable", "legacy", "archived"}


def parse_domain(domain_str):
    """解析 domain 字段 → (一级, 二级)。

    支持格式：
        "开发"       → ("开发", None)
        "开发/后端"  → ("开发", "后端")
        None/""      → ("综合", None)
    """
    if not domain_str or not str(domain_str).strip():
        return ("综合", None)
    parts = str(domain_str).strip().split("/", 1)
    primary = parts[0].strip()
    secondary = parts[1].strip() if len(parts) > 1 else None
    return (primary, secondary)


def domain_primary(domain_str):
    """快捷取一级 domain。"""
    return parse_domain(domain_str)[0]


def domain_secondary(domain_str):
    """快捷取二级 domain。"""
    return parse_domain(domain_str)[1]


def extract_relations(fm):
    """从 frontmatter 提取关系型字段。

    Returns:
        dict: {field: value} 仅含非空字段
    """
    from kb_metautil import RELATION_FIELDS   # 延迟 import 避免环
    return {f: fm[f] for f in RELATION_FIELDS if fm.get(f)}


def add_relation(fm, field, target_rel):
    """向 frontmatter 添加关系（幂等，不重复添加）。

    Args:
        fm: frontmatter dict
        field: related_to / prerequisite / supersedes / superseded_by
        target_rel: 目标笔记相对路径
    """
    from kb_metautil import RELATION_FIELDS   # 延迟 import 避免环
    if field not in RELATION_FIELDS:
        return
    current = fm.get(field)
    if current is None:
        fm[field] = target_rel
    elif isinstance(current, list):
        if target_rel not in current:
            current.append(target_rel)
    elif isinstance(current, str):
        if current != target_rel:
            fm[field] = [current, target_rel]
