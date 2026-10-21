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
from typing import Any, Dict, List, Optional, Tuple, Union

from kb_io import ROOT_DEFAULT, EXCLUDE, FM, load_note, iter_notes
from kb_metautil import parse_date as _kb_parse_date
from kb_text import tokenize, norm, cos, build_inverted_index, candidate_pairs
from kb_constants import ARCHIVE_DAYS, DECAY_DAYS, PERF_WARN_THRESHOLD

load = load_note  # 兼容本模块原有 load(p) 调用

INBOX_DIR = "00-收件箱 Inbox"
INBOX_DIR_SLUGS = ["00收件箱Inbox", "00收件箱"]


def outbound_links(text: str) -> List[str]:
    # [[Target]] / [[Target|alias]] / [[Target#seg]]
    return [ln.split("#")[0].split("|")[0].strip()
            for ln in re.findall(r"\[\[([^\]]+)\]\]", text)]


def parse_date(v: Optional[str], root_mtime_default: Any = None) -> Optional[datetime]:
    # 保留双参数签名以兼容现有调用方；内部委托 kb_common.parse_date。
    # root_mtime_default 参数未实际使用（原实现也未使用），仅为向后兼容保留。
    return _kb_parse_date(v)


def metrics(root: Union[str, Path]) -> Dict[str, Any]:
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
    # 倒排索引预过滤：仅对共享至少 1 个 token 的笔记对计算余弦相似度。
    # 共享 token 为空 → 余弦必为 0 → 低于 0.9 阈值可安全跳过。
    vecs_dict = {rel: v for rel, v in vecs}
    inv_index = build_inverted_index(vecs_dict)
    rel2idx = {rel: idx for idx, rel in enumerate(rels)}
    # 按 domain 分桶
    from collections import defaultdict
    domain_buckets = defaultdict(list)
    for idx, rel in enumerate(rels):
        domain = info[rel].get("domain", "-")
        domain_buckets[domain].append(idx)
    # 桶内比对 —— 使用 candidate_pairs 限制在桶内索引范围
    for domain, indices in domain_buckets.items():
        bucket_rels = [rels[idx] for idx in indices]
        for a_rel, b_rel in candidate_pairs(inv_index, bucket_rels):
            a = rel2idx[a_rel]
            b = rel2idx[b_rel]
            sc = cos(vecs[a][1], vecs[b][1])
            if sc > 0.9:
                dups.append((round(sc, 3), vecs[a][0], vecs[b][0]))
    # 跨域比对（不同 domain 之间）—— 合并两个桶生成候选对，再过滤跨域
    domains = list(domain_buckets.keys())
    for di, da in enumerate(domains):
        for db in domains[di + 1:]:
            if da == "-" or db == "-":
                continue
            a_rels = [rels[idx] for idx in domain_buckets[da]]
            b_rels = [rels[idx] for idx in domain_buckets[db]]
            combined = a_rels + b_rels
            dom_of = {r: da for r in a_rels}
            dom_of.update({r: db for r in b_rels})
            for a_rel, b_rel in candidate_pairs(inv_index, combined):
                if dom_of[a_rel] == dom_of[b_rel]:
                    continue
                a = rel2idx[a_rel]
                b = rel2idx[b_rel]
                sc = cos(vecs[a][1], vecs[b][1])
                if sc > 0.9:
                    dups.append((round(sc, 3), vecs[a][0], vecs[b][0]))
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


def datetime_now() -> datetime:
    from datetime import datetime
    return datetime.now()


def first_line(body: str) -> str:
    for ln in body.splitlines():
        ln = ln.strip(" \t>*#")
        if ln and not ln.startswith("`"):
            return ln[:160]
    return ""


def tags_of(fm: Dict[str, Any]) -> List[str]:
    t = fm.get("tags", "")
    if isinstance(t, str):
        return [x.strip() for x in t.replace("[", "]").split(",") if x.strip()]
    return list(t) if t else []


def counter_vec(s: str) -> Counter:
    c = Counter(tokenize(s))
    return c


