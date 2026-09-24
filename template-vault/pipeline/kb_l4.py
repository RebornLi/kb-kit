#!/usr/bin/env python3
# ============================================================
# kb_l4.py —— L4 宪法（反馈阶梯 · 人工裁判层）
# ------------------------------------------------------------
# 使命：让“反馈阶梯本身”可被真人所裁判——
#   L0/L1/L2/L3 各自优化一个*代理信号*（proxy）：漏检 token / 再问 / 外部接地 / 漏检率。
#   代理会失真（Goodhart）——某 rung 自己的“有效”未必真是现实要的好。谁来判断？
#   L4 是反馈阶梯 Rungs 的第五级（宪法层 · 人工裁判）：由**真人**逐条裁决低层的自动
#   调整是否真的正确，并把“连续被裁决为错”的 rung 冻结（宪法 veto）。
#
#     L0 检索表层修正（kb_retriage）← 已完成
#     L1 查询即反馈（kb_adaptretrieve）← 已完成
#     L2 自我加权（kb_selfweight）← 已完成
#     L3 元调参（kb_meta）← 已完成
#     L4 宪法（人工裁判）← 本文件
#
#   与 L3 的同构（重要）：
#     · L3 在「指标负 delta」上回滚旋钮 —— 自动、只看漏检率。
#     · L4 在「人类裁决负」上冻结 rung —— 人工监督、看真人判对错。
#     · L3 = 指标负回滚；L4 = 裁决负回滚。真人裁决是比任何代理都硬的外部锚。
#
#  铁律（与 kb_adaptretrieve / kb_selfweight / kb_retriage / kb_meta 完全一致）：
#    · 外部锚最硬：L4 的信号 = 真人显式裁决（`judge` CLI 逐条传入，--judge <谁>），
#       非 self-impact 打分、非 rung 自述“我有用”。 rung 自己说有用 ≠ 宪法认可。
#    · 接地阀（P0-1）：知识库疑似自闭时，冻结 L4 的“内部自改”——由系统*自动*冻结 rung
#       只会让闭锁系统更锁死（同理 L3 松覆盖门给回声室加杠杆）。但**仍接收真人裁决**
#       （裁决本身就是外部锚，闭库里的人判也对）。即：未接地 → 不自动冻，但记录仍写。
#    · 有界：每 rung 信任度 ∈[0,1]；连续错 >= L4_FREEZE_CONSEC 才冻；至少 L4_MIN_SAMPLE
#       条裁决才具统计意义（绝不因 1 条错裁决就冻 rung）。freeze 只写配置+checkpoint，
#       不自动 kill 任何 rung（人工在环：冻结=“该 rung 进入人工复核”，需人工 unfreeze）。
#    · raw/ 不改：L4 从不碰笔记、不改任何 rung 的笔记正文；只写裁决账本 + 冻结配置。
#    · best-effort：无裁决 / 无 rung ledger → 静默降级（信任度=unknown，不冻结），绝不锁死流水线。
#    · 可回滚：冻结配置写前 force-add 作回滚锚，单次 git checkpoint。
#
#   裁决值：`correct` / `wrong` / `ignore`（ignore 仅记档，不进信任度分母）。
#   每 rung 调整键（docket key）：l0/l2 用 note rel；l1 用 `action::note`；l3 用 knob 名。
#
#   用法:
#     python3 pipeline/kb_l4.py docket --root R          # 只读：汇编“已应用·待裁决”清单
#     python3 pipeline/kb_l4.py report --root R           # 只读：接地阀 + 每 rung 信任 + 冻结
#     python3 pipeline/kb_l4.py judge  --root R --rung <l0|l1|l2|l3> \
#                                              --key <KEY> [--rel R] [--knob K] \
#                                              [--verb correct|wrong|ignore] [--judge WHO]
#     python3 pipeline/kb_l4.py freeze --root R           # 连续负裁决 → 冻结 rung（接地阀门控）
#     python3 pipeline/kb_l4.py unfreeze --root R --rung X # 人工 lift 冻结（宪法否决需人工解除）
#     python3 pipeline/kb_l4.py status --root R [--json]  # 完整裁决账本 + 信任 + 冻结态
# ============================================================
import argparse, json, subprocess, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import kb_rsi  # 复用 collect/metrics（接地阀 P0-1）

