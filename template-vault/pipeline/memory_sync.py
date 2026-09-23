#!/usr/bin/env python3
# ============================================================
# memory_sync.py —— 记忆同步（成长引擎 ③c：agent 记忆流 → 知识库 的 ETL）
#   review : 逐条原始记忆跑「五问 + 去重 + 路由」，出待晋升清单，不写
#   promote: 写前 checkpoint → 对过审记忆结构化 + 路由到 PARA + 精确去重 → 提交
#   硬门槛（可计算）：⑤信号密度 ≥阈值 + 新颖度(对比 KB 顶 sims<0.70)+ 长度≥最小
#   问①②③(根因/泛化/持久)为语义判断：--semantic 走 ornith 作答（仅设 ORNITH 时），
#   否则人工复核 review 输出。五问：任一'否'则仅留 daily memory。
#   去重：与 KB 顶 sims≥0.92 → 重复→跳过(提示增量到某笔记)；0.70-0.92 → 相关→标注 related。
#   安全：只 add 新建笔记文件，绝不 git add -A（不 sweep Obsidian 运行时态）。
#   用法:
#     python3 pipeline/memory_sync.py review  [--root R] [--density D] [--min-chars N]
#     python3 pipeline/memory_sync.py promote [--root R] [--density D] [--min-chars N] [--semantic]
# ============================================================
import argparse, os, re, sys, json, datetime, subprocess
from pathlib import Path
from collections import Counter
import rag
from kb_common import (ROOT_DEFAULT, iter_notes, load_note_full as load,
                       infer_domain, infer_tags, list_of, fmt_value,
                       parse_date_str as parse_date, get_dedup_thresholds,
                       content_fingerprint)

MIN_CHARS_DEFAULT = 80
# DUP_SIM / NOVEL_SIM 已改为动态加载（Task 3），见 grade() 中 get_dedup_thresholds()
# 保留模块级常量作为默认值，供 memory_ingest.py 向后兼容 import
DUP_SIM = 0.92    # 默认值（实际使用时通过 get_dedup_thresholds 动态获取）
NOVEL_SIM = 0.70  # 默认值
DEFAULT_DENSITY = 0.15

# 信号词：行为指令 / 知识要点占比（⑤密度）
# Task 5: 扩展英文技术信号词（双语化）
SIGNAL_KW = {"规则", "应该", "必须", "务必", "注意", "记住", "命令", "检查", "排查", "部署",
             "配置", "复盘", "教训", "经验", "决策", "应", "禁止", "报错", "错误", "根因",
             "fix", "error", "run", "deploy", "build", "test", "verify", "check", "install",
             "config", "pull", "push", "restart",
             "debug", "rollback", "refactor", "optimize", "setup",
             "bug", "issue", "warning", "log", "trace", "profile", "benchmark"}
CMD_PREFIX = ("$ ", "sudo ", "git ", "pip ", "docker ", "npm ", "cd ", "#!", "`")

# Task 5: 五问门禁关键词双语化
Q1_KW = ["根因", "为什么", "原因", "因为", "所以", "导致",
         "root cause", "because", "therefore", "thus", "hence",
         "reason", "why", "due to", "leads to", "results in"]
Q3_KW = ["长期", "持久", "预防", "反复", "每次", "总是", "原则", "机制",
         "always", "every time", "permanently", "long-term",
         "prevent", "recurring", "principle", "mechanism",
         "consistently", "by default"]

# 路由：域 → 目标 bucket（真实目录名）
FOLDER_TARGET = {
    "开发": "20-技术 Technology",
    "运维": "20-技术 Technology",
    "安全": "20-技术 Technology",
    "数据": "20-技术 Technology",
    "产品": "40-资源库 Resources",
    "管理": "60-运营 Operations",
    "综合": "40-资源库 Resources",
}
DEFAULT_TARGET = "00-收件箱 Inbox"


def signal_density(body):
    lines = [l for l in body.splitlines() if l.strip()]
    if not lines:
        return 0.0
    sig = 0
    for l in lines:
        ll = l.strip().lower()
        if any(k in ll for k in SIGNAL_KW):
            sig += 1
        elif any(ll.startswith(p) for p in CMD_PREFIX):
            sig += 1
    return sig / len(lines)


def slug(name):
    t = re.sub(r"[/\\:*?\"<>|#\n\t]+", "-", name.strip(" \t#")).strip("-")
    t = re.sub(r"[^\w\u4e00-\u9fff\-]", "", t)[:60]
    return t or "memory-note"


