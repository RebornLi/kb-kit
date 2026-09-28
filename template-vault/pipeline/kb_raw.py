#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# kb_raw.py —— 原记忆（证据层）可达性：按需调用原始文件
#   show   : 按 ID 或路径取出**原文真实内容**（不是索引摘要）
#   find   : 在「原记忆全文」里搜索（不是检索索引，是原文 grep）
#   list   : 列出证据层清单（ID / 来源 / 字节 / 指针）
#   path   : 只解析出原文件的绝对路径（供脚本/Agent 管道消费）
#
#   设计意图：知识层只放结晶后的干净内容，但每条内容必须能一键回到原记忆。
#   因此这里不做任何摘要/改写——原样返回原文，可加行号、可截断。
#
#   ID 解析顺序（宽松、绝不写死私人路径）：
#     1) vault 相对路径（只要文件存在且位于 raw/ 或 memory/ 证据层）
#     2) 任何笔记 frontmatter 的 memory_source 字段（值 = ID），正查/反查
#     3) 按 ID 子串在证据层里模糊匹配（唯一命中才返回，多命中给出候选）
#     4) 已注册 Agent 的源根（kb-agent.json 的 sources.*）下按文件名/路径匹配
#
#   用法:
#     python3 pipeline/kb_raw.py list  [--root R] [--json]
#     python3 pipeline/kb_raw.py show  <ID> [--root R] [--lines N] [--full] [--path-only]
#     python3 pipeline/kb_raw.py find  "关键词" [--root R] [--limit N] [--context K] [--json]
#     python3 pipeline/kb_raw.py path  <ID> [--root R]
# ============================================================
import argparse, json, os, re, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import ROOT_DEFAULT, iter_notes, load_note_full as load, is_raw_path

EVIDENCE_DIRS = ("raw", "memory")           # 证据层顶层目录（原记忆 / 网页抓取）
DEFAULT_HEAD = 40                           # show 默认只给前 N 行
FM_ID_KEYS = ("memory_source", "kb_synced_from", "kb_source")


# ── 工具 ────────────────────────────────────────────────────
def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _is_evidence(rel: str) -> bool:
    """rel 是否位于证据层（顶层目录 raw/ 或 memory/）。"""
    try:
        parts = [p.lower() for p in Path(str(rel)).parts]
        return bool(parts) and parts[0] in EVIDENCE_DIRS
    except (TypeError, ValueError, AttributeError):
        return False


def _walk(root: Path):
    """遍历 vault：先 chdir 到 root 再用相对路径 os.walk。

    为什么不用绝对路径 os.walk：受限执行环境（sandbox）会拒绝绝对路径的目录列举，
    导致 iter_notes 静默返回 0 个文件。相对路径遍历对所有环境一致可用。
    """
    cwd = os.getcwd()
    try:
        os.chdir(root)
        for p in iter_notes(Path(".")):
            yield (root / p).resolve()
    finally:
        os.chdir(cwd)


def _evidence_files(root: Path) -> List[Path]:
    out = []
    for p in _walk(root):
        rel = str(p.relative_to(root))
        if _is_evidence(rel):
            out.append(p)
    return sorted(out)


def _agent_roots(root: Path) -> List[Tuple[str, Path]]:
    """从 kb-agent.json 读出已注册 Agent 的源根目录（只读，缺失则空）。"""
    cfg = root / "kb-agent.json"
    if not cfg.exists():
        cfg = root / ".kb-agent.json"
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    roots: List[Tuple[str, Path]] = []
    for name, spec in (data.get("agents") or {}).items():
        src = (spec or {}).get("sources") or {}
        for key in ("root", "daily_src", "data_dir", "db", "json", "evolve_json"):
            v = src.get(key)
            if not v:
                continue
            p = Path(str(v)).expanduser()
            if p.is_file():
                p = p.parent
            if p.is_dir():
                roots.append((f"{name}:{key}", p))
        for r in (src.get("roots") or []):
            p = Path(str(r)).expanduser()
            if p.is_dir():
                roots.append((f"{name}:roots", p))
    return roots


# ── ID 解析 ─────────────────────────────────────────────────
def _build_id_map(root: Path) -> Dict[str, List[Path]]:
    """ID → vault 内证据层文件（一对多）。ID 取 `memory_source`，回退为相对路径。

    同一逻辑原文可能被多个 Agent 摄取进库（如 openclaw 与 memory_sync 各一份），
    因此 ID 可能对应多个文件；`show` 打印全部，`list` 按 ID 聚合。
    """
    m: Dict[str, List[Path]] = {}
    for p in _walk(root):
        rel = str(p.relative_to(root))
        if not _is_evidence(rel):
            continue
        keys = [rel]
        fm, _text, _block, _body = load(p)
        for key in FM_ID_KEYS:
            v = str(fm.get(key, "") or "").strip()
            if v:
                keys.append(v)
                keys.append(v.split("::")[0])
        for k in keys:
            bucket = m.setdefault(k, [])
            if p not in bucket:
                bucket.append(p)
    for k in m:
        m[k].sort()
    return m


