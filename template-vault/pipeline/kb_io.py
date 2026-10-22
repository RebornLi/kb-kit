"""kb_io — 文件 I/O 层（从 kb_common.py 拆分，P1-1）。

提供笔记/元数据/JSON 的读写与 frontmatter 解析，是 kb_common 中最底层的 I/O 模块。
被 kb_metautil、kb_common shim 以及下游各 pipeline 模块依赖。
"""
import os
import re
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union


# ── vault 根默认值（所有模块共用）────────────────────────────
ROOT_DEFAULT = os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[1])

# ── 遍历与"系统文件"排除（P2-2：全库唯一定义）──────────────
#
# 历史上这里有**三套**集合，元素不同、语义也不同：
#   · kb_io.EXCLUDE                 —— os.walk 剪枝（按**目录名精确匹配**）
#   · kb-healthcheck.SYSTEM_HINTS   —— 内容层判定（按**子串匹配**）
#   · kb_audit._SYS                 —— 与上面的内容逐字相同，但按**路径段匹配**
#
# 后果（kb_audit.py 的原注释记录）：「曾出现 healthcheck=0 / audit=46 的
# 相反结论，根因是排除集不同却没写明。」修过一次，但**没有收敛到单一常量**，
# 下次新增目录仍会分叉。
#
# 现在拆成两个语义清晰、职责不重叠的常量：
#   EXCLUDE         遍历层：os.walk 遇到这些**目录名**就不进去
#   SYSTEM_SEGMENTS 内容层：已经遍历到的文件，若**路径段**命中就不算知识笔记

EXCLUDE = {".git", "backups", "logs", "vector index", "pipeline", "_SELF_OPT",
           ".obsidian", "__pycache__", ".pytest_cache", ".agents", ".trash", ".codeartsdoer",
           "_curate", "_audit",
           # `_trash` 是归档垃圾桶（intake trash / clean 去重的旧份）。
           # 它可能装着没有 frontmatter 的原始输入；若参与遍历，`kb validate`
           # 会把垃圾当笔记报硬错误，`kb rag index` 还会把垃圾索引进检索结果。
           "_trash",
           # `.kb` 是**运行态目录**（状态文件 + 报告），永远不是笔记。
           # 漏排的实测后果：consolidate 把报告写成 `.kb/state/consolidate_report.md` 后，
           # `kb validate` 把它当笔记扫进去 → 报「缺少 frontmatter」硬错误。
           ".kb"}

# 内容层的"系统/特殊目录"：位于这些顶层分区里的文件不参与
# 死链/骨架/标签/孤儿/颗粒度等**知识笔记**巡检。
#   memory/    Agent 记忆镜像（待晋升，不是知识正文）
#   reference/ 参考与契约（核心文件镜像、项目上下文）
#   raw/       证据层（抓取物 + 保号原文），天生无出链、非字数受控对象
#   docs/      人写的交付文档（报告/手册），本来就长，分块会毁可读性
#   scripts/ main/ media/ 工程与媒体资源
#   点开头的（.git/.obsidian/.openclaw/.venvo/.trash）是工具运行时态
SYSTEM_SEGMENTS = frozenset({
    "memory", "reference", "main", "media", "raw", "docs", "scripts",
    ".git", ".obsidian", ".openclaw", ".venvo", ".trash", "_trash", "_audit",
})


def is_system_path(rel) -> bool:
    """rel 是否是"系统/特殊目录"下的文件（**不属于知识笔记**）。

    P2-2 唯一实现。**按路径段精确匹配**，不做子串匹配 ——
    子串匹配会误伤 `mydocs/a.md`（被 `docs/` 子串命中）和
    `a.venvo/b.md`（被 `.venvo` 子串命中），实测两者都会从巡检里消失。
    """
    if rel is None:
        return False
    try:
        text = str(rel)
    except (TypeError, ValueError, AttributeError):
        return False
    if not text:
        return False
    # 先归一分隔符：Windows 反斜杠不归一会让整串变成一个"文件名"，
    # 系统目录判定静默全部失效（同 kb_metautil._segments 的理由）。
    if "\\" in text:
        text = text.replace("\\", "/")
    parts = [seg.lower() for seg in text.split("/") if seg not in ("", ".")]
    return any(p in SYSTEM_SEGMENTS for p in parts[:-1]) or (
        bool(parts) and parts[0] in SYSTEM_SEGMENTS)

# ── frontmatter 边界正则 ────────────────────────────────────
FM = re.compile(r"^---\s*$", re.M)

def atomic_write_text(path, text: str) -> None:
    """原子写入文本：先写同目录临时文件，再 os.replace 覆盖目标。

    为什么必须这样写：直接 `p.write_text(...)` 在写到一半时崩溃/断电会留下**半截文件**，
    而状态文件（旋钮、用户表、agent 注册表、反馈状态）一旦半截就无法解析，
    轻则配置回退默认、重则整表丢失。
    为什么用 os.replace 而不是 Path.rename：Windows 上 rename 在目标已存在时会抛
    FileExistsError，导致第二次起写入静默失败（调用方多吞异常）。
    os.replace 在 POSIX/Windows 均为原子覆盖；失败时清理残留临时文件。
    顺带确保父目录存在（原先多处调用方各自 mkdir，漏一处就写失败）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

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
    """遍历 root 下所有 .md 文件（排除 EXCLUDE 目录、嵌套 git 仓库）。

    注意：root 统一转成字符串再交给 os.walk —— 在部分受限执行环境中，
    传 Path 对象时 os.walk 的第一个元组可能回传 str 形式的 dp，
    使 `dp != root` 判定为真、误触发"嵌套 git 仓库"剪枝，整库遍历静默返回 0。
    """
    root = os.fspath(root)
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE]
        if dp != root and os.path.exists(os.path.join(dp, ".git")):
            dn[:] = []
            continue
        for f in fn:
            if f.endswith(".md"):
                yield Path(dp) / f

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
