#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consolidate.py — 睡眠时整理（sleep-time consolidation）。

## 为什么要有它

2025 的"睡眠时计算"研究（Letta + Berkeley, arXiv:2504.13171）把记忆管理从**查询路径**搬到
**空闲路径**：空闲期预先把 raw 上下文整理成 learned 上下文，同等精度下省约 5× test-time 算力。
这同时是本库"反馈阶梯"的组织学依据 —— **只读的主流程 + 有写权限的整理流程**
（MemGPT 2.0 的双 agent 形态）。

本模块就是那个"整理者"，跑在空闲时：

  ① **近重复**：bottom-k 候选 → 精确 Jaccard 复核，产出"疑似重复"清单（**只提议不合并**）
  ② **冲突候选**：对已抽出的 claim 做**槽位 + 时间**对齐，区分"矛盾"与"时序更新"
     （`kb_claim.classify_pair`）—— 区分不开就会出现"去调和一个并不存在的冲突"
  ③ **陈旧高重要**：高 importance 但长期未复核的笔记
  ④ **闸门扫描**：跑 `gate.check`，把 quarantine 的笔记列出来

设计原则（全部来自实测教训）：
  · **只产出提议，不写笔记** —— 合并/删除不可逆，个人库里代价不对称。
  · **幂等 + 增量**：用 `last_run` 记录水位，重复跑不重复算。
  · **有界**：`--max-seconds` 到点就停；不做无止境的 LLM 循环。
  · **默认不用 LLM**：上面四项全是确定性算法；需要 LLM 的部分（claim 抽取）由 `kb claim`
    单独跑，避免整理任务变成不可预测的开销。

用法：
    python3 pipeline/consolidate.py run [--root R] [--max-seconds 300]
    python3 pipeline/consolidate.py status
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 精确 Jaccard 复核的**上限对数**（按 bottom-k 估计取最像的前 N 对）。
# 它决定这一轮的墙钟上限：每对要重读 2 篇文件 + 重建 2 个 shingle 集合。
_MAX_VERIFY = 400
# 全局候选对数上限：bottomk_similarity 每对建两个集合，是全库跑慢的主因。
_MAX_CANDS = 10000
# 单个 bottom-k 值允许的最大共享笔记数：超过说明它是随处可见的片段，无判别力。
_MAX_BUCKET = 50

STATE = "kb_consolidate_state.json"
REPORT = "consolidate_report.md"


def _load_state(root: Path) -> Dict[str, Any]:
    from state_manager import read_json
    return read_json(root, STATE) or {}


def _save_state(root: Path, st: Dict[str, Any]) -> None:
    from state_manager import write_json
    write_json(root, STATE, st)


def _changed_since(root: Path, since: Optional[str]) -> List[str]:
    """自上次整理以来新增/修改的笔记（按 mtime，避免读全库正文）。"""
    import rag
    out = []
    for p in rag.iter_notes(root):
        rel = str(p.relative_to(root))
        if rag.is_generated_report(rel):
            continue
        try:
            mt = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            continue
        if not since or mt > since:
            out.append(rel)
    return out