def _read_one(p: Path) -> str:
    return _read(p)


def cmd_find(root: Path, needle: str, limit: int, context: int, as_json: bool) -> int:
    """在证据层原文里搜关键词（原文 grep，不是索引检索）。

    同一原文被多个 Agent 摄取时，按「内容指纹 + 首处行号」去重，只报第一份，
    并在结果里标注该 ID 共有几份副本。
    """
    if not needle:
        print("✗ 请给出关键词", file=sys.stderr)
        return 2
    pat = re.compile(re.escape(needle), re.I)
    id_map = _build_id_map(root)
    results = []
    seen_fp = set()
    for p in _evidence_files(root):
        text = _read_one(p)
        if not pat.search(text):
            continue
        rel = str(p.relative_to(root))
        fm, _t, _b, _body = load(p)
        raw_id = str(fm.get("memory_source") or rel).strip() or rel
        copies = len(id_map.get(raw_id, [p]))
        ls = text.splitlines()
        first_line = next((i + 1 for i, l in enumerate(ls) if pat.search(l)), 0)
        fp = (len(text), first_line, len(pat.findall(text)))
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        snips = []
        for i, l in enumerate(ls):
            if pat.search(l):
                lo, hi = max(0, i - context), min(len(ls), i + context + 1)
                snips.append({"line": i + 1, "text": "\n".join(ls[lo:hi])})
                if len(snips) >= 3:
                    break
        results.append({"id": raw_id, "path": rel, "hits": len(pat.findall(text)),
                        "copies": copies, "snippets": snips})
        if len(results) >= limit:
            break
    if as_json:
        print(json.dumps({"query": needle, "count": len(results), "results": results},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"🔎 原记忆全文搜索：{needle}   命中 {len(results)} 个 ID（已按内容去重）")
    for r in results:
        extra = f"  ·本 ID 共 {r['copies']} 份副本" if r["copies"] > 1 else ""
        print(f"\n📄 {r['id']}{extra}\n   路径: {r['path']}  （{r['hits']} 处）")
        for s in r["snippets"]:
            print(f"   L{s['line']}: " + s["text"].replace("\n", "\n        "))
        print(f"   取原文: kb raw show \"{r['id']}\"")
    if not results:
        print("   （无命中。这里搜的是 raw/ 与 memory/ 的原文全文，不是知识层索引）")
    return 0


def cmd_list(root: Path, as_json: bool) -> int:
    """按 ID 聚合列出证据层（同一原文的多个副本合并为一行）。"""
    id_map = _build_id_map(root)
    rows = []
    for raw_id, paths in sorted(id_map.items()):
        if raw_id.startswith("openclaw/") and raw_id.count("/") == 1 and "." in raw_id.split("/")[-1]:
            pass  # 正常 ID
        first = paths[0]
        fm, _t, _b, body = load(first)
        rel = str(first.relative_to(root))
        if raw_id != str(fm.get("memory_source") or rel).strip() and raw_id != rel:
            continue  # 只列主 ID（memory_source / 相对路径），不列 :: 子节等变体
        rows.append({
            "id": raw_id,
            "path": rel,
            "copies": len(paths),
            "source": str(fm.get("kb_source") or ""),
            "bytes": len(body.encode("utf-8")),
        })
    if as_json:
        print(json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False, indent=2))
        return 0
    print(f"🗂 证据层清单：{len(rows)} 个 ID（raw/ + memory/，已合并同内容副本）")
    for r in rows[:60]:
        cp = f" ×{r['copies']}" if r["copies"] > 1 else ""
        print(f"   {r['bytes']:>7}B  [{r['source'] or '-':<12}] {r['id']}{cp}")
    if len(rows) > 60:
        print(f"   … 其余 {len(rows) - 60} 个；用 --json 或 kb raw find \"关键词\" 精确定位")
    return 0


