#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest_convert.py — 格式归一化器：任意格式文件 → Markdown。

零依赖（只用 Python 标准库），不认识格式不崩溃存镜像。
支持 TXT/CSV/HTML/EML/JSON 零依赖转换，PDF/DOCX/XLSX 可选外部工具降级。

设计原则：
  - 原文永远保留镜像（保真优先）
  - 不认识格式不崩溃，存镜像 + 标记 convert_needed: true
  - 任何异常都不崩溃
"""
import csv, json, re, shutil, subprocess
from dataclasses import dataclass
from pathlib import Path
from html.parser import HTMLParser
from email import message_from_bytes


@dataclass
class ConvertResult:
    """转换结果。"""
    success: bool               # 是否成功转为 Markdown
    markdown: str | None        # 转换后的 Markdown（失败时 None）
    mirror_path: str | None     # 原文镜像路径（永远保留）
    convert_needed: bool        # 是否需要后续转换（外部工具未装等）
    schema_type: str | None     # JSON schema 类型（仅 JSON 转换有）
    error: str | None           # 错误信息（失败时有）


# ── 镜像保真存储 ────────────────────────────────────────────
def _save_mirror(path, mirror_dir):
    """原文保真镜像存储（原样复制，不修改）。"""
    mirror_dir = Path(mirror_dir)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    dest = mirror_dir / path.name
    # 同名冲突时加数字后缀
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = mirror_dir / f"{stem}-{i}{suffix}"
            i += 1
    shutil.copy2(path, dest)
    return str(dest)


# ── 零依赖转换器 ────────────────────────────────────────────
def _convert_txt(path):
    """TXT/LOG → Markdown（加 frontmatter，正文一致）。"""
    body = path.read_text(encoding="utf-8", errors="replace")
    return f"---\nsource_format: txt\n---\n\n{body}"


def _convert_csv(path):
    """CSV → Markdown 表格。"""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return "---\nsource_format: csv\n---\n\n（空表格）"
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
    lines = [header, sep]
    for row in rows[1:]:
        # 补齐列数
        while len(row) < len(rows[0]):
            row.append("")
        lines.append("| " + " | ".join(row) + " |")
    body = "\n".join(lines)
    return f"---\nsource_format: csv\n---\n\n{body}"


class _HTMLTextExtractor(HTMLParser):
    """HTML 正文提取器（标准库 html.parser）。"""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0  # 嵌套跳过深度

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip > 0:
            self.skip -= 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip == 0:
            self.parts.append(data)

    def get_text(self):
        text = "".join(self.parts)
        # 压缩多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _convert_html(path):
    """HTML → Markdown（去标签提正文）。"""
    html = path.read_text(encoding="utf-8", errors="replace")
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    body = extractor.get_text()
    return f"---\nsource_format: html\n---\n\n{body}"


def _convert_eml(path):
    """EML 邮件 → Markdown（含发件人/主题/时间）。"""
    raw = path.read_bytes()
    msg = message_from_bytes(raw)
    sender = msg.get("From", "")
    subject = msg.get("Subject", "")
    date = msg.get("Date", "")
    # 提取正文
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
            elif ct == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode("utf-8", errors="replace")
                    extractor = _HTMLTextExtractor()
                    extractor.feed(html)
                    body = extractor.get_text()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")
    fm = f"---\nsource_format: eml\nsender: \"{sender}\"\nsubject: \"{subject}\"\ndate: \"{date}\"\n---"
    return f"{fm}\n\n# {subject}\n\n{body}"


# ── JSON schema 识别 + 转换 ────────────────────────────────
def detect_schema(data):
    """检测 JSON schema 类型。

    Returns:
        "dsh"          - DSH agent-memory 格式（含 tables.records）
        "codex"        - Codex 会话格式（含 thread_items）
        "nested_array" - 顶层数组或嵌套数组
        "generic"      - 通用 JSON
    """
    if isinstance(data, dict):
        if "tables" in data and isinstance(data["tables"], dict) and "records" in data["tables"]:
            return "dsh"
        if "thread_items" in data:
            return "codex"
        for v in data.values():
            if isinstance(v, list):
                return "nested_array"
    if isinstance(data, list):
        return "nested_array"
    return "generic"


def _json_to_markdown(data, indent=0):
    """递归把 JSON 展平为 Markdown key-value 列表。"""
    lines = []
    pad = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}- **{k}**:")
                lines.append(_json_to_markdown(v, indent + 1))
            else:
                lines.append(f"{pad}- **{k}**: {v}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}- [{i}]:")
                lines.append(_json_to_markdown(item, indent + 1))
            else:
                lines.append(f"{pad}- [{i}]: {item}")
    else:
        lines.append(f"{pad}{data}")
    return "\n".join(lines)


def _convert_json(path):
    """JSON → Markdown（按 schema 探测展平）。"""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    schema = detect_schema(data)
    body = _json_to_markdown(data)
    return f"---\nsource_format: json\nschema_type: {schema}\n---\n\n{body}", schema


# ── 可选依赖转换器（外部工具）──────────────────────────────
def _convert_pdf(path):
    """PDF → Markdown（需 pdftotext，未装返回 None）。"""
    if not shutil.which("pdftotext"):
        return None
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return f"---\nsource_format: pdf\n---\n\n{result.stdout}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _convert_docx(path):
    """DOCX → Markdown（需 pandoc，未装返回 None）。"""
    if not shutil.which("pandoc"):
        return None
    try:
        result = subprocess.run(
            ["pandoc", str(path), "-t", "markdown"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return f"---\nsource_format: docx\n---\n\n{result.stdout}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _convert_xlsx(path):
    """XLSX → Markdown（需 openpyxl，未装返回 None）。"""
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"## {ws.title}\n")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
        wb.close()
        body = "\n".join(lines)
        return f"---\nsource_format: xlsx\n---\n\n{body}"
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None


def _convert_pptx(path):
    """PPTX → Markdown（需 pandoc，未装返回 None）。"""
    if not shutil.which("pandoc"):
        return None
    try:
        result = subprocess.run(
            ["pandoc", str(path), "-t", "markdown"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return f"---\nsource_format: pptx\n---\n\n{result.stdout}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# ── 格式路由表 ─────────────────────────────────────────────
CONVERTERS = {
    ".txt": _convert_txt,
    ".log": _convert_txt,
    ".csv": _convert_csv,
    ".tsv": _convert_csv,
    ".html": _convert_html,
    ".htm": _convert_html,
    ".eml": _convert_eml,
    ".json": _convert_json,
    ".pdf": _convert_pdf,
    ".docx": _convert_docx,
    ".xlsx": _convert_xlsx,
    ".pptx": _convert_pptx,
}


def convert(file_path, mirror_dir):
    """转换主入口。

    Args:
        file_path: 源文件路径
        mirror_dir: 镜像目录（原文保真存储）

    Returns:
        ConvertResult
    """
    path = Path(file_path)
    if not path.exists():
        return ConvertResult(False, None, None, True, None, f"file not found: {path}")

    # ① 原文永远保留镜像
    mirror = _save_mirror(path, mirror_dir)

    # ② 查路由
    ext = path.suffix.lower()
    converter = CONVERTERS.get(ext)
    if converter is None:
        return ConvertResult(False, None, mirror, True, None, f"unknown format: {ext}")

    # ③ 调转换器，异常捕获不崩溃
    try:
        result = converter(path)
        # JSON 转换器返回 (markdown, schema_type) 元组
        if ext == ".json" and isinstance(result, tuple):
            md, schema = result
            if md is None:
                return ConvertResult(False, None, mirror, True, schema, "external tool not installed")
            return ConvertResult(True, md, mirror, False, schema, None)
        # 其他转换器返回 markdown 字符串或 None
        if result is None:
            return ConvertResult(False, None, mirror, True, None, "external tool not installed")
        return ConvertResult(True, result, mirror, False, None, None)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        return ConvertResult(False, None, mirror, True, None, str(e))


# ── CLI 入口 ───────────────────────────────────────────────
def main():
    import argparse, sys
    ap = argparse.ArgumentParser(description="格式归一化器")
    ap.add_argument("file", help="要转换的文件路径")
    ap.add_argument("--mirror-dir", default=None, help="镜像目录")
    args = ap.parse_args()
    path = Path(args.file)
    mirror_dir = args.mirror_dir or path.parent / "_mirror"
    r = convert(path, mirror_dir)
    print(f"success: {r.success}")
    print(f"mirror_path: {r.mirror_path}")
    print(f"convert_needed: {r.convert_needed}")
    if r.schema_type:
        print(f"schema_type: {r.schema_type}")
    if r.error:
        print(f"error: {r.error}")
    if r.success:
        print(f"\n--- markdown (前 500 字) ---")
        print(r.markdown[:500])
    return 0 if r.success else 1


if __name__ == "__main__":
    sys.exit(main())
