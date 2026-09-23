#!/usr/bin/env python3
# ============================================================
# kb_calibrate.py —— P3-2 阈值标定：在仿真大语料上 A/B 标定硬编码阈值
# ------------------------------------------------------------
# 痛点：DUP_SIM/MERGE_MIN_SIM/RETIRE_AGE_DAYS / 惩罚权重 是从 ~29 篇小库"上脑袋"假设的，
#   未在大语料上验证。本脚本生成可控仿真语料（已知 ground truth 重复对/过期对），
#   扫过候选阈值算 precision/recall/F1，给出推荐值区间 + 与当前默认值对比。
#   默认不改任何阈值（只读标定）；结果可人工决策后写入 .kb_rsi_config.json 或常量。
# ------------------------------------------------------------
#   用法:
#     python3 pipeline/kb_calibrate.py synth --root T --n 300 --pairs 40 --seed 7
#     python3 pipeline/kb_calibrate.py dup   --root T [--csv out.csv]
#     python3 pipeline/kb_calibrate.py merge --root T [--csv out.csv]
#     python3 pipeline/kb_calibrate.py retire --root T [--csv out.csv]
#     python3 pipeline/kb_calibrate.py all   --root T
# 默认值（当前，供对比）: DUP_SIM=0.85 MERGE_MIN_SIM=0.97 RETIRE_AGE_DAYS=180
# ------------------------------------------------------------
import argparse, json, math, random, re, shutil, sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from kb_common import cos as _cos  # 余弦相似度（行为等价：非空稀疏向量点积）

# ── 双轨字符向量（中文字按字 / 英文词）+ L2 归一化（与 kb_query 一致，避免长度稀释）──
_CN = re.compile(r"[\u4e00-\u9fff]")
_LAT = re.compile(r"[A-Za-z0-9]+")


# 注：_tokenize / _vec 保留本地实现——与 kb_common.tokenize/char_vec 不同：
#   本地 _tokenize 无 lower()、无 CJK bigram，刻意保持以不改变标定结果。
def _tokenize(s):
    return _CN.findall(s or "") + _LAT.findall(s or "")


def _vec(s):
    c = Counter(_tokenize(s))
    n = math.sqrt(sum(v * v for v in c.values())) or 1
    return {k: v / n for k, v in c.items()}


# ── 仿真语料生成（已知 ground truth）───────────────────────
# 中文洞见词池（保证注入的重复对与独立笔记有区分度）
_SEED_TEXTS = [
    "本地知识库应该支持版本控制和回滚以防止自编辑造成不可逆损失",
    "外部真实反馈是校准笔记权重的最主要锚点而非内部命中计数",
    "去重合并前必须人工确认避免自动合并误伤低相似度但语义不同的笔记",
    "过期制度笔记应按 review_cycle 定期复审失效则退役防止坏血病",
    "跨域连接比例是衡量知识库是否闭合回声室的关键多样性指标",
    "记忆写入应该只升不降且任何改动都可回滚到上一次快照",
    "检索应该走双层索引层做机器查询markdown层给人读",
    "矛盾检测只读交人工复核绝不自动删改笔记以保持可审计",
]
_FREQ_CN = "的知识的管理的项目的设计的技术的决策的运营的系统的数据的模型"


def _synth_text(rng, base, keep_ratio, vocab):
    """保留 keep_ratio 的原词、其余替换为词池词 → cos(原,复述)≈keep_ratio。"""
    toks = _tokenize(base)
    out = []
    for tok in toks:
        if rng.random() < keep_ratio:
            out.append(tok)
        else:
            out.append(rng.choice(vocab))
    return " ".join(out)


