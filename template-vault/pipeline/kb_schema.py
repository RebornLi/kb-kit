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
import argparse
import os
import subprocess, json, re, sys
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


# ── 双时态迁移（P1#5）─────────────────────────────────────────
#   为什么做：2025–2026 的时序知识图谱研究（Zep/Graphiti 等）指出，「事实随时间变化」与
#   「同一时刻的冲突说法」是两件事，必须靠 **事件时间 vs 事务时间** 分开才能区分。
#   而拿到这个语义**不需要图数据库** —— frontmatter 加字段即可（零依赖）。
#   本命令把存量笔记补上 valid_from / recorded_at（用 created / updated 作为最佳代理），
#   valid_to / superseded_by / conflict_with 留空（只在真的发生取代/冲突时写）。
#
#   **默认 dry-run**：要改几百篇笔记 frontmatter，必须先让人看到清单。
def cmd_bitemporal(root: Path, apply: bool, limit: int) -> int:
    from kb_common import iter_notes, load_meta, kb_layer_of
    import kb_schema_migrate as mig
    try:
        from rag import is_generated_report
    except ImportError:
        def is_generated_report(_rel):      # 退化：导入失败就不过滤
            return False
    cands, total, already = [], 0, 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel):
            continue        # 运行期报告产物（feedback_hits.md / recall_deck.md 等）会重新生成，
                            #   给它们写字段毫无意义，还会被下一次运行覆盖掉。
        try:
            fm, _body = load_meta(p)
        except OSError:
            continue
        total += 1
        if fm.get("valid_from") and fm.get("recorded_at"):
            already += 1
            continue
        # 只迁移"编译态"笔记（canon/page）：raw 证据层保持原样（不可变原则）
        layer = kb_layer_of(fm, rel)
        if layer not in ("canon", "page"):
            continue
        cands.append((rel, str(fm.get("created") or fm.get("updated") or "")[:10],
                      str(fm.get("updated") or fm.get("created") or "")[:10]))

    print(f"🕰  双时态字段迁移 · {root}")
    print(f"   扫描 {total} 篇 · 已有字段 {already} 篇 · 待迁移 {len(cands)} 篇"
          f"（canon/page 层；raw 证据层不动）")
    if not cands:
        print("   无需迁移。")
        return 0
    print(f"\n   样例（valid_from←created · recorded_at←updated）：")
    for rel, vf, ra in cands[:8]:
        print(f"     {rel[:60]:62} {vf or '（缺日期）':12} {ra}")
    if not apply:
        print(f"\n   预演模式（默认）：未写入任何文件。")
        print(f"   确认后执行：kb schema bitemporal --apply --limit {len(cands)}")
        return 0
    # checkpoint **只登记本次要写的文件** —— 不要用 `git add -A`：
    #   生产库里通常躺着使用者自己的未提交改动（实测 66 个已修改 + 47 个未跟踪），
    #   `-A` 会把它们一起卷进这次自动提交，等于替使用者做了一次语义不明的提交。
    targets = [rel for rel, _vf, _ra in cands[:limit]]
    rollback_sha, dirty_committed = None, False
    if (root / ".git").exists() and targets:
        # ⚠️ 关键：回滚点就是**当前 HEAD** —— 别想着"先 git add 再 commit"，
        #   那一刻文件相对 HEAD 还没有任何变化，提交必然 nothing to commit
        #   （本文件初版就踩了这个坑：写完 842 篇后才发现根本没建回滚点）。
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        rollback_sha = r.stdout.strip() or None
        # 只有当目标文件**本来就有**未提交改动时，才需要先提交它们，
        #   好让回滚点包含使用者的既有改动（否则回滚会连带丢掉它们）。
        d = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--"] + targets,
                           capture_output=True, text=True)
        if d.stdout.strip():
            subprocess.run(["git", "-C", str(root), "add", "--"] + targets, capture_output=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m",
                            f"kb: 双时态迁移前 checkpoint（{len(targets)} 篇）", "--no-verify"],
                           capture_output=True, text=True)
            dirty_committed = True
    written = 0
    for rel, vf, ra in cands[:limit]:
        p = root / rel
        if vf and mig.set_fm(p, "valid_from", vf):
            written += 1
        if ra:
            mig.set_fm(p, "recorded_at", ra)
    if rollback_sha:
        print(f"\n   ✅ 已写入 {written} 篇")
        print(f"   回滚点：{rollback_sha[:10]}"
              + ("（含本次先提交的既有改动）" if dirty_committed else "（改动前的状态本就在 HEAD）"))
        print(f"   回滚命令：git -C {root} checkout {rollback_sha[:10]} -- <要回滚的文件>")
    else:
        print(f"\n   ✅ 已写入 {written} 篇（非 git 库 → 无 git 回滚点，请自行备份）")
    print(f"   提示：valid_to / superseded_by / conflict_with 交由冲突检测按需写入，不在本次批量填充。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="知识契约检查（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--root", default=ROOT_DEFAULT)
    c.add_argument("--json", action="store_true", dest="as_json")
    c.add_argument("--limit", type=int, default=40)
    f = sub.add_parser("fields"); f.add_argument("--root", default=ROOT_DEFAULT)
    bt = sub.add_parser("bitemporal", help="补双时态字段（默认 dry-run）")
    bt.add_argument("--root", default=ROOT_DEFAULT)
    bt.add_argument("--apply", action="store_true")
    bt.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "check":
        return check(root, args.limit, args.as_json)
    if args.cmd == "fields":
        return cmd_fields(root)
    if args.cmd == "bitemporal":
        return cmd_bitemporal(root, args.apply, args.limit)
    return 1


if __name__ == "__main__":
    sys.exit(main())
