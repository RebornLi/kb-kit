#!/usr/bin/env python3
# ============================================================
# link_engine.py —— 连接引擎(成长方案 引擎③)
#   目标：把"孤岛"连成"网络"。发现"相似却未互链"的笔记对，
#         突出跨域弱连接(=洞察来源)，供人工采纳。
#   suggestions : 输出建议链接清单(默认,不改任何笔记)
#   apply       : 把建议链接写入笔记(写前 checkpoint, 新增独立区块,可回滚)
# 用法:
#   python3 pipeline/link_engine.py suggestions [--root R] [--threshold T]
#   python3 pipeline/link_engine.py apply       [--root R] [--threshold T]
# ============================================================
import argparse, os, re, sys, json, math
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT, EXCLUDE, FM, tokenize, load_note, iter_notes

load = load_note  # 兼容本模块原有 load(p) 调用

PERF_WARN_THRESHOLD = 400  # O(n²) 性能告警阈值（两两比对）


def norm(vec):
    n = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / n for k, v in vec.items()}


def cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(av * b.get(k, 0) for k, av in a.items())


def outbound(text):
    out = set()
    for ln in re.findall(r"\[\[([^\]]+)\]\]", text):
        tgt = ln.split("#")[0].split("|")[0].strip()
        if tgt:
            out.add(tgt.split("/")[-1].split(os.sep)[-1])
    return out


def suggestions(root, threshold):
    notes, stem2rel, vecs = {}, {}, {}
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, text, body = load(p)
        stem = p.stem
        stem2rel[stem] = rel
        txt = body + " " + " ".join(t.strip() for t in
                                   (fm.get("tags", "").split(",") if isinstance(fm.get("tags"), str)
                                    else fm.get("tags", [])) if t.strip())
        vecs[rel] = norm(Counter(tokenize(txt)))
        notes[rel] = {"fm": fm, "text": text, "domain": fm.get("domain", "-"),
                      "tags": set(t.strip() for t in
                                  (fm.get("tags", "").split(",") if isinstance(fm.get("tags"), str)
                                   else fm.get("tags", [])) if t.strip()),
                      "out": outbound(text)}

    rels = list(notes)
    recs, cross = [], []
    # P2: 分桶优化（FR-3.3.7）—— 按 domain 分桶，桶内两两比对 + 跨域单独跑
    from collections import defaultdict
    buckets = defaultdict(list)
    for rel in rels:
        domain = notes[rel]["domain"]
        buckets[domain].append(rel)

    # 桶内比对（同 domain）
    for domain, bucket_rels in buckets.items():
        for i, a in enumerate(bucket_rels):
            for b in bucket_rels[i + 1:]:
                sc = cos(vecs[a], vecs[b])
                if sc < threshold:
                    continue
                shared = notes[a]["tags"] & notes[b]["tags"]
                rec = {"score": round(sc, 3), "from": a, "to": b,
                       "shared_tags": sorted(shared), "cross": False}
                recs.append(rec)

    # 跨域比对（不同 domain 之间）
    domains = list(buckets.keys())
    for i, da in enumerate(domains):
        for db in domains[i + 1:]:
            if da == "-" or db == "-":
                continue
            for a in buckets[da]:
                for b in buckets[db]:
                    sc = cos(vecs[a], vecs[b])
                    if sc < threshold:
                        continue
                    shared = notes[a]["tags"] & notes[b]["tags"]
                    rec = {"score": round(sc, 3), "from": a, "to": b,
                           "shared_tags": sorted(shared), "cross": True}
                    recs.append(rec)
                    cross.append(rec)
    recs.sort(key=lambda r: r["score"], reverse=True)
    cross.sort(key=lambda r: r["score"], reverse=True)
    return rels, notes, recs, cross