# 常量单一事实源，收敛在 kb_constants（与 L0/L2/L3 常量同中心）
from kb_constants import (
    L4_RUNGS, L4_FREEZE_CONSEC, L4_MIN_SAMPLE, L4_TRUST_FLOOR,
)

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)  # template-vault 根

VERDICT_FILE = ".kb_l4_verdicts.jsonl"      # 宪法裁决账本（gitignore，人工逐条写）
STATE_FILE = ".kb_l4_state.json"            # 逐 rung 信任尾部状态（gitignore）
CONFIG_FILE = ".kb_l4_config.json"          # 冻结配置（人工 freeze/unfreeze 写，gitignore）

# 各 rung 的应用账本（L4 docket 的只读来源，best-effort，缺一 rung 不爆炸）。
#   注意：账本在 vault/<rel>，读者必须相对 root 解析（非 CWD），否则跨目录跑会读错。
_L0_LEDGER = "pipeline/.kb_retriage_applied.json"    # {applied:{rel:_}}
_L1_LEDGER = "pipeline/.kb_retrieval_adjust.json"    # {adjustments:[{action,note,...}]}
_L2_LEDGER = "pipeline/.kb_selfweight_applied.json"  # {applied:{rel:_}}
_L3_LEDGER = "pipeline/.kb_meta_config.json"         # {knovals:{knob:_}}


# ── 状态 / 文件路径 ──────────────────────────────────────────
def _p(rel_file: str) -> Path:
    return Path(rel_file)


def _verdict_path(root: Union[str, Path]) -> Path:
    return Path(root) / VERDICT_FILE


def _state_path(root: Union[str, Path]) -> Path:
    return Path(root) / STATE_FILE


def _config_path(root: Union[str, Path]) -> Path:
    return Path(root) / CONFIG_FILE


def _read_state(root: Union[str, Path]) -> Dict[str, Any]:
    p = _state_path(root)
    if not p.exists():
        return {"trust": {}, "consec_wrong": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("trust"), dict) and isinstance(d.get("consec_wrong"), dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"trust": {}, "consec_wrong": {}}


def _write_state(root: Union[str, Path], state: Dict[str, Any]) -> None:
    p = _state_path(root)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_config(root: Union[str, Path]) -> Dict[str, Any]:
    p = _config_path(root)
    if not p.exists():
        return {"frozen": {r: False for r in L4_RUNGS}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("frozen"), dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"frozen": {r: False for r in L4_RUNGS}}


def _write_config(root: Union[str, Path], config: Dict[str, Any]) -> None:
    p = _config_path(root)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _checkpoint(root: Union[str, Path], message: str, paths: List[Path]) -> bool:
    """把所选 transient 文件 force-add（作回滚锚）并原子提交。返回是否真的提交。"""
    for pt in paths:
        if pt.exists():
            subprocess.run(["git", "-C", str(root), "add", "--force", "--", str(pt)],
                           capture_output=True)
    if not subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip():
        return False
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message],
                   capture_output=True)
    return True


# ── 接地阀（P0-1，复用 kb_rsi 指标，与 L0/L2/L3 完全一致）────────────────────
def grounding(root: Union[str, Path]) -> Tuple[bool, str, Dict[str, Any]]:
    m = kb_rsi.metrics(kb_rsi.collect(root), root=root)
    if not m:
        return False, "库为空或无编译笔记，无法评估接地", {}
    grounded = m["external_inflow"] >= 5.0 and m["cross_ratio"] >= 0.15
    if grounded:
        note = f"外部接地正常（新鲜外部源 {m['external_inflow']}%≥5% · 跨域 {m['cross_ratio']}≥0.15）"
    else:
        note = (f"⚠️ 外部接地不足（新鲜外部源 {m['external_inflow']}%<5% 或 跨域 {m['cross_ratio']}<0.15）"
                "→ 知识库疑似自闭，冻结内部自动冻结逻辑（系统自动冻 rung 只会让闭锁更锁死；真人裁决仍收）")
    return grounded, note, m