def run(root: Path, max_seconds: int = 300, changed_only: bool = True) -> int:
    t0 = time.time()
    st = _load_state(root)
    since = st.get("last_run")
    changed = _changed_since(root, since) if changed_only else []
    print(f"😴 睡眠时整理 · {root}")
    print(f"   水位 {since or '（首次）'} → 新增/变更 {len(changed)} 篇"
          f"{'（增量模式）' if changed_only else '（全量模式）'}")
    report: List[str] = [f"# 睡眠时整理报告 · {datetime.now().isoformat(timespec='seconds')}", ""]

    from kb_common import iter_notes, load_meta

    # ① 近重复（bottom-k 候选 + 精确复核）
    #   **缓存签名**：全库算一遍 bottom-k 在 4000 篇量级要十几分钟，作为每夜任务不可接受
    #   （实测首跑 >10 分钟仍未结束）。改为把 (mtime, 签名, shingle 数) 存进状态：
    #   重复运行只对**新增/变更**的笔记重算签名，候选生成走缓存签名倒排。
    dup_pairs: List[Dict[str, Any]] = []
    sig_cache: Dict[str, Any] = dict(st.get("signatures") or {})
    reused = 0
    try:
        import dedup
        sigs: Dict[str, Any] = {}
        dirty = 0
        for rel in [str(x.relative_to(root)) for x in iter_notes(root)]:
            fp = root / rel
            if not fp.is_file():
                sig_cache.pop(rel, None)
                continue
            try:
                mt = fp.stat().st_mtime
            except OSError:
                continue
            ent = sig_cache.get(rel)
            if ent and ent.get("sig") and abs(float(ent.get("mtime") or 0) - mt) < 1e-6:
                sigs[rel] = tuple(ent["sig"])
                reused += 1
                continue
            if time.time() - t0 > max_seconds:
                break        # 预算到：剩余留到下一轮（缓存里已有的照常参与）
            try:
                _fm, body = load_meta(fp)
            except OSError:
                continue
            sh = dedup.shingles(body) if len(body) > 200 else None
            if not sh:
                sig_cache.pop(rel, None)
                continue
            sigs[rel] = dedup.bottomk_signature(sh)
            sig_cache[rel] = {"mtime": mt, "sig": list(sigs[rel]), "size": len(sh)}
            dirty += 1
        post: Dict[int, List[str]] = {}
        for rel, sig in sigs.items():
            for hv in sig:
                post.setdefault(hv, []).append(rel)
        seen = set()
        cands: List[Any] = []      # (估计相似度, a, b)
        hot_skipped = 0
        for members in post.values():
            if len(members) < 2:
                continue
            # ⚠️ **桶上限是性能的关键**：某个 bottom-k 值若被几千篇共享（中文里
            #   「的就是」这类高频片段），桶内两两枚举就是**几百万对** —— 实测这一处
            #   让首跑从「秒级」变成 >10 分钟。这类桶对「近重复」毫无判别力（谁都有它），
            #   跳过既可提速又不损精度。
            if len(members) > _MAX_BUCKET:
                hot_skipped += 1
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    key = (a, b) if a < b else (b, a)
                    if key in seen:
                        continue
                    seen.add(key)
                    ea, eb = sig_cache.get(a) or {}, sig_cache.get(b) or {}
                    la, lb = int(ea.get("size") or 0), int(eb.get("size") or 0)
                    if la and lb and min(la, lb) / max(la, lb) < 0.7:
                        continue
                    # **全局候选上限（必须在 bottomk_similarity 之前）**：
                    #   后者每对要建两个集合，83 万候选对 = 几百秒 CPU
                    #   （实测首跑 6 分 40 秒，全在 user 态）。近重复本就稀少，
                    #   保留 top-5 万对足够覆盖；剩余的是"主题相近但不够像"的噪声。
                    if len(cands) >= _MAX_CANDS:
                        break
                    est = dedup.bottomk_similarity(sigs[a], sigs[b])
                    if est < 0.55:
                        continue
                    cands.append((est, a, b))
        # ⚠️ 精确复核要**重读两篇文件并重建 shingle 集合**，很贵（候选上万对时会跑几十分钟）。
        #   所以先按 bottom-k 估计降序，只对**最像的 top-N 对**做精确 Jaccard 复核。
        #   这样既避免"估计当判据"的边界误差，又把最贵的一步限制在有界数量内。
        cands.sort(key=lambda x: -x[0])
        for est, a, b in cands[:_MAX_VERIFY]:
            if time.time() - t0 > max_seconds * 2:
                break
            try:
                _f1, b1 = load_meta(root / a)
                _f2, b2 = load_meta(root / b)
            except OSError:
                continue
            s1, s2 = dedup.shingles(b1), dedup.shingles(b2)
            u = len(s1 | s2)
            if not u:
                continue
            exact = len(s1 & s2) / u
            if exact < 0.7:
                continue
            cont = dedup.containment(s1, s2)
            dup_pairs.append({"sim": round(exact, 3), "a": a, "b": b,
                              "containment": round(cont, 3),
                              "kind": "section-dup" if (cont >= 0.9 and exact < 0.85)
                                      else "note-dup"})
        dup_pairs.sort(key=lambda x: -x["sim"])
        st["signatures"] = sig_cache
        _save_state(root, st)   # 阶段结束即落盘，超时不丢缓存
        if dirty:
            print(f"      （签名缓存：重算 {dirty} 篇 · 复用 {reused} 篇）")
    except (ImportError, OSError):
        pass
    note_dup = [d for d in dup_pairs if d["kind"] == "note-dup"]
    sect_dup = [d for d in dup_pairs if d["kind"] == "section-dup"]
    print(f"   ① 近重复：整体重复 {len(note_dup)} 对 · 片段重复 {len(sect_dup)} 对（**只提议不合并**）")
    report.append(
        f"## ① 近重复\n\n- 整体重复 **{len(note_dup)}** 对（候选合并）\n"
        f"- 片段重复 **{len(sect_dup)}** 对（只提示『该段已在别处』，**不要合并** —— "
        f"合并会丢掉另一篇的独有内容）\n")

    # ② 冲突 vs 时序更新（槽位 + 时间对齐）
    conflicts: List[Dict[str, Any]] = []
    updates: List[Dict[str, Any]] = []
    claim_state = "no-data"
    try:
        import kb_claim
        from state_manager import read_json
        data = read_json(root, "kb_claims.json", legacy=(".kb_claims.json",)) or {}
        claims = [c for c in (data.get("claims") or []) if isinstance(c, dict)]
        claim_state = "checked" if claims else "no-data"
        by_slot: Dict[tuple, List[Dict[str, Any]]] = {}
        for c in claims:
            k = kb_claim._slot_key(c)
            if k:
                by_slot.setdefault(k, []).append(c)
        for k, group in by_slot.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    v = kb_claim.classify_pair(group[i], group[j])
                    if v == "conflict":
                        conflicts.append({"slot": list(k), "a": group[i].get("claim"),
                                          "b": group[j].get("claim"),
                                          "a_rel": group[i].get("note_rel"),
                                          "b_rel": group[j].get("note_rel")})
                    elif v == "update":
                        updates.append({"slot": list(k), "a": group[i].get("claim"),
                                        "b": group[j].get("claim")})
    except (ImportError, OSError, KeyError, TypeError):
        pass
    if claim_state == "no-data":
        # 诚实区分"查过了没有矛盾"与"根本没数据可查" —— 后者不该显示成 0
        print("   ② claim 对齐：**无 claim 数据**（先跑 `kb claim extract`）—— 不是『0 组矛盾』")
        report.append("## ② claim 槽位对齐\n\n**无数据**：尚未抽取 claim（`kb claim extract`）。"
                      "此项 N/A，不代表没有矛盾。\n")
    else:
        print(f"   ② claim 对齐：**矛盾** {len(conflicts)} 组 · **时序更新** {len(updates)} 组"
              f"（后者不是矛盾，不该去调和）")
        report.append(f"## ② claim 槽位对齐\n\n- 矛盾 **{len(conflicts)}** 组"
                      f"（同槽位 + 时间相交 + 取值不同）\n"
                      f"- 时序更新 **{len(updates)}** 组（同槽位但时间不相交 → **不是矛盾**）\n")

    # ③ 陈旧高重要 + ④ 质量门禁：**同样增量 + 缓存**。
    #   原来这两段各把全库读一遍（加上签名共三遍全库）→ 每夜任务不可接受
    #   （实测首跑 >10 分钟仍未结束）。改成与签名一致的策略：按 mtime 只重算变更笔记，
    #   结果累积在状态里；下一轮通常只有个位数笔记需要重算。
    from kb_common import parse_date
    import kb_health
    import gate as _gate
    cache3 = dict(st.get("stale_cache") or {})
    cache4 = dict(st.get("gate_cache") or {})
    stale = []
    gate_rows = {}
    touched = 0
    from kb_common import is_generated_report, is_source_note
    for rel in [str(x.relative_to(root)) for x in iter_notes(root)]:
        fp = root / rel
        if not fp.is_file() or is_generated_report(rel):
            cache3.pop(rel, None)
            cache4.pop(rel, None)
            continue
        try:
            mt = fp.stat().st_mtime
        except OSError:
            continue
        e3, e4 = cache3.get(rel), cache4.get(rel)
        fresh = (e3 is not None and abs(float(e3.get("mtime") or 0) - mt) < 1e-6
                 and e4 is not None and abs(float(e4.get("mtime") or 0) - mt) < 1e-6)
        if not fresh:
            if time.time() - t0 > max_seconds * 2:
                break
            try:
                fm, body = load_meta(fp)
            except OSError:
                continue
            if is_source_note(fm):
                cache3.pop(rel, None); cache4.pop(rel, None)
                continue
            touched += 1
            try:
                imp = float(fm.get("importance") or 0)
            except (TypeError, ValueError):
                imp = 0.0
            upd = parse_date(fm.get("updated"))
            days = (datetime.now() - upd).days if upd else None
            if imp >= 0.7:
                e3 = {"mtime": mt, "stale": bool(days is not None
                                                 and days > kb_health.ARCHIVE_DAYS),
                      "importance": imp, "days": days}
            else:
                e3 = {"mtime": mt, "stale": False}
            cache3[rel] = e3
            gv = _gate.classify(fm, body, rel)
            e4 = {"mtime": mt, "verdict": gv["verdict"],
                  "reasons": gv["reasons"][:3], "tier": gv["source_tier"]}
            cache4[rel] = e4
        if e3 is not None and e3.get("stale"):
            stale.append({"rel": rel, "importance": e3.get("importance"),
                          "days": e3.get("days")})
        if e4 is not None:
            v = e4.get("verdict", "pass")
            gate_rows[v] = gate_rows.get(v, 0) + 1
    st["stale_cache"] = cache3
    st["gate_cache"] = cache4
    _save_state(root, st)
    print("   ③ 陈旧高重要：" + str(len(stale)) + " 篇")
    report.append("## ③ 陈旧的高 importance 笔记\n\n" + str(len(stale)) + " 篇\n")
    print("   ④ 闸门：pass " + str(gate_rows.get("pass", 0))
          + " · quarantine " + str(gate_rows.get("quarantine", 0))
          + " · reject " + str(gate_rows.get("reject", 0))
          + "（本次重算 " + str(touched) + " 篇，其余用缓存）")
    report.append("## ④ 质量门禁\n\n" + str(gate_rows) + "\n")


    # 落盘：提议（供人工/自主裁决消费）+ 报告
    out = root / ".kb" / "state"
    out.mkdir(parents=True, exist_ok=True)
    (out / "kb_consolidate_proposals.json").write_text(json.dumps(
        {"duplicates": note_dup[:200], "sections": sect_dup[:200],
         "conflicts": conflicts[:200], "temporal_updates": updates[:200],
         "stale": stale[:200], "gate": gate_rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out / REPORT).write_text("\n".join(report), encoding="utf-8")

    st.update({"last_run": datetime.now().isoformat(timespec="seconds"),
               "n_changed": len(changed), "counts": {
                   "note_dup": len(note_dup), "section_dup": len(sect_dup),
                   "conflicts": len(conflicts), "updates": len(updates),
                   "stale": len(stale), "gate": gate_rows, "claim_state": claim_state},
               "secs": round(time.time() - t0, 1)})
    _save_state(root, st)
    print(f"\n   ✅ 整理完成 · {time.time() - t0:.1f}s · 水位已推进到 "
          f"{st['last_run']}")
    print(f"   提议 → .kb/state/kb_consolidate_proposals.json（**仅供裁决，未改任何笔记**）")
    print(f"   报告 → .kb/state/{REPORT}")
    return 0


def status(root: Path) -> int:
    st = _load_state(root)
    if not st:
        print("  从未整理过（跑 `kb consolidate run`）")
        return 0
    print(f"😴 睡眠时整理状态 · {root}")
    print(f"   上次：{st.get('last_run')} · 耗时 {st.get('secs')}s · 增量 {st.get('n_changed')} 篇")
    for k, v in (st.get("counts") or {}).items():
        print(f"     {k}: {v}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="睡眠时整理（空闲期跑，不阻塞查询）")
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "status"])
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or ".")
    ap.add_argument("--max-seconds", type=int, default=300)
    ap.add_argument("--full", action="store_true", help="全量模式（默认增量）")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if a.cmd == "status":
        return status(root)
    return run(root, a.max_seconds, changed_only=not a.full)


if __name__ == "__main__":
    sys.exit(main())
