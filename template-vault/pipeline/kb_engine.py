#!/usr/bin/env python3
# ============================================================
# kb_engine.py —— kb-kit RSI 引擎编排器（T1/T2/T3 + T3 开关 + 质量门 + 可回滚）
# ------------------------------------------------------------
# 与 kb_rsi.py 分工：
#   kb_rsi.py  = 提议层（只读）：产出带置信度的改进建议 + 外部接地防塌阀
#   kb_engine  = 执行层：质量门通过才写；每次写都 git checkpoint + 留回滚锚点
#
# T3 开关（默认 OFF）：
#   关闭 → 引擎 = T1 + T2   （自整理 + 自我加权）
#   打开 → 引擎 = T1 + T2 + T3（+ 自我调参）
#   开关位置：pipeline/.kb_rsi_config.json 的 t3_enabled 字段
#
# 用法：
#   python3 pipeline/kb_engine.py --dry --run t1,t2            # 只提议（默认行为）
#   python3 pipeline/kb_engine.py --run t1,t2 --auto           # 执行 T1+T2（写库）
#   python3 pipeline/kb_engine.py --run t1,t2,t3 --auto        # 需 t3_enabled=true 才真跑 T3
#   python3 pipeline/kb_engine.py --status                     # 看当前开关/配置
#   python3 pipeline/kb_engine.py --reset-config               # 重置为默认开关
# ------------------------------------------------------------
import argparse, os, re, sys, json, math
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi  # 复用其只读信号采集（collect/metrics/load_note/vec/cos/parse_date）
import kb_fitness  # P1-A: 外部锚定 fitness（解耦 T3 于可自抬的内部 health）
import kb_scale  # P3-1: 规模档位 + 索引层覆盖感知（双层检索预留）
from kb_constants import MERGE_MIN_SIM, RETIRE_AGE_DAYS

CONFIG_PATH = Path(__file__).resolve().parent / ".kb_rsi_config.json"
CONFIG_DEFAULT = {
    "version": 1,
    "auto_apply": False,        # 是否自动写入（对应 --auto）
    "confidence_floor": 0.60,   # 质量门：低于此置信度的提议只记录不执行
    "tiers_enabled": ["t1", "t2"],  # 默认启用 T1+T2
    "t3_enabled": False,        # ★★★ T3 开关：默认关闭
    "t3_step": 0.10,            # T3 单次调参步长（保守）
    "tuned": {},                # T3 调过的参数快照
    "health_weights": {},       # P4: health_score 权重；留空 → 用常量 HEALTH_SCORE_WEIGHTS
}
MERGE_MAX_IMP = 0.70         # 只有两个都"低权重"才考虑自动合并

# ── health_score 权重（P4：原硬编码 → 可配置，理由见下）─────────────
# 健康分 = 100 − 孤儿惩罚 − 过期惩罚 − 未接地惩罚。三项各自对应一种已知的知识库塌缩形态，
# 权重即"哪种退化更致命"的取值，会影响 T3 做趋势对照时的判断，故集中写常量并允许
# .kb_rsi_config.json 的 health_weights 部分覆盖（load_config 后从 cfg 取）。
#   orphan(无出链)↑  → 知识网络退化为孤岛，引用链断裂，新笔记无处锚定
#   stale(未review)↑ → 坏血病：过期/失效知识仍在决策时被调用
#   未外部接地       → 新鲜流入<5% 或 跨域连接<15% = 知识库趋自闭（RSI 第一塌）
#   cap             → 单类惩罚封顶，避免单一指标把分压到 0 而丧失其它维度区分度
HEALTH_SCORE_WEIGHTS = {
    "orphan_per_pct": 3.0,      # 孤儿笔记每占 1% 扣 3 分
    "stale_per_pct": 3.0,       # 过期笔记每占 1% 扣 3 分
    "ungrounded_penalty": 15.0, # 未外部接地时整笔扣 15
    "cap": 45.0,                # 单类惩罚封顶值
}


def _resolve_health_weights(cfg):
    """健康分权重：用用户 cfg["health_weights"] 部分覆盖默认（缺省走 HEALTH_SCORE_WEIGHTS）。
    只在调用方显式传 cfg 时才走覆盖分支；否则直接用模块常量（保持 health_score 可独立调用）。"""
    base = dict(HEALTH_SCORE_WEIGHTS)
    user = (cfg or {}).get("health_weights")
    if isinstance(user, dict):
        base.update(user)
    return base


