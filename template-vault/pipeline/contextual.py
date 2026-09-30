#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contextual.py — 上下文强化（Contextual Retrieval 的落地）。

问题：分块后每个块脱离原文就失去意义（"它涨到 200" 不知道"它"是谁），
      检索时自然匹配不上用户的问法。

两条手段（按成本从低到高）：
  1. **CCH（Contextual Chunk Headers，零成本）**：用文档标题/路径/域/标签拼一行定位语。
     实测（Anthropic/社区）提升 10–20%。
  2. **LLM 上下文串**（可选）：把「整篇 + 该块」交给小模型生成 50–100 字定位语。
     代价是每块一次调用；收益 35%（失败率 5.7%→3.7%）。

纪律：
  · 上下文**只写进索引**，不改 .md 正文（正文保真、可 diff、零副作用）。
  · LLM 路径 best-effort：端点不可用/超时 → 静默回退 CCH。
  · 可开关、可预算：只对"短块/无标题块"生成（长块本身自带上下文）。

用法:
  python3 pipeline/contextual.py build --root R [--llm] [--limit N] [--dry-run]
  python3 pipeline/contextual.py show  --root R --rel <笔记>
"""
import argparse, json, re, sys, urllib.request, urllib.error, os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kb_common import ROOT_DEFAULT, iter_notes, load_note_full as load, is_generated_report, is_raw_path

CACHE = Path(".kb") / "context_cache.json"
# 需要 LLM 上下文的块特征：太短（缺上下文）或没有标题（无结构）
SHORT_CHUNK_CHARS = 600


def cch(rel: str, fm: Dict[str, Any], body: str) -> str:
    """确定性上下文头（零成本）。用路径/标题/域/标签拼一行「这段在讲什么」。"""
    title = str(fm.get("title") or Path(rel).stem).strip()
    parts = [p for p in Path(rel).parts[:-1] if p and not p.startswith("_")]
    domain = str(fm.get("domain", "") or "").strip()
    tags = fm.get("tags")
    tag_s = ""
    if isinstance(tags, list):
        tag_s = "、".join(str(t) for t in tags[:4])
    elif tags:
        tag_s = str(tags)[:60]
    head = f"《{title}》"
    where = []
    if parts:
        where.append("/".join(parts[:2]))
    if domain:
        where.append(f"域:{domain}")
    if tag_s:
        where.append(f"标签:{tag_s}")
    first = ""
    for line in (body or "").splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "-", ">", "|", "`")):
            first = s[:60]
            break
    tail = f"｜{first}" if first and first not in title else ""
    return f"{head}{('（' + ' · '.join(where) + '）') if where else ''}{tail}"


# ── LLM 上下文串（可选，best-effort）──────────────────────────
CTX_PROMPT = """<文档标题>{title}</文档标题>
<完整原文>
{document}
</完整原文>

下面是要嵌入检索的片段：
<片段>
{chunk}
</片段>

