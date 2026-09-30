#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_eval_judge.py — RAGAS 式四指标评测（P4）。

指标（对应 RAGAS 的定义，用**本地模型**当裁判，零成本、不出机器）：
  1. faithfulness        答案里每条断言是否都能在检索上下文里找到（防幻觉）
  2. answer_relevancy    答案是否真的回答了问题（防答偏）
  3. context_precision   召回的上下文有多少是真相关的（防噪声）
  4. context_recall      该召回的是否都召回了（**需要 ground truth**）

前两个不需要标准答案；context_precision 用"命中里正典占比 + 是否含关键词"近似；
context_recall 需要标准答案，由人确认（`kb eval gold --review`）。

纪律：
  · 裁判是本地模型 → 结果只有相对意义，同一套题目内部比较才有效。
  · 每步都落盘（`.kb/eval/`），可复跑、可对比、可追因。
  · 解析容错：裁判输出必须是 JSON，解析失败**大声报错**（不静默当 0 分）。

用法:
  python3 pipeline/kb_eval_judge.py run     [--root R] [--limit N] [--json]
  python3 pipeline/kb_eval_judge.py gold    [--root R] [--generate] [--review]
  python3 pipeline/kb_eval_judge.py compare [--root R] [--baseline F]
"""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kb_common import ROOT_DEFAULT

EVAL_DIR = Path(".kb") / "eval"
GOLD = EVAL_DIR / "gold_set.jsonl"          # 人确认过的 ground truth
GOLD_CAND = EVAL_DIR / "gold_candidates.jsonl"
LAST_RUN = EVAL_DIR / "last_run.json"

# 与 scripts/eval_retrieval.py 保持同一套题（便于横向对比）
from importlib import util as _util
_ER = Path(__file__).resolve().parents[1] / "scripts" / "eval_retrieval.py"
_spec = _util.spec_from_file_location("_eval_retrieval", _ER)
_er = _util.module_from_spec(_spec); _spec.loader.exec_module(_er)
GOLD_Q = list(_er.GOLD)


# ── 本地裁判 ────────────────────────────────────────────────
class Judge:
    def __init__(self) -> None:
        from kb_common import model_config
        c = model_config()
        self.base = (c.get("base_url") or "http://127.0.0.1:8000/v1").rstrip("/")
        self.key = c.get("api_key") or ""
        self.model = c.get("chat_model") or "ornith1.5-35b"

    def available(self) -> bool:
        return bool(self.key)

    # 注意：ornith 是**带 reasoning 的模型**，completion_tokens 里 reasoning 常占 200–800。
    # max_tokens 给太小 → content 为空（finish_reason=stop 却什么都没输出）。
    # 这是评测里最隐蔽的坑：看起来"模型不可用"，其实是预算不够。
    def ask(self, prompt: str, max_tokens: int = 1500) -> Optional[str]:
        import urllib.request, urllib.error
        payload = json.dumps({"model": self.model, "temperature": 0.0, "max_tokens": max_tokens,
                              "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            return ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return None

    def ask_json(self, prompt: str, max_tokens: int = 1500) -> Optional[dict]:
        txt = self.ask(prompt, max_tokens)
        if txt is None:
            return None
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        raw = m.group(0)
        for cand in (raw, raw.replace('\\n', '\n'), raw.replace('\\"', '"')):
            try:
                obj = json.loads(cand)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                continue
        return None


# ── 检索 + 作答（走真实链路，不做特例）────────────────────────
def retrieve(root: Path, q: str, top: int = 6) -> Dict[str, Any]:
    r = subprocess.run([sys.executable, str(root / "pipeline" / "rag.py"), "query", q,
                        "--root", str(root), "--top", str(top), "--json", "--context", "500"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"检索失败（退出码 {r.returncode}）：{(r.stderr or '')[:200]}")
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"检索输出非 JSON：{(r.stdout or '')[:120]}") from e


def answer(root: Path, q: str, hits: List[dict], judge: Judge) -> str:
    ctx = "\n\n".join(f"[{i+1}] {h.get('path','')}\n{(h.get('snippet') or '')[:700]}"
                      for i, h in enumerate(hits[:6]))
    prompt = (f"只根据下面的资料回答问题。资料没有的就说「资料未覆盖」，不要凭常识补充。\n\n"
              f"资料：\n{ctx}\n\n问题：{q}\n\n答案（≤150 字，直接给结论）：")
    txt = judge.ask(prompt, max_tokens=1200)
    return (txt or "").strip()


# ── 三个不需要 ground truth 的指标 ───────────────────────────
JUDGE_FAITH = """判断「答案」的每条断言是否都能在「资料」里找到依据。
只输出 JSON：{{"claims": 总断言数, "supported": 有依据的断言数, "unsupported": ["没有依据的断言"]}}

资料：
{ctx}

答案：
{ans}"""

JUDGE_RELEV = """判断「答案」是否真的回答了「问题」。
只输出 JSON：{{"relevant": 0到1的小数, "reason": "一句话理由"}}

