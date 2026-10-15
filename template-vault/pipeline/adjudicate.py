#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adjudicate.py — 用**本地模型**自主裁决 L0–L5 提案。

## 为什么要有它

提案队列此前只能人工 `apply --id N`。实测（2026-10-16）842 条待裁决 —— 人工不可能逐条看，
结果是**队列只涨不清**（L2 一次就积 776 条）。本模块把"裁决"交给本地模型，
但**不是让模型随便点同意**，而是分两类证据：

  · **L1 有 ground truth**：可以**实测**「加了这条契约，目标笔记的排名会不会变好」。
    这是客观证据，比任何模型判断都硬。LLM 只补一个模型做不了的判断：
    「这条笔记到底是不是这个问题该命中的答案」（语义相关性 ≠ 相似度）。
    → **实测通过 + LLM 认为相关**，两者都过才落地。

  · **L2/L3 没有 ground truth**：只能靠 LLM 判「这个 importance 提升 / 这个旋钮调整是否合理」。
    → LLM 判断 + **硬边界**（单次上限、只升不降、raw/ 不改、写前 checkpoint）+ 全量账本 + 可回滚。

## 安全边界（有实测依据，不是保守）

L1 契约正确时黄金集 recall@10 98.3%→100%；但契约指向无关笔记时 top1 90.0%→**76.7%**、
MRR 0.950→**0.883**。所以：
  · 每一级都可 `--dry-run` 预演；
  · 落地必经**账本**（`kb_ladder_verdicts.jsonl`：谁判的、判了什么、依据是什么、模型是谁）；
  · 端点不可用 → **不裁决**（绝不"无证据放行"）；
  · `--max` 限单次落地条数（防一次跑飞）。

用法：
    python3 pipeline/adjudicate.py run   --level L1 [--dry-run] [--max 20]
    python3 pipeline/adjudicate.py run   --all  [--dry-run]
    python3 pipeline/adjudicate.py log                    # 看裁决账本
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LEDGER = "kb_ladder_verdicts.jsonl"          # 裁决账本（审计用）
JUDGE_TIMEOUT = 180
EXCERPT = 700                                 # 送给模型判的相关文本上限（字符）

# 各级提案文件名（与各模块一致）
PROP_FILES = {
    "L0": "kb_retriage_proposals.jsonl",
    "L1": "kb_adaptretrieve_proposals.jsonl",
    "L2": "kb_selfweight_proposals.jsonl",
    "L3": "kb_meta_proposals.jsonl",
}
MAX_PER_RUN = {"L0": 20, "L1": 20, "L2": 30, "L3": 5}   # 单次落地上限（有界）