请给这个片段写一句 50 字以内的定位语，说明它在这篇文档里讲什么、属于哪个主题。
只输出这一句定位语，不要任何其他文字。"""


class _LLM:
    def __init__(self) -> None:
        # 走统一配置（环境变量 > 库内 .kb/model.json）。
        #   为什么：cron/systemd 读不到 ~/.bashrc → key 为空 → 这里会**静默不干活**，
        #   而"静默降级"在本项目已经坑过多次（同 kb_embed / curate 的根因）。
        try:
            from kb_common import model_config
            c = model_config()
        except Exception:
            c = {}
        self.base = (c.get("base_url") or os.environ.get("ORNITH_BASE_URL")
                     or "http://127.0.0.1:8000/v1").rstrip("/")
        self.key = c.get("api_key") or os.environ.get("ORNITH_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        self.model = c.get("chat_model") or os.environ.get("ORNITH_CHAT_MODEL") or "ornith1.5-35b"

    def available(self) -> bool:
        if not self.key:
            return False
        try:
            req = urllib.request.Request(f"{self.base}/models",
                                         headers={"Authorization": f"Bearer {self.key}"})
            with urllib.request.urlopen(req, timeout=6) as r:
                return r.status < 400
        except (urllib.error.URLError, OSError, ValueError):
            return False

    # 注意：max_tokens 必须覆盖 **reasoning** 再留出正文额度。
    #   实测（2026-10-01）：max_tokens=64 时 64 token 全是 reasoning、content 为空，
    #   而 finish_reason 仍是 stop —— 看起来像"模型没输出"，其实是预算不够。
    #   旧值 160 低于该模型的 reasoning 开销（实测单次 162+）→ 此函数**一直静默返回 None**。
    def _ask_raw(self, prompt: str, max_tokens: int) -> Optional[str]:
        """单次调用并抽 content（收紧重试用；不递归、不告警）。"""
        payload = json.dumps({"model": self.model, "temperature": 0.1, "max_tokens": max_tokens,
                              "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return re.sub(r"\s+", " ", txt).strip()[:120] or None
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return None

    def ask(self, prompt: str, max_tokens: int = 2048) -> Optional[str]:
        payload = json.dumps({"model": self.model, "temperature": 0.1, "max_tokens": max_tokens,
                              "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            ch = (d.get("choices") or [{}])[0]
            txt = (ch.get("message") or {}).get("content") or ""
            if not txt.strip() and (d.get("usage") or {}).get("completion_tokens"):
                # 有 token 消耗却无正文 = reasoning 吃光预算。
                #   实测：长片段会让模型陷入超长思考（>1200 token 全在 reasoning）。
                #   策略：**收紧输入 + 直接要结果**，重试一次（重试仍失败才算失败）。
                short = prompt[:900] + "\n\n（直接给一句定位语，不要推理过程）"
                return self._ask_raw(short, max_tokens)
            txt = re.sub(r"\s+", " ", txt).strip()
            return txt[:120] or None
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return None


def _cache_load(root: Path) -> Dict[str, str]:
    try:
        return json.loads((root / CACHE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_save(root: Path, d: Dict[str, str]) -> None:
    p = root / CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")


def llm_context(llm: _LLM, title: str, document: str, chunk: str) -> Optional[str]:
    return llm.ask(CTX_PROMPT.format(title=title, document=document[:8000], chunk=chunk[:1500]))


# ── 主流程：为每个块产出「上下文 + 块」的索引文本 ─────────────
def build_for_note(root: Path, rel: str, fm: Dict[str, Any], body: str,
                   llm: Optional[_LLM] = None, cache: Optional[Dict[str, str]] = None,
                   dry_run: bool = False) -> Dict[str, Any]:
    """给单篇笔记算上下文（用于索引）。返回 {ctx, source(cch|llm|none), chars}。"""
    ctx = cch(rel, fm, body)
    source = "cch"
    if llm is not None and len(body) < SHORT_CHUNK_CHARS:
        key = f"{rel}::{len(body)}"
        hit = (cache or {}).get(key)
        if hit:
            return {"ctx": hit, "source": "llm-cache", "chars": len(hit)}
        got = llm_context(llm, str(fm.get("title") or Path(rel).stem), body, body)
        if got:
            if cache is not None and not dry_run:
                cache[key] = got
            return {"ctx": got, "source": "llm", "chars": len(got)}
    return {"ctx": ctx, "source": source, "chars": len(ctx)}


def cmd_build(root: Path, use_llm: bool, limit: int, dry: bool, as_json: bool) -> int:
    llm = _LLM() if use_llm else None
    llm_ok = llm.available() if llm else False
    if use_llm and not llm_ok:
        print("⚠️ --llm 已请求但本地模型不可用（缺 ORNITH_API_KEY 或端点不可达）→ 退回纯 CCH")
    cache = _cache_load(root)
    rows, n_llm, n_cch = [], 0, 0
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if is_generated_report(rel) or is_raw_path(rel):
            continue
        fm, _t, _b, body = load(p)
        if not body.strip():
            continue
        r = build_for_note(root, rel, fm, body, llm if llm_ok else None, cache, dry)
        r["rel"] = rel
        rows.append(r)
        n_llm += 1 if r["source"].startswith("llm") else 0
        n_cch += 1 if r["source"] == "cch" else 0
        if limit and len(rows) >= limit:
            break
    if not dry and llm_ok:
        _cache_save(root, cache)
    if as_json:
        print(json.dumps({"notes": len(rows), "llm": n_llm, "cch": n_cch,
                          "sample": rows[:20]}, ensure_ascii=False, indent=2))
        return 0
    print(f"🧭 上下文构建（{'dry-run' if dry else '已写缓存'}）：{len(rows)} 篇 · LLM {n_llm} · CCH {n_cch}")
    print("   抽样（上下文串）：")
    for r in rows[:8]:
        ctx = r["ctx"]
        print(f"   [{r['source']:<9}] {r['rel'][:44]}")
        print(f"        {ctx[:96]}")
    return 0


def cmd_show(root: Path, rel: str) -> int:
    p = root / rel
    if not p.exists():
        print(f"✗ 不存在：{rel}", file=sys.stderr)
        return 1
    fm, _t, _b, body = load(p)
    print(f"📄 {rel}\n   CCH 上下文：{cch(rel, fm, body)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="上下文强化（索引用；不改正文）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--root", default=ROOT_DEFAULT)
    b.add_argument("--llm", action="store_true", help="对短块额外用本地模型生成上下文（默认只用零成本 CCH）")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--dry-run", action="store_true", dest="dry")
    b.add_argument("--json", action="store_true", dest="as_json")
    s = sub.add_parser("show"); s.add_argument("--root", default=ROOT_DEFAULT)
    s.add_argument("--rel", required=True)
    args = ap.parse_args()
    root = Path(args.root)
    if args.cmd == "build":
        return cmd_build(root, args.llm, args.limit, args.dry, args.as_json)
    if args.cmd == "show":
        return cmd_show(root, args.rel)
    return 1


if __name__ == "__main__":
    sys.exit(main())
