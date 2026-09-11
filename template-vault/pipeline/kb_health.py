#!/usr/bin/env python3
# ============================================================
# kb_health.py —— 知识库"成长性"度量引擎（成长方案 §8）
#   目标：把"知识库有没有在成长"从玄学变成可度量。
#   六大维度：
#     ① 连接度   平均出链 / 孤儿笔记% / 跨域连接比（孤岛症预警）
#     ② 时效     过期未 review 笔记%（坏血病预警）
#     ③ 新鲜     近30天新增 vs 合并 vs 清理（新陈代谢）
#     ④ 沉淀质量 收件箱积压 vs 已处理（暴食症预警）
#     ⑤ 涌现     MOC/综合类笔记数（有机体是否在"创造"）
#     ⑥ 去重候选 摘要相似度>0.9 的笔记对（合并机会）
#   输出：人读报告（默认）/ --json 出结构化 dict 供 dashboard 消费
# 用法:
#   python3 pipeline/kb_health.py [--root R] [--json]
# ============================================================
import argparse, os, re, sys, json, math
from pathlib import Path
from collections import Counter
from datetime import datetime  # metrics() 第122行 datetime.fromtimestamp 需要模块级

from kb_common import ROOT_DEFAULT, EXCLUDE, FM, tokenize, load_note, iter_notes

load = load_note  # 兼容本模块原有 load(p) 调用

DECAY_DAYS = 30      # L3 级记忆快取窗（memory-management.md）
ARCHIVE_DAYS = 90    # L4 归档阈值
INBOX_DIR = "00-收件箱 Inbox"
INBOX_DIR_SLUGS = ["00收件箱Inbox", "00收件箱"]
PERF_WARN_THRESHOLD = 400  # O(n²) 性能告警阈值（link_engine/kb_health 两两比对）


def outbound_links(text):
    # [[Target]] / [[Target|alias]] / [[Target#seg]]
    return [ln.split("#")[0].split("|")[0].strip()
            for ln in re.findall(r"\[\[([^\]]+)\]\]", text)]


def norm(vec):
    n = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / n for k, v in vec.items()}


def cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(av * b.get(k, 0) for k, av in a.items())


def parse_date(v, root_mtime_default):
    if v and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        from datetime import date
        try:
            from datetime import datetime
            return datetime.fromisoformat(v[:10])
        except ValueError:
            pass
    return None


def metrics(root):
    notes = list(iter_notes(root))
    stem2dom, stem2rel = {}, {}
    info = {}  # rel -> dict(fm,text,body,links,stem,domain,summ)
    now = datetime_now()
    stale, new = [], []

    for p in notes:
        rel = str(p.relative_to(root))
        fm, text, body = load(p)
        stem = p.stem
        stem2dom[stem] = fm.get("domain", "-")
        stem2rel[stem] = rel
        dom = fm.get("domain", "-")
        links = outbound_links(text)
        summ = fm.get("kb_summary", "") or first_line(body)
        info[rel] = {"fm": fm, "text": text, "body": body, "links": links,
                     "stem": stem, "domain": dom, "summ": summ}
        # 时效：按 updated 字段（我清洗时统一写过），回退 mtime
        upd = parse_date(fm.get("updated"), None)
        ref = upd or datetime.fromtimestamp(p.stat().st_mtime)
        age = (now - ref).days
        if 0 < age > ARCHIVE_DAYS and not is_archive(rel):
            stale.append((age, rel))
        created = parse_date(fm.get("created"), None)
        if created and (now - created).days <= 30:
            new.append(rel)

    # 连接度
    total_out = sum(len(i["links"]) for i in info.values())
    orphans = [rel for rel, i in info.items() if not i["links"]]
    cross = 0
    for rel, i in info.items():
        for tgt in i["links"]:
            tgt_stem = tgt.split("/")[-1].split(os.sep)[-1]
            if stem2dom.get(tgt_stem, "-") != i["domain"]:
                cross += 1
    avg_out = total_out / len(info) if info else 0
    cross_ratio = cross / total_out if total_out else 0

    # 涌现：MOC / 索引 / 综合类笔记（带 "toc" tag 或名称含 索引/首页/治理总方案）
    emergence = [rel for rel, i in info.items()
                 if "toc" in tags_of(i["fm"]) or is_moc_name(rel)]

    # 去重候选：按正文向量相似度 > 0.9（用正文而非摘要，避免分块共享首句误判）
    # P2: 分桶优化（FR-3.3.8）—— 按 domain 分桶，桶内两两比对
    vecs, rels = [], []
    for rel, i in info.items():
        body = i["body"]
        if body.strip():
            txt = body + " " + " ".join(tags_of(i["fm"]))
            vecs.append((rel, norm(counter_vec(txt))))
            rels.append(rel)
    dups = []
    # 按 domain 分桶
    from collections import defaultdict
    domain_buckets = defaultdict(list)
    for idx, rel in enumerate(rels):
        domain = info[rel].get("domain", "-")
        domain_buckets[domain].append(idx)
    # 桶内比对
    for domain, indices in domain_buckets.items():
        for ai, a in enumerate(indices):
            for b in indices[ai + 1:]:
                if cos(vecs[a][1], vecs[b][1]) > 0.9:
                    dups.append((round(cos(vecs[a][1], vecs[b][1]), 3), vecs[a][0], vecs[b][0]))
    # 跨域比对（不同 domain 之间）
    domains = list(domain_buckets.keys())
    for di, da in enumerate(domains):
        for db in domains[di + 1:]:
            if da == "-" or db == "-":
                continue
            for a in domain_buckets[da]:
                for b in domain_buckets[db]:
                    if cos(vecs[a][1], vecs[b][1]) > 0.9:
                        dups.append((round(cos(vecs[a][1], vecs[b][1]), 3), vecs[a][0], vecs[b][0]))
    dups.sort(reverse=True)

    # 沉淀质量：收件箱积压
    backlog = [rel for rel in info if in_inbox(rel)]

    orphan_n, stale_n = len(orphans), len(stale)
    orphan_pct = round(100 * orphan_n / len(info), 1) if info else 0
    stale_pct = round(100 * stale_n / len(info), 1) if info else 0
    score, label, alerts = score_and_alerts(avg_out, orphan_n, orphan_pct,
                                            stale_n, stale_pct, backlog, dups, emergence,
                                            total=len(info))

    return {
        "total": len(info),
        "label": label,
        "connectivity": {
            "avg_outbound_links": round(avg_out, 2),
            "orphan_notes": len(orphans),
            "orphan_pct": round(100 * len(orphans) / len(info), 1) if info else 0,
            "cross_domain_ratio": round(cross_ratio, 2),
            "orphan_sample": orphans[:6],
        },
        "staleness": {
            "stale_notes": len(stale),
            "stale_pct": round(100 * len(stale) / len(info), 1) if info else 0,
            "stale_sample": sorted(stale, reverse=True)[:6],
        },
        "freshness": {
            "new_30d": len(new),
            "new_pct": round(100 * len(new) / len(info), 1) if info else 0,
        },
        "intake": {"backlog": len(backlog), "backlog_sample": backlog[:6]},
        "emergence": {
            "moc_count": len(emergence),
            "moc_sample": emergence[:6],
        },
        "dedup": {"candidates": dups[:10]},
        "score": score,
        "alerts": alerts,
    }


