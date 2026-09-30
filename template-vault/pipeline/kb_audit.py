#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_audit.py — 知识库审计与"确认后修复"（P5）。

对照 wiki-lint 的检查项，补齐 kb healthcheck **没有**的三类结构性问题：

  1. **溯源漂移** —— 正典里多少断言是"推断"而非"抽取"；hub 页面上漂移危害最大
  2. **标签簇内聚** —— 共享同一标签的页之间互链率；低于阈值说明"同类知识各说各话"
  3. **索引一致性** —— `_MOC.md` / `_INDEX.md` 的清单与实际文件对账
  （外加：孤儿页 / 死链 / 陈旧 / 缺 summary / 契约越界，复用已有能力做统一出口）

纪律（照搬 wiki-lint 的安全协议）：
  · `scan` 只读，**绝不写库**
  · `fix` 必须先 dry-run，打印逐项清单，人工 `--confirm` 后才落盘
  · 修复动作只做"安全的那些"：补 frontmatter 字段、修死链、加孤儿页交叉引用、标矛盾 callout
  · **绝不自动合并/删除页面**（那要人工判断）

用法:
  python3 pipeline/kb_audit.py scan  [--root R] [--json] [--limit N]
  python3 pipeline/kb_audit.py fix   [--root R] [--confirm] [--only <检查名>]
  python3 pipeline/kb_audit.py log   [--root R]        # 看审计历史
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import (ROOT_DEFAULT, iter_notes, load_note_full as load, tokenize,
                       is_raw_path, is_generated_report, kb_layer_of, is_index_stub)

STALE_DAYS = 120
COHESION_MIN = 0.15          # 标签簇内聚下限
COHESION_MIN_N = 5           # 簇规模下限（太小不算）
INFERRED_MAX = 0.20          # hub 页推断占比上限
AUDIT_DIR = Path("70-知识治理 Governance") / "_audit"


# ── 采样：一次遍历收集所有需要的东西 ────────────────────────
def all_stems(root: Path) -> set:
    """全库所有 md 的 stem（**含 raw/ 与 raw/_curated/**）。

    为什么必须含 raw：正典与 MOC 里大量链接指向 `raw/_curated/<原路径>`（保号原文），
    那**是存在的文件**；只统计知识层会把它们误报成"死链"（实测误报 112 条）。
    """
    out = set()
    for p in root.rglob("*.md"):
        if ".git" in p.parts:
            continue
        out.add(p.stem)
    return out


def sample(root: Path) -> Dict[str, Any]:
    notes: Dict[str, Dict[str, Any]] = {}
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_raw_path(rel):
            continue
        # 注意：**不排除** is_generated_report（_MOC/_INDEX/_graph）。
        #   它们自身不该被改，但它们是**入链的来源**；排除它们＝丢掉图谱的边，
        #   会把几百篇正常笔记误判成"孤儿页"（实测误报 683 篇）。
        gen = is_generated_report(rel)
        fm, text, _b, body = load(p)
        heads = [l.strip() for l in body.splitlines() if l.strip().startswith("-")]
        inferred = sum(1 for l in heads if "^[推断]" in l)
        links = set(re.findall(r"\[\[([^\]|#]+)", body))
        notes[rel] = {
            "rel": rel, "fm": fm, "text": text, "body": body,
            "layer": kb_layer_of(fm, rel), "tags": _tags(fm),
            "links": {l.strip() for l in links if l.strip()},
            "inferred": inferred, "bullets": len(heads),
            "updated": str(fm.get("updated") or fm.get("created") or "")[:10],
            "summary": str(fm.get("kb_summary") or ""),
            "generated": gen,
        }
    return notes


def _tags(fm: Dict[str, Any]) -> List[str]:
    t = fm.get("tags")
    if isinstance(t, list):
        return [str(x).strip() for x in t if str(x).strip()]
    if t:
        return [x.strip() for x in re.split(r"[,\s]+", str(t)) if x.strip()]
    return []


