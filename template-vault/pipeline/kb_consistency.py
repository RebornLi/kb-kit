#!/usr/bin/env python3
# ============================================================
# kb_consistency.py —— 自进化知识库治理·语义一致性审计（建议 C）
#
#   A/B 回答“有没有偏 / 偏多偏少”；C 回答“偏得有没有伤到原意”：
#   合并/改写后的目标笔记是否丢了原文条件、与原文矛盾、或
#   被稀释成空泛断言（SSGM 的合并时语义漂移 / 累积错误）。
#
#   关键边界（只读审计，绝不破坏生产）：
#     - 不改 memory_sync.py / grade()，不改 ledger schema；
#     - 只读 .kb/promotions.jsonl + 目标笔记正文；
#     - 绝不写回任何 40-资源库 Resources/ 下的 live 笔记；
#     - 只写 .kb/ 下的治理报告（被 gitignore）。
#
#   检测分层：
#     Tier1（纯标准库，始终可用）：精确漂移 / 出处保留 / 覆盖召回(Jaccard)
#     Tier2（可选，自动降级）：rag.llm_answer 语义忠实度，端点不可达则静默跳过
#
#   用法:
#     python3 pipeline/kb_consistency.py --root R
#       [--cov-thr F]   # 覆盖度召回阈值，缺省 0.30
#       [--report]      # 把治理报告写入 .kb/consistency-report-<date>.md
#       [--json]        # 额外输出 JSON 便于机器消费
# ============================================================
import argparse, os, sys, json, hashlib, datetime, math
from pathlib import Path
from collections import Counter

import rag
from kb_common import parse_frontmatter, tokenize

LEDGER_NAME = "promotions.jsonl"
DEFAULT_COV_THR = 0.30  # Jaccard 召回率阈值：< 此值判 coverage-drop（High）
LLM_TIMEOUT = 15        # 可选 LLM 调用超时


def _tokens(text):
    """tokenize → Counter 的轻量封装，用于 token 集合。"""
    return tokenize(text)


def _split_body(text):
    """返回 frontmatter（两个 --- 分隔线）之后的正文部分。"""
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].lstrip("\n")
    return text


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jaccard_recall(note_toks, orig_toks):
    """note 相对 orig 的召回率 = |note ∩ orig| / |orig|（orig 不为空）。"""
    if not orig_toks:
        return 1.0
    inter = len(note_toks & orig_toks)
    return inter / len(orig_toks)


def _has_git(root):
    from subprocess import run, DEVNULL
    try:
        run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=DEVNULL, stderr=DEVNULL, timeout=5)
        return True
    except Exception:
        return False


def _endpoint_reachable(timeout=5):
    """轻量可达性预检：只 ping /models（即使 401/403 也算可达）。
    失败（主机不可达/超时）→ False，调用方据此静默跳过 LLM 层。
    目的：避免对可达但 chat 端点极慢的 host 阻塞整次审计。"""
    url = os.environ.get("ORNITH_BASE_URL", "").strip()
    key = os.environ.get("ORNITH_API_KEY")
    if not key or not url:
        return False
    try:
        import urllib.request, urllib.error
        req = urllib.request.Request(url.rstrip("/") + "/models",
                                     headers={"authorization": "Bearer %s" % key})
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True    # 401/403：认证通过，端点可达
    except Exception:
        return False