def load_index(root):
    idx_path = Path(root) / "vector index" / "df_idf.json"
    if not idx_path.exists():
        rag.cmd_index(root)
    else:
        # 增量检测：检查笔记是否有变更，有变更才重建
        try:
            payload = json.loads(idx_path.read_text(encoding="utf-8"))
            doc_hashes = payload.get("doc_hashes", {})
            changed = False
            for p in iter_notes(root):
                rel = str(p.relative_to(root))
                try:
                    current_hash = content_fingerprint(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
                if doc_hashes.get(rel) != current_hash:
                    changed = True
                    break
            if changed:
                rag.cmd_index(root)
        except (OSError, json.JSONDecodeError, KeyError):
            # 索引文件损坏，重建
            rag.cmd_index(root)
    payload = json.loads(idx_path.read_text(encoding="utf-8"))
    vocab, idf, tf_all, meta_all = payload["vocab"], payload["idf"], payload["tf"], payload["meta"]
    rel2vec = {r: rag._vec(tf_all[r], vocab, idf) for r in tf_all}
    return vocab, idf, rel2vec, meta_all


def load_state(root):
    """加载状态文件，优先使用 StateStore（.kb/state/），回退旧路径。"""
    default = {"promoted": {}}
    try:
        from state_manager import StateStore
        store = StateStore(root)
        if store.exists("memory_sync_state.json"):
            return store.load("memory_sync_state.json", default)
        # 旧文件迁移
        legacy = Path(root) / ".memory_sync_state.json"
        if legacy.exists():
            try:
                data = json.loads(legacy.read_text(encoding="utf-8"))
                store.save("memory_sync_state.json", data)
                return data
            except (OSError, json.JSONDecodeError):
                pass
        return default
    except ImportError:
        try:
            return json.loads((Path(root) / ".memory_sync_state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default


def save_state(root, state):
    """保存状态文件，优先使用 StateStore（.kb/state/），回退旧路径。"""
    try:
        from state_manager import StateStore
        StateStore(root).save("memory_sync_state.json", state)
    except ImportError:
        (Path(root) / ".memory_sync_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def best_match(vocab, idf, rel2vec, meta_all, body, topk=2):
    if not rel2vec:
        return []
    qv = rag._vec(Counter(rag.tokenize(body)), vocab, idf)
    scored = [(r, rag._cos(v, qv), meta_all.get(r, {})) for r, v in rel2vec.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [(r, s, m) for r, s, m in scored if s > 0][:topk]


def memory_files(root):
    out = []
    for p in iter_notes(root):
        rel = str(p.relative_to(root))
        if rel.split(os.sep)[0] == "memory":
            fm, text, block, body = load(p)
            out.append((rel, p, fm, body))
    out.sort(key=lambda x: x[0])
    return out


def grade(rel, fm, body, vocab, idf, rel2vec, meta_all, density_thr, min_chars, semantic):
    dens = signal_density(body)
    matches = best_match(vocab, idf, rel2vec, meta_all, body, topk=2)
    best = matches[0] if matches else None
    best_sim = best[1] if best else 0.0
    best_rel = best[0] if best else None
    # Task 3: 动态去重阈值（按 domain 自适应）
    _domain = infer_domain("", fm, "", body)
    _thr = get_dedup_thresholds(_domain)
    dup_sim = _thr["dup_sim"]
    novel_sim = _thr["novel_sim"]
    is_dup = best is not None and best_sim >= dup_sim
    near = best is not None and novel_sim <= best_sim < dup_sim
    # ── 五问门禁（FR-3.3.10）──────────────────────────────
    # 硬门 ④增量 ⑤密度（不通过 → 不晋升）
    q5 = dens >= density_thr          # ⑤ 密度
    q4 = (best_sim < novel_sim)       # ④ 增量（新颖）
    len_ok = len(body.replace("\n", "")) >= min_chars
    # 软门 ①根因 ②泛化 ③持久（不通过 → 标记 review_needed，不阻断）
    # Task 5: 五问关键词双语化，body.lower() 匹配
    _body_lower = body.lower()
    q1 = any(w in _body_lower for w in Q1_KW)
    q2 = len(matches) >= 2 and any(m[1] > 0.1 for m in matches[1:])
    q3 = any(w in _body_lower for w in Q3_KW)
    soft_fail_reasons = []
    if not q1: soft_fail_reasons.append("①缺根因")
    if not q2: soft_fail_reasons.append("②缺泛化")
    if not q3: soft_fail_reasons.append("③缺持久性")
    verdict, reasons = True, []
    if not q5: reasons.append("⑤密度不足(信号<阈值)")
    if not len_ok: reasons.append("过短(<%s字)" % min_chars)
    if is_dup: reasons.append("与 KB 重复(→增量)" + (f" {best_rel}" if best_rel else ""))
    if not q4: reasons.append("④与 KB 已覆盖(增量比高)")
    # ①②③ 语义（可选）
    semantic_ok = True
    if semantic and os.environ.get("ORNITH_API_KEY"):
        try:
            ans = semantic_ask(root, body, matches)
            semantic_ok = (ans is not None) and ("不晋升" not in ans)
        except (OSError, ValueError, KeyError, TypeError):
            semantic_ok = True
    # 硬门：④⑤ + 长度 + 不重复 + 语义
    verdict = q5 and len_ok and (not is_dup) and q4 and semantic_ok
    # 软门：①②③ 不通过 → review_needed 标记
    review_needed = bool(soft_fail_reasons)
    return {"rel": rel, "density": round(dens, 2), "best_rel": best_rel, "best_sim": round(best_sim, 3),
            "is_dup": is_dup, "near": near,
            "q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5, "len_ok": len_ok,
            "semantic_ok": semantic_ok, "review_needed": review_needed,
            "soft_fail_reasons": soft_fail_reasons,
            "reasons": reasons, "pass": verdict, "domain": infer_domain("", fm, "", body),
            "stem": Path(rel).stem, "fm": fm, "body": body}


def semantic_ask(root, body, matches):
    hits = [r for r, _, _ in matches]
    ctx = ""
    for r in hits:
        try:
            b = (Path(root) / r).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        ctx += rag.best_sentence(b, body)[:160] + "\n"
    q = ("一段 agent 原始记忆，逐条回答五问并给结论(晋升/不晋升)：\n"
         "①根因②泛化(≥2上下文)③持久(30天≥3次或长期预防值)④增量(系统覆盖<30%)⑤密度(行为指令≥15%%)\n"
         "记忆：%s\nKB片段：%s" % (body, ctx))
    try:
        return rag.llm_answer(root, q, hits)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def memory_title(body, stem):
    """主题标题：跳过 markdown 标题行与日期前缀，取第一条实质内容行。"""
    for line in body.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("`") or raw.startswith("#"):
            continue
        s = re.sub(rf"^{re.escape(stem)}\s+", "", raw)
        if s:
            return s[:60]
    return stem


def build_note(root, rel_src, title, body, bucket, meta, dens, review_needed=False, soft_fail_reasons=None):
    src = Path(root) / rel_src
    rel_target = "%s/%s.md" % (bucket, slug(f"{src.stem} {title}"))
    domain = meta.get("domain", infer_domain("", meta, title, body))
    created = parse_date(src.stem, datetime.date.fromtimestamp(src.stat().st_mtime))
    today = datetime.date.today()
    fm2 = {
        "tags": list_of(meta) or infer_tags(bucket),
        "status": "active", "domain": domain,
        "created": created if isinstance(created, str) else today,
        "updated": today,
        "importance": 0.5,
        "kb_target": bucket, "kb_action": "new",
        "kb_summary": title,
        "kb_source": "memory_sync",
        "memory_source": rel_src,
        "memory_density": round(dens, 2),
    }
    if review_needed:
        fm2["review_needed"] = True
        fm2["review_reasons"] = soft_fail_reasons or []
    text = "---\n" + "\n".join("%s: %s" % (k, fmt_value(v)) for k, v in fm2.items()) \
        + "\n---\n\n" + body.lstrip("\n")
    return rel_target, text


def do_review(root, density_thr, min_chars, semantic):
    vocab, idf, rel2vec, meta_all = load_index(root)
    state = load_state(root)
    promoted = state.get("promoted", {})
    mems = memory_files(root)
    print(f"🔍 记忆源 {len(mems)} 条 · 硬门槛 密度≥{density_thr} 长度≥{min_chars} 新颖(顶sims<{NOVEL_SIM})")
    if not mems:
        print("  （无 memory/ 源——记忆同步无输入，数据源到位后自动生效）")
    for rel, _, fm, body in mems:
        if rel in promoted:
            rec = promoted[rel]
            print(f"\n[📌 已晋升] {rel}")
            print(f"   → {rec['note']}  (密度 {rec['density']} · {rec['date']})")
            continue
        g = grade(rel, fm, body, vocab, idf, rel2vec, meta_all, density_thr, min_chars, semantic)
        flag = "✅ 待晋升" if g["pass"] else "⏸ 留档"
        print(f"\n[{flag}] {rel}")
        print(f"   密度 {g['density']} · 顶命中 {g['best_sim']} ← {g['best_rel'] or '—'} · 域 {g['domain']}")
        if g["reasons"]:
            print(f"   未晋升因: {'; '.join(g['reasons'])}")
        elif g["near"]:
            print(f"   相关(顶命中 {g['best_sim']} ← {g['best_rel']}，标 related 后新建)")
    return 0


def do_promote(root, density_thr, min_chars, semantic):
    rel2vec_v = load_index(root)
    vocab, idf, rel2vec, meta_all = rel2vec_v
    state = load_state(root)
    promoted = state.setdefault("promoted", {})
    print("⛳ 写前 checkpoint（快照 HEAD，仅记录不 sweep）")
    baseline = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
    created, skipped, skipped_old, nearc = 0, 0, 0, 0
    staged = []
    for rel, p, fm, body in memory_files(root):
        if rel in promoted:
            skipped_old += 1          # 幂等：已晋升过，跳过
            continue
        g = grade(rel, fm, body, vocab, idf, rel2vec, meta_all, density_thr, min_chars, semantic)
        if not g["pass"]:
            skipped += 1
            continue
        bucket = FOLDER_TARGET.get(g["domain"], DEFAULT_TARGET)
        rel_target, text = build_note(root, rel, memory_title(body, g["stem"]), body, bucket, g["fm"], g["density"],
                                       review_needed=g.get("review_needed", False),
                                       soft_fail_reasons=g.get("soft_fail_reasons", []))
        target = Path(root) / rel_target
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            rel_target = "%s/%s-d%s.md" % (bucket, slug(g["stem"]),
                                           datetime.date.today().strftime("%m%d"))
            target = Path(root) / rel_target
        target.write_text(text, encoding="utf-8")
        staged.append(rel_target)
        promoted[rel] = {"note": rel_target, "bucket": bucket,
                         "density": g["density"], "date": datetime.date.today().strftime("%F")}
        created += 1
        if g["near"]:
            nearc += 1
    save_state(root, state)
    if staged:
        subprocess.run(["git", "-C", root, "add", "--", *staged], capture_output=True)
        if subprocess.run(["git", "-C", root, "status", "--porcelain"],
                              capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "-C", root, "commit", "-q",
                                "-m", f"kb: 记忆同步晋升 {created} 条（源 memory/）"],
                               capture_output=True)
        print(f"✅ 晋升 {created} 条  已晋升跳过 {skipped_old} 条  留档 {skipped} 条  相关 {nearc} 条")
        print(f"   基线 {baseline}（revert 即可回滚本次晋升）")
    else:
        why = []
        if skipped_old: why.append(f"已晋升跳过 {skipped_old} 条")
        if skipped: why.append(f"过门槛未通过留档 {skipped} 条")
        if not why: why.append("无记忆源 / 全部过门槛未通过")
        print("✅ 无记忆晋升（" + " · ".join(why) + "）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review"); r.add_argument("--root", default=ROOT_DEFAULT)
    r.add_argument("--density", type=float, default=DEFAULT_DENSITY)
    r.add_argument("--min-chars", type=int, default=MIN_CHARS_DEFAULT)
    r.add_argument("--semantic", action="store_true")
    pr = sub.add_parser("promote"); pr.add_argument("--root", default=ROOT_DEFAULT)
    pr.add_argument("--density", type=float, default=DEFAULT_DENSITY)
    pr.add_argument("--min-chars", type=int, default=MIN_CHARS_DEFAULT)
    pr.add_argument("--semantic", action="store_true")
    args = ap.parse_args()
    if args.cmd == "review":
        return do_review(args.root, args.density, args.min_chars, args.semantic)
    return do_promote(args.root, args.density, args.min_chars, args.semantic)


if __name__ == "__main__":
    sys.exit(main())