def _stem_index(notes: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """页面 stem → rel（用于解析 wikilink）。"""
    idx = {}
    for rel in notes:
        idx.setdefault(Path(rel).stem, rel)
        idx.setdefault(rel, rel)
        idx.setdefault(rel[:-3] if rel.endswith(".md") else rel, rel)
    return idx


# ── 检查 1：溯源漂移 ────────────────────────────────────────
def check_provenance(notes) -> List[Dict[str, Any]]:
    out = []
    for rel, n in notes.items():
        if n["generated"] or n["layer"] != "canon" or not n["bullets"]:
            continue
        ratio = n["inferred"] / n["bullets"]
        if ratio > 0 and (ratio > INFERRED_MAX or n["inferred"] >= 3):
            out.append({"rel": rel, "inferred": n["inferred"], "bullets": n["bullets"],
                        "ratio": round(ratio, 3),
                        "why": f"{n['inferred']}/{n['bullets']} 条标为推断（{ratio:.0%}）"})
    out.sort(key=lambda x: -x["ratio"])
    return out


# ── 检查 2：标签簇内聚 ──────────────────────────────────────
def check_tag_cohesion(notes) -> List[Dict[str, Any]]:
    by_tag: Dict[str, List[str]] = defaultdict(list)
    for rel, n in notes.items():
        if n["generated"] or n["layer"] not in ("canon", "page"):
            continue
        for t in n["tags"]:
            if t.startswith("chunk") or t in ("toc", "daily"):
                continue
            by_tag[t].append(rel)
    out = []
    stems = {Path(r).stem for r in notes}
    for tag, rels in by_tag.items():
        if len(rels) < COHESION_MIN_N:
            continue
        relset = set(rels)
        links = 0
        for r in rels:
            for l in notes[r]["links"]:
                tgt = l if l in relset else next((x for x in relset if Path(x).stem == Path(l).stem), None)
                if tgt and tgt != r:
                    links += 1
        possible = len(rels) * (len(rels) - 1)
        coh = links / possible if possible else 0.0
        if coh < COHESION_MIN:
            out.append({"tag": tag, "n": len(rels), "cohesion": round(coh, 3),
                        "links": links, "why": f"#{tag} 有 {len(rels)} 篇但互链率仅 {coh:.2f}"})
    out.sort(key=lambda x: x["cohesion"])
    return out


# ── 检查 3：索引一致性 ──────────────────────────────────────
def check_index_consistency(root: Path, notes) -> List[Dict[str, Any]]:
    out = []
    exist = all_stems(root)
    for name in ("_MOC.md", "_INDEX.md", "🏠-知识库首页.md"):
        for p in root.rglob(name):
            rel = str(p.relative_to(root))
            if is_raw_path(rel):
                continue
            listed = set(re.findall(r"\[\[([^\]|#]+)", p.read_text(encoding="utf-8")))
            stems = {Path(r).stem: r for r in notes}
            missing = [l for l in listed
                       if Path(l).stem not in stems and Path(l).stem not in exist
                       and not (root / (l if l.endswith(".md") else l + ".md")).exists()]
            if missing:
                out.append({"file": rel, "missing": missing[:8], "n_missing": len(missing),
                            "why": f"{rel} 里 {len(missing)} 条链接指向不存在的页面"})
    return out


# ── 检查 4：孤儿页 / 死链 / 陈旧 / 缺 summary ────────────────
def check_structure(root: Path, notes) -> Dict[str, List[Dict[str, Any]]]:
    stems = {Path(r).stem: r for r in notes}
    exist = all_stems(root)
    inbound: Counter = Counter()
    dead, orphans = [], []
    PLACEHOLDER = {"关联笔记", "相关笔记", "相关", "关联", "见", "X", "名称", "链接",
                   "wikilink", "path", "link", "note.md", "页面名"}
    for rel, n in notes.items():
        for l in n["links"]:
            if l in PLACEHOLDER or l.strip() in PLACEHOLDER:
                continue
            key = Path(l).stem
            if key in stems:
                inbound[stems[key]] += 1
            elif key in exist or (root / (l if l.endswith(".md") else l + ".md")).exists():
                pass                      # 指向证据层/归档：存在，不是死链
            else:
                dead.append({"rel": rel, "target": l})
    PLACEHOLDER = {"关联笔记", "相关笔记", "相关", "关联", "见", "X", "名称", "链接",
                   "wikilink", "path", "link", "note.md", "页面名"}
    for rel, n in notes.items():
        if n["generated"] or n["layer"] not in ("canon", "page"):
            continue                       # 索引文件自身不算孤儿候选
        if Path(rel).name.startswith(("_", "🏠", "📖", "01-", "如何使用")):
            continue
        if "模板" in rel:
            continue
        if inbound.get(rel, 0) == 0:
            orphans.append({"rel": rel, "why": "没有任何入链"})
    stale, no_summary = [], []
    today = date.today()
    for rel, n in notes.items():
        if n["generated"] or n["layer"] != "canon":
            continue
        if not n["summary"]:
            no_summary.append({"rel": rel, "why": "缺 kb_summary"})
        try:
            age = (today - date.fromisoformat(n["updated"])).days
            if age > STALE_DAYS:
                stale.append({"rel": rel, "age": age, "why": f"{age} 天未更新"})
        except ValueError:
            pass
    return {"deadlinks": dead[:60], "orphans": orphans[:60],
            "stale": sorted(stale, key=lambda x: -x["age"])[:60], "no_summary": no_summary[:60],
            "counts": {"deadlinks": len(dead), "orphans": len(orphans),
                       "stale": len(stale), "no_summary": len(no_summary)}}


# ── scan ────────────────────────────────────────────────────
def cmd_scan(root: Path, as_json: bool, limit: int) -> int:
    notes = sample(root)
    prov = check_provenance(notes)
    coh = check_tag_cohesion(notes)
    idxc = check_index_consistency(root, notes)
    st = check_structure(root, notes)
    res = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "notes": len(notes), "provenance_drift": prov, "tag_cohesion": coh,
           "index_consistency": idxc, "structure": st,
           "counts": {"provenance_drift": len(prov), "tag_cohesion": len(coh),
                      "index_consistency": len(idxc), **st["counts"]}}
    out_dir = root / AUDIT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"audit-{date.today().isoformat()}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2)); return 0
    c = res["counts"]
    print(f"🔍 知识库审计（{res['notes']} 篇 · 只读）")
    print(f"   结构：死链 {c['deadlinks']} · 孤儿页 {c['orphans']} · 陈旧 {c['stale']} · 缺 summary {c['no_summary']}")
    print(f"   漂移：溯源漂移 {c['provenance_drift']} 篇（正典里有推断断言）")
    print(f"   内聚：标签簇偏散 {c['tag_cohesion']} 个")
    print(f"   一致性：索引清单对不上 {c['index_consistency']} 处")
    if prov:
        print("\n   ▪ 溯源漂移（前 5）")
        for r in prov[:5]:
            print(f"      {r['ratio']:.0%}  {r['rel'][:58]}  ({r['why']})")
    if coh:
        print("\n   ▪ 标签簇内聚偏低（前 5）")
        for r in coh[:5]:
            print(f"      {r['cohesion']:.2f}  {r['why']}")
    if idxc:
        print("\n   ▪ 索引不一致（前 3）")
        for r in idxc[:3]:
            print(f"      {r['why']}  例：{r['missing'][:3]}")
    if st["deadlinks"]:
        print(f"\n   ▪ 死链（前 3）：{[(d['rel'][-24:], d['target'][:20]) for d in st['deadlinks'][:3]]}")
    print(f"\n   报告已写 {AUDIT_DIR}/audit-{date.today().isoformat()}.json")
    print("   修复：kb audit fix（先 dry-run 看清单，再 --confirm 落盘）")
    return 0


