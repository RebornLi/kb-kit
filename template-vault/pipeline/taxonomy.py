#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""taxonomy.py — 受控词表（domain/category/status 枚举 + tag 层级 + 别名归一）。

规则来自 reference/taxonomy.json；缺失时用内置默认值降级（零依赖）。
提供：load_taxonomy / normalize_tag / normalize_tags / is_known_tag。
"""
import json
import re
from pathlib import Path
from typing import Dict, List

DEFAULT = {
    "domains": ["运维", "开发", "安全", "产品", "数据", "管理", "综合"],
    "categories": ["project", "experience", "reference", "sop", "meta", "knowledge"],
    "statuses": ["draft", "active", "stable", "legacy", "archived"],
    "tag_hierarchy": {},
    "aliases": {},
}


def load_taxonomy(root=None) -> Dict:
    """加载 taxonomy.json：先 root/reference/，回退到包内 reference/，再回退默认。"""
    candidates = []
    if root:
        candidates.append(Path(root) / "reference" / "taxonomy.json")
    candidates.append(Path(__file__).resolve().parents[1] / "reference" / "taxonomy.json")
    data = None
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            continue
    if not isinstance(data, dict):
        return dict(DEFAULT)
    out = dict(DEFAULT)
    for k in DEFAULT:
        if k in data:
            out[k] = data[k]
    return out


def _leaf_to_group(tax) -> Dict[str, str]:
    m = {}
    for group, leaves in (tax.get("tag_hierarchy") or {}).items():
        for leaf in leaves:
            m[leaf.lower()] = f"{group}/{leaf}"
    return m


def parse_tags(v) -> List[str]:
    """健壮解析 tags：支持真正列表、JSON 数组字符串 `["a","b"]`、逗号/空格分隔。"""
    if isinstance(v, list):
        return [str(x).strip().strip('"\'') for x in v if str(x).strip()]
    if not isinstance(v, str):
        return []
    s = v.strip()
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except json.JSONDecodeError:
            pass
        s = s.strip("[]")
    return [t.strip().strip('"\'') for t in re.split(r"[,\s]+", s) if t.strip()]


def normalize_tag(tag, tax) -> str:
    """单 tag 归一：别名→规范；已知 leaf→`group/leaf`；group 本身保持；未知原样。"""
    t = str(tag).strip()
    if not t:
        return ""
    aliases = {k.lower(): v for k, v in (tax.get("aliases") or {}).items()}
    if t.lower() in aliases:
        return aliases[t.lower()]
    leaf_map = _leaf_to_group(tax)
    if t.lower() in leaf_map:
        return leaf_map[t.lower()]
    return t


def normalize_tags(tags: List[str], tax) -> List[str]:
    """归一 + 去重（保序）。"""
    out = []
    for t in tags or []:
        n = normalize_tag(t, tax)
        if n and n not in out:
            out.append(n)
    return out


def known_tags(tax) -> set:
    """受控集合：domains/categories/statuses + 层级 group/leaf。"""
    s = set(tax.get("domains") or []) | set(tax.get("categories") or []) | set(tax.get("statuses") or [])
    for group, leaves in (tax.get("tag_hierarchy") or {}).items():
        s.add(group)
        s.update(f"{group}/{leaf}" for leaf in leaves)
    return s


def is_known_tag(tag, tax) -> bool:
    return str(tag).strip() in known_tags(tax)
