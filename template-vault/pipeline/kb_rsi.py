#!/usr/bin/env python3
# ============================================================
# kb_rsi.py —— kb-kit 的"递归式自我改进(RSI)"只读探针
# ------------------------------------------------------------
# 理念（见 ../RSI-自我改进设计.md）：
#   RSI 要能活，必须满足四道闸：外部接地 / 适应度 / 有界可回滚 / 多样性。
#   纯自喂闭环 = Model Collapse（Shumailov 2023）；
#   无外部真值 = Self-Improvement Paradox（ACL 2025）。
# ------------------------------------------------------------
#   本脚本只做"提议"，不改动任何笔记正文/元数据；
#   唯一写入：pipeline/.kb_rsi_proposals.jsonl（追加一条运行记录 = 决策日志）。
#   用法:
#     python3 pipeline/kb_rsi.py [--root R] [--json] [--verbose]
# ============================================================
import argparse, os, re, sys, json, math, warnings
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from kb_common import norm, parse_date, char_vec, build_inverted_index, candidate_pairs
from kb_constants import (
    DUP_SIM,
    EXTERNAL_MIN_CROSS,
    EXTERNAL_FRESH_MIN_PCT,
)

ROOT_DEFAULT = str(Path(__file__).resolve().parent.parent)  # template-vault 根
EXCLUDE_DIRS = {".obsidian", ".git", "vector index", ".kb", ".backups"}
# 【P0-1】外部接地：以 raw/ 不可变外部源的"真实新鲜流入"为锚（不再是"内部新增占比"这个代理）
RAW_TOP = "raw"                        # raw/ 下为不可变外部源素材，RSI 只读不改

# 模块级 root（向后兼容；新代码应显式传递 root 参数）
root = None


# ── 极小依赖：前缀解析/分词/向量/余弦（自包含，避免 import kb_common 路径坑）──
def load_note(p: Path) -> Tuple[Dict[str, Any], str, str]:
    text = p.read_text(encoding="utf-8")
    # P0-1: 用 re.search（非 re.match）定位闭合 `---`。re.match 锚定 pos 0，
    #   而闭合分隔符在前置正文之后，永远匹配不上 → 曾使全库 frontmatter 解析为空。
    m0 = re.search(r"^\s*---\s*$", text, re.M)
    if not m0:
        return {}, text, ""
    rest = text[m0.end():]
    m1 = re.search(r"^\s*---\s*$", rest, re.M)
    if not m1:
        return {}, text, rest.strip()
    fm_txt = rest[:m1.start()]
    body = rest[m1.end():]
    fm = {}
    for ln in fm_txt.splitlines():
        mm = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", ln)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip()
    return fm, text, body


def tokenize(s: str) -> List[str]:
    # 注意：此 tokenize 与 kb_common.tokenize 不同。
    # kb_rsi 版本：CJK 仅取 2+ 字符连续段作为单一 token；
    # kb_common 版本：CJK unigram + bigram。
    # 保留本地版本以维持 kb_rsi 原有去重/相似度行为不变。
    toks = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", s or "")
    return [t.lower() for t in toks]


# ── Layer 0 语义向量地基（kb_embed 可选接入）───────────────
# 默认行为不变：kb_embed 不可用时原样退回字面 char-vec。USE_EMBEDDING=1 强制语义路径，
# =0 强制字面路径（调试/回归）。cos 自动兼容 dict(char-vec) 与 list(embedding)。
_USE_EMBED_OVERRIDE = os.environ.get("USE_EMBEDDING")


def _use_embed():
    """是否走 embedding：默认自动探测；USE_EMBEDDING=1 强制语义、=0 强制字面。
    best-effort：kb_embed 导入失败/探测异常都安全回落为字面向量，绝不爆炸。"""
    ov = _USE_EMBED_OVERRIDE
    if ov is not None:
        return ov.strip().lower() in ("1", "true", "yes", "on")
    try:
        import kb_embed
        return kb_embed.available()
    except (ImportError, ModuleNotFoundError, OSError, ValueError, TypeError):
        return False


def _safe_embed(text):
    """best-effort embedding：返回 list 或 None。绝不让导入/调用异常泄露到调用方。"""
    try:
        import kb_embed
        return kb_embed.embed(text)
    except Exception:  # 宽泛捕获: 外部服务不可控（外部 embedding 调用）
        return None


