#!/usr/bin/env python3
# ============================================================
# kb_claim.py —— 方案 C / Layer 1 · 声明验证循环
# ------------------------------------------------------------
# 直击"适应度闸本质"：把 fitness 从"手搓比率 proxy"升级为**对现实的预测误差**。
#
#   机制（只读为主，不改动任何笔记正文/元数据）：
#     1. 建库/周期：LLM 把每条*编译笔记*抽成结构化"可证伪声明"(claim)+ 抽取日期，
#        落 .kb/state/claims.json（StateStore，与 feedback_state 同机制 → 可审计/可回滚）。
#     2. 新 raw/ 数据流入时：embedding 匹配"新数据 ↔ 旧声明"候选对（签名预过滤 + 余弦两档），
#        有界地只对这些对调 LLM 判【证实/证伪/无关】。
#     3. 适应度闸 = verified_ratio（被现实支持）vs falsified_ratio（被现实推翻）= 真·预测误差。
#
#   铁律（P4 起一以贯之，与 rag.py / kb_embed 完全一致）：
#     - LLM chat 接线：读 ORNITH_BASE_URL/ORNITH_API_KEY，Bearer 认证，
#       端点不可达即静默降级（记 pending），绝不连写死 LAN 地址、绝不锁死流水线。
#     - 有界：每条笔记 ≤MAX_PER_NOTE 声明、全库 ≤MAX_TOTAL、每声明最多匹配 MAX_PAIRS。
#     - 日期硬约束：只取 date(raw) > date(claim) 的配对——否则用"未来数据"检验过去声明=泄露。
#     - best-effort：LLM/embedding 不可用 → 该声明记 pending，不进适应度闸。
#
#   用法:
#     python3 pipeline/kb_claim.py sync      # 全量抽取声明入库（建库/补抽）
#     python3 pipeline/kb_claim.py validate  # 用新 raw/ 验证旧声明 → 回算适应度闸
#     python3 pipeline/kb_claim.py report    # 打印适应度闸指标（verified/falsified ratio）
#     python3 pipeline/kb_claim.py stats     # claims 统计
# ------------------------------------------------------------
import argparse, hashlib, json, math, os, re, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple, Union

CLAIMS_FILE = "claims.json"           # 存于 .kb/state/claims.json（StateStore）
CHAT_MODEL = os.environ.get("ORNITH_CHAT_MODEL", "ornith1.5-35b")  # 与 rag.py chat 同源
_MAX_PER_NOTE = 5                      # 每条笔记最多抽几条声明
_MAX_TOTAL = 300                       # 全库声明总数上限
_MAX_PAIRS = 60                        # 单次 validate 最多判定多少对
_LLM_TIMEOUT = 40                      # chat 超时（与 rag.py llm_answer 一致）
_SIM_THRESHOLD = 0.55                  # embedding 余弦匹配阈值
_JACCARD_MIN = 0.12                    # 签名 Jaccard 预过滤下限（省 embedding 调用）
_RAW_TEXT_CAP = 2000                   # 匹配用 raw 文本截断长度

_CN = re.compile(r"[\u4e00-\u9fff]")
_LAT = re.compile(r"[A-Za-z0-9]+")
_EMBED_CACHE = {}


