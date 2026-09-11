#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_common.py — 公共工具：frontmatter 解析 + ROOT_DEFAULT + EXCLUDE 集合。

各 pipeline 模块 `from kb_common import ...` 复用，消除重复定义。
"""
import os, re, math, hashlib
from pathlib import Path
from collections import Counter

# ── vault 根默认值（所有模块共用）────────────────────────────
ROOT_DEFAULT = os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[1])

# ── os.walk 排除集合（与其他 pipeline 模块保持一致）──────────
EXCLUDE = {".git", "backups", "logs", "vector index", "pipeline", "_SELF_OPT",
           ".obsidian", "__pycache__", ".pytest_cache"}

# ── domain 白名单（frontmatter domain 字段合法取值，clean/validate 共用）──
DOMAIN_WHITELIST = {"运维", "开发", "安全", "产品", "数据", "管理", "综合"}

# ── frontmatter 边界正则 ────────────────────────────────────
FM = re.compile(r"^---\s*$", re.M)

# ── 分词正则（CJK unigram+bigram + 拉丁词）────────────────────
CJK = re.compile(r"[\u4e00-\u9fff]")
LAT = re.compile(r"[A-Za-z0-9]+")


def tokenize(s):
    """分词：拉丁词 lower + 中文字符 unigram + bigram（无 jieba 兜底）。

    rag/intake/link/recall/kb_health 共用此实现，保证去重判定与 RAG 索引同构。
    """
    toks = [m.lower() for m in LAT.findall(s)]
    cjk = CJK.findall(s)
    for i, c in enumerate(cjk):
        toks.append(c)
        if i + 1 < len(cjk):
            toks.append(c + cjk[i + 1])
    return toks


def parse_frontmatter(text):
    """解析 markdown frontmatter，返回 (fm_dict, body_str)。

    - 无 frontmatter → ({}, text)
    - 有 frontmatter → ({key: value, ...}, body)

    value 做 strip + 去引号；支持嵌套键（line 以 ":" 结尾时设为 None）。
    """
    fm, body = {}, text
    m = FM.search(text)
    if not m:
        return fm, body
    end = FM.search(text, m.end())
    if not end:
        return fm, body
    block = text[m.end():end.start()]
    body = text[end.end():]
    cur = None
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":"):
            cur = line[:-1].strip()
            fm[cur] = None
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"\'')
    return fm, body


def load_meta(path):
    """从文件路径读取并解析 frontmatter，返回 (fm_dict, body_str)。

    读取失败 → ({}, "")。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    return parse_frontmatter(text)


def load_note(path):
    """读取笔记并解析 frontmatter，返回 (fm_dict, text, body) 三元组。

    feedback_loop/link_engine/kb_health 共用此实现。
    与 load_meta 的区别：额外返回原始 text（调用方需用于写回/正则匹配）。
    """
    text = Path(path).read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    return fm, text, body


def iter_notes(root):
    """遍历 root 下所有 .md 文件（排除 EXCLUDE 目录、嵌套 git 仓库）。"""
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE]
        if dp != root and os.path.exists(os.path.join(dp, ".git")):
            dn[:] = []
            continue
        for f in fn:
            if f.endswith(".md"):
                yield Path(dp) / f


def count_tokens(text):
    """粗略 token 计数：CJK 1 字 = 1 token，拉丁 4 字符 = 1 token。

    零依赖（不依赖 tiktoken），用于上下文预算控制。
    rag.py --context-budget 用此函数计算拼接 token 数，确保不超限。
    """
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    lat_chars = sum(len(m) for m in LAT.findall(text))
    return cjk + lat_chars // 4


def text_entropy(text):
    """Shannon 信息熵，0-1 归一化。

    用 Python 标准库 math + collections.Counter 计算。
    空文本返回 0.0，单字符重复返回 0.0，每字符不同返回 1.0。
    高熵 = 信息丰富，低熵 = 重复/模板化内容。
    """
    if not text:
        return 0.0
    freq = Counter(text)
    n = len(text)
    num_types = len(freq)
    if num_types <= 1:
        return 0.0
    h = -sum((c / n) * math.log2(c / n) for c in freq.values())
    return h / math.log2(num_types)