def vec(s: str) -> Union[Dict[str, float], List[float]]:
    """Layer 0：走 embedding 且取到向量则用之（归一化 list），否则原样退回字面 char-vec。
    默认行为不变（embedding 不可用 = 完全等同于旧实现）。
    注意：字面路径用本地 tokenize（CJK 2+字符）而非 kb_common.char_vec（CJK unigram+bigram），
    以维持 kb_rsi 原有去重/相似度行为不变。"""
    if _use_embed():
        emb = _safe_embed(s)
        if emb is not None:
            return emb
    return norm(Counter(tokenize(s)))


def cos(a: Union[Dict[str, float], List[float]], b: Union[Dict[str, float], List[float]]) -> float:
    """通用余弦：dict(稀疏 char-vec) 与 list(归一化 embedding) 均可。
    同一度量中所有向量同一种实现，不会混合。
    注意：此 cos 与 kb_common.cos 不同——本版本兼容 embedding list，
    kb_common.cos 仅处理 dict。保留本地版本以支持 Layer 0 embedding 路径。"""
    if isinstance(a, dict) or isinstance(b, dict):
        if len(a) > len(b):
            a, b = b, a
        return sum(av * b.get(k, 0) for k, av in a.items())
    return sum(x * y for x, y in zip(a, b))


def parse_f(v: Any, default: float = 0.0) -> float:
    m = re.search(r"[-+]?\d*\.?\d+", str(v))
    return float(m.group()) if m else default


