#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_schema.py — 知识契约检查器（只读）。

对照 reference/kb-schema.json 检查全库 frontmatter 的**合规性与漂移**，
产出一份可行动的清单（不修改任何笔记）。

检查项：
  1. 必填字段缺失（按层放宽：raw/ 只需证据字段）
  2. 枚举越界（status/domain/kb_layer/curate_verdict/lifecycle）
  3. 类型/范围错误（importance、confidence 是否 0–1）
  4. canon 层缺溯源（source_ref / archived_original / kb_summary）
  5. noise 层未按契约退出检索（kb_index 应为 false、kb_layer 应为 noise）
  6. evidence 层越界（raw 不应有 kb_layer）
  7. summary 超长（>200 字符）
  8. 陈旧度（计算值，不存储）

用法:
  python3 pipeline/kb_schema.py check  [--root R] [--json] [--limit N]
  python3 pipeline/kb_schema.py fields [--root R]     # 统计字段使用率（看契约实际覆盖）
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

from kb_common import (ROOT_DEFAULT, iter_notes, load_note_full as load,
                       is_generated_report, is_raw_path, kb_layer_of)

SCHEMA_PATH = Path("reference") / "kb-schema.json"


def load_schema(root: Path):
    """读契约；缺文件 → None（调用方降级为"仅报字段使用率"）。"""
    try:
        return json.loads((root / SCHEMA_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _kind(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, list):
        return "list"
    return "string"


def check(root: Path, limit: int = 40, as_json: bool = False) -> int:
    schema = load_schema(root)
    if schema is None:
        print(f"✗ 找不到契约文件 {SCHEMA_PATH}（先放入 reference/kb-schema.json）", file=sys.stderr)
        return 2
    F = schema["fields"]
    enums = {k: v["values"] for k, v in F.items() if v.get("type") == "enum" and "values" in v}
    aspirational = {k for k, v in F.items() if v.get("_maturity") == "aspirational"}
    required = {k for k, v in F.items() if v.get("required")}
    canon_required = schema.get("canon_required", [])

    problems = defaultdict(list)   # 类别 → [(rel, 详情)]
    layer_stat = Counter()
    field_use = Counter()
    n = 0

    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue
        n += 1
        fm, _t, _b, _body = load(p)
        L = kb_layer_of(fm, rel)
        layer_stat[L] += 1
        for k in fm:
            field_use[k] += 1

        # 1) 必填（证据层按 is_source/所在层放宽）
        if not (is_raw_path(rel) or fm.get("is_source")):
            for k in sorted(required):
                if k not in fm or fm.get(k) in (None, ""):
                    problems["缺必填字段"].append((rel, k))

        # 2) 枚举越界
        for k, allowed in enums.items():
            v = fm.get(k)
            if v in (None, "", "None", "null"):
                continue   # 空值不算越界（缺失由必填项单独管）
            vs = str(v).strip().lower() if k in ("kb_layer", "curate_verdict", "lifecycle") else str(v).strip()
            pool = [str(x).lower() for x in allowed] if k in ("kb_layer", "curate_verdict", "lifecycle") else allowed
            if vs not in pool:
                cat = "自由标签（建议归一）" if k in aspirational else "枚举越界"
                problems[cat].append((rel, f"{k}={v}"))

        # 3) 类型/范围
        for k in ("importance", "confidence", "curate_confidence", "canon_ratio"):
            if k in fm and fm.get(k) not in (None, "", "None", "null"):
                try:
                    x = float(re.search(r"[-+]?\d*\.?\d+", str(fm[k])).group())
                except (AttributeError, TypeError, ValueError):
                    problems["类型错误"].append((rel, f"{k}={fm.get(k)!r}"))
                    continue
                if k in ("importance", "confidence", "curate_confidence") and not (0.0 <= x <= 1.0):
                    problems["范围越界"].append((rel, f"{k}={x}"))

        # 4) canon 层溯源
        if L == "canon":
            for k in canon_required:
                if k not in fm or fm.get(k) in (None, "", []):
                    problems["canon 缺溯源"].append((rel, k))
            arch = str(fm.get("archived_original") or "")
            if arch and not (root / arch).exists():
                problems["canon 溯源断裂"].append((rel, f"archived_original 不存在: {arch}"))

        # 5) noise 契约
        if L == "noise" or str(fm.get("curate_verdict", "")).lower() == "noise":
            if str(fm.get("kb_index", "")).strip().lower() not in ("false", "no", "0"):
                problems["noise 未退出检索"].append((rel, f"kb_index={fm.get('kb_index')!r}"))

        # 6) 证据层越界
        if is_raw_path(rel) and str(fm.get("kb_layer", "")).lower() in ("canon", "noise", "index"):
            problems["证据层越界"].append((rel, f"kb_layer={fm.get('kb_layer')}"))

        # 7) summary 超长（证据层是原文保真副本，不做长度约束）
        s = str(fm.get("kb_summary") or "")
        if len(s) > 200 and not is_raw_path(rel):
            problems["summary 超长"].append((rel, f"{len(s)} 字"))

    total = sum(len(v) for v in problems.values())
    if as_json:
        print(json.dumps({
            "notes": n, "layers": dict(layer_stat), "problems": {k: v[:limit] for k, v in problems.items()},
            "problem_total": total, "schema_version": (schema.get("_meta") or {}).get("version"),
            "field_usage": dict(field_use.most_common(60)),
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"📐 知识契约检查（schema v{(schema.get('_meta') or {}).get('version')}）  root={root}")
    print(f"   笔记 {n} · 分层 {dict(layer_stat)}")
    if not problems:
        print("   ✅ 全部符合契约")
        return 0
    print(f"   发现 {total} 处偏差，按类：")
    for k, items in sorted(problems.items(), key=lambda x: -len(x[1])):
        print(f"\n   ▪ {k}：{len(items)} 处")
        for rel, detail in items[:limit]:
            print(f"      - {rel[:66]}  →  {detail}")
        if len(items) > limit:
            print(f"      … 其余 {len(items) - limit} 处（--json 看全部）")
    print("\n   说明：本检查**只读**，不修改任何笔记。契约文件：reference/kb-schema.json")
    return 0


def cmd_fields(root: Path) -> int:
    schema = load_schema(root)
    known = set((schema or {}).get("fields", {}))
    use, n = Counter(), 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue
        n += 1
        fm, _t, _b, _body = load(p)
        for k in fm:
            use[k] += 1
    print(f"🗂 frontmatter 字段使用率（{n} 篇）")
    print(f"{'字段':28}{'出现':>7}{'占比':>8}  契约")
    for k, c in use.most_common(60):
        mark = "✔ 已声明" if k in known else "＋ 未声明(建议入契约)"
        print(f"{k:28}{c:7}{c / max(n, 1):8.0%}  {mark}")
    undeclared = [k for k in use if k not in known]
    if undeclared:
        print(f"\n未声明字段 {len(undeclared)} 个：{', '.join(sorted(undeclared)[:25])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="知识契约检查（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--root", default=ROOT_DEFAULT)
    c.add_argument("--json", action="store_true", dest="as_json")
    c.add_argument("--limit", type=int, default=40)
    f = sub.add_parser("fields"); f.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "check":
        return check(root, args.limit, args.as_json)
    if args.cmd == "fields":
        return cmd_fields(root)
    return 1


if __name__ == "__main__":
    sys.exit(main())