# ── 裁决账本（只增，真人逐条写）─────────────────────────────────────
def _read_verdicts(root: Union[str, Path]) -> List[Dict[str, Any]]:
    p = _verdict_path(root)
    if not p.exists():
        return []
    try:
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def _write_verdict(root: Union[str, Path], rec: Dict[str, Any]) -> None:
    p = _verdict_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── docket：只读汇编各 rung “已应用·待裁决”清单（best-effort）───────────────────
#   每个 rung 读者独立 try/except，缺/损坏账本 → 该 rung 贡献 0 条，绝不爆炸。
def _docket_l0(root: Union[str, Path]) -> List[Dict[str, Any]]:
    try:
        d = json.loads((Path(root) / _L0_LEDGER).read_text(encoding="utf-8"))
        return [{"rung": "l0", "key": rel, "kind": "tags/summary", "rel": rel}
                for rel in (d.get("applied") or {}) if rel]
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _docket_l1(root: Union[str, Path]) -> List[Dict[str, Any]]:
    try:
        d = json.loads((Path(root) / _L1_LEDGER).read_text(encoding="utf-8"))
        out = []
        for a in (d.get("adjustments") or []):
            rel = a.get("note")
            if not rel:
                continue
            action = a.get("action", "boost")
            out.append({"rung": "l1", "key": f"{action}::{rel}", "kind": "adjust", "rel": rel})
        return out
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _docket_l2(root: Union[str, Path]) -> List[Dict[str, Any]]:
    try:
        d = json.loads((Path(root) / _L2_LEDGER).read_text(encoding="utf-8"))
        return [{"rung": "l2", "key": rel, "kind": "importance", "rel": rel}
                for rel in (d.get("applied") or {}) if rel]
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _docket_l3(root: Union[str, Path]) -> List[Dict[str, Any]]:
    try:
        d = json.loads((Path(root) / _L3_LEDGER).read_text(encoding="utf-8"))
        return [{"rung": "l3", "key": k, "kind": "knob", "rel": None}
                for k in (d.get("knovals") or {})]
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


_DOCKET_READERS = {"l0": _docket_l0, "l1": _docket_l1, "l2": _docket_l2, "l3": _docket_l3}


def docket(root: Union[str, Path]) -> Dict[str, Any]:
    """返回“已应用·待裁决”清单（每条目带最新裁决或 None）。best-effort 汇编，缺一 rung 不炸。"""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for v in _read_verdicts(root):
        rk = (v.get("rung"), v.get("key"))
        cur = latest.get(rk)
        # 时间序靠后者覆盖（裁决可改判）
        if cur is None or str(v.get("ts", "")) >= str(cur.get("ts", "")):
            latest[rk] = v
    items: List[Dict[str, Any]] = []
    for rung in L4_RUNGS:
        for it in _DOCKET_READERS[rung](root):
            v = latest.get((rung, it["key"]))
            items.append({**it, "verdict": v.get("verb") if v else None, "judge": v.get("judge") if v else None})
    # 排序（先未裁决·再 rung 序·key）：把最该裁的放前面
    items.sort(key=lambda x: (x["verdict"] is not None, L4_RUNGS.index(x["rung"]), x["key"]))
    return {"grounded": grounding(root)[0], "items": items,
            "n_pending": sum(1 for x in items if x["verdict"] is None)}


# ── 键构造（judge CLI 友好入口 → docket key）────────────────────────────
def build_key(rung: str, rel: Optional[str] = None,
              knob: Optional[str] = None, action: str = "boost") -> Optional[str]:
    """l0/l2 用 rel；l3 用 knob；l1 用 action::rel。缺必需字段 → None。"""
    if rung == "l0":
        return rel
    if rung == "l2":
        return rel
    if rung == "l3":
        return knob
    if rung == "l1":
        return f"{action}::{rel}" if rel else None
    return None


# ── 信任聚合：逐 rung correct/(correct+wrong)，ignore 不进分母───────────────────
def _verdict_stats(verdicts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    per_rung: Dict[str, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "wrong": 0})
    for v in verdicts:
        r = v.get("rung")
        verb = v.get("verb")
        if verb == "correct":
            per_rung[r]["correct"] += 1
        elif verb == "wrong":
            per_rung[r]["wrong"] += 1
        # ignore → 仅记档，不计信任
    return per_rung


