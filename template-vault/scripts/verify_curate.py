#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_curate.py — 结晶层端到端验收探针（只读，不写库）。

把「空闲本地 Agent 结晶层」的四阶段目标逐条变成可复现的数字化断言，
一条命令给出 PASS/FAIL 表。用于每次改动后回归，也用于月度巡检。

用法:
  python3 scripts/verify_curate.py [--root /path/to/vault] [--json]

退出码：全部 PASS → 0；有 FAIL → 1（WARN 不阻断）。
"""
import argparse, json, subprocess, sys
from pathlib import Path

EX = {".git", "backups", "logs", "vector index", "pipeline", "_SELF_OPT", ".obsidian",
      "__pycache__", ".pytest_cache", ".agents", ".trash", ".codeartsdoer", "_curate"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "pipeline"))

    from kb_common import (iter_notes, load_note_full as load, is_index_stub,
                           is_pointer_page, kb_layer_of, is_generated_report)

    checks = []
    def check(name, ok, detail, warn=False):
        checks.append({"check": name, "status": "WARN" if (warn and not ok) else ("PASS" if ok else "FAIL"),
                       "detail": detail})

    # ── 采集 ─────────────────────────────────────────────
    n = ph = stub_kb = ptr_kb = 0
    layers, canon = {}, []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, _t, _b, body = load(p)
        n += 1
        if "{name} · 块{i}" in p.read_text(encoding="utf-8", errors="ignore") and not is_generated_report(rel):
            ph += 1
        if is_index_stub(fm):
            if not rel.startswith("raw/"):
                stub_kb += 1
        if is_pointer_page(body, rel) and not rel.startswith("raw/"):
            ptr_kb += 1
        L = kb_layer_of(fm, rel)
        layers[L] = layers.get(L, 0) + 1
        if L == "canon":
            canon.append((rel, fm))

    idx_path = root / "vector index" / "df_idf.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    meta = idx.get("meta", {}) or {}
    idx_n = idx.get("n_docs", 0)
    idx_stub = sum(1 for m in meta.values() if str(m.get("layer")) == "index")
    idx_noise = sum(1 for m in meta.values() if str(m.get("layer")) == "noise")
    idx_raw = sum(1 for r in (idx.get("tf") or {}) if str(r).startswith("raw/"))

    # ── P0：止血 ─────────────────────────────────────────
    check("P0 占位符残留为 0（生成物除外）", ph == 0, f"残留 {ph} 处")
    check("P0 分块索引页不入索引", idx_stub == 0, f"索引含 index 层 {idx_stub} 篇")
    check("P0 判定为 noise 的笔记不入索引", idx_noise == 0, f"索引含 noise 层 {idx_noise} 篇")
    check("P0 分层统计可见（page/raw/index/canon）", len(layers) >= 3, f"{layers}")

    # ── P1：结晶骨架跑通 ─────────────────────────────────
    n_canon = len(canon)
    check("P1 正典篇数 ≥ 20（试点达标）", n_canon >= 20, f"canon {n_canon} 篇")
    check("P1 正典覆盖率（知识层）≥ 1%", (n_canon / max(n - layers.get("raw", 0) - stub_kb, 1)) >= 0.01,
          f"{n_canon / max(n - layers.get('raw', 0) - stub_kb, 1):.2%}")
    ratios = [float(fm.get("canon_ratio") or 0) for _r, fm in canon]
    ratios = [r for r in ratios if r]
    if ratios:
        in_range = sum(1 for r in ratios if 0.04 <= r <= 0.65)
        check("P1 压缩比落在 [4%,65%]", in_range == len(ratios),
              f"{in_range}/{len(ratios)} 在区间，中位 {sorted(ratios)[len(ratios)//2]:.1%}")
    for f in ("pipeline/curate.py", "pipeline/kb_raw.py"):
        check(f"P1 模块存在 {f}", (root / f).exists(), str(root / f))

    # ── P2：写回 + 溯源 + 调度 ───────────────────────────
    archived = len(list((root / "raw" / "_curated").rglob("*.md"))) if (root / "raw" / "_curated").exists() else 0
    check("P2 原文保号归档数 = 正典数", archived >= n_canon, f"raw/_curated {archived} 篇 / canon {n_canon} 篇")
    lin = root / ".kb" / "state" / "lineage.jsonl"
    n_lin = sum(1 for l in lin.read_text(encoding="utf-8").splitlines() if l.strip()) if lin.exists() else 0
    check("P2 lineage 血缘条目 ≥ 正典数", n_lin >= n_canon, f"{n_lin} 条")
    check("P2 空闲调度脚本存在", (root / "scripts" / "kb-curate.sh").exists(), "scripts/kb-curate.sh")

    # 追溯闭环：每篇正典的 source_ref 都能取出原文
    ok_trace = 0
    for rel, fm in canon:
        sids = fm.get("source_ref") or []
        sids = [sids] if isinstance(sids, str) else sids
        good = any(_raw_ok(root, s) for s in sids)
        if good and (root / str(fm.get("archived_original", ""))).exists():
            ok_trace += 1
    check("P2 追溯闭环 100%（source_ref + 原文保号）", n_canon > 0 and ok_trace == n_canon,
          f"{ok_trace}/{n_canon}")

    # ── P3：人工闸门 ─────────────────────────────────────
    try:
        out = subprocess.run([sys.executable, str(root / "pipeline" / "kb_l4.py"), "docket",
                              "--root", str(root)], capture_output=True, text=True, timeout=120)
        has_lc = "`lc`" in out.stdout
    except (OSError, subprocess.SubprocessError):
        has_lc = False
    check("P3 L4 宪法含 lc rung（结晶待裁）", has_lc, "kb_l4 docket 输出含 lc")
    mod = (root / "pipeline" / "curate.py").read_text(encoding="utf-8", errors="ignore")
    check("P3 冻结闸门存在（l4_frozen）", "def l4_frozen" in mod, "curate.l4_frozen")
    check("P3 判定写回存在（mark_evidence_verdict）", "def mark_evidence_verdict" in mod,
          "noise/raw-only → frontmatter")

    # ── 汇总 ─────────────────────────────────────────────
    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in checks if c["status"] == "WARN")
    if args.as_json:
        print(json.dumps({"root": str(root), "checks": checks, "fail": n_fail, "warn": n_warn,
                          "layers": layers, "index_docs": idx_n}, ensure_ascii=False, indent=2))
    else:
        print(f"🧪 结晶层验收探针  root={root}")
        print(f"   笔记 {n} · 索引 {idx_n} 篇（含 raw {idx_raw}）· 分层 {layers}")
        print("")
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
        for c in checks:
            print(f"   {icon[c['status']]} {c['check']}")
            print(f"        {c['detail']}")
        print("")
        print(f"   结果：PASS {len(checks) - n_fail - n_warn} · WARN {n_warn} · FAIL {n_fail}")
    return 1 if n_fail else 0


def _raw_ok(root: Path, sid) -> bool:
    """用 kb_raw 解析一个 source_ref 是否可达（子进程隔离，避免污染本进程 cwd）。"""
    try:
        r = subprocess.run([sys.executable, str(root / "pipeline" / "kb_raw.py"), "show",
                            str(sid), "--root", str(root), "--json"],
                           capture_output=True, text=True, timeout=120)
        return bool(json.loads(r.stdout or "{}").get("ok"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


if __name__ == "__main__":
    sys.exit(main())