问题：{q}
答案：{ans}"""

JUDGE_PREC = """判断下面每段「资料」与「问题」的相关性（0=无关，1=高度相关）。
只输出 JSON：{{"scores": [每段一个 0/0.5/1]}}

问题：{q}
资料（按序）：
{ctx}"""


def score_faithfulness(judge: Judge, ctx: str, ans: str) -> Optional[dict]:
    return judge.ask_json(JUDGE_FAITH.format(ctx=ctx[:4000], ans=ans[:1500]))


def score_relevancy(judge: Judge, q: str, ans: str) -> Optional[dict]:
    return judge.ask_json(JUDGE_RELEV.format(q=q, ans=ans[:1200]))


def score_precision(judge: Judge, q: str, hits: List[dict]) -> Optional[dict]:
    ctx = "\n".join(f"[{i+1}] {(h.get('snippet') or '')[:300]}" for i, h in enumerate(hits[:6]))
    return judge.ask_json(JUDGE_PREC.format(q=q, ctx=ctx[:4000]))


def _precision_at_k(scores: List[float]) -> float:
    """RAGAS 风格的位置加权 precision：靠前的相关项权重更大。"""
    if not scores:
        return 0.0
    num = sum((s > 0) * (sum(1 for x in scores[:i + 1] if x > 0) / (i + 1)) for i, s in enumerate(scores))
    den = sum(1 for s in scores if s > 0) or 1
    return round(num / den, 3)


def cmd_run(root: Path, limit: int, as_json: bool) -> int:
    judge = Judge()
    if not judge.available():
        print("⚠️ 裁判模型不可用（缺 api_key）→ 先配 .kb/model.json 或 ORNITH_API_KEY", file=sys.stderr)
        return 2
    gold = _load_gold(root)
    qs = GOLD_Q[:limit] if limit else GOLD_Q
    rows = []
    t0 = time.time()
    for i, q in enumerate(qs, 1):
        data = retrieve(root, q)
        hits = data.get("hits") or []
        ctx = "\n\n".join(f"[{j+1}] {(h.get('snippet') or '')[:500]}" for j, h in enumerate(hits))
        ans = answer(root, q, hits, judge)
        if not ans:
            print(f"  ⚠️ 第 {i} 题作答为空（模型 reasoning 吃掉了预算？）——本题指标不可信")
        f = score_faithfulness(judge, ctx, ans) or {}
        r = score_relevancy(judge, q, ans) or {}
        p = score_precision(judge, q, hits) or {}
        ps = [float(x) for x in (p.get("scores") or []) if isinstance(x, (int, float))]
        claims = f.get("claims")
        sup = f.get("supported")
        faith = round(sup / claims, 3) if isinstance(claims, int) and claims and isinstance(sup, int) else None
        hit_paths = [h.get("path") for h in hits]
        canon = sum(1 for h in hits if str((h.get("path") or "")).find("raw/") != 0)
        gt = gold.get(q)
        recall = _recall_against_gt(gt, hits) if gt else None
        rows.append({"q": q, "faithfulness": faith, "answer_relevancy": r.get("relevant"),
                     "context_precision": _precision_at_k(ps), "context_recall": recall,
                     "canon_share": round(canon / max(len(hits), 1), 3),
                     "answer": ans[:300], "n_hits": len(hits),
                     "unsupported": (f.get("unsupported") or [])[:3],
                     "relevancy_reason": r.get("reason")})
        print(f"  [{i}/{len(qs)}] faith={faith} relev={r.get('relevant')} "
              f"prec={rows[-1]['context_precision']} recall={recall}  {q[:26]}")
    out = {"ts": datetime.now().isoformat(timespec="seconds"), "n": len(rows),
           "secs": round(time.time() - t0, 1), "rows": rows,
           "avg": {k: _avg(rows, k) for k in
                   ("faithfulness", "answer_relevancy", "context_precision", "context_recall", "canon_share")}}
    (root / EVAL_DIR).mkdir(parents=True, exist_ok=True)
    (root / LAST_RUN).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0
    print(f"\n📊 RAGAS 式四指标（{out['n']} 题 · {out['secs']}s · 裁判 {judge.model}）")
    for k, v in out["avg"].items():
        print(f"   {k:20} {v if v is not None else '—（缺 ground truth）'}")
    print(f"   明细已写 {LAST_RUN}")
    return 0


def _avg(rows: List[dict], key: str) -> Optional[float]:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return round(sum(vals) / len(vals), 3) if vals else None


def _recall_against_gt(gt: dict, hits: List[dict]) -> Optional[float]:
    """context_recall：标准答案的关键点有多少能在召回上下文里找到。"""
    pts = gt.get("key_points") or []
    if not pts:
        return None
    ctx = "\n".join((h.get("snippet") or "") + " " + (h.get("context") or "") for h in hits)
    hit = sum(1 for p in pts if str(p) and str(p)[:12] in ctx)
    return round(hit / len(pts), 3)


# ── ground truth：生成候选 + 人工确认 ───────────────────────
def cmd_gold(root: Path, generate: bool, review: bool) -> int:
    p = root / GOLD
    cand = root / GOLD_CAND
    if generate:
        judge = Judge()
        if not judge.available():
            print("⚠️ 裁判模型不可用", file=sys.stderr); return 2
        rows = []
        for i, q in enumerate(GOLD_Q, 1):
            data = retrieve(root, q)
            hits = data.get("hits") or []
            ans = answer(root, q, hits, judge)
            pts = _extract_points(judge, q, ans)
            rows.append({"q": q, "candidate_answer": ans, "key_points": pts,
                         "sources": [h.get("path") for h in hits[:4]]})
            print(f"  [{i}/{len(GOLD_Q)}] {q[:30]} → {len(pts)} 个关键点")
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(f"\n✅ 候选标准答案已生成：{cand}")
        print("   下一步：kb eval gold --review    # 逐题看，确认/改写/丢弃")
        return 0
    if review:
        if not cand.exists():
            print("✗ 还没有候选（先跑 kb eval gold --generate）", file=sys.stderr); return 2
        rows = [json.loads(l) for l in cand.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"📝 逐题确认（共 {len(rows)} 题）。每题按回车=接受；输入 n=丢弃/改写；s=跳过")
        accepted = _load_gold(root) if p.exists() else {}
        new = 0
        for i, r in enumerate(rows, 1):
            if r["q"] in accepted:
                continue
            print(f"\n[{i}/{len(rows)}] 问题：{r['q']}")
            print(f"  候选答案：{r['candidate_answer'][:220]}")
            print(f"  关键点：{r['key_points']}")
            print(f"  来源：{r['sources'][:2]}")
            try:
                resp = input("  确认？[回车=接受 / 输入新答案 / n=丢弃 / s=跳过] ").strip()
            except EOFError:
                print("\n（非交互环境：已停止，未改动）"); break
            if resp == "s":
                continue
            if resp == "n":
                continue
            gt = {"q": r["q"], "gold_answer": resp or r["candidate_answer"],
                  "key_points": r["key_points"],
                  "confirmed_by": "human", "confirmed_at": datetime.now().isoformat(timespec="seconds")}
            accepted[r["q"]] = gt
            new += 1
        lines = [json.dumps(v, ensure_ascii=False) for v in accepted.values()]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n✅ 已写入 {p}（本次新增 {new} 题，累计 {len(accepted)} 题）")
        return 0
    gold = _load_gold(root)
    print(f"🗂 ground truth：{len(gold)}/{len(GOLD_Q)} 题已确认（文件 {p}）")
    if len(gold) < len(GOLD_Q):
        print(f"   未确认的 {len(GOLD_Q) - len(gold)} 题，context_recall 会是 —")
        print("   生成候选：kb eval gold --generate  →  逐题确认：kb eval gold --review")
    return 0


POINTS_PROMPT = """把下面这段答案拆成 3–6 个「关键点」，每个关键点是一个可在原文中检索到的短语
（人名/数字/路径/结论短句），用于后续评测"检索有没有召回该信息"。
只输出 JSON：{{"key_points": ["...", "..."]}}