# ── fix：只做安全动作，且必须 --confirm ─────────────────────
def _link_block(parent: str, siblings: List[str], me: str) -> str:
    """为孤立笔记生成"结构补链"区块（父文档 + 同族兄弟）。"""
    lines = ["", "<!-- 结构补链（kb audit 生成，可人工移除） -->"]
    if parent:
        lines.append(f"[[{Path(parent).stem}]] ← 所属文档")
    sibs = [x for x in siblings if x != me][:8]
    if sibs:
        lines.append("同族： " + " · ".join(f"[[{Path(x).stem}]]" for x in sibs))
    return "\n".join(lines) + "\n"


def _safe_fix_actions_orphans(root: Path, notes) -> List[Dict[str, Any]]:
    """为**分块族内**的孤立笔记补结构链接（父文档 + 同族兄弟）。

    严格判据（第一版踩过坑，必须记住）：
      · **只处理真正的分块族**：要求 `parent_of(rel)` 能解析出父文档（有 `chunk_of`
        或文件名 `-pN` 链）。非分块笔记**不补**——按"文件名相同"去连会把
        `README-p2.md` 连到 `README.md`、把 `测试-p2.md` 连到 `测试.md` 这类无关文档，
        第一版就是这么误伤 48 个文件的（还写出了 `同族： [[README]] · [[README]]` 的自链）。
      · **排除自链与重复**：父/兄弟的 stem 等于自己的一律跳过。
    """
    from kb_common import parent_of, root_of
    st = check_structure(root, notes)
    actions = []
    for o in st["orphans"]:
        rel = o["rel"]
        n = notes.get(rel)
        if not n or n.get("generated"):
            continue
        fm = n["fm"]
        par = parent_of(rel, fm, root)
        if not par:
            continue                       # 不是分块族 → 不补链（避免误伤）
        anchor = root_of(rel, fm, root) or par
        me = Path(rel).stem
        sibs = []
        for r, m in notes.items():
            if r == rel or m.get("generated"):
                continue
            if parent_of(r, m["fm"], root) != par:
                continue                   # 必须同父
            st_r = Path(r).stem
            if st_r == me or st_r == Path(anchor).stem:
                continue                   # 排除自链与父链
            sibs.append(r)
        sibs = sorted(set(sibs))[:8]
        if not sibs and Path(anchor).stem == me:
            continue
        actions.append({"kind": "link_orphan", "rel": rel, "parent": anchor,
                        "parent_ref": par, "siblings": sibs,
                        "action": f"补结构链接（父 {Path(anchor).stem}"
                                  + (f" + {len(sibs)} 同族" if sibs else "") + "）",
                        "safe": True})
    return actions


