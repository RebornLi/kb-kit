#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tag_fix.py — 标签越界治理（P6）。

治理三类越界（依据 v1.3.0 的既定思路：**归一，不堆白名单**）：

  1. **目录路径型**（`40-资源库 Resources`、`20-技术 Technology`…）
     → **删除**。目录已经在路径与 `kb_target`/`domain` 里表达了，当标签是纯冗余，
       还会把 tag 空间灌成"路径转写"。这类**不扩白名单**（否则等于承认它合法）。
  2. **阶段/主题型**（`P2`…`P6`、`知识治理`、`概念索引`、`embedding-model`…）
     → **扩入受控词表**（真主题，检索有用），并按组归档进 `tag-taxonomy.json`。
  3. **别名型**（`嵌入模型` vs `embedding-model`、`测试` vs `testing`）
     → **归一为规范词**，避免同一概念两种写法。

纪律：
  · 默认 dry-run，逐条打印将做的改动
  · `--apply` 才写；写前由调用方打 git checkpoint
  · **绝不动 `raw/` 原件**（证据层保真）；`raw/_curated/` 副本按"可选"处理

用法:
  python3 pipeline/tag_fix.py scan  [--root R] [--json]
  python3 pipeline/tag_fix.py fix   [--root R] [--apply] [--include-raw]
