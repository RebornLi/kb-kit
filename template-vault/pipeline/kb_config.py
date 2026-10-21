#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_config.py — 库配置与契约加载（从 kb_common.py 拆出）。

职责：读 `reference/` 下的 JSON 契约 + 模型端点配置，全部带内存缓存。

原 kb_common 拆分的第三层（§P1-1 收尾）：
  - kb_io.py      文件 I/O
  - kb_text.py    文本/向量
  - kb_metautil.py 领域元数据（含日期/重要性解析）
  - kb_config.py   ← 本文件：配置加载 + 模型端点
  - kb_domain.py  域词表与两级解析
  - kb_infer.py   笔记字段推断（status/tags/domain/summary/aggregate）

注意：`DOMAIN_WHITELIST` 放在这里是因为它是「契约词表」（由
reference/domain-taxonomy.json 定义），clean/validate/curate 共用；
parse_date / parse_importance 归 kb_metautil，不在这里。
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

# ── domain 白名单（frontmatter domain 字段合法取值，clean/validate 共用）──
DOMAIN_WHITELIST = {"运维", "开发", "安全", "产品", "数据", "管理", "综合"}

# ── 模型端点配置（P2）：环境变量优先，回退库内 .kb/model.json ──────
#   为什么要文件：cron / systemd / 自动化跑的命令**读不到 ~/.bashrc**，
#   于是 ORNITH_API_KEY 缺失 → 结晶与嵌入静默降级。库内配置文件让"库自带模型配置"。
_MODEL_CFG_CACHE: Dict[str, Any] = {}


def model_config(root: Union[str, Path, None] = None) -> Dict[str, str]:
    """读模型端点配置：环境变量 > `.kb/model.json`。

    文件格式（本机私有，已 gitignore）：
      {"api_key": "sk-...", "base_url": "http://127.0.0.1:8000/v1",
       "chat_model": "ornith1.5-35b",
       "embed_url": "http://127.0.0.1:8081/v1", "embed_model": "qwen3-embedding"}
    """
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    key = str(root)
    if key in _MODEL_CFG_CACHE:
        return _MODEL_CFG_CACHE[key]
    file_cfg: Dict[str, Any] = {}
    for cand in (root / ".kb" / "model.json", root / "kb-model.json"):
        try:
            file_cfg = json.loads(cand.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            continue
    cfg = {
        "api_key": os.environ.get("ORNITH_API_KEY") or os.environ.get("OPENAI_API_KEY")
                   or str(file_cfg.get("api_key") or ""),
        "base_url": os.environ.get("ORNITH_BASE_URL") or str(file_cfg.get("base_url") or ""),
        "chat_model": os.environ.get("ORNITH_CHAT_MODEL") or str(file_cfg.get("chat_model") or ""),
        "embed_url": os.environ.get("ORNITH_EMBED_URL") or str(file_cfg.get("embed_url") or ""),
        "embed_model": os.environ.get("ORNITH_EMBED_MODEL") or str(file_cfg.get("embed_model") or ""),
    }
    _MODEL_CFG_CACHE[key] = cfg
    return cfg


# ── 配置加载（带缓存）──────────────────────────────────────
_config_cache = {}


def load_config(name, root=None):
    """加载 reference/ 下的 JSON 配置文件，带内存缓存。

    Args:
        name: 文件名（如 "domain-taxonomy.json"）
        root: vault 根路径（默认推断）

    Returns:
        dict（配置内容），文件不存在返回 {}
    """
    if name in _config_cache:
        return _config_cache[name]
    if root is None:
        root = Path(__file__).resolve().parents[1]
    p = Path(root) / "reference" / name
    if not p.exists():
        _config_cache[name] = {}
        return {}
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    data.pop("_meta", None)
    _config_cache[name] = data
    return data


def load_domain_taxonomy(root=None):
    """加载 domain 分类法。返回 {一级: [二级, ...]}"""
    return load_config("domain-taxonomy.json", root)


def load_tag_taxonomy(root=None):
    """加载标签白名单。返回 {标签: [子标签, ...]}"""
    return load_config("tag-taxonomy.json", root)


def load_dedup_config(root=None):
    """加载去重阈值配置。"""
    return load_config("dedup-config.json", root)


# 去重阈值默认值（配置缺失时回退）
_DEDUP_DEFAULTS = {
    "dup_sim": 0.92,
    "novel_sim": 0.70,
    "sync_sim": 0.85,
    "replace_sim": 0.85,
    "merge_sim": 0.70,
}


def get_dedup_thresholds(domain: Optional[str] = None,
                         root: Union[str, Path, None] = None) -> Dict[str, float]:
    """获取去重阈值，支持按 domain 自适应。

    加载 reference/dedup-config.json，合并 global + by_domain[domain]。
    配置缺失时回退到硬编码默认值。

    Args:
        domain: 笔记域（如 "开发"、"综合"），None 则仅用 global
        root: vault 根路径（默认推断）

    Returns:
        阈值字典，包含 dup_sim / novel_sim / sync_sim / replace_sim / merge_sim
    """
    config = load_config("dedup-config.json", root)
    # 从 global 或旧格式回退
    global_cfg = config.get("global", {})
    result = dict(_DEDUP_DEFAULTS)
    for k in _DEDUP_DEFAULTS:
        if k in global_cfg:
            result[k] = float(global_cfg[k])
    # 旧格式兼容：semantic_dedup.threshold → dup_sim
    if "dup_sim" not in global_cfg:
        sem = config.get("semantic_dedup", {})
        if "threshold" in sem:
            result["dup_sim"] = float(sem["threshold"])
    if "novel_sim" not in global_cfg:
        rel = config.get("semantic_related", {})
        if "threshold_min" in rel:
            result["novel_sim"] = float(rel["threshold_min"])
    # by_domain 覆盖
    if domain and "by_domain" in config:
        domain_cfg = config["by_domain"].get(domain, {})
        for k in _DEDUP_DEFAULTS:
            if k in domain_cfg:
                result[k] = float(domain_cfg[k])
    return result


def is_valid_domain(domain_str, root=None):
    """检查 domain 是否在白名单内。"""
    from kb_domain import parse_domain      # 延迟 import 避免环
    primary, secondary = parse_domain(domain_str)
    taxonomy = load_domain_taxonomy(root)
    if not taxonomy:
        return True  # 无配置则不校验
    if primary not in taxonomy:
        return False
    if secondary and secondary not in taxonomy[primary]:
        return False
    return True


def is_valid_tag(tag, root=None):
    """检查标签是否在白名单内（支持层级标签 '开发/后端'）。"""
    taxonomy = load_tag_taxonomy(root)
    if not taxonomy:
        return True
    parts = tag.split("/", 1)
    primary = parts[0].strip()
    if primary not in taxonomy:
        return False
    if len(parts) > 1:
        secondary = parts[1].strip()
        if secondary and secondary not in taxonomy[primary]:
            return False
    return True
