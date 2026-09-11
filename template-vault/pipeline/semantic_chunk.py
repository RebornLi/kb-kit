#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic_chunk.py — 语义分块器：对无 ## 标题的超大文档按段落滑动窗口切分。

每块约 2000 字，相邻块 500 字重叠，生成父索引页 + 分块笔记。
frontmatter 含 chunk_of / chunk_index / chunk_total 字段，可追溯父文档。
"""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChunkNote:
    """分块笔记。"""
    filename: str               # 文件名
    frontmatter: dict           # frontmatter 字段
    body: str                   # 正文
    is_parent_index: bool       # 是否父索引页


def _sliding_window(body, chunk_size=2000, overlap=500):
    """滑动窗口分块：按段落累积，超 chunk_size 产出一块，回退 overlap 字符。

    确保不拆分段落（在段落边界对齐）。
    """
    # 按空行分段
    paragraphs = re.split(r"\n\s*\n", body)
    paragraphs = [p for p in paragraphs if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        # 当前块已满，产出一块
        if current and current_len + para_len > chunk_size:
            chunks.append("\n\n".join(current))
            # 回退 overlap 字符，保留尾部段落作为下一块开头
            current, current_len = _rewind(current, overlap)
        current.append(para)
        current_len += para_len

    # 最后一块
    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _rewind(paras, overlap):
    """从段落列表末尾回退 overlap 字符，在段落边界对齐。

    Returns:
        (kept_paras, kept_len)
    """
    acc = 0
    kept = []
    for p in reversed(paras):
        if acc + len(p) > overlap:
            break
        kept.insert(0, p)
        acc += len(p)
    return kept, acc


def _first_sentence(text, max_len=80):
    """提取首句作为摘要（用于父索引页）。"""
    text = text.strip().replace("\n", " ")
    # 按句号/问号/感叹号切分
    m = re.match(r"^[^。！？\.\!\?]*[。！？\.\!\?]", text)
    if m:
        return m.group()[:max_len]
    return text[:max_len]


def _make_parent_index(parent_path, chunks):
    """生成父索引页 Markdown，列出所有分块及每块首句摘要。"""
    parent_name = Path(parent_path).name
    lines = [f"# {parent_name}（分块索引）", ""]
    lines.append(f"> 原文档过大，已切分为 {len(chunks)} 块。")
    lines.append("")
    lines.append("## 分块列表")
    lines.append("")
    for i, chunk in enumerate(chunks, 1):
        summary = _first_sentence(chunk)
        lines.append(f"- [[{Path(parent_path).stem}-c{i}]] — {summary}")
    lines.append("")
    return "\n".join(lines)


def chunk(body, parent_path, chunk_size=2000, overlap=500):
    """语义分块主入口。

    Args:
        body: 要分块的正文
        parent_path: 父文档路径（用于生成 chunk_of 字段和分块文件名）
        chunk_size: 每块目标字符数（默认 2000）
        overlap: 相邻块重叠字符数（默认 500）

    Returns:
        list[ChunkNote]：分块笔记列表（最后一个是父索引页）
    """
    chunks_text = _sliding_window(body, chunk_size, overlap)
    if len(chunks_text) <= 1:
        # 无需分块，返回空列表
        return []

    parent_stem = Path(parent_path).stem
    parent_name = Path(parent_path).name
    total = len(chunks_text)
    result = []

    for i, text in enumerate(chunks_text, 1):
        filename = f"{parent_stem}-c{i}.md"
        fm = {
            "chunk_of": str(parent_path),
            "chunk_index": i,
            "chunk_total": total,
        }
        result.append(ChunkNote(filename, fm, text, False))

    # 父索引页
    parent_index_body = _make_parent_index(parent_path, chunks_text)
    parent_index_fm = {
        "is_chunk_index": True,
        "chunk_total": total,
        "chunk_of": str(parent_path),
    }
    parent_index_filename = f"{parent_stem}-index.md"
    result.append(ChunkNote(parent_index_filename, parent_index_fm, parent_index_body, True))

    return result


# ── CLI 入口 ───────────────────────────────────────────────
def main():
    import argparse, sys
    ap = argparse.ArgumentParser(description="语义分块器")
    ap.add_argument("file", help="要分块的文件路径")
    ap.add_argument("--chunk-size", type=int, default=2000)
    ap.add_argument("--overlap", type=int, default=500)
    args = ap.parse_args()
    path = Path(args.file)
    body = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk(body, str(path), args.chunk_size, args.overlap)
    if not chunks:
        print("无需分块（内容不足）")
        return 0
    print(f"分块数: {len(chunks) - 1}（+ 1 父索引页）")
    for cn in chunks:
        print(f"  - {cn.filename} ({len(cn.body)} 字, parent_index={cn.is_parent_index})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