"""
import argparse, json, re, sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

from kb_common import (ROOT_DEFAULT, iter_notes, load_note_full as load,
                       is_raw_path, is_generated_report)
import taxonomy

# 目录路径型：`40-资源库 Resources` / `40资源库Resources` / `20-技术 Technology`
PATH_LIKE = re.compile(r"^\d{2}[- ]?[\u4e00-\u9fffA-Za-z].*(Technology|Projects|Resources|"
                       r"Decisions|Governance|Operations|Archive|Inbox|Templates|知识库|"
                       r"资源库|项目|决策日志|知识治理|归档|收件箱)$")
# 别名 → 规范词（同一概念的两种写法统一）
ALIASES = {
    "嵌入模型": "embedding-model",
    "中英文检索": "multilingual-retrieval",
    "性能测试": "perf-test",
    "选型": "model-selection",
    "评测": "eval",
    "优化": "optimization",
    "报告": "report",
    "知识治理": "knowledge-governance",
    "概念索引": "concept-index",
    "写侧协议": "write-side-protocol",
    "置信分": "confidence",
    "矛盾在环": "contradiction",
    "检索优化": "retrieval-optimization",
}


def scan(root: Path, include_raw: bool = False) -> Dict[str, Any]:
    """扫描三类越界，返回可执行的改动清单（只读）。"""
    path_tags, unknown, alias_hits = Counter(), Counter(), Counter()
    path_where, alias_where = {}, {}
    tax = taxonomy.load_taxonomy(root)
    known = taxonomy.known_tags(tax, root)
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue
        if is_raw_path(rel) and not include_raw:
            continue
        fm, _t, _b, _body = load(p)
        for t in taxonomy.parse_tags(fm.get("tags")):
            if PATH_LIKE.match(t):
                path_tags[t] += 1
                path_where.setdefault(t, []).append(rel)
            elif t in ALIASES:
                alias_hits[t] += 1
                alias_where.setdefault(t, []).append(rel)
            elif not taxonomy.is_known_tag(t, tax, root):
                unknown[t] += 1
    return {
        "path_like": {"tags": dict(path_tags), "n": sum(path_tags.values()),
                      "where": {k: v[:5] for k, v in path_where.items()}},
        "alias": {"tags": dict(alias_hits), "n": sum(alias_hits.values()),
                  "where": {k: v[:5] for k, v in alias_where.items()}},
        "unknown": {"tags": dict(unknown), "n": sum(unknown.values())},
    }


def fix(root: Path, apply: bool, include_raw: bool) -> int:
    """执行：删路径型 / 归一别名。默认 dry-run。"""
    removed_n = renamed_n = touched = 0
    changes: List[str] = []
    files_touched: List[str] = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue
        if is_raw_path(rel) and not include_raw:
            continue
        fm, _t, _b, _body = load(p)
        old = taxonomy.parse_tags(fm.get("tags"))
        if not old:
            continue
        new, notes = [], []
        for t in old:
            if PATH_LIKE.match(t):
                notes.append(f"删 {t}")
                removed_n += 1
                continue
            if t in ALIASES:
                new.append(ALIASES[t])
                notes.append(f"{t}→{ALIASES[t]}")
                renamed_n += 1
                continue
            if t not in new:
                new.append(t)
        if new == old:
            continue
        # 去重后顺序保持
        if notes:
            changes.append(f"  {rel[:64]}: " + "; ".join(notes))
            files_touched.append(rel)
            touched += 1
            if apply:
                text = p.read_text(encoding="utf-8")
                nt = _rewrite_tags(text, new)
                if nt and nt != text:
                    p.write_text(nt, encoding="utf-8")
    print(f"{'✅ 已应用' if apply else '🔍 dry-run'}：涉及 {touched} 个文件 · "
          f"删路径型 {removed_n} 处 · 归一别名 {renamed_n} 处")
    for c in changes[:25]:
        print(c)
    if len(changes) > 25:
        print(f"  … 其余 {len(changes) - 25} 个文件")
    if not apply and touched:
        print("\n   应用：kb tag fix --apply")
    return touched


def _rewrite_tags(text: str, tags: List[str]) -> str:
    if not text.startswith("---"):
        return ""
    m = re.search(r"^---\s*$", text, re.M)
    end = re.search(r"^---\s*$", text[m.end():], re.M) if m else None
    if not (m and end):
        return ""
    block = text[m.end():m.end() + end.start()]
    body = text[m.end() + end.end():]
    line = "tags: [" + ", ".join(f'"{t}"' for t in tags) + "]"
    if re.search(r"^tags:.*$", block, re.M):
        block = re.sub(r"^tags:.*$", line, block, count=1, flags=re.M)
    else:
        block = line + "\n" + block.lstrip("\n")
    return "---\n" + block + "\n---\n" + body


def main() -> int:
    ap = argparse.ArgumentParser(description="标签越界治理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--root", default=ROOT_DEFAULT)
    s.add_argument("--json", action="store_true", dest="as_json")
    s.add_argument("--include-raw", action="store_true", dest="include_raw")
    f = sub.add_parser("fix"); f.add_argument("--root", default=ROOT_DEFAULT)
    f.add_argument("--apply", action="store_true"); f.add_argument("--include-raw", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "scan":
        res = scan(root, args.include_raw)
        if args.as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2)); return 0
        print(f"🏷 标签治理扫描（{'含' if args.include_raw else '不含'} raw/）")
        print(f"   ① 目录路径型（应删）: {len(res['path_like']['tags'])} 种 / {res['path_like']['n']} 处")
        for t, n in sorted(res["path_like"]["tags"].items(), key=lambda x: -x[1])[:8]:
            print(f"       {n:4}×  {t}")
        print(f"   ② 别名型（应归一）: {len(res['alias']['tags'])} 种 / {res['alias']['n']} 处")
        for t, n in sorted(res["alias"]["tags"].items(), key=lambda x: -x[1])[:8]:
            print(f"       {n:4}×  {t} → {ALIASES.get(t)}")
        print(f"   ③ 未收录（应扩词表）: {len(res['unknown']['tags'])} 种 / {res['unknown']['n']} 处")
        for t, n in sorted(res["unknown"]["tags"].items(), key=lambda x: -x[1])[:8]:
            print(f"       {n:4}×  {t}")
        return 0
    if args.cmd == "fix":
        return 0 if fix(root, args.apply, args.include_raw) >= 0 else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