def datetime_now():
    from datetime import datetime
    return datetime.now()


def first_line(body):
    for ln in body.splitlines():
        ln = ln.strip(" \t>*#")
        if ln and not ln.startswith("`"):
            return ln[:160]
    return ""


def tags_of(fm):
    t = fm.get("tags", "")
    if isinstance(t, str):
        return [x.strip() for x in t.replace("[", "]").split(",") if x.strip()]
    return list(t) if t else []


def counter_vec(s):
    c = Counter(tokenize(s))
    return c


def is_archive(rel):
    return "归档" in rel or "trash" in rel.lower() or rel.split(os.sep)[0].startswith("90-")


def in_inbox(rel):
    top = rel.split(os.sep)[0]
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", top)
    return slug in (INBOX_DIR_SLUGS[1], "00收件箱") or INBOX_DIR in rel


def is_moc_name(rel):
    # 名称即 MOC/索引/综合页：无需读文件（调用方已带 info）
    return any(k in Path(rel).stem for k in ["首页", "索引", "治理总方案"])


def score_and_alerts(avg_out, orphan_n, orphan_pct, stale_n, stale_pct,
                     backlog, dups, emergence, total=0):
    alerts = []
    score = 100
    # O(n²) 性能告警：笔记量接近阈值时提示 link_engine/kb_health 可能变慢
    if total > PERF_WARN_THRESHOLD:
        alerts.append(f"⚡ 笔记量 {total} 已超过 O(n²) 性能阈值 {PERF_WARN_THRESHOLD}，"
                      f"link_engine/health 去重比对可能变慢（当前暂缓优化，详见审计 B-1）")
    # 连接度扣分
    if orphan_pct > 5:
        alerts.append(f"🔗 {orphan_n} 条孤儿笔记（无出链），建议补链或合并")
    if avg_out < 1.5:
        score -= 20
        alerts.append(f"🔗 平均出链仅 {avg_out:.2f}，孤岛症预警（目标 ≥2/条）")
    # 时效扣分
    if stale_pct > 10:
        score -= min(30, int(stale_pct))
        alerts.append(f"⏰ {stale_n} 条过期笔记未 review（坏血病预警）")
    # 收件箱扣分
    if backlog:
        alerts.append(f"📥 收件箱积压 {len(backlog)} 条待 triage")
        score -= min(15, len(backlog))
    # 去重
    if dups:
        alerts.append(f"🧩 {len(dups)} 对摘要高度相似，建议合并")
        score -= min(10, len(dups))
    # 涌现加分
    if emergence:
        score += min(10, len(emergence))
    score = max(0, min(100, int(score)))
    label = ("🟢 成长性良好" if score >= 80
             else "🟡 成长需推动" if score >= 55
             else "🔴 需重点治理")
    return score, label, alerts


