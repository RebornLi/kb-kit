#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_common.py — 公共工具：frontmatter 解析 + ROOT_DEFAULT + EXCLUDE 集合。

各 pipeline 模块 `from kb_common import ...` 复用，消除重复定义。

本模块提供以下公共能力:
    - 文本处理: tokenize / parse_frontmatter / count_tokens / text_entropy
    - 文件 I/O: iter_notes / load_note / load_meta / safe_read_text / safe_load_note / safe_json_load
    - 向量运算: norm / cos / char_vec
    - 日期解析: parse_date / parse_date_safe
    - 重要性解析: parse_importance
    - 倒排索引: build_inverted_index / candidate_pairs
    - 配置加载: load_config / load_domain_taxonomy / load_tag_taxonomy / load_dedup_config
    - 关系管理: extract_relations / add_relation
    - 去重检查: dedup_check
"""
import os, re, math, json, hashlib
from pathlib import Path
from datetime import datetime
from collections import Counter
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

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


# ── 向量运算（norm / cos / char_vec）──────────────────────────
def norm(vec: Dict[str, float]) -> Dict[str, float]:
    """L2 归一化稀疏向量。

    Args:
        vec: 稀疏向量 {token: weight}

    Returns:
        归一化后的稀疏向量，使 L2 范数为 1。
        空向量或零向量返回原向量（不除以 0）。

    Examples:
        >>> norm({"a": 3.0, "b": 4.0})
        {'a': 0.6, 'b': 0.8}
    """
    if not vec:
        return vec
    n = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / n for k, v in vec.items()}


def cos(a: Dict[str, float], b: Dict[str, float]) -> float:
    """计算两个稀疏向量的余弦相似度。

    假设输入已归一化（或仅关心点积相对大小）。
    自动选择较短向量遍历以提升性能。

    Args:
        a: 稀疏向量 {token: weight}
        b: 稀疏向量 {token: weight}

    Returns:
        余弦相似度（点积，若输入已归一化则为余弦值）。

    Examples:
        >>> cos({"a": 0.6, "b": 0.8}, {"a": 0.6, "b": 0.8})
        1.0
    """
    if not a or not b:
        return 0.0
    # 自动 swap 使 a 为较短向量
    if len(b) < len(a):
        a, b = b, a
    return sum(av * b.get(k, 0.0) for k, av in a.items())


def char_vec(text: str) -> Dict[str, float]:
    """从文本构建归一化字符向量（tokenize → Counter → norm）。

    Args:
        text: 输入文本

    Returns:
        归一化稀疏向量 {token: weight}，L2 范数为 1。
        空文本返回 {}。

    Examples:
        >>> v = char_vec("hello world")
        >>> abs(sum(x * x for x in v.values()) - 1.0) < 1e-9
        True
    """
    if not text:
        return {}
    return norm(dict(Counter(tokenize(text))))


# ── 日期解析（parse_date / parse_date_safe）──────────────────
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def parse_date(v: Optional[str]) -> Optional[datetime]:
    """解析 YYYY-MM-DD 格式日期字符串。

    Args:
        v: 日期字符串（如 "2024-01-15"），None 或空字符串返回 None

    Returns:
        datetime 对象，解析失败返回 None。

    Examples:
        >>> parse_date("2024-01-15")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> parse_date(None) is None
        True
        >>> parse_date("invalid") is None
        True
    """
    if not v:
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        # 尝试从字符串中提取日期（如 "2024-01-15T10:30:00"）
        m = _DATE_RE.search(str(v))
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, TypeError):
                return None
        return None


def parse_date_safe(
    v: Optional[str], default: Optional[datetime] = None
) -> datetime:
    """安全解析日期，失败返回 default。

    Args:
        v: 日期字符串
        default: 解析失败时的默认值（None 时使用 datetime(1970,1,1)）

    Returns:
        datetime 对象。

    Examples:
        >>> parse_date_safe("2024-01-15")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> parse_date_safe("invalid")
        datetime.datetime(1970, 1, 1, 0, 0)
    """
    result = parse_date(v)
    if result is not None:
        return result
    return default if default is not None else datetime(1970, 1, 1)


# ── 重要性解析（parse_importance）─────────────────────────────
_IMPORTANCE_RE = re.compile(r"[-+]?\d*\.?\d+")


def parse_importance(s: Any) -> float:
    """从 frontmatter importance 字段解析浮点值。

    支持纯数字字符串、带前缀的字符串（如 "P0"、"0.85"）、数字类型。

    Args:
        s: 重要性值（str / int / float / None）

    Returns:
        浮点值，解析失败返回 0.0。

    Examples:
        >>> parse_importance("0.85")
        0.85
        >>> parse_importance("P0")
        0.0
        >>> parse_importance(None)
        0.0
    """
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    m = _IMPORTANCE_RE.search(str(s))
    if not m:
        return 0.0
    try:
        return float(m.group())
    except (ValueError, TypeError):
        return 0.0


# ── 安全 I/O（safe_read_text / safe_load_note / safe_json_load）──
def safe_read_text(path: Union[str, Path]) -> Optional[str]:
    """安全读取文本文件，失败返回 None。

    统一捕获 (OSError, UnicodeDecodeError)，替代各模块重复的 try-except 读取模式。

    Args:
        path: 文件路径

    Returns:
        文件文本内容，读取失败返回 None。
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def safe_load_note(
    path: Union[str, Path]
) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """安全加载笔记，失败返回 None。

    Args:
        path: 笔记文件路径

    Returns:
        (frontmatter_dict, raw_text, body_str) 三元组，读取/解析失败返回 None。
    """
    text = safe_read_text(path)
    if text is None:
        return None
    fm, body = parse_frontmatter(text)
    return fm, text, body