def _tail_consecutive_wrong(per_rung: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    """逐 rung：按 ts 升序后，尾部连续 wrong 的次数（遇到 correct/ignore 打断）"""
    out: Dict[str, int] = {}
    for r, vs in per_rung.items():
        vs2 = sorted(vs, key=lambda x: x.get("ts") or "")
        tail = 0
        for v in reversed(vs2):
            if v.get("verb") == "wrong":
                tail += 1
            else:
                break
        out[r] = tail
    return out


# ── judge：记一条真人裁决（只增·不改任何笔记）────────────────────────────
def judge(root: Union[str, Path], rung: str, key: Optional[str] = None,
          rel: Optional[str] = None, knob: Optional[str] = None,
          verb: str = "correct", action: str = "boost",
          judge_by: str = "anon") -> Dict[str, Any]:
    """记一条真人裁决。可用 --key（精确）或 --rel/--knob（友好，内部建 key）。"""
    if rung not in L4_RUNGS:
        return {"ok": False, "note": f"未知 rung {rung}（允许 {L4_RUNGS}）"}
    if verb not in ("correct", "wrong", "ignore"):
        return {"ok": False, "note": f"未知裁决值 {verb}（允许 correct/wrong/ignore）"}
    if key is None:
        key = build_key(rung, rel=rel, knob=knob, action=action)
    if not key:
        return {"ok": False, "note": f"缺 key：l1 需 --rel（+--action，默认 boost）/l3 需 --knob/l0/l2 需 --rel"}
    rec = {"rung": rung, "key": key, "verb": verb, "judge": judge_by, "ts": datetime.now().isoformat(timespec="seconds")}
    _write_verdict(root, rec)
    return {"ok": True, "recorded": rec, "note": f"裁决已记：{rung} {key} = {verb}（{judge_by}）"}


# ── freeze：连续负裁决 → 冻结 rung（接地阀门控·不自动 kill rung）──────────────────
def freeze(root: Union[str, Path]) -> Dict[str, Any]:
    """接地阀：未接地 → 不自动冻（仅记录）。已接地 → 尾连续错 >= L4_FREEZE_CONSEC 且达样本量才冻。"""
    grounded, ground_note, _ = grounding(root)
    config = _read_config(root)
    frozen = dict(config.get("frozen", {r: False for r in L4_RUNGS}))
    verdicts = _read_verdicts(root)
    per_rung = _verdict_stats(verdicts)
    per_rung_list: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in verdicts:
        per_rung_list[v.get("rung")].append(v)
    consec = _tail_consecutive_wrong(per_rung_list)

    newly: List[Dict[str, Any]] = []
    for r in L4_RUNGS:
        st = per_rung.get(r, {"correct": 0, "wrong": 0})
        n = st["correct"] + st["wrong"]
        should = (consec.get(r, 0) >= L4_FREEZE_CONSEC) and (n >= L4_MIN_SAMPLE)
        # 只在“该冻而当前未冻”时记录一次（避免每次重复合并）
        if should and not frozen.get(r):
            frozen[r] = True
            newly.append({"rung": r, "consecutive_wrong": consec.get(r, 0),
                          "n_verdicts": n, "trust": round(st["correct"] / n, 3) if n else None})
    if newly and grounded:
        cfg = {"frozen": frozen, "updated": datetime.now().isoformat(timespec="seconds"),
               "reason": f"连续负裁决冻结 {len(newly)} 个 rung（{', '.join(x['rung'] for x in newly)}）"}
        _write_config(root, cfg)
        _checkpoint(root, f"kb: L4 宪法冻结 {', '.join(x['rung'] for x in newly)} 个 rung（连续负裁决）",
                    [_config_path(root), _state_path(root)])
    else:
        cfg = {"frozen": frozen, "updated": datetime.now().isoformat(timespec="seconds"),
               "note": "无新增冻结（" + ("接地不足·不自动冻" if not grounded else "未达连续/样本门槛") + ")"}
        _write_config(root, cfg)

    # 同步尾部状态到 state（供 status/render 快速查看）
    trust = {}
    for r in L4_RUNGS:
        st = per_rung.get(r, {"correct": 0, "wrong": 0})
        n = st["correct"] + st["wrong"]
        trust[r] = {"correct": st["correct"], "wrong": st["wrong"], "n": n,
                    "trust": round(st["correct"] / n, 3) if n else None,
                    "consecutive_wrong": consec.get(r, 0)}
    _write_state(root, {"trust": trust, "consec_wrong": consec})

    note = "✅ 冻结态已更新"
    if not grounded:
        note = f"⚠️ 接地不足 → 未自动冻 rung（仍记录裁决）：{ground_note}"
    elif not newly:
        note = "✅ 无新增冻结（未达连续负裁决/样本门槛，或已全部冻结）"
    return {"grounded": grounded, "ground_note": ground_note, "frozen": frozen,
            "newly_frozen": newly, "trust": trust, "note": note}


# ── unfreeze：人工 lift 冻结（宪法否决需人工解除）────────────────────────────
def unfreeze(root: Union[str, Path], rung: str) -> Dict[str, Any]:
    if rung not in L4_RUNGS:
        return {"ok": False, "note": f"未知 rung {rung}（允许 {L4_RUNGS}）"}
    config = _read_config(root)
    frozen = dict(config.get("frozen", {r: False for r in L4_RUNGS}))
    if not frozen.get(rung):
        return {"ok": True, "note": f"{rung} 本未冻结"}
    frozen[rung] = False
    cfg = {**config, "frozen": frozen, "updated": datetime.now().isoformat(timespec="seconds"),
           "reason": f"人工 unfreeze {rung}（宪法否决解除）"}
    _write_config(root, cfg)
    _checkpoint(root, f"kb: L4 人工解冻 {rung}", [_config_path(root), _state_path(root)])
    return {"ok": True, "rung": rung, "note": f"已人工解冻 {rung}"}


# ── status / report ────────────────────────────────────────────────────
def status(root: Union[str, Path]) -> Dict[str, Any]:
    grounded, ground_note, _ = grounding(root)
    verdicts = _read_verdicts(root)
    per_rung = _verdict_stats(verdicts)
    per_rung_list: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in verdicts:
        per_rung_list[v.get("rung")].append(v)
    consec = _tail_consecutive_wrong(per_rung_list)
    trust = {}
    for r in L4_RUNGS:
        st = per_rung.get(r, {"correct": 0, "wrong": 0})
        n = st["correct"] + st["wrong"]
        trust[r] = {"correct": st["correct"], "wrong": st["wrong"], "n": n,
                    "trust": round(st["correct"] / n, 3) if n else None,
                    "consecutive_wrong": consec.get(r, 0),
                    "significant": n >= L4_MIN_SAMPLE}
    config = _read_config(root)
    frozen = config.get("frozen", {r: False for r in L4_RUNGS})
    docket_items = docket(root)["items"]
    latest: Dict[Tuple[str, str], Optional[str]] = {}
    for it in docket_items:
        latest[(it["rung"], it["key"])] = it.get("verdict")
    return {"grounded": grounded, "ground_note": ground_note, "trust": trust,
            "frozen": frozen, "docket_count": len(docket_items),
            "n_pending": sum(1 for it in docket_items if it["verdict"] is None),
            "docket": docket_items, "latest_verdict": latest,
            "n_verdicts": len(verdicts)}


def render(root: Union[str, Path], summary: Optional[Dict[str, Any]] = None) -> str:
    s = status(root)
    L = ["# ⚖️ L4 宪法（反馈阶梯 · 人工裁判层）", "",
         f"> 接地阀: **{s['grounded']}** — {s['ground_note']}",
         f"> 真人裁决 {s['n_verdicts']} 条 · 待裁 {s['n_pending']} 条（docket）"
         f" · 连续错 ≥{L4_FREEZE_CONSEC} 且达样本（≥{L4_MIN_SAMPLE}）→ 冻结 rung（人工解冻）"]

    # 信任表
    L += ["", "## 🧑‖ 每 rung 信任度（真人裁决聚合）", ""]
    L.append("| rung | 裁决数 | correct | wrong | 信任度 | 连续错 | 统计有效 | 冻结 |")
    L.append("|------|-------|---------|-------|--------|--------|---------|------|")
    for r in L4_RUNGS:
        t = s["trust"].get(r, {})
        trust = t.get("trust")
        trust_s = "—" if trust is None else f"{trust:.2f}"
        frozen = "⛔**是**" if s["frozen"].get(r) else "否"
        L.append(f"| `{r}` | {t.get('n', 0)} | {t.get('correct', 0)} | {t.get('wrong', 0)} "
                 f"| {trust_s} | {t.get('consecutive_wrong', 0)} "
                 f"| {'是' if t.get('significant') else '否'} | {frozen} |")

    # 冻结告警
    if any(s["frozen"].values()):
        frz = [r for r in L4_RUNGS if s["frozen"].get(r)]
        L += ["", f"⛔ **宪法已冻结 rung: {', '.join(frz)}** —— 进入人工复核，需 `unfreeze --rung X` 解除。"]

    if not s["grounded"]:
        L += ["", "⚠️ 接地不足 → 系统不*自动*冻 rung（真人裁决照常记录）。"]

    # docket 清单
    if s["docket"]:
        L += ["", f"## 📋 待裁清单（{s['n_pending']} 条待裁决 / {len(s['docket'])} 条已应用）", ""]
        for it in s["docket"]:
            mark = "⬜待裁" if it["verdict"] is None else f"🟢{it['verdict']}"
            tgt = it["rel"] or it["key"]
            L.append(f"- [{mark}] `{it['rung']}` {it['kind']} — `{tgt}`")
        L += ["", "  裁决：`judge --rung <r> --key <KEY> --verb correct|wrong|ignore [--judge <谁>]`"]
    else:
        L += ["", "✅ 暂无 rung 应用账本可裁（各 rung 先 apply 出账本；或账本缺失/best-effort 降级）。"]
    return "\n".join(L) + "\n"


# ── CLI ─────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="L4 宪法（反馈阶梯 · 人工裁判层）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("report", "docket"):
        sp = sub.add_parser(name); sp.add_argument("--root", required=True)

    j = sub.add_parser("judge")
    j.add_argument("--root", required=True); j.add_argument("--rung", required=True)
    j.add_argument("--key", default=None)
    j.add_argument("--rel", default=None); j.add_argument("--knob", default=None)
    j.add_argument("--verb", default="correct", choices=["correct", "wrong", "ignore"])
    j.add_argument("--action", default="boost")
    j.add_argument("--judge", default="anon")

    fr = sub.add_parser("freeze"); fr.add_argument("--root", required=True)
    uf = sub.add_parser("unfreeze"); uf.add_argument("--root", required=True); uf.add_argument("--rung", required=True)

    st = sub.add_parser("status"); st.add_argument("--root", required=True); st.add_argument("--json", action="store_true")

    a = ap.parse_args()
    root = a.root
    if a.cmd == "report":
        print(render(root, status(root)))
        return 0
    if a.cmd == "docket":
        d = docket(root)
        print(render(root))
        print(f"\n## 📋 待裁清单（{d['n_pending']} 待裁决 / {len(d['items'])} 已应用）")
        for it in d["items"]:
            mark = "⬜待裁" if it["verdict"] is None else f"🟢{it['verdict']}"
            tgt = it["rel"] or it["key"]
            print(f"- [{mark}] `{it['rung']}` {it['kind']} — `{tgt}`")
        return 0
    if a.cmd == "judge":
        r = judge(root, a.rung, key=a.key, rel=a.rel, knob=a.knob, verb=a.verb,
                  action=a.action, judge_by=a.judge)
        print(f"{'✅' if r.get('ok') else '❌'} {r.get('note', '')}")
        return 0 if r.get("ok") else 1
    if a.cmd == "freeze":
        r = freeze(root)
        print(render(root))
        if r["newly_frozen"]:
            print(f"\n↩️ 已冻结 {len(r['newly_frozen'])} 个 rung（连续负裁决）：")
            for nf in r["newly_frozen"]:
                print(f"   {nf['rung']}：连续错 {nf['consecutive_wrong']} · 样本 {nf['n_verdicts']} · 信任 {nf['trust']}")
        else:
            print(f"\n备注: {r['note']}")
        return 0
    if a.cmd == "unfreeze":
        r = unfreeze(root, a.rung)
        print(f"{'✅' if r.get('ok') else '❌'} {r.get('note', '')}")
        return 0
    if a.cmd == "status":
        stt = status(root)
        print(json.dumps(stt, ensure_ascii=False, indent=2) if getattr(a, "json", False) else render(root))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