def content_fingerprint(body):
    """内容指纹：body 的 sha256 哈希（64 字符十六进制）。

    用于精确去重和变更检测。零依赖（hashlib 标准库）。
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ── P2: domain 两级解析 + 配置加载（FR-3.3.3 + FR-3.3.4）──────
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


def is_valid_domain(domain_str, root=None):
    """检查 domain 是否在白名单内。"""
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


# ── P2: frontmatter 关系型字段（FR-3.3.1）──────────────────
RELATION_FIELDS = ("related_to", "prerequisite", "supersedes", "superseded_by")


def extract_relations(fm):
    """从 frontmatter 提取关系型字段。

    Returns:
        dict: {field: value} 仅含非空字段
    """
    return {f: fm[f] for f in RELATION_FIELDS if fm.get(f)}


def add_relation(fm, field, target_rel):
    """向 frontmatter 添加关系（幂等，不重复添加）。

    Args:
        fm: frontmatter dict
        field: related_to / prerequisite / supersedes / superseded_by
        target_rel: 目标笔记相对路径
    """
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


# ── P2: 三级去重阈值校准（FR-3.3.11）──────────────────────────
def dedup_check(body, existing_notes, root=None):
    """三级去重检查。

    Args:
        body: 新内容正文
        existing_notes: [(rel, fm, body), ...] 现有笔记列表
        root: vault 根路径（用于加载配置）

    Returns:
        dict: {
            "action": "skip" | "mark_related" | "new",
            "reason": str,
            "matched_rel": str | None,
            "similarity": float,
        }
    """
    config = load_dedup_config(root)
    exact_method = config.get("exact_dedup", {}).get("method", "sha256")
    sem_threshold = config.get("semantic_dedup", {}).get("threshold", 0.92)
    rel_min = config.get("semantic_related", {}).get("threshold_min", 0.70)
    rel_max = config.get("semantic_related", {}).get("threshold_max", 0.92)

    # ① 精确去重：sha256
    new_hash = content_fingerprint(body)
    for rel, fm_ex, body_ex in existing_notes:
        if content_fingerprint(body_ex) == new_hash:
            return {"action": "skip", "reason": "精确重复(sha256一致)",
                    "matched_rel": rel, "similarity": 1.0}

    # ② 语义去重 + ③ 语义相关：TF-IDF 余弦
    from collections import Counter
    import math
    new_toks = Counter(tokenize(body))
    if not new_toks:
        return {"action": "new", "reason": "无有效 token", "matched_rel": None, "similarity": 0.0}
    new_vec = {t: c for t, c in new_toks.items()}
    new_norm = math.sqrt(sum(v * v for v in new_vec.values())) or 1.0
    new_vec = {k: v / new_norm for k, v in new_vec.items()}

    best_sim, best_rel = 0.0, None
    for rel, fm_ex, body_ex in existing_notes:
        ex_toks = Counter(tokenize(body_ex))
        if not ex_toks:
            continue
        ex_vec = {t: c for t, c in ex_toks.items()}
        ex_norm = math.sqrt(sum(v * v for v in ex_vec.values())) or 1.0
        ex_vec = {k: v / ex_norm for k, v in ex_vec.items()}
        dot = sum(new_vec.get(t, 0) * ex_vec.get(t, 0) for t in new_vec if t in ex_vec)
        if dot > best_sim:
            best_sim, best_rel = dot, rel

    if best_sim >= sem_threshold:
        return {"action": "skip", "reason": f"语义重复(TF-IDF={best_sim:.3f}≥{sem_threshold})",
                "matched_rel": best_rel, "similarity": best_sim}
    if rel_min <= best_sim < rel_max:
        return {"action": "mark_related", "reason": f"语义相关(TF-IDF={best_sim:.3f})",
                "matched_rel": best_rel, "similarity": best_sim}
    return {"action": "new", "reason": "新内容", "matched_rel": None, "similarity": best_sim}
