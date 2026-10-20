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

# ── os.walk 排除集合（与其他 pipeline 模块保持一致）──────────
EXCLUDE = {".git", "backups", "logs", "vector index", "pipeline", "_SELF_OPT",
           ".obsidian", "__pycache__", ".pytest_cache", ".agents", ".trash", ".codeartsdoer",
           "_curate",
           # `.kb` 是**运行态目录**（状态文件 + 报告），永远不是笔记。
           # 漏排的实测后果：consolidate 把报告写成 `.kb/state/consolidate_report.md` 后，
           # `kb validate` 把它当笔记扫进去 → 报「缺少 frontmatter」硬错误。
           ".kb"}

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
