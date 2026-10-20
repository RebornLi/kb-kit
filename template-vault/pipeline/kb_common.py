"""kb_common — 兼容 shim（P1-1 拆分后）。

本模块原为 1236 行的 god-module，已在 P1-1 中按职责拆分：
  - kb_io.py：文件 I/O（atomic_write_text / parse_frontmatter / load_meta / load_note / iter_notes / safe_*）
  - kb_text.py：文本处理（tokenize / count_tokens / content_fingerprint / norm / cos / char_vec / 倒排索引）
  - kb_metautil.py：领域元数据（kb_layer_of / root_of / parent_of / is_stale / retrievable_status / record_lineage）

本文件保留：
  1. 上述三个模块的 re-export（from … import *），确保所有现有 `from kb_common import X` 调用者零改动可用。
  2. 尚未迁出的函数与常量（config / domain / date / inference / dedup 等）——后续迭代可继续拆分。

迁移策略（ARCHITECTURE-REVIEW.md §P1-1）：保留 shim → 分批迁移调用者 → 最后删除 shim。
"""
import os
import re
import math
import json
import hashlib
from pathlib import Path
from datetime import datetime, date
from collections import Counter
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

# ── re-export：让 `from kb_common import X` 继续工作 ──────────
from kb_io import *        # noqa: F401,F403  I/O 层
from kb_text import *      # noqa: F401,F403  文本/向量层
from kb_metautil import *  # noqa: F401,F403  领域元数据层

# ── 以下为尚未迁出的定义（config / domain / date / inference / dedup）──



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
_LAT_RE = re.compile(r"[A-Za-z0-9]+")

# status 白名单
STATUS_SET = {"draft", "active", "stable", "legacy", "archived"}


def _tokens(s: str) -> List[str]:
    """拉丁词 lower + 中文字符 unigram（clean.py infer_domain 用）。"""
    return [m.lower() for m in _LAT_RE.findall(s)] + CJK.findall(s)


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
        # YAML 块列表续行：`tags:` 换行后的 `  - 开发` / `  - kb-kit`
        # 以前这类行被直接丢弃 → tags 恒为 None → clean 回退成目录名派生标签，
        # 而 validate 判「缺必填字段: tags」：同一份合法 YAML 两个模块两种结论。
        if cur and (line == "-" or line.startswith("- ")):
            item = line[1:].strip().strip('"\'')
            if not isinstance(fm.get(cur), list):
                fm[cur] = []
            if item:
                fm[cur].append(item)
            continue
        if line.endswith(":"):
            cur = line[:-1].strip().strip('"\''); fm[cur] = None; continue
        if ":" in line:
            k, _, v = line.partition(":"); v = v.strip()
            cur = None
            if v.startswith("[") and v.endswith("]"):
                # source_ref 元素（如 openclaw/USER.md::… 片段名）可合法含逗号，
                # 必须按引号分块，不能无脑 split(",") 切成碎片；其余字段经全库比对，
                # 元素内从不含逗号，沿用旧 split 以保证行为零偏移。
                if k == "source_ref":
                    fm[k.strip()] = _split_bracket_aware(v[1:-1])
                else:
                    fm[k.strip()] = [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip().strip('"\'')
    return fm, text, block, body


def _split_bracket_aware(inner: str) -> List[str]:
    """把 [..] 块内内容按逗号分块，但引号内的逗号不计（保留为同一元素）。

    source_ref 元素形如 `openclaw/USER.md::Keep stable communication style,
    relationships, …`，逗号是其内容而非分隔符；无脑 split(",") 会把一条 ID
    切成碎片，导致 kb_raw 追溯解析全部失败（P2 追溯闭环 FAIL）。
    """
    toks, buf, i, n = [], [], 0, len(inner)
    in_q = False
    while i < n:
        c = inner[i]
        if in_q:
            buf.append(c)
            if c in ('"', "'"):
                in_q = False
            i += 1
        else:
            if c in ('"', "'"):
                in_q = True
                buf.append(c)
                i += 1
            elif c == ",":
                toks.append("".join(buf).strip())
                buf = []
                i += 1
            else:
                buf.append(c)
                i += 1
    if any(x.strip() for x in buf):
        toks.append("".join(buf).strip().strip('\"\''))
    return toks


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