def resolve(raw_id: str, root: Path) -> Tuple[Optional[Path], List[str]]:
    """把 ID 解析为原文件路径。返回 (主文件路径, 额外副本/候选的相对路径列表)。"""
    raw_id = str(raw_id or "").strip()
    if not raw_id:
        return None, []
    # 0) `vault:<rel>` 别名：正典的 source_ref 对没有 memory_source 的笔记会退化用它。
    #    先解析原位置；原位置已被结晶改写时，回落到保号副本 raw/_curated/<rel>。
    if raw_id.startswith("vault:"):
        rel = raw_id[len("vault:"):].strip()
        for cand_rel in (rel, str(Path("raw") / "_curated" / rel)):
            c = root / cand_rel
            if c.is_file():
                return c, []
    # 1) vault 相对路径
    cand = root / raw_id
    if cand.is_file() and _is_evidence(str(Path(raw_id))):
        return cand, []
    # 1b) raw/_curated/ 保号副本（结晶前的原文）：即使 ID 不是证据层路径也能取回
    cur = root / "raw" / "_curated" / raw_id
    if cur.is_file():
        return cur, []
    # 2) ID 映射（memory_source 等）—— 可能一 ID 多副本
    id_map = _build_id_map(root)
    if raw_id in id_map:
        paths = id_map[raw_id]
        extra = [str(p.relative_to(root)) for p in paths[1:]]
        return paths[0], extra
    # 3) 子串模糊：按相对路径/ID 子串匹配（唯一命中才返回）
    hits = sorted({p for k, paths in id_map.items() if raw_id.lower() in str(k).lower()
                   for p in paths})
    if len(hits) == 1:
        return hits[0], []
    if len(hits) > 1:
        return None, [str(h.relative_to(root)) for h in hits[:20]]
    # 4) 注册 Agent 源根（原记忆流可能还没被摄取进库）
    for label, base in _agent_roots(root):
        p = base / raw_id
        if p.is_file():
            return p, []
        try:
            for f in base.rglob(Path(raw_id).name):
                if f.is_file():
                    return f, []
        except OSError:
            continue
    return None, []


# ── 子命令 ─────────────────────────────────────────────────
def cmd_show(root: Path, raw_id: str, lines: int, full: bool, path_only: bool,
             as_json: bool = False) -> int:
    p, extra = resolve(raw_id, root)
    if p is None:
        if as_json:
            print(json.dumps({"ok": False, "id": raw_id, "candidates": extra[:20]},
                             ensure_ascii=False))
        else:
            print(f"✗ 未找到原记忆：{raw_id}", file=sys.stderr)
            if extra:
                print("  同名/相近候选（请用更完整的 ID）：", file=sys.stderr)
                for c in extra[:20]:
                    print(f"   - {c}", file=sys.stderr)
        return 1
    if path_only:
        print(str(p))
        return 0
    text = _read(p)
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = p
    # --json：纯结构化输出（Agent/管道消费），不带任何装饰行
    if as_json:
        print(json.dumps({"ok": True, "id": raw_id, "path": str(rel),
                          "chars": len(text), "lines": text.count("\n") + 1,
                          "copies": extra[:10], "content": text}, ensure_ascii=False))
        return 0
    n = len(text)
    print(f"📄 原记忆：{rel}    {n} 字符 / {text.count(chr(10)) + 1} 行")
    if extra:
        print(f"   ↩ 同 ID 另有 {len(extra)} 份副本（多为不同 Agent 或不同流水线版本）：")
        for e in extra[:5]:
            print(f"      - {e}")
    print("─" * 60)
    if full or lines <= 0:
        print(text)
        return 0
    body_lines = text.splitlines()
    head = body_lines[:lines]
    print("\n".join(head))
    if len(body_lines) > lines:
        print(f"\n…（省略 {len(body_lines) - lines} 行；--full 看全部，--lines 0 等价 --full）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="原记忆（证据层）按需调用")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("id"); s.add_argument("--root", default=ROOT_DEFAULT)
    s.add_argument("--lines", type=int, default=DEFAULT_HEAD); s.add_argument("--full", action="store_true")
    s.add_argument("--path-only", action="store_true", dest="path_only")
    s.add_argument("--json", action="store_true", dest="as_json",
                   help="纯 JSON 输出（Agent/管道消费；内容不带装饰行）")
    f = sub.add_parser("find"); f.add_argument("q"); f.add_argument("--root", default=ROOT_DEFAULT)
    f.add_argument("--limit", type=int, default=20); f.add_argument("--context", type=int, default=2)
    f.add_argument("--json", action="store_true", dest="as_json")
    l = sub.add_parser("list"); l.add_argument("--root", default=ROOT_DEFAULT)
    l.add_argument("--json", action="store_true", dest="as_json")
    p = sub.add_parser("path"); p.add_argument("id"); p.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "show":
        return cmd_show(root, args.id, args.lines, args.full, args.path_only,
                        as_json=getattr(args, "as_json", False))
    if args.cmd == "find":
        return cmd_find(root, args.q, args.limit, args.context, args.as_json)
    if args.cmd == "list":
        return cmd_list(root, args.as_json)
    if args.cmd == "path":
        pth, cands = resolve(args.id, root)
        if pth is None:
            print(f"✗ 未找到：{args.id}", file=sys.stderr)
            return 1
        print(str(pth))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