def synth(root: Union[str, Path], n: int = 300, pairs: int = 40,
          seed: int = 7, today: Optional[date] = None) -> Dict[str, Any]:
    """生成仿真 vault（git init）+ ground truth 文件。返回 gt dict。"""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    (root / "raw").mkdir(parents=True)
    (root / "20-技术 Technology").mkdir(parents=True)
    (root / "40-资源库 Resources").mkdir(parents=True)
    rng = random.Random(seed)
    vocab = _TOKEN_VOCAB = _CN.findall(_FREQ_CN) + ["knowledge", "retention", "retire", "merge"]

    today = today or date(2026, 9, 18)
    gt_dup, gt_over = [], []
    info = {}

    def write(rel, domain, body, created):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        fm = (f"---\ndomain: {domain}\ncategory: insight\nimportance: 0.5\n"
              f"created: {created}\nupdated: {created}\n---\n{body}\n")
        p.write_text(fm, encoding="utf-8")

    n_indep = n - 2 * pairs
    # 重复对：目标 sim 从 0.55 线性到 0.97（覆盖阈值敏感带）
    for i in range(pairs):
        target = round(0.55 + 0.42 * (i / max(1, pairs - 1)), 2)
        base = _SEED_TEXTS[i % len(_SEED_TEXTS)]
        keep = min(0.99, max(0.4, target + rng.uniform(-0.05, 0.05)))
        rel_a = f"20-技术 Technology/orig_{i:03d}.md"
        rel_b = f"40-资源库 Resources/para_{i:03d}.md"
        write(rel_a, "开发", _synth_text(rng, base, 1.0, vocab), str(today))
        write(rel_b, "开发", _synth_text(rng, base, keep, vocab), str(today))
        gt_dup.append([rel_a, rel_b, round(keep, 3)])
    # 独立笔记（彼此低相似）+ 注入过期/未过期
    for j in range(n_indep):
        rel = f"20-技术 Technology/indep_{j:03d}.md"
        body = " ".join(rng.choice(vocab) for _ in range(rng.randint(20, 50)))
        age_days = rng.randint(10, 400)
        created = (today - timedelta(days=age_days)).isoformat()
        write(rel, "开发", body, created)
        if age_days > 180:
            gt_over.append([rel, age_days])

    (root / "gt_dup.json").write_text(json.dumps(gt_dup, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "gt_over.json").write_text(json.dumps(gt_over, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess_run(root)
    return {"root": str(root), "n": n, "pairs": pairs, "gt_dup": gt_dup, "gt_over": gt_over}


def subprocess_run(root: Union[str, Path]) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(root), "init"], capture_output=True)


def _load_notes(root):
    from kb_rsi import collect
    return {n["rel"]: n for n in collect(Path(root))}


def _read_gt(root, name):
    """读取标定用 ground-truth 文件；缺失时给出清晰指引（不再裸崩）。"""
    p = root / name
    if not p.exists():
        raise SystemExit(
            f"❌ 缺少标定数据集 {name}。\n"
            f"   kb_calibrate 必须对【仿真 vault】标定：先\n"
            f"     python3 pipeline/kb_calibrate.py synth --root {root}  # 生成 {name}\n"
            f"   再运行 dup/merge/retire/all。真实 vault 无 GT，无法标定。"
        )
    return json.loads(p.read_text(encoding="utf-8"))


# ── 标定引擎────────────────────────────────────────────────
def calibrate(root: Union[str, Path], what: str, csv_out: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """what ∈ {dup, merge, retire}。返回 (rows, recommend)。"""
    root = Path(root)
    notes = _load_notes(root)
    vecs = {rel: _vec((n.get("body") or "")) for rel, n in notes.items() if (n.get("body") or "").strip()}

    if what == "retire":
        gt_over = _read_gt(root, "gt_over.json")
        today = date(2026, 9, 18)
        pos_age = {rel: (today - _parse_d(d)).days for rel, d in gt_over}  # 正样本(过期)年龄
        neg_age = {}
        for n in notes.values():
            c = _parse_d(n["fm"].get("created"))
            if c and (rel := str(n["rel"])) not in pos_age:
                neg_age[rel] = (today - c).days
        ts = list(range(30, 370, 10))
        pos = sorted(pos_age.values())
        neg = sorted(neg_age.values())

        rows, best = [], None
        for t in ts:
            tp = sum(1 for a in pos if a >= t)
            fp = sum(1 for a in neg if a >= t)
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec = tp / len(pos) if pos else 0.0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            rows.append({"threshold": t, "precision": round(prec, 3),
                         "recall": round(rec, 3), "f1": round(f1, 3), "tp": tp, "fp": fp})
            if f1 > (best['f1'] if best else -1):
                best = rows[-1]
        _maybe_csv(csv_out, what, rows)
        return rows, best

    # dup / merge：cos 判定
    rels = list(vecs.keys())
    gt_dup = _read_gt(root, "gt_dup.json")
    pos_sims = [p[2] for p in gt_dup]  # 注入目标 sim
    # 负样本：随机抽取的对（排除 GT 对内），模拟"本不重复但对"
    rng = random.Random(1234)
    neg_sims = []
    neg_candidates = rels[:]
    for _ in range(len(gt_dup) * 3):
        if len(neg_candidates) < 2:
            break
        a, b = rng.sample(neg_candidates, 2)
        neg_sims.append(round(_cos(vecs[a], vecs[b]), 3))
    lo, hi = (0.90, 0.99) if what == "merge" else (0.60, 0.99)
    ts = [round(lo + 0.01 * i, 3) for i in range(int((hi - lo) / 0.01) + 1)]
    rows, best = [], None
    for t in ts:
        tp = sum(1 for s in pos_sims if s >= t)
        fp = sum(1 for s in neg_sims if s >= t)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / len(pos_sims) if pos_sims else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        rows.append({"threshold": t, "precision": round(prec, 3),
                     "recall": round(rec, 3), "f1": round(f1, 3), "tp": tp, "fp": fp})
        if f1 > (best['f1'] if best else -1):
            best = rows[-1]
    _maybe_csv(csv_out, what, rows)
    return rows, best


# 注：_parse_d 保留本地实现——返回 date 对象（非 datetime），
#   kb_common.parse_date_safe 返回 datetime，date-datetime 做 .days 会 TypeError。
def _parse_d(s):
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return date(y, m, d)
    except (ValueError, TypeError):
        return date(1970, 1, 1)


def _maybe_csv(csv_out, what, rows):
    if csv_out:
        Path(csv_out).write_text("threshold,precision,recall,f1,tp,fp\n"
                                 + "\n".join(f"{r['threshold']},{r['precision']},{r['recall']},{r['f1']},{r['tp']},{r['fp']}" for r in rows),
                                 encoding="utf-8")


# 当前默认值（供标定对比）
_DEFAULTS = {"dup": 0.85, "merge": 0.97, "retire": 180}


def _print_report(what, rows, best, default, unit=""):
    print(f"\n=== 标定: {what} ===")
    print(f"{'阈值':>8} {'精确':>6} {'召回':>6} {'F1':>6}  (TP/FP)")
    for r in rows:
        mark = " ◀★推荐" if r is best else ""
        print(f"  {what} >= {r['threshold']}{unit:<3} 精确{r['precision']} 召回{r['recall']} "
              f"F1={r['f1']}  TP={r['tp']} FP={r['fp']}{mark}")
    if best:
        print(f"\n★ 推荐 {what} 阈值 ≈ {best['threshold']}{unit}  (F1={best['f1']})"
              f"  | 当前默认 {default}{unit}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth"); s.add_argument("--root", required=True)
    s.add_argument("--n", type=int, default=300)
    s.add_argument("--pairs", type=int, default=40)
    s.add_argument("--seed", type=int, default=7)

    common = lambda p: (p.add_argument("--root", required=True), p.add_argument("--csv", default=None))
    for name in ("dup", "merge", "retire"):
        sp = sub.add_parser(name); common(sp)
    al = sub.add_parser("all"); al.add_argument("--root", required=True)

    a = ap.parse_args()
    if a.cmd == "synth":
        gt = synth(Path(a.root), a.n, a.pairs, a.seed)
        print(f"✅ 仿真 vault 已建: {a.root} · {a.n} 篇 / {a.pairs} 对重复 / "
              f"{len(gt['gt_over'])} 篇过期")
        return 0
    if a.cmd == "all":
        for w in ("dup", "merge", "retire"):
            rows, best = calibrate(Path(a.root), w)
            _print_report(w, rows, best, _DEFAULTS[w], "days" if w == "retire" else "")
        return 0
    rows, best = calibrate(Path(a.root), a.cmd, a.csv)
    _print_report(a.cmd, rows, best, _DEFAULTS[a.cmd], "days" if a.cmd == "retire" else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
