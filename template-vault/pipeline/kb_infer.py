#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_infer.py — 笔记字段推断（从 kb_common.py 拆出，原自 clean.py）。

职责：从路径/frontmatter/content_type 推断笔记的 status / tags / domain /
summary，以及收件箱月度聚合桶（aggregate_bucket）落盘。

依赖方向：kb_infer → kb_text（CJK 正则）、kb_domain（域词表）、
kb_config（DOMAIN_WHITELIST）。无反向依赖，无环。

注意 `_tokens` 的口径与 `kb_text.tokenize` **有意不同**：
这里是 CJK **仅 unigram**（域推断用，避免 bigram 噪声）；
kb_text.tokenize 是 unigram+bigram（检索索引用）。
tests/test_intentional_divergence.py 钉住了这个差异，别统一。
"""
import datetime as _dt
import os as _os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from kb_config import DOMAIN_WHITELIST
from kb_domain import (DOMAIN_MAP, FOLDER_DOMAIN, KEYWORD_DOMAIN,
                       STATUS_SET)
from kb_text import CJK

# frontmatter 边界正则（与 kb_io.FM 同义但保留独立引用）
_FM_RE = re.compile(r"^---\s*$", re.M)
_LAT_RE = re.compile(r"[A-Za-z0-9]+")

# 聚合桶常量
AGGREGATE_DIR = "00-收件箱 Inbox/_aggregated"
AGGREGATE_LIMIT = 2000
AGGREGATE_IMPORTANCE = 0.3


def _tokens(s: str) -> List[str]:
    """拉丁词 lower + 中文字符 unigram（infer_domain 用）。"""
    return [m.lower() for m in _LAT_RE.findall(s)] + CJK.findall(s)


def fmt_value(v: Any) -> str:
    """frontmatter 值格式化：list → [a, b]，其他 → str。"""
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def parse_date_str(v: Any, default: _dt.date) -> str:
    """解析日期 → YYYY-MM-DD 字符串。

    与 kb_metautil.parse_date（返回 datetime）不同，此函数返回字符串，
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


def load_note_full(path) -> tuple:
    """加载笔记 → (fm, text, block, body) 四元组。

    与 kb_io.load_note（返回三元组）不同，此函数额外返回 block
    （frontmatter 原始文本块）。

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
        toks.append("".join(buf).strip().strip('"\''))
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


def aggregate_bucket(root, body: str, content_type: str,
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