def report(m):
    c, s, f, k, e = (m["connectivity"], m["staleness"], m["freshness"],
                     m["intake"], m["emergence"])
    out = [f"# 🌱 知识库成长性报告",
           "",
           f"> 总分 **{m['score']}** — {m['label']}  ·  笔记总数 {m['total']}",
           "",
           "## 📊 六维度量",
           "",
           "| 维度 | 指标 | 状态 |",
           "|------|------|------|",
           f"| 🔗 连接度 | 平均出链 {c['avg_outbound_links']} / 孤儿 {c['orphan_notes']} ({c['orphan_pct']}%) | "
           f"{'🟢' if c['avg_outbound_links']>=2 and c['orphan_pct']<5 else '🟡'} |",
           f"| ⏰ 时效 | 过期 {s['stale_notes']} ({s['stale_pct']}%) | "
           f"{'🟢' if s['stale_pct']<10 else '🔴'} |",
           f"| 🌱 新鲜 | 近30天新增 {f['new_30d']} ({f['new_pct']}%) | 🟢 |",
           f"| 📥 沉淀 | 收件箱积压 {k['backlog']} 条 | "
           f"{'🟢' if not k['backlog'] else '🟡'} |",
           f"| 💡 涌现 | MOC/综合笔记 {e['moc_count']} 条 | "
           f"{'🟢' if e['moc_count'] else '🟡'} |",
           f"| 🧩 去重 | 高相似 {len(m['dedup']['candidates'])} 对 | "
           f"{'🟢' if not m['dedup']['candidates'] else '🟡'} |",
           "",
           "## ⚠️ 治理建议",
           ""]
    for a in m["alerts"]:
        out.append(f"- {a}")
    if c["orphan_sample"]:
        out.append("\n### 孤儿笔记样例（可补链/归档）")
        out += [f"- `{r}`" for r in c["orphan_sample"]]
    if s["stale_sample"]:
        out.append("\n### 过期待 review 样例")
        out += [f"- `{r}` ({age}天前)" for age, r in s["stale_sample"]]
    if k["backlog_sample"]:
        out.append("\n### 收件箱积压样例")
        out += [f"- `{r}`" for r in k["backlog_sample"]]
    if m["dedup"]["candidates"]:
        out.append("\n### 去重合并候选")
        out += [f"- [{sc}] `{a}` ↔ `{b}`" for sc, a, b in m["dedup"]["candidates"]]
    return "\n".join(out) + "\n"


# ── P2: 制度知识过期检测（FR-3.3.14）──────────────────────────
def check_policy_stale(root):
    """检测 policy 类型笔记是否过期。

    policy 笔记带 review_cycle（天），若 last_reviewed + review_cycle < now → 过期。
    过期的笔记标 review_needed: true。

    Returns:
        list: [(rel, days_overdue, review_cycle), ...] 过期笔记列表
    """
    import datetime
    stale = []
    today = datetime.date.today()
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        try:
            fm, text, body = load_note(p)
        except (OSError, UnicodeDecodeError):
            continue
        ct = fm.get("content_type", "")
        if ct != "policy":
            continue
        review_cycle = fm.get("review_cycle")
        if not review_cycle:
            continue
        try:
            cycle_days = int(review_cycle)
        except (ValueError, TypeError):
            continue
        last_reviewed = fm.get("last_reviewed", "")
        if not last_reviewed:
            # 从未复核 → 直接过期
            stale.append((rel, cycle_days, cycle_days))
            continue
        try:
            last_date = datetime.date.fromisoformat(str(last_reviewed)[:10])
            overdue = (today - last_date).days - cycle_days
            if overdue > 0:
                stale.append((rel, overdue, cycle_days))
        except (ValueError, TypeError):
            continue
    return stale


def mark_policy_stale(root):
    """标记过期的 policy 笔记（写 review_needed: true）。"""
    stale = check_policy_stale(root)
    if not stale:
        return 0
    marked = 0
    for rel, _, _ in stale:
        p = Path(root) / rel
        try:
            text = p.read_text(encoding="utf-8")
            if "review_needed: true" in text:
                continue  # 已标记
            # 在 frontmatter 中添加 review_needed: true
            new_text = text.replace("---\n", "---\nreview_needed: true\n", 1)
            p.write_text(new_text, encoding="utf-8")
            marked += 1
        except (OSError, UnicodeDecodeError):
            continue
    return marked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    m = metrics(args.root)
    if args.json:
        # label 含 emoji，单独吐给 dashboard
        print(json.dumps(m, ensure_ascii=False))
    else:
        print(report(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
