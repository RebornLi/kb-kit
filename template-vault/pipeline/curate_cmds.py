#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""curate_cmds.py — 知识结晶的**子命令层**（P2-3 从 curate.py 拆出）。

分工：
  · `curate.py`      —— 核心不变量：`curate_one` / `verify_note` / `apply_canon`
                        / `render_canon` / `select_batch` 等。**改这些才影响质量**。
  · `curate_cmds.py` —— 本文件：把核心能力编排成五个 CLI 子命令 + 两个批量辅助
                        （`cmd_plan` / `cmd_run` / `cmd_verify` / `cmd_report` /
                        `cmd_review` / `cmd_recheck`，`_write_proposals`）。

依赖方向（单向，无环）：
    curate_cmds  →  curate（核心）
    curate       →  不 import curate_cmds（只有 `main()` 里按需转发，见下）

为什么这么拆：拆之前 curate.py 1289 行，读 `verify_note` 的校验规则时要跨过
300 行子命令样板；改子命令输出格式时又要提心 128 行的质量判据。两者变更频率
和风险完全不同 —— 一个是"文案/流程"，一个是"质量红线"。

`curate.py` 仍 re-export 本文件的全部子命令（`from curate_cmds import ...`），
保证 `kb curate ...`、`curate_plugin.py`、以及历史 `from curate import cmd_run`
调用方零改动。等调用方都迁到 `curate_cmds` 后可删除那层 re-export。
"""
import datetime
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from curate import (
    BATCH_DEFAULT, CONF_MIN, LEDGER, MAX_RATIO, MIN_RATIO, PROMPT_STYLE,
    PROPOSAL_DIR, PROPS, REVIEW, STATE,
    _ledger_append, _load_state, _now, _props_append, _read, _read_only,
    _save_state, apply_canon, checkpoint, curate_one, l4_frozen, load,
    render_canon, select_batch, verify_note,
)
from kb_infer import load_note_full
from kb_io import iter_notes
from kb_metautil import kb_layer_of
from kb_text import content_fingerprint


def _write_proposals(root: Path, batch_id: str, recs: List[Dict[str, Any]]) -> None:
    out = root / PROPOSAL_DIR
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# 🧪 结晶提案 {batch_id}", "",
             f"> 生成 {_now()}　·　{len(recs)} 篇　·　"
             f"`kb curate run`（本地 Agent 结晶，代码校验后写回）", ""]
    for r in recs:
        v = r.get("verify") or {}
        lines.append(f"## {r['rel']}")
        lines.append(f"- 判定：`{r.get('verdict', '-')}`　动作：`{r.get('action', '-')}`　"
                     f"通过校验：{'✅' if r.get('ok') else '❌'}　耗时 {r.get('secs', '-')}s")
        lines.append(f"- 原文 {r.get('body_chars', '-')} 字 → 正典比例 "
                     f"{(v.get('checks', {}) or {}).get('ratio', '-')}")
        if v.get("issues"):
            lines.append("- ⚠️ 校验问题：" + "；".join(v["issues"][:4]))
        prop = r.get("proposal") or {}
        if prop:
            lines.append(f"- 标题：{prop.get('title', '-')}")
            lines.append(f"- 摘要：{prop.get('summary', '-')}")
            lines.append("")
            lines.append("```markdown")
            lines.append(render_canon(prop).strip())
            lines.append("```")
        lines.append("")
    (out / f"{batch_id}.md").write_text("\n".join(lines), encoding="utf-8")




def cmd_verify(root: Path, batch: Optional[str], pending: bool) -> int:
    """重新校验提案（离线，不调模型）。

    读**旁路完整提案**（PROPS）而不是精简账本（LEDGER）：账本在 `_ledger_append` 前
    已被剥掉 `proposal` 字段（cmd_run 里 `{k: v for k, v in rec.items() if k != "proposal"}`），
    所以以前从账本读、再要求 `r.get("proposal")` → 恒为空 → 本命令永远输出「无提案记录」。
    """
    led = _read_only("curate_proposals.jsonl")(root)
    if led is None or not led.exists():
        print("（无提案记录：先跑 kb curate run）")
        return 0
    rows = []
    for line in led.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if batch and r.get("batch") != batch:
            continue
        if not r.get("proposal"):
            continue
        rows.append(r)
    if not rows:
        print(f"（无提案记录{'：batch ' + batch if batch else ''}）")
        return 0
    n_ok = n_bad = 0
    dropped_total = 0
    for r in rows:
        p = root / r["rel"]
        fm, _t, _b, body = load(p) if p.exists() else ({}, "", "", "")
        v = verify_note(body, r["proposal"], root, r.get("sid", ""))
        dropped_total += len(v["dropped_facts"])
        flag = "✅" if v["ok"] else "❌"
        n_ok += v["ok"]
        n_bad += (not v["ok"])
        print(f"{flag} {r['rel']}  比例 {v['checks']['ratio']}  "
              f"接地 {v['checks']['facts_grounded']}/{v['checks']['facts_total']}")
        for i in v["issues"]:
            print(f"     · {i}")
    print(f"\n合计：通过 {n_ok}，不通过 {n_bad}，累计丢弃未接地事实 {dropped_total} 条")
    return 0




def cmd_report(root: Path, as_json: bool) -> int:
    """结晶进度 + 质量指标（正典覆盖率、压缩比、接地率）。"""
    n_notes = n_canon = n_raw = n_index = 0
    canon_chars = orig_chars = 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        fm, _t, _b, body = load(p)
        layer = kb_layer_of(fm, rel)
        n_notes += 1
        if layer == "canon":
            n_canon += 1
            canon_chars += len(body)
            orig_chars += int(fm.get("source_chars") or 0)
        elif layer == "raw":
            n_raw += 1
        elif layer == "index":
            n_index += 1
    led = _read_only("curate_ledger.jsonl")(root)
    n_runs = n_ok = n_drop = 0
    # `_read_only` 在文件不存在时返回 None（不是 Path）—— 以前没判空，
    # 于是**全新库上 `kb curate report` 必崩** AttributeError: 'NoneType' has no
    # attribute 'exists'。这里按"没有结晶记录"处理（报告应为全 0，不是异常）。
    if led is not None and led.exists():
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_runs += 1
            n_ok += bool(r.get("ok"))
            n_drop += len(((r.get("verify") or {}).get("checks") or {}).get("facts_dropped") or [])
    n_review = 0
    rv = root / REVIEW
    if rv.exists():
        n_review = sum(1 for l in rv.read_text(encoding="utf-8").splitlines() if l.strip())
    data = {
        "notes": n_notes, "canon": n_canon, "raw": n_raw, "index_stubs": n_index,
        "pending_review": n_review,
        "canon_ratio": round(n_canon / max(n_notes - n_raw - n_index, 1), 3),
        "compression": round(canon_chars / orig_chars, 3) if orig_chars else None,
        "curate_runs": n_runs, "curate_ok": n_ok,
        "ok_rate": round(n_ok / n_runs, 3) if n_runs else None,
        "facts_dropped_total": n_drop,
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print("📊 结晶报告")
    print(f"   笔记总数 {n_notes}　正典 {n_canon}　证据层(raw) {n_raw}　索引页 {n_index}")
    print(f"   正典覆盖率（知识层）: {data['canon_ratio']:.1%}")
    if data["compression"] is not None:
        print(f"   压缩比（正典/原文）: {data['compression']:.1%}"
              f"（目标区间 {MIN_RATIO:.0%}–{MAX_RATIO:.0%}）")
    # ok_rate 在 n_runs=0 时是 None（分母为 0）—— 直接插值会打印 "（None）"，
    # 读起来像 bug。没有记录时就不显示通过率。
    rate_txt = f"{data['ok_rate']}" if data["ok_rate"] is not None else "—"
    print(f"   结晶运行 {n_runs} 次，通过校验 {n_ok} 次（{rate_txt}），"
          f"累计丢弃未接地事实 {n_drop} 条")
    if n_review:
        print(f"   待人工裁决 {n_review} 条（低置信）：kb curate review")
    if not n_runs:
        print("   （还没有结晶记录：kb curate plan → kb curate run）")
    return 0




def cmd_recheck(root: Path, apply_pass: bool, export: bool) -> int:
    """用**当前**校验规则重审历史提案（离线，不调模型）。

    用途：校验规则变严/变松后，把「当时没过、现在达标」的提案补写回；
    把仍未达标的导成 `_curate/REVIEW-NEEDED.md` 人工清单（附模型草稿与未过原因）。
    只对最终动作是 propose / review 的笔记动手（canon-written / skip:* 不动）。
    """
    src = _read_only("curate_proposals.jsonl")(root)
    if src is None or not src.exists():
        # 兼容：早期只有账本（proposal 已被剥离）；提示用 _curate/*.md 人工复核
        print("（无完整提案旁路 .kb/curate_proposals.jsonl；历史提案请看 "
              "70-知识治理 Governance/_curate/b*.md，本次运行起会自动记录）")
        return 3
    led = src
    final: Dict[str, Dict[str, Any]] = {}
    for line in led.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rel = r.get("rel")
        if not rel or not r.get("proposal"):
            continue
        prev = final.get(rel)
        if prev is None or str(r.get("ts", "")) >= str(prev.get("ts", "")):
            final[rel] = r

    pass_now, still, applied = [], [], 0
    for rel, r in final.items():
        if (r.get("action") or "") not in ("propose", "review:low-confidence"):
            continue
        p = root / rel
        if not p.exists():
            continue
        fm, _t, _b, body = load(p)
        v = verify_note(body, r["proposal"], root, r.get("sid", ""))
        # 低置信原样保留人工裁决（不因改规则就绕过置信闸门）
        if (r.get("action") or "") == "review:low-confidence":
            still.append((rel, "低置信（人工裁决闸门）", r))
            continue
        if v["ok"]:
            pass_now.append((rel, v))
        else:
            still.append((rel, "；".join(v["issues"][:2]) or "未过校验", r))

    print(f"🔁 重审历史提案：达标 {len(pass_now)} 篇 · 仍未达标 {len(still)} 篇")
    if apply_pass and pass_now:
        checkpoint(root)
        for rel, v in pass_now:
            fm, _t, _b, body = load(root / rel)
            sid = str(fm.get("memory_source") or f"vault:{rel}")
            v["checks"]["orig_chars"] = len(body)
            act = apply_canon(root, rel, fm, final[rel]["proposal"], sid, v, "recheck")
            applied += 1
            print(f"   ✅ {act}  {rel[:64]}")
        st = _load_state(root)
        for rel, _v in pass_now:
            st.setdefault("done", {})[rel] = {
                "fp": content_fingerprint(_read(root / rel)), "ts": _now(),
                "batch": "recheck", "ok": True, "action": "canon-written"}
        _save_state(root, st)
        print(f"   已写回 {applied} 篇；建议随后 kb rag index")

    if export:
        out = root / PROPOSAL_DIR / "REVIEW-NEEDED.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# 🧑⚖️ 待人工复核的结晶提案（{len(still)} 篇）", "",
                 f"> 生成 {_now()}　·　这些提案**未写回知识层**，仍在原处等待判断。",
                 "> 认可某篇：`kb curate judge` 或直接手工把下方草稿合进原笔记；",
                 "> 不认可：不必处理（下次不会再被自动重跑，见 MAX_ATTEMPTS）。", ""]
        for rel, why, r in still:
            prop = r.get("proposal") or {}
            lines += [f"## {rel}", "",
                      f"- 未过原因：{why}",
                      f"- 模型标题：{prop.get('title', '-')}",
                      f"- 摘要：{prop.get('summary', '-')}",
                      f"- 取原文：`kb raw show \"{r.get('sid', '')}\"`",
                      f"- 原记忆 ID：`{r.get('sid', '')}`", "",
                      "```markdown", render_canon(prop).strip(), "```", ""]
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"   人工清单：{out.relative_to(root)}")
    return 0




def cmd_review(root: Path, limit: int, as_json: bool) -> int:
    """列出待人工裁决的结晶提案（低置信度）。人看完用 kb curate run 手动补跑或直接改笔记。"""
    p = _read_only("curate_review.jsonl")(root)
    if p is None or not p.exists():
        print("✅ 无待裁决提案（人工队列为空）")
        return 0
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if as_json:
        print(json.dumps({"count": len(rows), "items": rows[:limit]}, ensure_ascii=False, indent=2))
        return 0
    print(f"🧑⚖️ 待人工裁决 {len(rows)} 条（低置信度，未自动写回）")
    for r in rows[:limit]:
        print(f"\n· {r.get('rel')}")
        print(f"  置信度 {r.get('confidence')}  三关校验 {'✅' if r.get('verify_ok') else '❌'}"
              f"  标题：{r.get('title')}")
        if r.get("issues"):
            print(f"  问题：{'；'.join(str(i) for i in r['issues'][:3])}")
        print(f"  取原文：kb raw show \"{r.get('sid')}\"")
    print(f"\n看完若认可：kb curate run --limit N（会跳过已处理；需要时可改 CONF_MIN 或直接编辑笔记）")
    return 0




def cmd_run(root: Path, domain: Optional[str], limit: int, apply: bool,
            model: Optional[str], as_json: bool, max_seconds: int = 0) -> int:
    # 用 `curate.LLM` 而不是裸 `LLM`：裸名在 import 时就把类对象绑死了，
    # 于是 `curate.LLM = StubLLM` 这类打桩/替换对子命令无效（拆分后实测踩到 ——
    # cmd_run 永远用真实 LLM → 测试里"模型不可用"分支根本进不去）。
    # 走模块属性查找，替换点始终是 `curate.LLM` 这一处。
    import curate as _curate
    llm = _curate.LLM(model=model)
    ok, note = llm.available()
    if not ok:
        print(f"⚠️ 本地模型不可用：{note}")
        print("   → 降级为只读 plan（不消耗任何推理）：")
        cand = select_batch(root, domain, limit)
        for c in cand:
            print(f"   {c['score']:>4}  {c['body_chars']:>6}字  {c['rel'][:70]}")
        return 0
    cand = select_batch(root, domain, limit)
    if not cand:
        print("✅ 无待结晶候选")
        return 0
    batch_id = "b" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    budget = f"　·　时间预算 {max_seconds}s" if max_seconds else ""
    print(f"⛳ 结晶批次 {batch_id}：{len(cand)} 篇（模型 {llm.model}）"
          f"{' · 直接写回' if apply else ' · 只出提案'}{budget}")
    st = _load_state(root)
    recs = []
    t_start = time.time()
    if apply and l4_frozen(root):
        print("  ⚖️ L4 宪法已冻结 lc（知识结晶）→ 本轮只出提案，不自动写回；"
              "人工解冻：python3 pipeline/kb_l4.py unfreeze --root . --rung lc")
        apply = False
    if apply:
        checkpoint(root)
    for i, c in enumerate(cand, 1):
        if max_seconds and (time.time() - t_start) > max_seconds:
            print(f"  ⏹ 达到时间预算（{max_seconds}s），本轮停在第 {i - 1} 篇；未处理的留待下次 cron")
            break
        t0 = time.time()
        rec = curate_one(root, c["rel"], llm, apply=apply)
        rec["batch"] = batch_id
        rec["secs"] = round(time.time() - t0, 1)
        prev = (st.get("done") or {}).get(c["rel"]) or {}
        st.setdefault("done", {})[c["rel"]] = {"fp": content_fingerprint(_read(root / c["rel"])),
                                              "ts": _now(), "batch": batch_id,
                                              "attempts": int(prev.get("attempts") or 0) + 1,
                                              "ok": rec.get("ok"), "action": rec.get("action")}
        recs.append(rec)
        _ledger_append(root, {k: v for k, v in rec.items() if k != "proposal"})
        if rec.get("proposal"):
            _props_append(root, rec)
        mark = "✅" if rec.get("ok") else "⚠️"
        why = ""
        if not rec.get("ok"):
            why = str(rec.get("error") or (rec.get("verify") or {}).get("issues") or rec.get("action") or "")
        print(f"  [{i}/{len(cand)}] {mark} {str(rec.get('action') or '-'):<16} {c['rel'][:58]}"
              f"  ({rec['secs']}s{'' if not why else ' · ' + why[:100]})")
    st.setdefault("batches", {})[batch_id] = {"ts": _now(), "n": len(recs),
                                             "apply": bool(apply), "model": llm.model}
    _save_state(root, st)
    _write_proposals(root, batch_id, recs)
    n_ok = sum(1 for r in recs if r.get("ok"))
    n_canon = sum(1 for r in recs if r.get("action") == "canon-written")
    print(f"✅ 批次 {batch_id} 完成：通过校验 {n_ok}/{len(recs)}，写回正典 {n_canon} 篇")
    print(f"   提案：{(root / PROPOSAL_DIR / (batch_id + '.md')).relative_to(root)}"
          f"　报告：kb curate report")
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
    return 0