# ── 配置读写 ──────────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    cfg = dict(CONFIG_DEFAULT)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(user)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


# ── git checkpoint + 记录（所有写操作必经此门）──────────────
def git_add_commit(root: Union[str, Path], rels: List[str], msg: str) -> bool:
    import subprocess
    sp = subprocess.run(["git", "-C", str(root), "add", "--", *[r for r in rels]],
                        capture_output=True)
    if sp.returncode != 0:
        return False
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip() == "":
        return True  # 无变更，无需提交
    r = subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg],
                       capture_output=True, text=True)
    return r.returncode == 0


def git_backup(root: Union[str, Path], msg: str) -> None:
    """写前做一次完整 checkout 级备份（git commit -a），失败也继续。"""
    import subprocess
    subprocess.run(["git", "-C", str(root), "add", "-u"], capture_output=True)
    if subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg],
                       capture_output=True)


# ── P0-1: raw/ 不可变层硬隔离守卫 ─────────────────────────
def is_raw_rel(root: Union[str, Path], rel: str) -> bool:
    """rel 是否在 raw/ 下（不可变外部源）。与 kb_rsi.is_raw 同约定，供引擎复用。"""
    try:
        parts = [str(x).lower() for x in Path(rel).parts]
    except (TypeError, ValueError, AttributeError):
        return False
    return "raw" in parts[:-1]


def _raw_guard(root, p):
    """P0-1 写入守卫：p 在 raw/ 不可变层内 → 返回 True（须拒绝写入）。"""
    try:
        rel = str(Path(p).resolve().relative_to(Path(root).resolve()))
    except (OSError, ValueError, TypeError):
        rel = str(p)
    return is_raw_rel(root, rel)


# ── 写前缀式 frontmatter 字段（复用 kb_rsi 解析 + 写字段）──
def read_note(p: Path) -> Tuple[Dict[str, Any], str, str]:
    return kb_rsi.load_note(p)


def set_fm_field(p: Path, key: str, value: Any) -> bool:
    """把 frontmatter 字段设为 value（缺失则插到 firstmatter 末尾）。只改元数据不改正文语义。
    P0-1: 硬隔离 —— 若 p 落在 raw/ 不可变层内，直接拒绝写入（静默返回 False）。"""
    for parent in Path(p).parents:
        if (parent / "pipeline").is_dir():
            if _raw_guard(parent, p):
                return False
            break
    text = p.read_text(encoding="utf-8")
    m0 = re.match(r"^\s*---\s*$", text, re.M)
    if not m0:
        return False
    rest = text[m0.end():]
    m1 = re.search(r"^\s*---\s*$", rest, re.M)
    if not m1:
        return False
    fm_txt, body = rest[:m1.start()], rest[m1.end():]
    if not body.strip() or body.lstrip().startswith("---"):
        return False
    val = str(value)
    line = f"{key}: {val}"
    if re.search(rf"^{re.escape(key)}\s*:", fm_txt, re.M):
        new_fm = re.sub(rf"^{re.escape(key)}\s*:.*$", line, fm_txt, count=1, flags=re.M)
    else:
        new_fm = fm_txt.rstrip("\n") + "\n" + line + "\n"
    p.write_text("---\n" + new_fm + "\n---\n" + body, encoding="utf-8")
    return True


