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


def load_tag_whitelist(root=None) -> set:
    """加载 `tag-taxonomy.json`（**巡检与审计共用的标签白名单**，键即合法标签）。

    为什么单独有这一份：仓库里存在两套词表——
      · taxonomy.json    → domain/category/status 枚举 + 别名归一（用于"归一"）
      · tag-taxonomy.json → 标签白名单（用于"判定越界"）
    审计若只看 taxonomy.json，会把 `#chunk`/`#toc`/`#daily` 这类**合法标签**误判越界
    （实测误报 145 种，而日报只报 58 种）。两份都要认，判定才一致。
    """
    names = ["tag-taxonomy.json"]
    dirs = ([Path(root) / "reference"] if root else []) + [Path(__file__).resolve().parents[1] / "reference"]
    out = set()
    for d in dirs:
        for n in names:
            try:
                data = json.loads((d / n).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                for k, v in data.items():
                    if str(k).startswith("_"):
                        continue
                    out.add(str(k).strip())
                    if isinstance(v, list):
                        out.update(str(x).strip() for x in v)
                    elif isinstance(v, dict):
                        out.update(str(x).strip() for x in v.keys())
    return {t for t in out if t}


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


_KNOWN_CACHE: Dict = {}


def known_tags(tax, root=None) -> set:
    """受控集合 = taxonomy.json（domain/category/status + 层级）∪ tag-taxonomy.json 白名单。

    两份合起来才是完整受控词表（见 load_tag_whitelist 的说明）。
    """
    key = id(tax)
    if key in _KNOWN_CACHE:
        return _KNOWN_CACHE[key]
    s = set(tax.get("domains") or []) | set(tax.get("categories") or []) | set(tax.get("statuses") or [])
    for group, leaves in (tax.get("tag_hierarchy") or {}).items():
        s.add(group)
        s.update(f"{group}/{leaf}" for leaf in leaves)
    s |= load_tag_whitelist(root)
    # 并入 kb-healthcheck 的系统/主题白名单——否则会出现"日报判合法、审计判越界"
    # 的双标（实测：审计多报 80 种，含 chunk/toc/daily/基础设施 这些明显合法的标签）。
    try:
        import importlib.util as _ilu
        _hp = Path(__file__).resolve().parent / "kb-healthcheck.py"
        _spec = _ilu.spec_from_file_location("_kbhc", _hp)
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        s |= set(getattr(_m, "TAG_WHITELIST_SYS", set()))
        s |= set(getattr(_m, "TAG_WHITELIST_TOPIC", set()))
        s |= set(getattr(_m, "NOISE_TAGS", set()))
    except Exception:
        pass
    s |= set(tax.get("aliases") or {}.keys())        # 别名也算"已知"（归一阶段再映射）
    for v in (tax.get("aliases") or {}).values():
        s.add(str(v))
    _KNOWN_CACHE[key] = s
    return s


def is_known_tag(tag, tax, root=None) -> bool:
    return str(tag).strip() in known_tags(tax, root)