def _safe_fix_actions(root: Path, notes) -> List[Dict[str, Any]]:
    """列出"可安全自动修复"的动作（不含合并/删除）。"""
    st = check_structure(root, notes)
    idx = _stem_index(notes)
    actions = []
    # 1) 死链：优先按"路径后缀唯一匹配"改写成真实路径（修 _INDEX 的前缀不一致）
    allrel = [str(q.relative_to(root)) for q in root.rglob("*.md") if ".git" not in q.parts]
    for d in st["deadlinks"]:
        tgt = d["target"]
        # a) 后缀匹配：`数据库/SQL优化` → `20-技术 Technology/数据库/SQL优化.md`
        cands = [r for r in allrel
                 if r.endswith(tgt) or r.endswith(tgt + ".md")
                 or Path(r).stem == Path(tgt).stem]
        if len(cands) == 1:
            # 同理：普通笔记里的死链也按"目标 stem"改写（Obsidian 双链解析 stem 即可）
            actions.append({"kind": "deadlink", "rel": d["rel"], "target": tgt,
                            "action": f"改写为 [[{Path(cands[0]).stem}]]", "safe": True})
            continue
        # b) 唯一 stem 包含匹配
        stem = Path(tgt).stem
        loose = [r for r in allrel if stem and (stem in r or Path(r).stem in stem)]
        if len(loose) == 1:
            actions.append({"kind": "deadlink", "rel": d["rel"], "target": tgt,
                            "action": f"改写为 [[{Path(loose[0]).stem}]]", "safe": True})
        else:
            actions.append({"kind": "deadlink_annotate", "rel": d["rel"], "target": tgt,
                            "action": "转为纯文本 + 注释", "safe": True})
    # 1b) 索引文件（_MOC/_INDEX）里的悬空链接：同样按后缀唯一匹配改写
    for ic in check_index_consistency(root, notes):
        for l in ic["missing"]:
            cands = [r for r in allrel
                     if r.endswith(l) or r.endswith(l + ".md") or Path(r).stem == Path(l).stem]
            if len(cands) == 1:
                # 索引文件通篇用 stem 链接（[[名称]]），保持一致，不改成全路径
                actions.append({"kind": "index_link", "rel": ic["file"], "target": l,
                                "action": f"改写为 [[{Path(cands[0]).stem}]]", "safe": True})
            else:
                actions.append({"kind": "index_link_drop", "rel": ic["file"], "target": l,
                                "action": "转为纯文本（无唯一匹配）", "safe": True})

    # 1c) 知识层孤立笔记：补"所属文档 + 同族"结构链接
    actions.extend(_safe_fix_actions_orphans(root, notes))

    # 2) 缺 summary 的正典：用首条结论补
    for r in st["no_summary"]:
        n = notes[r["rel"]]
        first = next((l.strip("- ").strip() for l in n["body"].splitlines()
                      if l.strip().startswith("-")), "")
        if first:
            actions.append({"kind": "add_summary", "rel": r["rel"],
                            "action": f"kb_summary ← {first[:60]}", "safe": True})
    return actions