# ── LLM chat 接线（照搬 rag.py 铁律，绝不连写死地址）───────
def _chat(messages):
    """返回 (content 或 None, reason)。key/url 任一缺失或端点不可达 → None（静默降级）。"""
    key = (os.environ.get("ORNITH_API_KEY") or "").strip()
    url = (os.environ.get("ORNITH_BASE_URL") or "").strip()
    if not key or not url:
        return None, ("未设 ORNITH_API_KEY/ORNITH_BASE_URL，声明抽取/判定跳过"
                      "（需 export ORNITH_BASE_URL=http://<vllm>/v1）")
    try:
        import urllib.request
        req = urllib.request.Request(url.rstrip("/") + "/chat/completions",
            data=json.dumps({"model": CHAT_MODEL, "messages": messages,
                             "temperature": 0.0}).encode("utf-8"),
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"})
        content = json.loads(urllib.request.urlopen(req, timeout=_LLM_TIMEOUT)
                             .read().decode("utf-8"))["choices"][0]["message"]["content"]
        return content, ""
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        return None, f"连接 {url} 失败({e})"


# ── 结构化解析（ tolerant：允许 LLM 包 markdown fence）────────
def _parse_json(s):
    if not s:
        return None
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\n?", "", s)
        s = re.sub(r"\n?```+\s*$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i = min([k for k in (s.find("{"), s.find("[")) if k >= 0], default=-1)
        if i < 0:
            return None
        try:
            return json.loads(s[i:])
        except json.JSONDecodeError:
            return None


def _extract_claims(text):
    """LLM 抽取可证伪声明 → list[{"claim","type","confidence"}]；不可用返回 []。"""
    prompt = [
        {"role": "system", "content":
            "你是知识库声明提取器。从笔记正文抽取「可证伪断言」——对真实世界做出可被"
            "新数据检验的断言（事实、因果、预测、政策主张）。"
            "跳过：纯模板/元数据/占位符、纯主观偏好、无法检验的模糊词。"
            "返回 JSON 数组，每项 {claim:简短单句断言, type:fact|prediction|policy|relation, "
            "confidence:0-1数字}。至少1条、最多5条，不要多余文字。"},
        {"role": "user", "content": f"笔记正文：\n{text[:2500]}"},
    ]
    content, _ = _chat(prompt)
    if content is None:
        return []
    arr = _parse_json(content)
    if not isinstance(arr, list):
        return []
    out = []
    for item in arr:
        if isinstance(item, dict) and item.get("claim"):
            c = str(item["claim"]).strip()
            if len(c) >= 6:
                out.append({"claim": c,
                            "type": item.get("type", "fact"),
                            "confidence": round(float(_num(item.get("confidence")) or 0.7), 2)})
    return out[:_MAX_PER_NOTE]


def _judge(claim_text, raw_text):
    """LLM 判旧声明 vs 新 raw 数据 → (verdict, evidence, ok)。ok=False=判定不可用(pending)。"""
    prompt = [
        {"role": "system", "content":
            "你是知识库真实性裁判。给定一条「旧声明」(某笔记在指定日期作出的可证伪断言)和一段"
            "「新流入的外部数据」。判断新数据对旧声明是【confirm】(支持/证实)、"
            "【falsify】(矛盾/证伪)还是【irrelevant】(不相关)。"
            "只返回 JSON {verdict:\"confirm\"|\"falsify\"|\"irrelevant\", evidence:一句依据(中文)}。"},
        {"role": "user", "content":
            f"旧声明：{claim_text}\n\n新流入外部数据：\n{raw_text[:_RAW_TEXT_CAP]}"},
    ]
    content, _ = _chat(prompt)
    if content is None:
        return None, None, False
    r = _parse_json(content)
    if not isinstance(r, dict):
        return None, None, False
    v = str(r.get("verdict", "")).strip().lower()
    if v not in ("confirm", "falsify", "irrelevant"):
        return None, None, False
    return v, (str(r.get("evidence", ""))[:200] or None), True


# ── 签名（廉价预过滤：中文 bigram + 英文词，归一化 Jaccard）──
def _signature(text):
    toks = _CN.findall(text or "") + _LAT.findall(text or "")
    return {t.lower() for t in toks}


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    u = len(a | b) or 1
    return len(a & b) / u


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))  # 两者均已 L2 归一 → 点积=余弦


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# 注：_parse_d 保留本地实现——与 kb_common.parse_date_safe 签名/行为不同：
#   本地版返回 None（调用方据此跳过日期约束），parse_date_safe 永不返回 None；
#   本地版严格校验 v[4]=="-" and v[7]=="-"，parse_date 更宽松（正则提取）。
def _parse_d(v):
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _store(root):
    """统一 StateStore（带文件锁）；缺失则降级直接文件读写。"""
    try:
        from state_manager import StateStore
        return StateStore(root)
    except (ImportError, ModuleNotFoundError, TypeError):
        return None


def _claims_path(root):
    return Path(root) / ".kb" / "state" / CLAIMS_FILE


def _load_claims(root):
    st = _store(root)
    if st is not None:
        try:
            return st.load(CLAIMS_FILE, {"version": 0, "claims": []})
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    p = _claims_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": 0, "claims": []}


def _save_claims(root, data):
    st = _store(root)
    if st is not None:
        try:
            st.save(CLAIMS_FILE, data)
            return
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    p = _claims_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 主流程 ──────────────────────────────────────────────
def collect_notes(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """只读采集：编译笔记(可自改进)与 raw(不可变外部源)。复用 kb_rsi 采集逻辑避免重复。"""
    try:
        import kb_rsi
        return kb_rsi.collect(Path(root))
    except (ImportError, ModuleNotFoundError, OSError, UnicodeDecodeError):
        # 极简内联兜底（不依赖 kb_rsi）
        out = []
        for p in Path(root).rglob("*.md"):
            if any(d in p.parts for d in (".obsidian", ".git", "vector index", ".kb", ".backups")):
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(p.relative_to(root))
            out.append({"rel": rel, "body": txt, "fm": {}, "kind": "raw" if "raw" in Path(rel).parts[:-1] else "compiled"})
        return out


def sync_claims(root: Union[str, Path], max_per_note: int = _MAX_PER_NOTE,
                max_total: int = _MAX_TOTAL) -> Dict[str, Any]:
    """把编译笔记抽成可证伪声明入库（只读笔记、只写 claims.json）。返回统计。"""
    root = Path(root)
    notes = [n for n in collect_notes(root)
             if n.get("kind") != "raw" and str(n.get("body", "")).strip()]
    data = _load_claims(root)
    existing_ids = {c.get("claim_id") for c in data.get("claims", [])}
    today = datetime.now().date().isoformat()

    # 联网确认解法：逐个串行调 chat LLM 抽取声明过慢（单条 ~27s，29 条≈13 分钟）。
    # 抽取彼此独立，改为有界并发（不 flood vLLM）；去重/归并仍在主线程，保证幂等正确。
    def _note_text(n):
        return str(n.get("body", "")) + "\n" + str(n.get("fm", {}).get("tags", ""))
    workers = min(8, max(2, len(notes)))
    with ThreadPoolExecutor(max_workers=workers) as _ex:
        extracted_list = list(_ex.map(_extract_claims, map(_note_text, notes)))

    new_claims = []
    llm_used = False
    for n, extracted in zip(notes, extracted_list):
        if len(new_claims) >= max_total:
            break
        llm_used = llm_used or bool(extracted)
        for idx, c in enumerate(extracted):
            cid = hashlib.md5(f"{n['rel']}\x00{c['claim']}\x00{idx}".encode()).hexdigest()[:16]
            if cid in existing_ids:
                continue
            new_claims.append({
                "claim_id": cid, "note_rel": n["rel"],
                "claim": c["claim"], "type": c.get("type", "fact"),
                "confidence": c.get("confidence", 0.7),
                "signature": sorted(_signature(c["claim"])),
                "extracted_at": today,
                "verdict": None, "verdict_at": None, "evidence": None, "history": [],
            })
    existing_ids |= {c["claim_id"] for c in new_claims}
    merged = list(new_claims) + [c for c in data.get("claims", []) if c.get("claim_id") not in existing_ids]
    merged = merged[:max_total]

    out = {"version": (data.get("version") or 0) + 1, "updated_at": datetime.now().isoformat(timespec="seconds"),
           "generated_at": today, "total_claims": len(merged), "claims": merged}
    _save_claims(root, out)
    return {"synced_new": len(new_claims), "total": len(merged),
            "llm_available": llm_used}


def _candidate_pairs(root, claims, sim_threshold=_SIM_THRESHOLD, jaccard_min=_JACCARD_MIN):
    """返回候选 (claim, raw_rel, raw_text) 列表，带日期约束 + 签名/embedding 两档匹配。"""
    import kb_embed  # 局部 import：embedding 不可用时整块降级，不污染热路径
    use_emb = kb_embed.available()
    raws = [(n["rel"], _parse_d(n["fm"].get("created")) or _parse_d(str(n.get("fm", {}).get("updated"))),
             str(n.get("body", "")))
            for n in collect_notes(root) if n.get("kind") == "raw" and str(n.get("body", "")).strip()]

    pairs = []
    for c in claims:
        cdate = _parse_d(c.get("extracted_at"))
        csig = set(c.get("signature") or [])
        cemb = kb_embed.embed(c["claim"]) if use_emb else None
        for rel, rdate, rtext in raws:
            if rdate is None or cdate is None or rdate <= cdate:
                continue  # 日期硬约束：只让"后来的真实数据"检验"过去的声明"
            rsig = _signature(rtext)
            remb = kb_embed.embed(rtext) if use_emb else None
            if use_emb and cemb is not None and remb is not None:
                sim, mode = _dot(cemb, remb), "embed"
                threshold = sim_threshold
            else:
                sim, mode = _jaccard(csig, rsig), "sig"
                threshold = max(sim_threshold * 0.4, jaccard_min)
            if sim >= threshold:
                pairs.append((c, rel, rtext[:_RAW_TEXT_CAP], round(sim, 3), mode))
    return pairs[:_MAX_PAIRS], use_emb


def validate(root: Union[str, Path], max_pairs: int = _MAX_PAIRS,
             sim_threshold: float = _SIM_THRESHOLD,
             jaccard_min: float = _JACCARD_MIN) -> Dict[str, Any]:
    """用新 raw/ 验证旧声明，写回 verdict，回算适应度闸。返回扁平指标 dict。"""
    root = Path(root)
    data = _load_claims(root)
    claims = [c for c in data.get("claims", []) if c.get("extracted_at")]
    if not claims:
        return {"status": "no_claims", "judged": 0, "confirmed": 0, "falsified": 0,
                "irrelevant": 0, "pending": 0, "verified_ratio": None, "falsified_ratio": None}

    pairs, use_emb = _candidate_pairs(root, claims, sim_threshold, jaccard_min)
    confirmed = falsified = irrelevant = pending = judged = 0

    for c, rel, rtext, sim, mode in pairs:
        verdict, evidence, ok = _judge(c["claim"], rtext)
        if not ok:
            pending += 1
            continue
        judged += 1
        if verdict == "confirm":
            confirmed += 1
        elif verdict == "falsify":
            falsified += 1
        else:
            irrelevant += 1
        c["verdict"] = verdict
        c["verdict_at"] = datetime.now().date().isoformat()
        c["evidence"] = {"rel": rel, "sim": sim, "mode": mode,
                         "evidence": evidence, "verified_at": c["verdict_at"]}
        c.setdefault("history", []).append({"verdict": verdict, "raw_rel": rel,
                                            "sim": sim, "mode": mode, "at": c["verdict_at"]})

    data["version"] = (data.get("version") or 0) + 1
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save_claims(root, data)

    def ratio(x):
        return round(x / judged, 3) if judged else None

    return {"status": "ok", "use_embedding": use_emb, "pairs_matched": len(pairs),
            "judged": judged, "confirmed": confirmed, "falsified": falsified,
            "irrelevant": irrelevant, "pending": pending,
            "verified_ratio": ratio(confirmed),     # 被现实支持的声明占比
            "falsified_ratio": ratio(falsified)}    # 被现实推翻的声明占比（=预测误差）


def fitness_from_claims(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从 claims 的 verdict 汇总适应度闸（供 report / 他处复用）。"""
    confirmed = falsified = irrelevant = 0
    for c in claims:
        v = c.get("verdict")
        if v == "confirm":
            confirmed += 1
        elif v == "falsify":
            falsified += 1
        elif v == "irrelevant":
            irrelevant += 1
    judged = confirmed + falsified + irrelevant
    def ratio(x):
        return round(x / judged, 3) if judged else None
    return {"judged": judged, "confirmed": confirmed, "falsified": falsified,
            "verified_ratio": ratio(confirmed), "falsified_ratio": ratio(falsified)}


def report(root: Union[str, Path]) -> str:
    data = _load_claims(root)
    claims = data.get("claims", [])
    if not claims:
        return "无声明（先跑 sync）"
    ft = fitness_from_claims(claims)
    L = ["# 🎯 RSI 适应度闸（方案 C / Layer 1 · 声明验证）", "",
         f"总声明 {len(claims)} · 已判定 {ft['judged']} · 待判定(ft/pending) {sum(1 for c in claims if not c.get('verdict'))}"]
    L += [f"- 被现实**证实**：{ft['confirmed']}（verified_ratio={ft['verified_ratio']}）",
          f"- 被现实**证伪**：{ft['falsified']}（falsified_ratio={ft['falsified_ratio']}）← 预测误差",
          "- 解读：falsified_ratio 持续走高 = 知识被现实推翻，自我改进应打折（呼应 P0-1 接地阀）"]
    return "\n".join(L) + "\n"


def stats(root: Union[str, Path]) -> str:
    data = _load_claims(root)
    claims = data.get("claims", [])
    verdicts = Counter(c.get("verdict") for c in claims)
    return ("claims.json 统计：总声明 {} · 判定分布 {}"
            ).format(len(claims), dict(verdicts) or "（全部 pending，LLM/embedding 未接通）")


def main() -> int:
    ap = argparse.ArgumentParser(description="kb_claim: 声明验证循环（方案 C/Layer 1）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sync", "validate", "report", "stats"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()

    if args.cmd == "sync":
        print(json.dumps(sync_claims(args.root), ensure_ascii=False, indent=2))
    elif args.cmd == "validate":
        print(json.dumps(validate(args.root), ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print(report(args.root))
    else:
        print(stats(args.root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