def safe_json_load(path: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """安全加载 JSON 文件，失败返回 None。

    统一捕获 (OSError, json.JSONDecodeError)。

    Args:
        path: JSON 文件路径

    Returns:
        解析后的 dict，读取/解析失败返回 None。
    """
    text = safe_read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ── 倒排索引（build_inverted_index / candidate_pairs）─────────
def build_inverted_index(vecs: Dict[str, Dict[str, float]]) -> Dict[str, Set[str]]:
    """构建 token → {rel} 倒排索引。

    用于 O(n²) 成对比较的预过滤：仅对共享至少 1 个 token 的笔记对计算余弦相似度。

    Args:
        vecs: {rel: 归一化向量} 字典

    Returns:
        {token: {rel1, rel2, ...}} 倒排索引。

    Examples:
        >>> idx = build_inverted_index({"a.md": {"x": 0.6, "y": 0.8},
        ...                              "b.md": {"y": 0.5, "z": 0.9}})
        >>> idx["y"] == {"a.md", "b.md"}
        True
    """
    index: Dict[str, Set[str]] = {}
    for rel, vec in vecs.items():
        for token in vec:
            if token not in index:
                index[token] = set()
            index[token].add(rel)
    return index


def candidate_pairs(
    index: Dict[str, Set[str]], rels: Optional[List[str]] = None
) -> Iterator[Tuple[str, str]]:
    """生成候选对（共享至少 1 个 token 的笔记对）。

    每对仅生成一次（按 (a, b) 顺序，a < b 字典序）。
    若提供 rels 参数，仅生成 rels 内部的候选对。

    Args:
        index: build_inverted_index 构建的倒排索引
        rels: 限制候选对范围的笔记列表（None 表示全部）

    Yields:
        (rel_a, rel_b) 候选对，rel_a < rel_b（字典序）。
    """
    rel_set = set(rels) if rels is not None else None
    seen: Set[Tuple[str, str]] = set()
    for token_rels in index.values():
        # 过滤到目标范围
        if rel_set is not None:
            token_rels = token_rels & rel_set
        if len(token_rels) < 2:
            continue
        # 生成有序对
        sorted_rels = sorted(token_rels)
        for i, a in enumerate(sorted_rels):
            for b in sorted_rels[i + 1:]:
                pair = (a, b)
                if pair not in seen:
                    seen.add(pair)
                    yield pair


# ── 从 clean.py 迁入的复用函数 + 常量（Task 1 解耦）──────────────
import datetime as _dt
import os as _os

# 聚合桶常量
AGGREGATE_DIR = "00-收件箱 Inbox/_aggregated"
AGGREGATE_LIMIT = 2000
AGGREGATE_IMPORTANCE = 0.3

# domain 推断常量
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

# frontmatter 边界正则（clean.py 本地版本，与 FM 同义但保留独立引用）
_FM_RE = re.compile(r"^---\s*$", re.M)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LAT_RE = re.compile(r"[A-Za-z0-9]+")

# status 白名单
STATUS_SET = {"draft", "active", "stable", "legacy", "archived"}


def _tokens(s: str) -> List[str]:
    """拉丁词 lower + 中文字符 unigram（clean.py infer_domain 用）。"""
    return [m.lower() for m in _LAT_RE.findall(s)] + _CJK_RE.findall(s)


def fmt_value(v: Any) -> str:
    """frontmatter 值格式化：list → [a, b]，其他 → str。"""
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def parse_date_str(v: Any, default: _dt.date) -> str:
    """解析日期 → YYYY-MM-DD 字符串。

    与 kb_common.parse_date（返回 datetime）不同，此函数返回字符串，
    保留 clean.py 原始签名以兼容 memory_sync 调用。

    Args:
        v: 日期值（frontmatter created/updated）
        default: 解析失败时的默认日期

    Returns:
        YYYY-MM-DD 格式字符串
    """
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    return default.strftime("%F")


def load_note_full(path: Union[str, Path]) -> Tuple[Dict[str, Any], str, str, str]:
    """加载笔记 → (fm, text, block, body) 四元组。

    与 kb_common.load_note（返回三元组）不同，此函数额外返回 block
    （frontmatter 原始文本块），保留 clean.py 原始签名以兼容调用。

    Args:
        path: 笔记文件路径

    Returns:
        (frontmatter_dict, raw_text, fm_block_str, body_str)
    """
    text = Path(path).read_text(encoding="utf-8")
    m = _FM_RE.search(text)
    if m:
        end = _FM_RE.search(text, m.end())
        block = text[m.end():end.start()] if end else ""
        body = text[end.end():] if end else text[m.end():]
    else:
        block, body = "", text
    fm: Dict[str, Any] = {}
    cur = None
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("{", "}"):
            continue
        if line.endswith(":"):
            cur = line[:-1].strip().strip('"\''); fm[cur] = None; continue
        if ":" in line:
            k, _, v = line.partition(":"); v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip().strip('"\'')
    return fm, text, block, body


def infer_domain(rel: str, fm: Dict[str, Any], title: str, body: str) -> str:
    """推断笔记 domain：fm > 路径 > 关键词 > 默认综合。"""
    if fm.get("domain") in DOMAIN_WHITELIST:
        return fm["domain"]
    d = DOMAIN_MAP.get(str(fm.get("domain", "")), "")
    if d:
        return d
    for p in reversed(rel.split(_os.sep)):
        base = p.split(" ")[0]
        if base in FOLDER_DOMAIN:
            return FOLDER_DOMAIN[base]
    tk = Counter(_tokens(title + " " + body[:400]))
    for kw, dom in KEYWORD_DOMAIN.items():
        if kw in title or tk.get(kw):
            return dom
    return "综合"


def infer_status(rel: str, fm: Dict[str, Any], mtime: float) -> str:
    """推断笔记 status：归档路径 > fm > mtime 年龄。"""
    if "归档" in rel or "trash" in rel.lower():
        return "archived"
    if fm.get("status") in STATUS_SET:
        return fm["status"]
    age = (_dt.datetime.now() - _dt.datetime.fromtimestamp(mtime)).days
    if age > 180: return "archived"
    if age > 90:  return "legacy"
    if age > 30:  return "stable"
    return "active"


def infer_tags(folder: str) -> List[str]:
    """推断标签：folder + slug（去特殊字符）。"""
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", folder)
    if slug and folder != slug:
        return [folder, slug]
    return [folder] if folder else ["knowledge"]


def list_of(fm: Dict[str, Any], *more: str) -> List[str]:
    """从 fm.tags 提取标签列表 + 追加 more。"""
    base: List[str] = []
    tags = fm.get("tags")
    if isinstance(tags, list):
        base = [str(x).strip() for x in tags if str(x).strip()]
    elif isinstance(tags, str):
        base = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    return base + list(more)


def base_fm(rel: str, fm: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """合并 fm + extra，tags 字符串转列表。"""
    fm = dict(fm)
    fm.update(extra)
    if isinstance(fm.get("tags"), str):
        fm["tags"] = [t.strip() for t in re.split(r"[,\s]+", fm["tags"]) if t.strip()]
    return fm


def infer_summary(body: str) -> str:
    """推断摘要：首条非空非引用非标题行，截断 120 字。"""
    for line in body.splitlines():
        line = line.strip(" \t>*#")
        if line and not line.startswith("`"):
            return line[:120]
    return "（待补充摘要）"


def aggregate_bucket(root: Union[str, Path], body: str, content_type: str,
                     author: Optional[str] = None) -> str:
    """将简短内容追加到月度聚合桶。

    Args:
        root: vault 根路径
        body: 简短内容正文
        content_type: chat / chat_todo
        author: 贡献者（可选）

    Returns:
        聚合桶相对路径
    """
    bucket_type = "chat" if content_type in ("chat", "chat_decision", "chat_knowledge") else "todo"
    month = _dt.date.today().strftime("%Y-%m")
    bucket_path = Path(root) / AGGREGATE_DIR / f"{month}-{bucket_type}.md"

    timestamp = _dt.datetime.now().strftime("%H:%M")
    author_prefix = f" {author}:" if author else ""
    entry = f"\n---\n\n**[{timestamp}]{author_prefix}** {body}\n"

    if bucket_path.exists():
        existing = bucket_path.read_text(encoding="utf-8")
        bucket_path.write_text(existing + entry, encoding="utf-8")
    else:
        fm = {
            "tags": [bucket_type, "aggregated"],
            "status": "active", "domain": "综合",
            "created": _dt.date.today().strftime("%F"),
            "updated": _dt.date.today().strftime("%F"),
            "importance": AGGREGATE_IMPORTANCE,
            "kb_target": AGGREGATE_DIR, "kb_action": "new",
            "kb_summary": f"{month} {bucket_type} 汇总",
            "content_type": content_type,
        }
        header = "---\n" + "\n".join(f"{k}: {fmt_value(v)}" for k, v in fm.items()) + "\n---\n\n"
        bucket_path.parent.mkdir(parents=True, exist_ok=True)
        bucket_path.write_text(header + f"# {month} {bucket_type} 汇总\n" + entry, encoding="utf-8")

    return str(bucket_path.relative_to(root))


# ── 去重阈值配置化 + 按 domain 自适应（Task 3）──────────────────
# 默认阈值（配置缺失时回退）
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