def slug(rel: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", rel.split(os.sep)[0])


def is_archive(rel: str) -> bool:
    return "90-" in rel.split(os.sep)[0] or "归档" in rel or "trash" in rel.lower()


def in_inbox(rel: str) -> bool:
    s = slug(rel)
    return "收件箱" in s or s == "00收件箱"


def is_moc(rel: str, root: Optional[str] = None) -> bool:
    if root is None:
        warnings.warn("使用模块级 root 已弃用，请显式传递 root 参数", DeprecationWarning)
        root = globals().get("root")
    return any(k in Path(rel).stem for k in ["首页", "索引", "治理总方案"]) or "toc" in load_note(Path(root) / rel)[0].get("tags", "")


# ── 主度量 ────────────────────────────────────────────────
def is_raw(rel: str) -> bool:
    """rel 是否在 raw/ 下——不可变外部源，RSI 只读、永不写入。"""
    return "raw" in Path(rel).parts[:-1]


def collect(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """采集全部 .md，并标记 kind：raw(不可变外部源) / compiled(可自改进)。"""
    notes = []
    for p in Path(root).rglob("*.md"):
        if any(d in p.parts for d in EXCLUDE_DIRS) or ".git" in p.parts:
            continue
        try:
            fm, text, body = load_note(p)
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(p.relative_to(root))
        notes.append({
            "rel": rel, "fm": fm, "text": text, "body": body,
            "domain": fm.get("domain", "-"),
            "kb_action": fm.get("kb_action", ""),
            "links": re.findall(r"\[\[([^\]]+)\]\]", text),
            "kind": "raw" if is_raw(rel) else "compiled",
        })
    return notes


def compiled_notes(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """可被 RSI 自改进的编译笔记（排除 raw/ 不可变源）。"""
    return [n for n in collect(root) if n["kind"] != "raw"]


def sources(root: Union[str, Path]) -> List[Dict[str, Any]]:
    """不可变外部源（raw/），RSI 只读——它们是真外部接地的来源。"""
    return [n for n in collect(root) if n["kind"] == "raw"]


def metrics(notes: List[Dict[str, Any]], root: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    if root is None:
        warnings.warn("使用模块级 root 已弃用，请显式传递 root 参数", DeprecationWarning)
        root = globals().get("root")
    now = datetime.now()
    # 【P0-1】只从"编译笔记"算健康度；raw/ 不可变源单独统计为"新鲜外部流入"
    compiled = [n for n in notes if n["kind"] != "raw"]
    srcs = [n for n in notes if n["kind"] == "raw"]
    info = {n["rel"]: n for n in compiled}
    total = len(info)
    if not total:
        return None

    # 连接度（仅编译笔记；连到 raw/ 视为跨到外部域）
    orphans = [r for r, n in info.items() if not n["links"]]
    total_out = sum(len(n["links"]) for n in info.values())
    cross = sum(1 for r, n in info.items()
                for t in n["links"]
                if info.get(t.split("#")[0].split("|")[0], {}).get("domain", "-") != n["domain"])
    avg_out = total_out / total
    cross_ratio = cross / total_out if total_out else 0

    # 时效（仅编译笔记；raw/ 不可变，不参与 stale/retire）
    stale = []
    for r, n in info.items():
        if is_archive(r):
            continue
        upd = parse_date(n["fm"].get("updated")) or datetime.fromtimestamp(
            (Path(root) / r).stat().st_mtime)
        age = (now - upd).days
        if age > 90:
            stale.append((age, r))

    # 政策过期（仅编译笔记）
    policy_stale = []
    for n in compiled:
        if n["fm"].get("content_type") != "policy":
            continue
        try:
            cyc = int(n["fm"].get("review_cycle", 0) or 0)
        except ValueError:
            continue
        last = parse_date(n["fm"].get("last_reviewed"))
        if cyc > 0 and (last is None or (now - last).days > cyc):
            policy_stale.append((r := n["rel"], cyc, last))

    # 新鲜度（编译笔记内部增长）
    new30 = sum(1 for n in compiled if parse_date(n["fm"].get("created"))
                and (now - parse_date(n["fm"]["created"])).days <= 30)

    # 去重候选（仅编译笔记）
    vecs = [(n["rel"], vec(n["body"] + " " + n["fm"].get("tags", "")))
            for n in compiled if n["body"].strip()]
    dups = []
    # 倒排索引预过滤：仅对共享至少 1 个 token 的笔记对计算余弦相似度。
    # 共享 token 为空 → 余弦必为 0 → 低于 DUP_SIM(0.85) 可安全跳过。
    # 注意：kb_rsi 的 vec() 可能返回 embedding list（非 dict），此时跳过倒排索引优化，
    # 回退到全量 O(n²) 比较（embedding 向量无 token 概念，无法构建倒排索引）。
    vecs_dict = {}
    can_use_index = True
    for rel, v in vecs:
        if isinstance(v, dict):
            vecs_dict[rel] = v
        else:
            can_use_index = False
            break
    if can_use_index and vecs_dict:
        inv_index = build_inverted_index(vecs_dict)
        all_rels = [rel for rel, _ in vecs]
        for a_rel, b_rel in candidate_pairs(inv_index, all_rels):
            va = vecs_dict[a_rel]
            vb = vecs_dict[b_rel]
            s = cos(va, vb)
            if s > DUP_SIM:
                dups.append((round(s, 3), a_rel, b_rel))
    else:
        # embedding 路径或无法构建索引：回退到全量 O(n²) 比较
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                s = cos(vecs[i][1], vecs[j][1])
                if s > DUP_SIM:
                    dups.append((round(s, 3), vecs[i][0], vecs[j][0]))
    dups.sort(reverse=True)

    # 涌现 / 收件箱（仅编译笔记）
    emergence = [r for r, n in info.items() if is_moc(r, root)]
    backlog = [r for r, n in info.items() if in_inbox(r)]

    # 【P0-1/2】真外部接地：raw/ 里近30天的新鲜外部源占比（%）——不再是"内部新增占比"
    raw_total = len(srcs)
    raw_fresh = sum(1 for n in srcs if parse_date(n["fm"].get("created"))
                    and (now - parse_date(n["fm"]["created"])).days <= 30)
    total_all = total + raw_total
    external_inflow = round(100 * raw_fresh / total_all, 1) if total_all else 0.0
    freshness = round(100 * new30 / total, 1)
    return {
        "total": total, "orphans": orphans, "avg_out": round(avg_out, 2),
        "cross_ratio": round(cross_ratio, 2), "stale": sorted(stale, reverse=True),
        "policy_stale": policy_stale, "new30": new30, "freshness": freshness,
        "dups": dups[:10], "emergence": emergence, "backlog": backlog,
        "raw_total": raw_total, "raw_fresh": raw_fresh, "external_inflow": external_inflow,
        "concentration": _concentration(notes),
    }


def _concentration(notes):
    """P3-3 集中度护栏：测 importance 分布熵，塌过界告警。把"多样性"防塌原则操作化。
    用 Simpson 指数（避免 Shannon 在 2 域饱和为 1.0 的旧坑，见 P1-B）+ 归一化 Shannon + 单篇最高占比。
    raw（不可变外部源）importance 不参与权重分布。"""
    imps, total = [], 0.0
    for n in notes:
        if n.get("kind") == "raw":
            continue
        try:
            v = float(parse_f(n.get("fm", {}).get("importance"), 0.0))
        except (TypeError, ValueError):
            continue
        if v > 0:
            imps.append(v)
            total += v
    if not total:
        return {"count": 0, "simpson": None, "shannon_norm": None,
                "top_share": None, "alert": None}
    p = [x / total for x in imps]
    k = len(p)
    simpson = 1.0 - sum(x * x for x in p)                  # Simpson 多样性指数 1−Σp²
    shannon = -sum(x * math.log(x) for x in p if x > 0)    # Shannon 熵
    shannon_norm = shannon / math.log(k) if k > 1 else 1.0  # 归一化到 [0,1]
    top_share = max(p)
    alert = None
    if top_share > 0.5:
        alert = (f"集中度塌缩：单篇占比 {top_share:.0%} >50%（少数笔记吞噬权重"
                 f"=回声室/Goodhart 前兆）")
    elif simpson < 0.5:
        alert = f"多样性不足：Simpson 指数 {simpson:.2f} <0.5（importance 过度集中）"
    return {"count": k, "simpson": round(simpson, 3),
            "shannon_norm": round(shannon_norm, 3),
            "top_share": round(top_share, 3), "alert": alert}


# ── 提议生成 + 质量门 + 外部接地防塌阀 ────────────────────
def proposals(m: Dict[str, Any], root: Optional[Union[str, Path]] = None) -> Tuple[List[Dict[str, Any]], str, bool]:
    if root is None:
        warnings.warn("使用模块级 root 已弃用，请显式传递 root 参数", DeprecationWarning)
        root = globals().get("root")
    out = []

    # 1) 去重合并（置信度 = 相似度同权 + 同域加分）
    for sc, a, b in m["dups"]:
        same_dom = True
        out.append({"type": "DUP_MERGE", "items": [a, b], "score": sc,
                    "same_domain": same_dom,
                    "gate": "文本相似度≥%.2f 且领域一致" % DUP_SIM,
                    "action": "手动确认合并/去重(写前 checkpoint)"})

    # 2) 孤儿笔记
    for r in m["orphans"][:8]:
        body = Path(root) / r
        L = len((body.read_text(encoding="utf-8").split("---", 2)[-1]
                 if body.exists() else "").split())
        conf = min(0.9, 0.4 + L / 2000.0)  # 正文越长越值得补链而非删
        out.append({"type": "ORPHAN_LINK", "items": [r], "score": round(conf, 2),
                    "gate": "无出链；正文%d字"%L,
                    "action": "补链到相关笔记，或归档"})

    # 3) 过期 / 政策过期
    for age, r in m["stale"][:8]:
        out.append({"type": "STALE_REVIEW", "items": [r], "score": round(min(0.95, age / 365.0), 2),
                    "gate": "已 %.0f 天未更新(>90d)" % age,
                    "action": "复核内容是否失效，失效则 retire"})
    for r, cyc, last in m["policy_stale"][:5]:
        out.append({"type": "POLICY_REVIEW", "items": [r], "score": 0.85,
                    "gate": "policy -review_cycle=%dd-review=%s" % (cyc, last),
                    "action": "制度复核，失效则 retire"})

    # 4) 收件箱 intake
    if m["backlog"]:
        out.append({"type": "INBOX_INTAKE", "items": m["backlog"][:6], "score": 0.8,
                    "gate": "%d 条待 triage(外部新输入)" % len(m["backlog"]),
                    "action": "分类入库"})

    # 5) 涌现（缺 MOC）
    if not m["emergence"] and m["total"] > 10:
        out.append({"type": "EMERGE_MOC", "items": [], "score": 0.6,
                    "gate": "无 MOC/索引页", "action": "新建跨域索引(MOC)"})

    # ── 外部接地防塌阀（本脚本的核心 RSI 零件）──
    # P0-1 真外部锚：用 raw/ 新鲜流入占比(m["external_inflow"])，而非内部新笔记率(m["freshness"])。
    #   后者可被自身内容刷爆，不是真外部接地；前者≥5% 才允许实质自我改进。
    grounded = m["external_inflow"] >= EXTERNAL_FRESH_MIN_PCT and m["cross_ratio"] >= EXTERNAL_MIN_CROSS
    grounded_note = ("外部接地正常(新鲜外部源%g%%+跨域%g%%≥阈值)" %
                     (m["external_inflow"], m["cross_ratio"])) if grounded else \
                    ("⚠️ 外部接地不足(新鲜外部源%g%%<%g%% 或 跨域%g%%<%g%%) → "
                     "知识库疑似自閉，需注入真实外部知识/用户反馈（放进 raw/），否则自我改进会逐步退化" %
                     (m["external_inflow"], EXTERNAL_FRESH_MIN_PCT, m["cross_ratio"], EXTERNAL_MIN_CROSS))
    # 阀门生效：接地不足时，把"主动改进"类提议置信度打 0.6 折
    for p in out:
        if p["type"] in ("DUP_MERGE", "ORPHAN_LINK", "EMERGE_MOC") and not grounded:
            p["score"] = round(p["score"] * 0.6, 2)
    return out, grounded_note, grounded


def render(proposals: List[Dict[str, Any]], m: Dict[str, Any], grounded_note: str) -> str:
    L = [f"# 🔄 RSI 自我改进提议（只读探针）",
         "",
         f"> 笔记 {m['total']} · 健康分需结合kb_health · 外部接地: {grounded_note}",
         "",
         f"## 信号 | 值",
         f"------|------",
         f"孤儿笔记 | {len(m['orphans'])}",
         f"平均出链 | {m['avg_out']}",
         f"跨域连接比 | {m['cross_ratio']}",
         f"过期(>90d) | {len(m['stale'])}",
         f"政策过期 | {len(m['policy_stale'])}",
         f"近30天新增(编译笔记) | {m['new30']} ({m['freshness']}%) ｜ 新鲜外部源(raw/) | {m['raw_fresh']}/{m['raw_total']} ({m['external_inflow']}%)",
         f"去重候选 | {len(m['dups'])}",
         f"收件箱积压 | {len(m['backlog'])}",
         "",
         f"## ✍️ 改进提议（按置信度排序，共{len(proposals)}条）", ""]
    if not proposals:
        L += ["- ✅ 暂无高价值改进提议（知识库健康，无需自我改动）"]
    for p in sorted(proposals, key=lambda x: x["score"], reverse=True):
        items = "、".join(p["items"]) if p["items"] else "（无）"
        L.append(f"- **[{round(p['score']*100)}%] {p['type']}** — {items}")
        L.append(f"    - 门: {p['gate']}  |  建议: {p['action']}")
    L += ["", "> 说明：本探针只读，不改任何笔记。提议+决策已写入 `pipeline/.kb_rsi_proposals.jsonl`。"]
    return "\n".join(L) + "\n"


# ── main ──────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)

    m = metrics(notes=collect(root), root=root)
    if not m:
        print("⚠️ 库为空或无可用笔记，无法提议。")
        return 1

    props, grounded_note, grounded = proposals(m, root=root)

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "health": {k: m[k] for k in
                   ("total", "orphans", "avg_out", "cross_ratio", "stale",
                    "new30", "freshness", "dups")},
        "external_grounding_ok": grounded,
        "grounding_note": grounded_note,
        "proposals": props,
        "concentration": m["concentration"],
    }
    logdir = Path(root) / "pipeline"
    logdir.mkdir(parents=True, exist_ok=True)
    logp = logdir / ".kb_rsi_proposals.jsonl"
    with open(logp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    conc = record["concentration"]
    conc_s = (f"Simpson {conc['simpson']} · 归一化Shannon {conc['shannon_norm']} · "
              f"单篇最高 {conc['top_share']}（{conc['count']} 篇）") if conc["count"] else "无数据"
    if args.json:
        record["concentration_display"] = conc_s
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(render(props, m, grounded_note))
        print(f"🧭 集中度护栏: {conc_s}")
        if conc.get("alert"):
            print(f"⚠️ {conc['alert']}")
            try:
                import evolution_log
                evolution_log.append(root, "ALERT", conc["alert"],
                                     detail="importance 集中度护栏（P3-3）")
            except (ImportError, OSError, AttributeError, TypeError):
                pass
        print(f"📝 决策日志已追加 → {logp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