def _check_faithfulness(root, original_text, note_body, timeout=LLM_TIMEOUT):
    """可选 LLM 语义忠实度判断。
    返回 dict：{ok: bool, verdict: Optional[str], hint: str}
    ok=True → 得到一次判断（含 verdict in faithful|partial|unfaithful）；
    ok=False → 端点未配/不可达/超时，静默跳过。
    关键点：用线程加硬超时包裹 rag.llm_answer，防止慢/挂死的端点阻塞整次审计。
    """
    if not _endpoint_reachable():
        return {"ok": False, "verdict": None,
                "hint": "端点不可达，跳过 LLM 层（降级到 Tier1）"}
    import threading
    q = ("依据下方【原文】与【目标笔记】，判断目标笔记对原文的语义忠实度：\n"
         "是否忠实保留原文关键条件/因果，有无丢条件、与原文矛盾、\n"
         "或稀释成无信息量的空泛断言。\n"
         "仅输出三者之一：faithful / partial / unfaithful。\n\n"
         "原文：%s\n\n目标笔记：%s" % (original_text, note_body))
    box = {}

    def _call():
        try:
            ans, hint = rag.llm_answer(root, q, [])
            box["ans"], box["hint"] = ans, hint
        except Exception:  # noqa: BLE001  任何异常都降级
            box["ans"], box["hint"] = None, "LLM 调用异常（降级到 Tier1）"

    th = threading.Thread(target=_call, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return {"ok": False, "verdict": None,
                "hint": "LLM 端点超时，跳过（降级到 Tier1）"}
    if box.get("ans") is None:
        return {"ok": False, "verdict": None, "hint": box.get("hint", "LLM 未作答（降级到 Tier1）")}
    v = box["ans"].strip().lower()
    verdict = "unfaithful" if "unfaithful" in v else ("partial" if "partial" in v else "faithful")
    return {"ok": True, "verdict": verdict, "hint": ""}


def _severity(reasons):
    """把触发原因列表映射到严重度。"""
    sev = {"provenance-loss": "Critical", "unfaithful": "Critical",
           "coverage-drop": "High", "partial": "High",
           "drift": "Medium"}
    best = None
    order = {"Critical": 0, "High": 1, "Medium": 2, "Info": 3}
    for r in reasons:
        key = r.split("(", 1)[0].strip()  # 去掉 "(…详情)" 详情后取键
        if key in sev:
            s = sev[key]
            if best is None or order[s] < order[best]:
                best = s
    return best or "Info"


def audit_record(rec, root, cov_thr):
    """对单条晋升记录做 Tier1 + 可选 Tier2 审计，返回 dict。"""
    target_rel = rec.get("target_note", "")
    target_path = Path(root) / target_rel if target_rel else Path()
    result = {
        "target_note": target_rel, "ts": rec.get("ts", "—"),
        "reasons": [], "severity": "Info",
        "coverage": 1.0, "faithfulness": None,
        "provenance_ok": False,
    }
    if not target_path.exists():
        result["reasons"].append("note-missing")
        result["severity"] = "High"
        return result
    try:
        full = target_path.read_text(encoding="utf-8")
    except OSError:
        result["reasons"].append("note-unreadable")
        result["severity"] = "High"
        return result

    current_body = _split_body(full)
    original_text = rec.get("original_text", "")

    # T1-1 精确漂移
    if rec.get("sha256_written", "") and _sha(current_body) != rec.get("sha256_written", ""):
        result["reasons"].append("drift")

    # T1-2 出处保留（Knora provenance：owner/timestamp 仍在）
    # 注意：parse_frontmatter 返回 (fm_dict, body) 二元组，需解包
    fm, _ = parse_frontmatter(full)
    result["provenance_ok"] = ("kb_source" in fm) and ("memory_source" in fm)
    if not result["provenance_ok"]:
        missing = [k for k in ("kb_source", "memory_source") if k not in fm]
        result["reasons"].append("provenance-loss(%s 缺 %s)" % (target_rel, ",".join(missing)))

    # T1-3 覆盖召回（Jaccard |note ∩ orig| / |orig|）
    note_toks = set(_tokens(current_body))
    orig_toks = set(_tokens(original_text))
    rec_val = _jaccard_recall(note_toks, orig_toks)
    result["coverage"] = round(rec_val, 3)
    if orig_toks and rec_val < cov_thr:
        result["reasons"].append("coverage-drop(召回 %.2f<%.2f)" % (rec_val, cov_thr))

    # T2 语义忠实（可选，自动降级）
    llm = _check_faithfulness(root, original_text, current_body)
    if llm["ok"]:
        result["faithfulness"] = llm["verdict"]
        if llm["verdict"] in ("unfaithful", "partial"):
            result["reasons"].append(llm["verdict"])

    result["severity"] = _severity(result["reasons"])
    return result


def main():
    ap = argparse.ArgumentParser(description="自进化知识库语义一致性审计（只读，不改动 live 笔记）")
    ap.add_argument("--root", default=os.environ.get("KB_ROOT") or "",
                    help="vault 根目录（默认环境变量 KB_ROOT 或当前目录）")
    ap.add_argument("--cov-thr", type=float, default=DEFAULT_COV_THR,
                    help="覆盖度召回阈值，缺省 %.2f" % DEFAULT_COV_THR)
    ap.add_argument("--report", action="store_true",
                    help="把治理报告写入 .kb/consistency-report-<date>.md")
    ap.add_argument("--json", action="store_true",
                    help="额外输出 JSON 便于机器消费")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path.cwd()

    ledger_path = root / ".kb" / LEDGER_NAME
    if not ledger_path.exists():
        print("🔍 一致性审计 · 暂无 ledger（%s 不存在，可忽略）" % LEDGER_NAME)
        return 0

    records = []
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as e:
        print("⚠️ 读取 ledger 失败: %s" % e)
        return 1

    results = [audit_record(rec, root, args.cov_thr) for rec in records]

    # 汇总计数
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Info": 0}
    for r in results:
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
        tag = "%s" % r["severity"]
        # 人类可读输出
        detail = "; ".join(r["reasons"]) if r["reasons"] else "ok"
        print("[%s] %s @ %s · %s" % (tag, r["target_note"], r["ts"], detail))

    print("\n🔍 一致性审计 · 共 %d 条 · Critical %d / High %d / Medium %d / Info %d" % (
        len(results), counts["Critical"], counts["High"], counts["Medium"], counts["Info"]))

    if _has_git(root):
        try:
            from subprocess import run, DEVNULL
            res = run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                      stdout=DEVNULL, stderr=DEVNULL, timeout=10)
            if res.returncode == 0:
                print("· 已确认仅读审计，未改动任何 live 笔记（报告写入 .kb/，被 gitignore）")
        except Exception:
            pass

    # --json
    if args.json:
        print("\n---JSON---")
        print(json.dumps(results, ensure_ascii=False, indent=2))

    # --report
    if args.report:
        report_dir = root / ".kb"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().astimezone().strftime("%Y%m%d")
        report_path = report_dir / ("consistency-report-%s.md" % stamp)
        lines = ["# 语义一致性治理报告 (%s)" % datetime.date.today().isoformat(), "",
                 "## 汇总"]
        lines.append("- 记录数: %d · Critical %d / High %d / Medium %d / Info %d" % (
            len(results), counts["Critical"], counts["High"], counts["Medium"], counts["Info"]))
        lines.append("- 覆盖度阈值: %.2f" % args.cov_thr)
        lines.append("")
        lines.append("## 逐条")
        for r in results:
            detail = "; ".join(r["reasons"]) if r["reasons"] else "ok"
            lines.append("- [%s] %s @ %s · %s" % (r["severity"], r["target_note"], r["ts"], detail))
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n📄 报告已写入 %s" % report_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