def is_archive(rel: str) -> bool:
    return "归档" in rel or "trash" in rel.lower() or rel.split(os.sep)[0].startswith("90-")


def in_inbox(rel: str) -> bool:
    top = rel.split(os.sep)[0]
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", top)
    return slug in (INBOX_DIR_SLUGS[1], "00收件箱") or INBOX_DIR in rel


def is_moc_name(rel: str) -> bool:
    # 名称即 MOC/索引/综合页：无需读文件（调用方已带 info）
    return any(k in Path(rel).stem for k in ["首页", "索引", "治理总方案"])


def score_and_alerts(avg_out: float, orphan_n: int, orphan_pct: float,
                     stale_n: int, stale_pct: float,
                     backlog: List[str], dups: List[Tuple[float, str, str]],
                     emergence: List[str], total: int = 0) -> Tuple[int, str, List[str]]:
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


def report(m: Dict[str, Any]) -> str:
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
def check_policy_stale(root: Union[str, Path]) -> List[Tuple[str, int, int]]:
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


def mark_policy_stale(root: Union[str, Path]) -> int:
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


def kpi(root: Union[str, Path]) -> Dict[str, Any]:
    """**抗 Goodhart 的 5 个指标** —— 推荐的仪表盘集合。

    为什么另开一个而不是用 `metrics()`：后者算的多是**易刷量**（笔记数、链接总数、
    孤立数、跨域边数）—— "加动作就能变好"，只适合诊断，当 KPI 会被 Goodhart 化
    （把指标做上去，而不是把事情做好）。本函数只算**难刷**的：

      ① two_hop_reach   从任一入口页（MOC/首页/索引）**≤2 跳**可达的笔记占比
                        —— 直接测"可检索性"，比"链接总数"难刷得多。
      ② first_hit_rate  历史问题里 **top-3 一次命中**的比例（黄金集；无则 N/A）
                        —— 直接测"找得到"，是"第二大脑"的核心效用。
      ③ calibration     recall 的**预测校准**（Brier）；越准越难作弊。无复习数据则 N/A。
      ④ staleness       高 importance(≥0.7) 且超期未复核的占比
                        —— 防"重要知识默默腐烂"。
      ⑤ reuse           被**其他笔记引用**的比例（入链覆盖）
                        —— 与"产出物里真的用到了"最接近的可算代理。

    反指标（必须一起看）：每次复习后发生编辑/新增链接的比例；若复习很多次而笔记零变更，
    说明在刷量而不是在用。
    """
    from collections import deque
    notes = list(iter_notes(root))
    info, stem2rel, inbound = {}, {}, {}
    now = datetime_now()
    for p in notes:
        rel = str(p.relative_to(root))
        fm, text, body = load(p)
        rels_out = outbound_links(text)
        info[rel] = {"fm": fm, "links": rels_out, "stem": p.stem}
        stem2rel[p.stem] = rel
        for tgt in rels_out:
            inbound.setdefault(tgt.split("/")[-1].split(os.sep)[-1], set()).add(rel)
    # 反向邻接（用于可达性 BFS）：谁指向了 rel
    pred = {rel: set() for rel in info}
    for rel, i in info.items():
        for tgt in i["links"]:
            t = tgt.split("/")[-1].split(os.sep)[-1]
            if t in stem2rel:
                pred.setdefault(stem2rel[t], set()).add(rel)

    # ① 两跳可达：从入口页（MOC/首页/索引）出发，沿**出链**走 ≤2 跳
    entries = [rel for rel in info if is_moc_name(rel) or "MOC" in rel or "_INDEX" in rel]
    seen, frontier = set(entries), list(entries)
    for _ in range(2):
        nxt = []
        for rel in frontier:
            for tgt in info.get(rel, {}).get("links", []):
                t = tgt.split("/")[-1].split(os.sep)[-1]
                r = stem2rel.get(t)
                if r and r not in seen:
                    seen.add(r); nxt.append(r)
        frontier = nxt
    two_hop = len(seen) / len(info) if info else 0.0

    # ④ 陈旧率（高 importance）
    stale_hi = 0; hi = 0
    for rel, i in info.items():
        try:
            imp = float(i["fm"].get("importance") or 0)
        except (TypeError, ValueError):
            imp = 0.0
        if imp >= 0.7:
            hi += 1
            upd = parse_date(i["fm"].get("updated"), None)
            ref = upd or datetime.fromtimestamp((Path(root) / rel).stat().st_mtime)
            if (now - ref).days > ARCHIVE_DAYS:
                stale_hi += 1

    # ⑤ 入链覆盖（复用率代理）
    reused = sum(1 for rel in info if rel in pred and pred[rel])

    # ③ 预测校准（Brier）：需要复习结果，没有就 N/A
    calib = None
    try:
        from state_manager import read_json
        st = read_json(root, "recall_state.json", legacy=(".recall_state.json",)) or {}
        pairs = []
        for _rel, s in (st or {}).items():
            if isinstance(s, dict) and s.get("reps") and s.get("interval"):
                # 预测：到期时的可提取性 ≈ 0.9（FSRS 定义）；实际：是否还记得（lapses 少）
                pred_r = 0.9
                actual = 0.0 if int(s.get("lapses") or 0) > 0 else 1.0
                pairs.append((pred_r - actual) ** 2)
        if pairs:
            calib = round(sum(pairs) / len(pairs), 4)
    except (OSError, ImportError, TypeError, ValueError):
        calib = None

    return {
        "two_hop_reach": round(two_hop, 4),
        "two_hop_note": f"{len(seen)}/{len(info)} 篇可从入口页 ≤2 跳到达",
        "first_hit_rate": None,     # 由调用方按需注入（跑黄金集较慢）
        "calibration_brier": calib,
        "staleness": round(stale_hi / hi, 4) if hi else None,
        "staleness_note": f"高 importance 笔记 {hi} 篇 · 其中 {stale_hi} 篇超 {ARCHIVE_DAYS} 天未复核",
        "reuse": round(reused / len(info), 4) if info else 0.0,
        "reuse_note": f"{reused}/{len(info)} 篇被其他笔记引用",
        # 易刷量只作对照，明确标注"仅诊断，勿当 KPI"
        "_diagnostics_only": {"n_notes": len(info),
                              "n_links": sum(len(i["links"]) for i in info.values()),
                              "_warn": "这些是易刷量：加动作就能变好，只用于诊断，不要当 KPI"},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--kpi", action="store_true",
                    help="只算抗 Goodhart 的 5 个指标（难刷），而非易刷的诊断量")
    args = ap.parse_args()
    if args.kpi:
        k = kpi(args.root)
        if args.json:
            print(json.dumps(k, ensure_ascii=False))
        else:
            print("📐 健康 KPI（抗 Goodhart · 只算难刷的量）")
            print(f"   ① 两跳可达覆盖率   {k['two_hop_reach']:.1%}  {k['two_hop_note']}")
            fh = k.get("first_hit_rate")
            print(f"   ② 检索一次命中率   {'N/A（需跑黄金集：kb eval gold）' if fh is None else f'{fh:.1%}'}")
            cb = k.get("calibration_brier")
            print(f"   ③ 预测校准 Brier   {'N/A（无复习数据：kb recall mark）' if cb is None else cb}")
            st = k.get("staleness")
            print(f"   ④ 陈旧率(高重要)   {'N/A' if st is None else f'{st:.1%}'}  {k['staleness_note']}")
            print(f"   ⑤ 复用率(入链覆盖) {k['reuse']:.1%}  {k['reuse_note']}")
            d = k["_diagnostics_only"]
            print(f"\n   ⚠️ 仅诊断、勿当 KPI：{d['n_notes']} 篇 · {d['n_links']} 条出链（易刷）")
        return 0
    m = metrics(args.root)
    if args.json:
        # label 含 emoji，单独吐给 dashboard
        print(json.dumps(m, ensure_ascii=False))
    else:
        print(report(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