# ── 本地模型 ──────────────────────────────────────────────────
class Judge:
    """本地 chat 模型裁判（best-effort：不可用就拒绝裁决，绝不无证据放行）。"""

    def __init__(self, root: Path):
        from kb_common import model_config
        cfg = model_config(root)
        self.base = str(cfg.get("base_url") or "").rstrip("/")
        self.model = str(cfg.get("chat_model") or "")
        self.key = str(cfg.get("api_key") or "")
        self.timeout = JUDGE_TIMEOUT
        self.calls = 0
        self.truncated = 0        # 解析失败（多为被 max_tokens 截断）计数
        self.last_raw = ""        # 最近一次解析失败的原始文本（审计/调试用）
        self.empty = 0            # 模型返回空内容的次数
        self.last_finish = ""     # 最近一次空响应的 finish_reason（length=额度被推理吃光）

    def available(self) -> bool:
        if not self.base or not self.model:
            return False
        try:
            req = urllib.request.Request(f"{self.base}/models",
                                         headers={"Authorization": f"Bearer {self.key}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except Exception:       # noqa: BLE001
            return False

    def ask_json(self, system: str, user: str, max_tokens: int = 3000) -> Optional[dict]:
        msg = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        base = {"model": self.model, "temperature": 0.0, "max_tokens": max_tokens,
                "messages": msg}
        # 先试结构化输出；不支持 response_format 的端点会报错 → 退回纯文本再解析。
        for extra in ({"response_format": {"type": "json_object"}}, {}):
            payload = json.dumps({**base, **extra}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base}/chat/completions", data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.key}"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
            except Exception:   # noqa: BLE001  端点不支持该参数/网络失败 → 换一种再试
                continue
            self.calls += 1
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            fin = str(choice.get("finish_reason") or "")
            txt = (msg.get("content") or "").strip()
            if not txt:
                # ⚠️ 本地裁判是**推理模型**（实测：每次先生成约 1400 字 reasoning_content）。
                # 推理 token 会占用 max_tokens —— 额度不够时 content 被截成空
                # （finish_reason='length'）。这是首版 40/50 次"判不了"的真因。
                # 兜底：结论常常已经写在推理文本里，从里面抠 JSON。
                reason_txt = str(msg.get("reasoning_content") or msg.get("reasoning") or "")
                if reason_txt:
                    import re as _re
                    m = _re.search(r'\{[^{}]*"approve"[^{}]*\}', reason_txt, _re.S)
                    if m:
                        try:
                            return json.loads(m.group(0))
                        except json.JSONDecodeError:
                            pass
                self.empty += 1
                self.last_finish = fin
                if max_tokens < 8000:      # 额度不够 → 加倍重试（推理模型需要余量）
                    return self.ask_json(system, user, max_tokens=max_tokens * 2)
                continue
            if txt.startswith("```"):
                txt = txt.split("```")[1] if len(txt.split("```")) > 1 else txt
                txt = txt.removeprefix("json").strip()
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                # 兜底 1：从自由文本里抠出第一个 {...}（模型偶尔带前言）
                import re as _re
                m = _re.search(r"\{.*\}", txt, _re.S)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except json.JSONDecodeError:
                        pass
                # 兜底 2：**截断**（实测最常见：理由写长了被 max_tokens 砍断）。
                #   用更短的提示 + 上限翻倍重试一次；仍失败才认输。
                self.truncated += 1
                self.last_raw = txt[:300]
                if max_tokens < 800:
                    return self.ask_json(
                        system + "\n**只输出一行 JSON，reason 不超过 15 个字。**",
                        user[:800], max_tokens=800)
                return None
        return None


# ── 读提案 / 读笔记 ───────────────────────────────────────────
def _state_dir(root: Path) -> Path:
    return Path(root) / ".kb" / "state"


def load_proposals(root: Path, level: str) -> List[Dict[str, Any]]:
    p = _state_dir(root) / PROP_FILES.get(level, "")
    legacy = Path(root) / "pipeline" / ("." + PROP_FILES.get(level, ""))
    for cand in (p, legacy):
        if cand.exists():
            try:
                return [json.loads(l) for l in cand.read_text(encoding="utf-8").splitlines() if l.strip()]
            except (OSError, json.JSONDecodeError):
                return []
    return []


def _excerpt(root: Path, rel: str) -> str:
    try:
        import re
        t = (Path(root) / rel).read_text(encoding="utf-8")
        return re.sub(r"^---\s*\n.*?\n---\s*\n", "", t, flags=re.S)[:EXCERPT]
    except OSError:
        return ""


def _title(root: Path, rel: str) -> str:
    try:
        import re
        t = (Path(root) / rel).read_text(encoding="utf-8")
        m = re.search(r"^title:\s*(.+)$", t, re.M)
        if m:
            return m.group(1).strip().strip('"\'')
        m = re.search(r"^kb_summary:\s*(.+)$", t, re.M)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return Path(rel).stem


# ── L1：实测 + LLM 语义双闸 ───────────────────────────────────
def _rank_of(root: Path, q: str, target: str, top: int = 10) -> Optional[int]:
    """跑一次真实检索，返回 target 的排名（不在 top 内 → None）。"""
    cache = _state_dir(root) / "query_cache.json"
    try:
        cache.unlink()
    except OSError:
        pass
    env = {**os.environ}
    env.pop("KB_ROOT", None)
    try:
        r = subprocess.run([sys.executable, str(Path(root) / "pipeline" / "rag.py"), "query", q,
                            "--root", str(root), "--top", str(top), "--json", "--no-record"],
                           capture_output=True, text=True, env=env, timeout=300)
        hits = [h["path"] for h in (json.loads(r.stdout[r.stdout.find("{"):]).get("hits") or [])]
        return hits.index(target) + 1 if target in hits else None
    except Exception:           # noqa: BLE001
        return None


def _write_adjust(root: Path, rows: List[Dict[str, Any]]) -> None:
    from kb_common import atomic_write_text
    from state_manager import state_path
    atomic_write_text(state_path(root, "kb_retrieval_adjust.json"), json.dumps(
        {"adjustments": [{"query": r["query"], "action": "boost", "note": r["note"],
                          "sim": r.get("sim"), "issue": r.get("issue"),
                          "verdict": r.get("_verdict")} for r in rows]},
        ensure_ascii=False, indent=2))


def adjudicate_l1(root: Path, judge: Judge, dry: bool, limit: int) -> List[Dict[str, Any]]:
    props = load_proposals(root, "L1")
    if not props:
        return []
    existing = []
    adj_path = _state_dir(root) / "kb_retrieval_adjust.json"
    if adj_path.exists():
        try:
            existing = (json.loads(adj_path.read_text(encoding="utf-8")) or {}).get("adjustments") or []
        except (OSError, json.JSONDecodeError):
            existing = []
    have = {(a.get("note"), a.get("query")) for a in existing}
    todo = [r for r in props if (r.get("note"), r.get("query")) not in have][:limit]

    sys_prompt = ("你在裁决一个个人知识库的检索修正提议。用户反复问同一个问题，说明检索没给对答案。"
                  "现在有人提议：对这个查询，把某篇笔记在排序里往前拉。"
                  "请判断：**这篇笔记是否确实是这个查询应有的答案**（不是'主题相近'，而是'能回答它'）。"
                  '只输出 JSON：{"approve": true/false, "reason": "20字内理由"}')
    out = []
    for r in todo:
        q, note = r.get("query", ""), r.get("note", "")
        title, body = _title(root, note), _excerpt(root, note)
        # 闸①：LLM 语义相关性（相似度 ≠ 相关性）
        v = judge.ask_json(sys_prompt, f"查询：{q}\n\n候选笔记标题：{title}\n\n候选笔记正文：\n{body}")
        if not v:
            out.append({**r, "_verdict": "skip:judge-unavailable", "_approved": False})
            continue
        approve = bool(v.get("approve"))
        reason = str(v.get("reason") or "")[:80]
        rec = {**r, "_llm": {"approve": approve, "reason": reason}}
        if not approve:
            rec["_approved"] = False
            rec["_verdict"] = f"reject:llm:{reason}"
            out.append(rec)
            continue
        # 闸②：**实测**（ground truth）——加契约前后目标排名是否真的变好
        before = _rank_of(root, q, note)
        _write_adjust(root, [*existing, *[x for x in out if x.get("_approved")], r])
        after = _rank_of(root, q, note)
        improved = after is not None and (before is None or after < before)
        rec["_measured"] = {"before": before, "after": after}
        rec["_approved"] = bool(improved)
        rec["_verdict"] = (f"approve:measured {before}->{after}·llm:{reason}" if improved
                           else f"reject:no-measured-gain {before}->{after}")
        out.append(rec)
    if not dry:
        keep = [*existing, *[{k: v for k, v in x.items() if not k.startswith("_")}
                             for x in out if x.get("_approved")]]
        _write_adjust(root, keep)
    else:
        _write_adjust(root, existing)       # 预演：还原
    return out


# ── L2/L3：LLM 判断（无 ground truth，靠边界 + 账本兜底）────────
def adjudicate_l2(root: Path, judge: Judge, dry: bool, limit: int) -> List[Dict[str, Any]]:
    props = load_proposals(root, "L2")
    if not props:
        return []
    props = sorted(props, key=lambda r: -float(r.get("signal") or 0))[:limit]
    sys_prompt = ("你在裁决个人知识库的 importance 调升提议。importance 影响复习优先级与排序。"
                  "请判断：**这篇笔记是否真的值得提高 importance**。"
                  "注意：模板、目录页、项目自身的 README/CHANGELOG 这类'过程产物'不该提高；"
                  "承载可复用知识、被反复命中的笔记才该提高。"
                  '只输出 JSON：{"approve": true/false, "reason": "20字内理由"}')
    out = []
    for r in props:
        rel = r.get("rel", "")
        v = judge.ask_json(sys_prompt,
                           f"笔记：{rel}\n标题：{_title(root, rel)}\n"
                           f"importance：{r.get('old_importance')} → {r.get('new_importance')}\n"
                           f"信号：{r.get('signal')}（命中 {r.get('hits')} · 入链 {r.get('inbound')}）\n"
                           f"系统给的判据：{r.get('gate')}\n\n正文：\n{_excerpt(root, rel)}")
        if not v:
            out.append({**r, "_verdict": "skip:judge-unavailable", "_approved": False})
            continue
        ok = bool(v.get("approve"))
        out.append({**r, "_approved": ok,
                    "_verdict": f"{'approve' if ok else 'reject'}:llm:{str(v.get('reason') or '')[:80]}"})
    return out


def adjudicate_l3(root: Path, judge: Judge, dry: bool, limit: int) -> List[Dict[str, Any]]:
    props = load_proposals(root, "L3")
    if not props:
        return []
    sys_prompt = ("你在裁决自我调节阶梯的**内部旋钮**调整提议。这些旋钮只影响阶梯自身行为，"
                  "不影响笔记内容。请判断：给出的调整方向与理由是否自洽、幅度是否合理。"
                  '只输出 JSON：{"approve": true/false, "reason": "20字内理由"}')
    out = []
    for r in props[:limit]:
        v = judge.ask_json(sys_prompt, json.dumps(r, ensure_ascii=False)[:1500])
        if not v:
            out.append({**r, "_verdict": "skip:judge-unavailable", "_approved": False})
            continue
        ok = bool(v.get("approve"))
        out.append({**r, "_approved": ok,
                    "_verdict": f"{'approve' if ok else 'reject'}:llm:{str(v.get('reason') or '')[:80]}"})
    return out


ADJUDICATORS = {"L1": adjudicate_l1, "L2": adjudicate_l2, "L3": adjudicate_l3}


def apply_level(root: Path, level: str, approved: List[Dict[str, Any]]) -> Dict[str, Any]:
    """落地已批准的提案（复用各模块自己的 apply，保留其安全边界）。"""
    if not approved:
        return {"applied": 0}
    ids = [str(r.get("id")) for r in approved if r.get("id") is not None]
    if level == "L1":
        return {"applied": len(approved), "note": "L1 由 adjudicate 直接写入契约文件"}
    mod = {"L2": "kb_selfweight", "L3": "kb_meta"}.get(level)
    if not mod:
        return {"applied": 0}
    m = __import__(mod)
    try:
        m.apply(root, ids=ids)
    except Exception as e:      # noqa: BLE001
        return {"applied": 0, "error": f"{type(e).__name__}: {str(e)[:100]}"}
    return {"applied": len(ids)}


def append_ledger(root: Path, rows: List[Dict[str, Any]], level: str, judge: Judge,
                  applied: int) -> None:
    """写裁决账本 —— 谁判的、判了什么、依据、模型身份，全部留痕（可审计/可复盘）。"""
    if not rows:
        return
    from state_manager import append_jsonl
    ts = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        append_jsonl(root, LEDGER, {
            "ts": ts, "level": level, "judge": f"{judge.model}@local",
            "id": r.get("id"), "target": r.get("note") or r.get("rel") or r.get("knob"),
            "query": r.get("query"), "approved": bool(r.get("_approved")),
            "verdict": r.get("_verdict"), "measured": r.get("_measured"),
            "llm": r.get("_llm"), "applied": bool(r.get("_approved")) and applied > 0,
        })


def cmd_run(root: Path, levels: List[str], dry: bool, limit: Optional[int]) -> int:
    judge = Judge(root)
    print(f"⚖️  本地模型自主裁决 · {root}")
    print(f"   裁判模型: {judge.model or '（未配置）'} @ {judge.base or '-'}")
    if not judge.available():
        print("   ❌ 裁判模型不可用 —— **不裁决**（绝不无证据放行）。先确认本地模型端点。")
        return 2
    total_applied = 0
    for lv in levels:
        fn = ADJUDICATORS.get(lv)
        if fn is None:
            print(f"   ⏸ {lv}：无可自动裁决的提案类型（L4 需逐条 judge，L0 为建议类）")
            continue
        cap = limit if limit is not None else MAX_PER_RUN.get(lv, 20)
        print(f"\n   ── {lv}（上限 {cap} 条）──")
        rows = fn(root, judge, dry, cap)
        approved = [r for r in rows if r.get("_approved")]
        for r in rows:
            mark = "✅" if r.get("_approved") else "❌"
            print(f"     {mark} id={r.get('id')} {str(r.get('_verdict'))[:78]}")
        res = {"applied": 0}
        if not dry and approved:
            res = apply_level(root, lv, approved)
            total_applied += int(res.get("applied") or 0)
        append_ledger(root, rows, lv, judge, int(res.get("applied") or 0))
        print(f"     → 批准 {len(approved)}/{len(rows)}"
              + ("（预演，未落地）" if dry else f" · 落地 {res.get('applied', 0)}"))
    if judge.empty:
        print(f"   ℹ️ 有 {judge.empty} 次模型返回空内容（已自动换请求形态重试）")
    if judge.truncated:
        print(f"   ⚠️ 有 {judge.truncated} 次判词被截断并重试（原始文本留档：{judge.last_raw[:80]!r}）")
    print(f"\n   裁判调用 {judge.calls} 次 · 本次落地 {total_applied} 条"
          + ("（--dry-run 预演）" if dry else ""))
    print(f"   账本：.kb/state/{LEDGER}（人工可复查每一条判断）")
    return 0


def cmd_log(root: Path, n: int = 20) -> int:
    p = _state_dir(root) / LEDGER
    if not p.exists():
        print("  账本为空（还没裁决过）")
        return 0
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"⚖️  裁决账本 · 共 {len(rows)} 条 · 最近 {min(n, len(rows))} 条")
    for r in rows[-n:]:
        mark = "✅" if r.get("approved") else "❌"
        print(f"  {mark} {r['ts']} {r['level']} id={r.get('id')} {str(r.get('target'))[:40]}")
        print(f"      {str(r.get('verdict'))[:110]}")
    ok = sum(1 for r in rows if r.get("approved"))
    print(f"\n  批准率 {ok}/{len(rows)} = {ok/max(len(rows),1):.0%} · 裁判 {rows[-1].get('judge')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="用本地模型自主裁决 L0–L5 提案")
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "log"])
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or ".")
    ap.add_argument("--level", action="append", default=[], help="只裁决某级（可重复）")
    ap.add_argument("--all", action="store_true", help="裁决 L1/L2/L3")
    ap.add_argument("--dry-run", action="store_true", help="只预演，不落地")
    ap.add_argument("--max", type=int, default=None, help="单级落地上限")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if a.cmd == "log":
        return cmd_log(root)
    levels = [x.upper() for x in a.level] or (["L1", "L2", "L3"] if a.all else ["L1"])
    return cmd_run(root, levels, a.dry_run, a.max)


if __name__ == "__main__":
    sys.exit(main())