答案：{ans}"""


def _extract_points(judge: Judge, q: str, ans: str) -> List[str]:
    d = judge.ask_json(POINTS_PROMPT.format(ans=ans[:1200]), max_tokens=1200) or {}
    pts = [str(x).strip() for x in (d.get("key_points") or []) if str(x).strip()]
    return pts[:6]


def _load_gold(root: Path) -> Dict[str, dict]:
    p = root / GOLD
    out = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            out[r["q"]] = r
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def cmd_compare(root: Path, baseline: Optional[str]) -> int:
    cur_p = root / LAST_RUN
    if not cur_p.exists():
        print("✗ 还没有当前结果（先跑 kb eval judge run）", file=sys.stderr); return 2
    cur = json.loads(cur_p.read_text(encoding="utf-8"))
    if not baseline:
        print(f"📈 当前：{json.dumps(cur['avg'], ensure_ascii=False)}")
        print("   对比：kb eval judge compare --baseline .kb/eval/baseline.json")
        return 0
    base = json.loads(Path(baseline).read_text(encoding="utf-8"))
    print("📈 指标对比（当前 vs 基线）")
    for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall", "canon_share"):
        a, b = cur["avg"].get(k), base["avg"].get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            print(f"   {k:20} {b} → {a}   ({a - b:+.3f})")
        else:
            print(f"   {k:20} {b} → {a}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS 式四指标评测（本地裁判）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--root", default=ROOT_DEFAULT)
    r.add_argument("--limit", type=int, default=0); r.add_argument("--json", action="store_true", dest="as_json")
    g = sub.add_parser("gold"); g.add_argument("--root", default=ROOT_DEFAULT)
    g.add_argument("--generate", action="store_true"); g.add_argument("--review", action="store_true")
    c = sub.add_parser("compare"); c.add_argument("--root", default=ROOT_DEFAULT)
    c.add_argument("--baseline", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "run":
        return cmd_run(root, args.limit, args.as_json)
    if args.cmd == "gold":
        return cmd_gold(root, args.generate, args.review)
    if args.cmd == "compare":
        return cmd_compare(root, args.baseline)
    return 1


if __name__ == "__main__":
    sys.exit(main())