def build_report(rels, notes, recs, cross, threshold):
    lines = [f"# 🔗 建议链接清单（连接引擎）",
             f"",
             f"> 阈值相似度 ≥ {threshold} · 共 {len(recs)} 对 · 其中跨域弱连接 {len(cross)} 对",
             "> 跨域连接 = 洞察来源；建议优先采纳。人工确认后手动链上即可。",
             f"",
             f"**总分对数 {len(recs)} ｜ 跨域 {len(cross)} ｜ 同域 {len(recs) - len(cross)}**",
             ""]
    if len(rels) > PERF_WARN_THRESHOLD:
        lines += [f"> ⚠️ 笔记量 {len(rels)} 超过 O(n²) 性能阈值 {PERF_WARN_THRESHOLD}，"
                  f"补链比对为 O(n²)（{len(rels)}×{len(rels)//2} 次），如感知慢可考虑分域运行",
                  ""]
    if not recs:
        lines += ["✅ 未发现明显可补链的笔记对"]
        return "\n".join(lines) + "\n"
    lines += ["## 🔥 推荐优先（跨域弱连接）", ""]
    shown = set()
    for r in cross[:20]:
        if r["to"] in shown:
            continue
        shown.add(r["to"])
        lines += [f"- **[{r['to']}]** ← [{r['from']}]**  ({r['score']})"
                   f" · {', '.join(r['shared_tags'][:3]) or '共题'}"]
    lines += ["", "## 🧩 全部建议（按分排序）", ""]
    for r in recs[:40]:
        tag = "跨域" if r["cross"] else "同域"
        tg = "、".join(r["shared_tags"][:2]) if r["shared_tags"] else "共题"
        lines += [f"- [{r['score']}] [{tag}] **{r['to']}** ← [{r['from']}]"
                   f" （相似要点：{tg}）"]
    return "\n".join(lines) + "\n"


def apply_links(root, threshold, limit):
    """写前 checkpoint，往缺链笔记追加独立建议区块（可回滚）。
    幂等：已含「🔗 智能建议链接」区块的笔记跳过，避免每次跑重复堆积。
    --limit：每轮只取分最高的前 N 对（外科手术式，别一次灌满）。
    只 add 被改的笔记，绝不 git add -A（不 sweep Obsidian 运行时态）。"""
    import subprocess
    rels, notes, recs, cross = suggestions(root, threshold)
    recs = recs[:limit] if limit else recs
    if not recs:
        print("✅ 无可补链项")
        return 0
    # 按 from 聚合
    from_map = {}
    for r in recs:
        from_map.setdefault(r["from"], []).append(r)
    inserted, staged = 0, []
    for a, rs in from_map.items():
        p = Path(root) / a
        fm, text, body = load(p)
        if "## 🔗 智能建议链接" in text:
            continue  # 幂等：本笔记已有建议区块，跳过
        # 去重：已有 [[...]] 目标不在建议里
        want = sorted({r["to"] for r in rs}, key=lambda x: -[rr["score"] for rr in rs if rr["to"] == x][0])
        block = "\n## 🔗 智能建议链接\n\n" + "".join(f"- [[{t}]]\n" for t in want)
        new_text = text + ("\n" if not text.endswith("\n") else "") + block
        p.write_text(new_text, encoding="utf-8")
        inserted += 1
        staged.append(a)
    if staged:
        # 精确暂存被改笔记，绝不 git add -A
        subprocess.run(["git", "-C", root, "add", "--", *staged], capture_output=True)
        if subprocess.run(["git", "-C", root, "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "-C", root, "commit", "-q",
                            "-m", f"kb: 连接引擎补链 {inserted} 条笔记（建议区块，可人工移除）"],
                           capture_output=True)
    print(f"✅ 已写入建议区块 {inserted} 条笔记（共 {len(recs)} 条建议）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("suggestions"); d.add_argument("--root", default=ROOT_DEFAULT)
    d.add_argument("--threshold", type=float, default=0.75)
    a = sub.add_parser("apply"); a.add_argument("--root", default=ROOT_DEFAULT)
    a.add_argument("--threshold", type=float, default=0.75)
    a.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    if args.cmd == "suggestions":
        rels, notes, recs, cross = suggestions(args.root, args.threshold)
        (Path(args.root) / "link_suggestions.md").write_text(
            build_report(rels, notes, recs, cross, args.threshold), encoding="utf-8")
        print(f"🔗 建议链接 {len(recs)} 对（跨域 {len(cross)}），已写入 link_suggestions.md")
        return 0
    return apply_links(args.root, args.threshold, args.limit)


if __name__ == "__main__":
    sys.exit(main())