def log_decision(cfg: Dict[str, Any], root: Union[str, Path], tier: str, action: str,
                 target: Any, confidence: float, before: Any, after: Any,
                 extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tier": tier, "action": action, "target": target,
        "confidence": round(float(confidence), 3),
        "before": before, "after": after,
        "applied": cfg.get("auto_apply", False),
    }
    if extra:
        rec.update(extra)
    lp = Path(root) / "pipeline" / ".kb_engine_proposals.jsonl"
    lp.parent.mkdir(parents=True, exist_ok=True)  # 健壮：日志目录不存在则建
    with open(lp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ============================================================
# T1 · 自我整理（自我代谢/免疫）—— 质量门 + 可回滚
# ============================================================


def _grounded(m):
    """外部接地判定（P0-1 真外部锚）：
    raw/ 新鲜外部源占比 external_inflow ≥5% 且跨域连接 ≥15% 视为接地正常；
    否则知识库趋于自闭，实质自我改进应被阀门打折/阻断。
    注意：用 external_inflow(raw/新鲜流入) 而非 internal 新笔记率，
    因为后者可被自身内容刷爆，不是真外部接地。"""
    return (float(m.get("external_inflow", 0) or 0) >= 5.0
            and float(m.get("cross_ratio", 0) or 0) >= 0.15)


def health_score(m: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> float:
    """从 kb_rsi 扁平指标合成 0-100 健康分，供 T3 做趋势对照。
    权重来自 HEALTH_SCORE_WEIGHTS（默认），若传 cfg 且 cfg 含 health_weights 则部分覆盖（P4）。"""
    w = _resolve_health_weights(cfg)
    total = m["total"] or 1
    orphan_pct = 100 * len(m.get("orphans", [])) / total
    stale_pct = 100 * len(m.get("stale", [])) / total
    grounded = _grounded(m)
    score = (100
             - min(w["cap"], orphan_pct * w["orphan_per_pct"])
             - min(w["cap"], stale_pct * w["stale_per_pct"])
             - (w["ungrounded_penalty"] if not grounded else 0))
    return round(max(0, min(100, score)), 1)
def tier_t1(root: Union[str, Path], cfg: Dict[str, Any], do_apply: bool) -> List[Dict[str, Any]]:
    notes = kb_rsi.collect(root)
    m = kb_rsi.metrics(notes=notes, root=root)
    if not m:
        return []
    floor = cfg["confidence_floor"]
    info = {n["rel"]: n for n in notes}
    applied = []
    changed = []
    # 写前一次性 checkpoint（可回滚锚点）
    if do_apply:
        git_backup(root, "pre-rsi-t1 checkpoint")

    # (1) 去重合并：极高相似度 + 同域 + 都低权重 → 合并（写前 backup）
    if do_apply:
        git_backup(root, "pre-rsi-t1 merge checkpoint")
    for sc, a, b in m["dups"]:
        same_dom = info[a]["domain"] == info[b]["domain"]
        ia = float(kb_rsi.parse_f(info[a]["fm"].get("importance")))
        ib = float(kb_rsi.parse_f(info[b]["fm"].get("importance")))
        low = max(ia, ib) <= MERGE_MAX_IMP
        conf = min(0.99, sc)
        if do_apply and sc >= MERGE_MIN_SIM and same_dom and low:
            keep, drop = (a, b) if ia >= ib else (b, a)
            before = {"keep": keep, "drop": drop, "sim": sc}
            # 保留 keep；把 drop 标 retire
            dp = Path(root) / drop
            set_fm_field(dp, "kb_action", "retire")
            set_fm_field(dp, "category", "duplicated")
            changed.append(drop)
            applied.append({"tier": "t1", "action": "DUP_MERGE", "items": [keep, drop],
                            "sim": sc, "before": before, "after": {"retired": drop}})
        else:
            applied.append({"tier": "t1", "action": "DUP_MERGE_PROPOSE", "items": [a, b],
                            "sim": round(sc, 2), "blocked": ("sim<%.2f或不同域或权重偏高" % MERGE_MIN_SIM)
                            if sc < MERGE_MIN_SIM else "权重偏高",
                            "confidence": round(conf, 2)})

    # (2) 过期 retire（保守门槛）
    stale = [(age, r) for age, r in m["stale"] if age >= RETIRE_AGE_DAYS]
    policy_stale = [(r, cyc, last) for r, cyc, last in m.get("policy_stale", [])]
    for age, r in stale:
        conf = min(0.95, age / 365.0)
        if do_apply and conf >= floor:
            before = {"kb_action": info[r]["kb_action"], "age_days": age}
            set_fm_field(Path(root) / r, "kb_action", "retire")
            set_fm_field(Path(root) / r, "status", "legacy")
            changed.append(r)
            applied.append({"tier": "t1", "action": "RETIRED", "items": [r],
                            "age_days": age, "before": before,
                            "after": {"kb_action": "retire", "status": "legacy"}})
        else:
            applied.append({"tier": "t1", "action": "RETIRED_PROPOSE", "items": [r],
                            "age_days": age, "confidence": round(conf, 2),
                            "blocked": "未达 RETIRE_AGE_DAYS 或低于质量门"})

    # (3) 收件箱 intake：无法安全自动归类 → 始终只提议
    for r in m["backlog"]:
        applied.append({"tier": "t1", "action": "INBOX_INTAKE_PROPOSE", "items": [r],
                        "confidence": 0.8, "blocked": "需人工/按category路由归类"})

    # (4) 孤儿补链：link_engine 风格，连到同域最高相似度笔记或首页（非破坏）
    from kb_rsi import vec, cos
    body_vecs = {r: vec(info[r]["body"] + " " + info[r]["fm"].get("tags", ""))
                 for r in info if info[r]["body"].strip()}
    orphans = [r for r in info if not info[r]["links"]]
    for r in orphans:
        if not r.endswith(".md"):
            continue
        cand = None
        if r in body_vecs:
            best, best_s = None, 0.0
            for other, v in body_vecs.items():
                if other == r or info[other]["domain"] != info[r]["domain"]:
                    continue
                s = cos(body_vecs[r], v)
                if s > best_s:
                    best, best_s = other, s
            cand = best
        conf = 0.75 if cand and best_s > 0.5 else 0.45 if cand else 0.3
        target = cand or "🏠-知识库首页"
        if do_apply and conf >= floor and cand:
            before = {"outbound_links": len(info[r]["links"])}
            txt = info[r]["text"]
            conn = f"[[{target}]]"
            if conn not in txt:
                np = Path(root) / r
                new = txt.rstrip() + ("\n\n---\n<!-- RSI补链 -->\n" + conn + "\n")
                np.write_text(new, encoding="utf-8")
            applied.append({"tier": "t1", "action": "LINKED", "items": [r], "to": target,
                            "confidence": round(conf, 2), "before": before,
                            "after": {"outbound_links": len(info[r]["links"]) + 1}})
            changed.append(r)
        else:
            applied.append({"tier": "t1", "action": "LINK_PROPOSE", "items": [r],
                            "to": target, "confidence": round(conf, 2),
                            "blocked": "无匹配/低于质量门"})

    # 统一写决策日志（提议与执行都记录，供审计/T3 趋势使用）
    for e in applied:
        target = e.get("items", e.get("target", []))
        log_decision(cfg, root, e["tier"], e["action"], target,
                     e.get("confidence", 0.0), e.get("before"), e.get("after"))
    if do_apply and changed:
        git_add_commit(root, changed, "rsi-t1: 自我整理应用 %d 篇(可回滚)" % len(changed))
    return applied


# ============================================================
# T2 · 自我加权（外部接地价值 → importance）
# ============================================================
def tier_t2(root: Union[str, Path], cfg: Dict[str, Any], do_apply: bool) -> List[Dict[str, Any]]:
    """T2 自我加权：以【外部真反馈】为主锚的 importance 加权（P0-3）。
    RAG 命中退为次级信号（HIT_CAP 封顶，抑制富者更富），并加多样性 floor/cap。
    读端(fl.compute_signals)纯计算；写端复用 fl.apply_bumps（只升不降+封顶+commit+raw 硬隔离）。
    使权重反映"真实世界价值"而非"内部人气"（防 Self-Improvement Paradox 的自闭偏差）。"""
    import feedback_loop as fl
    state = fl.load_state(root)
    # P0-3：外部主锚信号 = external_count(主) + RAG命中(次级封顶)
    signals, external_count, internal_hits = fl.compute_signals(state)
    suggested, zero_n = fl.bumps(signals, external_count, internal_hits, root)
    applied = []
    # 先读写前 importance（避免循环内反复重读）
    before_map = {}
    for b in suggested:
        p = Path(root) / b["rel"]
        try:
            before_map[b["rel"]] = fl._parse_importance(read_note(p)[0].get("importance"))
        except (OSError, ValueError, IndexError):
            before_map[b["rel"]] = 0.0
    if do_apply:
        # 写外部信号计数回 state（保持 hits 为准），全量写库一次
        state["hits"] = internal_hits
        fl.save_state(root, state)
        fl.apply_bumps(root)
    for b in suggested:
        rel = b["rel"]
        old_imp = before_map.get(rel, 0.0)
        ext = b["external"]
        grounded = ext > 0
        conf = min(0.95, 0.5 + 0.2 * (3 if grounded else 1) + 0.15 * (b["hits"] >= fl.MIN_HITS))
        if do_apply:
            after_imp = fl._parse_importance(read_note(Path(root) / rel)[0].get("importance"))
            applied.append({"tier": "t2", "action": "WEIGHTED", "items": [rel],
                            "old": old_imp, "new": after_imp,
                            "external_grounding": grounded, "confidence": round(conf, 2),
                            "before": {"importance": old_imp},
                            "after": {"importance": after_imp}})
        else:
            hint = round(min(1.0, old_imp + b["delta"]), 3)
            applied.append({"tier": "t2", "action": "WEIGHTED_PROPOSE", "items": [rel],
                            "delta": b["delta"], "score": b["score"],
                            "external_grounding": grounded, "confidence": round(conf, 2),
                            "zero_cold": b["hits"] < fl.MIN_HITS and ext == 0,
                            "blocked": "未达质量门或 --auto 未开",
                            "before": {"importance": old_imp},
                            "after": {"importance_hint": hint}})
    for e in applied:
        target = e.get("items", e.get("target", []))
        log_decision(cfg, root, e["tier"], e["action"], target,
                     e.get("confidence", 0.0), e.get("before"), e.get("after"),
                     extra={"external_grounding": e.get("external_grounding"),
                            "score": e.get("score"), "zero_cold": e.get("zero_cold")})
    return applied


# ============================================================
# T3 · 自我调参（自我调"改进本身"）—— 受开关控制，保守、可回滚
# ============================================================

# ── T3 A/B 自调参验证机制 ──────────────────────────────────
def _load_last_ab_snapshot(root: Path) -> Optional[dict]:
    """从 .kb_engine_proposals.jsonl 读取最后一条 ab_snapshot。"""
    log_path = Path(root) / "pipeline" / ".kb_engine_proposals.jsonl"
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        for line in reversed(lines):
            try:
                rec = json.loads(line)
                if "ab_snapshot" in rec:
                    return rec["ab_snapshot"]
            except json.JSONDecodeError:
                continue
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _save_ab_snapshot(root: Path, snapshot: dict) -> None:
    """追加写入 ab_snapshot 到决策日志。"""
    log_path = Path(root) / "pipeline" / ".kb_engine_proposals.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ab_snapshot": snapshot, "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")


def _count_consecutive_neg_delta(root: Path) -> int:
    """统计连续负 delta 次数。"""
    log_path = Path(root) / "pipeline" / ".kb_engine_proposals.jsonl"
    if not log_path.exists():
        return 0
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        count = 0
        for line in reversed(lines):
            try:
                rec = json.loads(line)
                snap = rec.get("ab_snapshot", {})
                delta = snap.get("delta", 0)
                if delta < 0:
                    count += 1
                else:
                    break
            except json.JSONDecodeError:
                continue
        return count
    except (OSError, json.JSONDecodeError):
        return 0


def _build_ab_report(root: Path) -> str:
    """生成 A/B 调参效果报告。"""
    log_path = Path(root) / "pipeline" / ".kb_engine_proposals.jsonl"
    if not log_path.exists():
        return "无 A/B 调参记录"
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        snapshots = []
        for line in lines:
            try:
                rec = json.loads(line)
                if "ab_snapshot" in rec:
                    snapshots.append(rec["ab_snapshot"])
            except json.JSONDecodeError:
                continue
        if not snapshots:
            return "无 A/B 调参记录"
        total = len(snapshots)
        pos = sum(1 for s in snapshots if s.get("delta", 0) > 0)
        neg = sum(1 for s in snapshots if s.get("delta", 0) < 0)
        avg_delta = sum(s.get("delta", 0) for s in snapshots) / total
        report_lines = [
            "# T3 A/B 调参效果报告",
            f"总调参次数: {total}",
            f"正向 delta: {pos} ({pos/total*100:.0f}%)",
            f"负向 delta: {neg} ({neg/total*100:.0f}%)",
            f"平均 delta: {avg_delta:.4f}",
            "",
            "## 详细记录",
            "",
        ]
        for i, s in enumerate(snapshots, 1):
            report_lines.append(f"{i}. delta={s.get('delta', 0):.4f} tuned={s.get('tuned_fitness', 0):.4f} baseline={s.get('baseline_fitness', 0):.4f}")
        return "\n".join(report_lines)
    except (OSError, json.JSONDecodeError):
        return "读取 A/B 记录失败"


def tier_t3(root: Union[str, Path], cfg: Dict[str, Any], do_apply: bool) -> List[Dict[str, Any]]:
    """
    仅当 cfg['t3_enabled'] 为真时才真正执行调参（否则只评估 fitness/health 趋势）。
    调的是"引擎自身的参数"（置信度门槛/调参步长），依据=外部锚定 fitness 趋势
    （解耦于 T1/T2 可自抬的 health；Goodhart 防护 P1-A）；不碰笔记内容；
    小步长 + 写前备份 + 决策日志 + 可回滚。
    """
    notes = kb_rsi.collect(root)
    m = kb_rsi.metrics(notes=notes, root=root)
    if not m:
        return [{"tier": "t3", "action": "EVALUATE", "note": "空库"}]

    # ── P1-A: fitness 外部锚定，解耦于 T1/T2 可自抬的内部健康分 ──────────
    # 原隐患：T3 看 health 趋势 → T1 补链/retire 直接把 health 抬高 → T3 优化"系统
    #   能自抬的数字" → 自证其美、趋近 Model Collapse。现改看外部锚定 fitness 趋势
    #   (多样外部源 + 真实反馈 + 少矛盾)，只有真实外部接地变好才升，自编辑刷不出正趋势。
    fit = kb_fitness.fitness(root)
    fitness_now = fit.get("fitness") if fit.get("valid") else None

    log = Path(root) / "pipeline" / ".kb_engine_proposals.jsonl"
    prev = None
    if log.exists():
        try:
            # 向后扫描取"最后一条带 fitness 的记录"作基线（而非仅 health）：
            # 组合运行时 T3 在 T1/T2 之后执行，末行可能被 T2 记录占用；取 fitness
            # （外部锚定）避免被 T1/T2 自编辑刷出的正趋势误导趋势判断。
            for line in reversed(log.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict) and rec.get("fitness") is not None:
                    prev = rec
                    break
        except (OSError, json.JSONDecodeError):
            prev = None

    fitness_trend = None
    if prev and prev.get("fitness") is not None and fitness_now is not None:
        fitness_trend = round(fitness_now - prev["fitness"], 4)

    health = health_score(m, cfg)
    health_trend = None
    if prev and prev.get("health") is not None:
        health_trend = round(health - prev["health"], 1)

    grounded = _grounded(m)
    total = m["total"] or 1
    base = [{"tier": "t3", "action": "EVALUATE",
             "fitness": fitness_now, "fitness_trend": fitness_trend,
             "health": health, "health_trend": health_trend,
             "orphan_pct": round(100 * len(m.get("orphans", [])) / total, 1),
             "stale_pct": round(100 * len(m.get("stale", [])) / total, 1),
             "external_grounding_ok": grounded,
             "t3_enabled": cfg.get("t3_enabled", False),
             "fitness_components": fit.get("components", {}),
             "note": ("调参依据=外部锚定 fitness 趋势（非可自抬的 health）；"
                      "fitness 不变/下降 → 收缩，防闭环自我膨胀")}]

    if not do_apply or not cfg.get("t3_enabled", False):
        base[0]["action"] = "T3_DISABLED" if not cfg.get("t3_enabled") else "EVALUATE"
        return base

    # ── A/B 自调参验证机制 ──────────────────────────────────
    # 与上次 baseline fitness 对比，计算 delta；连续 3 次负 delta → 回滚（不调参）
    ab_snapshot = None
    ab_rollback = False
    if fitness_now is not None:
        last_ab = _load_last_ab_snapshot(Path(root))
        baseline_fitness = last_ab.get("tuned_fitness") if last_ab else None
        if baseline_fitness is not None:
            delta = round(fitness_now - baseline_fitness, 4)
            ab_snapshot = {"tuned_fitness": fitness_now,
                           "baseline_fitness": baseline_fitness, "delta": delta}
            if delta < 0:
                neg_count = _count_consecutive_neg_delta(Path(root))
                if neg_count >= 3:
                    ab_rollback = True
                    base.append({"tier": "t3", "action": "AB_ROLLBACK",
                                 "reason": f"连续 {neg_count} 次 delta 为负，自动回滚（不执行调参）",
                                 "ab_snapshot": ab_snapshot})
        else:
            # 首次运行，无 baseline，记录当前 fitness 作为下次 baseline
            ab_snapshot = {"tuned_fitness": fitness_now,
                           "baseline_fitness": fitness_now, "delta": 0.0}

    # 真实调参：fitness(外部锚定)停滞/下降（真外部接地未改善）→ 收缩步长+收紧质量门。
    #   即使 internal health 在升（T1/T2 自编辑的结果），fitness 不升也不膨胀 → 解耦 Goodhart。
    #   无历史基线（首次/无数据）则不调，保守。
    #   A/B 回滚时跳过调参。
    step = float(cfg.get("t3_step", 0.10))
    floor = float(cfg["confidence_floor"])
    if ab_rollback:
        # A/B 回滚：不执行调参，保存 ab_snapshot 供下次对比
        if ab_snapshot:
            _save_ab_snapshot(Path(root), ab_snapshot)
            base[0]["ab_snapshot"] = ab_snapshot
    elif fitness_trend is not None and fitness_trend <= 0:
        new_step = max(0.02, step * (1 - cfg["t3_step"]))   # 收缩
        new_floor = min(0.80, floor + 0.05)                  # 更严的质量门
        new_cfg = dict(cfg, t3_step=round(new_step, 3), confidence_floor=round(new_floor, 3))
        saved = dict(cfg.get("tuned", {}), last_tune=datetime.now().isoformat())
        saved["nudge"] = {"t3_step": new_step, "confidence_floor": new_floor,
                          "reason": "fitness趋势停滞/下降(外部锚定)→收缩"}
        new_cfg["tuned"] = saved
        if do_apply:
            save_config(new_cfg)
            git_backup(root, "pre-rsi-t3 tune checkpoint")
        base.append({"tier": "t3", "action": "TUNED", "from": {"step": step, "floor": floor},
                     "to": {"step": round(new_step, 3), "floor": round(new_floor, 3)},
                     "reason": "fitness(外部锚定)趋势停滞/下降 → 收缩步长+收紧质量门",
                     "rolled_back_possible": True,
                     "before": {"step": step, "floor": floor},
                     "after": {"step": round(new_step, 3), "floor": round(new_floor, 3)}})
    elif fitness_trend and fitness_trend > 0:
        base.append({"tier": "t3", "action": "T3_STABLE",
                     "reason": "fitness(外部锚定)趋势上升，维持当前参数"})

    # 保存 A/B snapshot（非回滚分支）
    if not ab_rollback and ab_snapshot:
        _save_ab_snapshot(Path(root), ab_snapshot)
        base[0]["ab_snapshot"] = ab_snapshot

    for e in base:
        log_decision(cfg, root, e["tier"], e["action"], [e.get("action")],
                     e.get("confidence", 0.0), e.get("before"), e.get("after"),
                     extra={"fitness": fitness_now, "fitness_trend": fitness_trend,
                            "health": health, "health_trend": health_trend,
                            "grounding": grounded})
    return base


# ── 编排 ──────────────────────────────────────────────────
def _log_ev(root, event, summary, detail=None):
    """把 engine 事件追加到统一演进日志（P2-2）；失败静默，不阻塞引擎。"""
    try:
        import evolution_log
        evolution_log.append(root, event, summary, detail=detail)
    except (ImportError, OSError, AttributeError, TypeError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(kb_rsi.Path(__file__).resolve().parent.parent))
    ap.add_argument("--run", default="t1,t2")
    ap.add_argument("--auto", action="store_true", help="实际写入（默认只提议）")
    ap.add_argument("--status", action="store_true", help="查看当前配置/开关")
    ap.add_argument("--reset-config", action="store_true", help="重置为默认开关(T3关)")
    ap.add_argument("--ab-report", action="store_true", help="输出 T3 A/B 调参效果报告")
    args = ap.parse_args()
    root = Path(args.root)

    cfg = load_config()

    if args.ab_report:
        print(_build_ab_report(root))
        return 0

    if args.reset_config:
        save_config(CONFIG_DEFAULT)
        print("🔄 已重置配置为默认：tiers_enabled=%s t3_enabled=%s auto_apply=%s"
              % (CONFIG_DEFAULT["tiers_enabled"], CONFIG_DEFAULT["t3_enabled"],
                 CONFIG_DEFAULT["auto_apply"]))
        return 0
    if args.status:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0

    run_tiers = [t.strip().lower() for t in args.run.split(",") if t.strip()]
    cfg["auto_apply"] = bool(args.auto)

    # T3 纳入执行的前提：在 run 列表里 且 t3_enabled=true（除非显式 --force-t3）
    effective = []
    for t in run_tiers:
        if t == "t3":
            if cfg.get("t3_enabled", False):
                effective.append(t)
            else:
                print("⏸️ T3 已跳过（开关关闭）。打开 .kb_rsi_config.json 的 t3_enabled 或下次 --run 不带 t3。")
        else:
            effective.append(t)

    if args.auto:
        print("ℹ️ 本次为 AUTO-apply（写入库）；auto_apply 是单次标志，不写回配置。")

    results = []
    print(f"🔧 RSI 引擎 run={effective} mode={'AUTO-apply' if args.auto else 'DRY(propose)'}"
          f" t3={'开' if cfg.get('t3_enabled') else '关'} floor={cfg['confidence_floor']}")

    if "t1" in effective:
        r = tier_t1(root, cfg, do_apply=args.auto)
        results += r; print(f"  T1: {len(r)} 条提议/动作")
        _log_ev(root, "T1", f"整理 {len(r)} 条提议/动作",
                detail="去重/补链/retire/收件箱归类" if args.auto else "只提议不写库")
    if "t2" in effective:
        r = tier_t2(root, cfg, do_apply=args.auto)
        results += r; print(f"  T2: {len(r)} 条提议/动作")
        _log_ev(root, "T2", f"加权 {len(r)} 条提议/动作",
                detail="外部主锚 + P1-B 多样性感知" if args.auto else "只提议不写库")
    if "t3" in effective:
        r = tier_t3(root, cfg, do_apply=args.auto)
        results += r; print(f"  T3: {len(r)} 条提议/动作")
        _log_ev(root, "T3", f"调参 {len(r)} 条提议/动作",
                detail="以 fitness 趋势为主调参依据" if args.auto else "只提议不写库")

    # 每次运行都把当前健康分写入决策日志（供 T3 趋势使用）
    notes = kb_rsi.collect(root)
    mh = kb_rsi.metrics(notes=notes, root=root)
    if mh:
        fh = kb_fitness.fitness(root)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "tier": "health", "health": health_score(mh, cfg)}
        rec["fitness"] = fh.get("fitness") if fh.get("valid") else None
        sc = kb_scale.scale_for_metrics(root)  # P3-1 规模档位 + 索引层状态
        rec["scale"] = {"bracket": sc["bracket"], "indexed_available": sc["indexed_available"],
                        "switch_ready": sc["switch_ready"], "coverage": sc["coverage"]}
        rec["concentration"] = mh["concentration"]  # P3-3 importance 集中度护栏
        with open(Path(root) / "pipeline" / ".kb_engine_proposals.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _log_ev(root, "HEALTH",
                f"health {health_score(mh, cfg)} · fitness {rec.get('fitness') if rec.get('fitness') is not None else 'n/a'} · 集中度 {mh['concentration']['simpson'] or 'n/a'}",
                detail=(f"档位 {sc['bracket']} · 索引层{'可用' if sc['indexed_available'] else '未建'}"
                        + (" · 趋势供 T3 调参" if mh else " · 库空/无健康分")))

    print(f"\n✅ 本次共 {len(results)} 条。完整决策日志 → pipeline/.kb_engine_proposals.jsonl")
    print("💡 提示：--auto 才会写库；所有写入均有 git checkpoint 可回滚。T3 默认关闭。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