def cmd_fix(root: Path, confirm: bool, only: Optional[str]) -> int:
    notes = sample(root)
    actions = _safe_fix_actions(root, notes)
    if only:
        actions = [a for a in actions if a["kind"] == only]
    print(f"🛠 可安全自动修复的动作：{len(actions)} 条")
    for a in actions[:20]:
        print(f"   [{a['kind']}] {a['rel'][:54]}  → {a['action']}")
    if len(actions) > 20:
        print(f"   … 其余 {len(actions) - 20} 条")
    if not actions:
        print("   （无需修复）"); return 0
    if not confirm:
        print("\n🔍 dry-run：未写任何文件。确认后：kb audit fix --confirm")
        print("   注意：**合并/删除页面**不在自动修复范围（需人工判断）")
        return 0
    import subprocess
    touched, done = [], 0
    by_rel: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for a in actions:
        by_rel[a["rel"]].append(a)
    for rel, acts in by_rel.items():
        p = root / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        new = text
        for a in acts:
            if a["kind"] == "add_summary":
                m = re.search(r"^kb_summary:.*$", new, re.M)
                val = a["action"].split("← ", 1)[-1].replace('"', "'")
                if m:
                    new = re.sub(r"^kb_summary:.*$", f"kb_summary: {val}", new, count=1, flags=re.M)
                else:
                    new = re.sub(r"^---\s*$", f"---\nkb_summary: {val}", new, count=1, flags=re.M)
            elif a["kind"] == "link_orphan":
                if "结构补链" not in new:
                    new = new.rstrip() + "\n" + _link_block(a.get("parent", ""),
                                                              a.get("siblings") or [], a["rel"])
            elif a["kind"] in ("index_link", "index_link_drop"):
                if a["action"].startswith("改写为"):
                    new = new.replace(f"[[{a['target']}]]", f"[[{a['action'][5:-2]}]]")
                else:
                    new = new.replace(f"[[{a['target']}]]",
                                      f"{a['target']} <!-- 悬空链接：无唯一匹配 -->")
            elif a["kind"] in ("deadlink", "deadlink_annotate"):
                if a["action"].startswith("改写为"):
                    new = new.replace(f"[[{a['target']}]]", f"[[{a['action'][5:-2]}]]")
                else:
                    new = new.replace(f"[[{a['target']}]]",
                                      f"{a['target']} <!-- broken link: no match -->")
        if new != text:
            p.write_text(new, encoding="utf-8")
            touched.append(rel); done += 1
    if touched:
        subprocess.run(["git", "-C", str(root), "add", "--", *touched], capture_output=True)
    # 审计日志（追加）
    logp = root / AUDIT_DIR / "log.md"
    logp.parent.mkdir(parents=True, exist_ok=True)
    with logp.open("a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().isoformat(timespec='seconds')} FIX actions={len(actions)} "
                f"files={done} kinds={sorted({a['kind'] for a in actions})}\n")
    print(f"\n✅ 已修复 {done} 个文件（{len(actions)} 条动作）")
    return 0


def cmd_log(root: Path) -> int:
    logp = root / AUDIT_DIR / "log.md"
    runs = sorted((root / AUDIT_DIR).glob("audit-*.json")) if (root / AUDIT_DIR).exists() else []
    print(f"🗂 审计历史：{len(runs)} 次扫描")
    for f in runs[-8:]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            print(f"   {f.name}  {json.dumps(d.get('counts', {}), ensure_ascii=False)}")
        except (OSError, json.JSONDecodeError):
            continue
    if logp.exists():
        print("\n🛠 修复日志：")
        for line in logp.read_text(encoding="utf-8").splitlines()[-8:]:
            print("   " + line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库审计与确认后修复（P5）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--root", default=ROOT_DEFAULT)
    s.add_argument("--json", action="store_true", dest="as_json"); s.add_argument("--limit", type=int, default=20)
    f = sub.add_parser("fix"); f.add_argument("--root", default=ROOT_DEFAULT)
    f.add_argument("--confirm", action="store_true"); f.add_argument("--only", default=None)
    l = sub.add_parser("log"); l.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "scan":
        return cmd_scan(root, args.as_json, args.limit)
    if args.cmd == "fix":
        return cmd_fix(root, args.confirm, args.only)
    if args.cmd == "log":
        return cmd_log(root)
    return 1


if __name__ == "__main__":
    sys.exit(main())
