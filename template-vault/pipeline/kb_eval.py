#!/usr/bin/env python3
# ============================================================
# kb_eval.py —— RAG 检索质量评估网（Node 0.1 / 地基Phase）
# ------------------------------------------------------------
# 把「检索变好了吗」从主观感觉变成可复跑的量化基线。
#
# 设计铁律（贴合 kb-kit 哲学）：
#   1. 复用【真实检索路径】pipeline/rag.py query --json 作为检索 oracle，
#      测的是线上那条链路本身，不是另起炉灶。
#   2. 零依赖确定性指标：用 kb_common.tokenize（与 rag 索引同构的词表）做
#      token 重叠，离线可跑、结果稳定，作为默认基线。
#   3. 可选 LLM judge：ORNITH 接通时 --judge on 用 kb_claim._chat 对「忠实度/
#      相关性」做 0-1 判分 + 理由，细化确定性近似；端点不可达则静默降级回
#      确定性指标（绝不报错爆炸）。
#   4. 回归护栏：每次跑把指标落 test_eval/eval_baseline_YYYYMMDD.json，
#      后续可 diff 对比——这是 RSI-T3 调参的"外部接地信号"。
#   5. 只读：不改索引结构、不改笔记，只读调 rag.py。
#
# 四项指标（RAGAS 式，口径见下）：
#   · Context Recall  上下文召回 = 标准答案关键术语被检索上下文（top-k snippet 并集）覆盖的比例
#   · Faithfulness    忠实度     = 参考答案的句子里、被检索上下文支撑(≥50% token)的比例
#   · Answer Rel.     答案相关   = 问句与答案的 token 重叠（Dice）
#   · Golden Hit      黄金命中   = top-k 里是否含「藏标准答案的那篇笔记」(检索准确性核心)
#
# 用法：
#   python3 pipeline/kb_eval.py eval   [--root R] [--rag PATH] [--top N]
#                                      [--limit N] [--judge on|off] [--strict]
#   python3 pipeline/kb_eval.py dataset [--root R]          # 列出标注集
#   python3 pipeline/kb_eval.py sample  [--dest PATH]       # 写出入门级标注集
#   python3 pipeline/kb_eval.py config  [--root R]          # 看阈值配置
# ============================================================
import argparse, json, math, os, re, subprocess, sys, shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ── 路径自解 ───────────────────────────────────────────────
PIPE_DIR = Path(__file__).resolve().parent
VAULT_BY_DEFAULT = Path(__file__).resolve().parents[1]   # template-vault/
TEST_DIR = PIPE_DIR / "test_eval"
DATASET_PATH = TEST_DIR / "eval_dataset.json"
CONFIG_PATH = TEST_DIR / "eval_config.json"

import kb_common            # tokenize 复用（与 rag 索引同构）
import kb_claim             # _chat 用于可选 LLM judge


# ── 指标常量 ───────────────────────────────────────────────
_FAITHFUL_SUPPORT = 0.50    # 一句参考答案 ≥50% token 被上下文支撑 → 判"有支撑"
_STRICT_MIN = {             # --strict 才据此判退出码 1（默认仅报告）
    "context_recall": 0.55,
    "golden_hit": 0.50,
    "faithfulness": 0.60,
}


def _tokenize(s):
    """复用 kb_common.tokenize（unigram+bigram CJK + 拉丁词），与 rag 索引同构。"""
    return list(kb_common.tokenize(s or ""))


def _content_tokens(s):
    """去掉纯标点/数字的实义 token（做支撑判定更准）。"""
    toks = _tokenize(s)
    return [t for t in toks if re.search(r"[A-Za-z\u4e00-\u9fff]", t)]


def _sentences(text):
    """按中文/英文句号断句（评估用，非生产）。"""
    parts = re.split(r"[。！？!.?\n]+", text or "")
    return [p.strip() for p in parts if p.strip()]


# ── 数据集/配置 IO ─────────────────────────────────────────
def load_dataset(root: Optional[Union[str, Path]] = None) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    if not DATASET_PATH.exists():
        return None
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return data.get("items", []), data.get("schema", "kb_eval/1")


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


# ── 真实检索 oracle ──────────────────────────────────────
def run_query(rag_abs: str, question: str, root: Union[str, Path], top: int,
              as_json: bool = True) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """调用 pipeline/rag.py query --json（与 DSH pre-step hook 同款），返回 hits 列表。

    返回 (hits, stderr)。rag.py 是脚本直跑，sys.path[0] 即 pipeline 目录 →
    `import kb_common` 天然可达；--root 显式传 vault 根。
    """
    cmd = [sys.executable, rag_abs, "query", question, "--top", str(top)]
    if as_json:
        cmd.append("--json")
    cmd += ["--root", str(root)]
    try:
        proc = subprocess.run(cmd, cwd=str(PIPE_DIR), capture_output=True,
                              text=True, timeout=120, env=dict(os.environ))
    except (subprocess.TimeoutExpired, OSError) as e:
        return [], f"query 调用失败: {e}"
    out = proc.stdout.strip()
    if not out:
        return [], (proc.stderr.strip() or "rag.py 无输出/索引缺失")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return [], f"rag.py 输出非 JSON: {out[:120]}"
    # 索引缺失时 rag.py 会打印友好提示（非 JSON）→ 已走上面的 except；
    # 索引存在但 zero hits 时 payload.hits == [] → 正常召回为 0。
    return payload.get("hits", []), None


def ensure_index(rag_abs: str, root: Union[str, Path]) -> None:
    """评测前强制重建索引（测量工具必须在当前笔记的新鲜索引上度量，旧索引会失真。

    不属于在线检索路径，重建开销对中小 vault 可接受。DSH 在线增量索引不受影响。"""
    cmd = [sys.executable, rag_abs, "index", "--root", str(root)]
    subprocess.run(cmd, cwd=str(PIPE_DIR), capture_output=True,
                   text=True, timeout=300, env=dict(os.environ))


def _norm_rel(rel):
    """归一化笔记相对路径用于黄金命中匹配。"""
    return str(rel).replace("\\", "/").lstrip("./")


# ── 指标计算 ───────────────────────────────────────────────
def compute_item(item: Dict[str, Any], rag_abs: str, root: Union[str, Path], top: int) -> Dict[str, Any]:
    q = item["question"]
    answer = item.get("reference_answer", "").strip()
    key_terms = [t.strip() for t in item.get("key_terms", []) if t.strip()]
    golden_rel = _norm_rel(item["reference_note"])

    hits, err = run_query(rag_abs, q, root, top)
    snippet_union = " ".join(h.get("snippet", "") for h in hits)
    path_union = set(_norm_rel(h.get("path", "")) for h in hits)
    context_tokens = set(_content_tokens(snippet_union))
    answer_toks = _content_tokens(answer)

    result = {"id": item.get("id"), "question": q,
              "golden_note": golden_rel, "n_hits": len(hits), "error": err}

    if err or not hits:
        result.update({"context_recall": 0.0, "faithfulness": 0.0,
                       "answer_relevance": 0.0, "golden_hit": 0.0})
        return result

    # 1) Context Recall：关键术语在 snippet 并集被子串覆盖的比例
    matched = sum(1 for t in key_terms if t and t in snippet_union)
    result["context_recall"] = (matched / len(key_terms)) if key_terms else 1.0

    # 2) Golden Hit：标准答案所在笔记是否命中 top-k
    result["golden_hit"] = 1.0 if golden_rel in path_union else 0.0

    # 3) Faithfulness：参考答案各句被上下文支撑(≥50% token)的比例
    if answer_toks:
        cov = sum(1 for tok in answer_toks if tok in context_tokens) / len(answer_toks)
        sent_supported = 0
        for sent in _sentences(answer):
            st = _content_tokens(sent)
            if not st:
                continue
            s_cov = sum(1 for t in st if t in context_tokens) / len(st)
            if s_cov >= _FAITHFUL_SUPPORT:
                sent_supported += 1
        n_sent = len([s for s in _sentences(answer) if _content_tokens(s)]) or 1
        result["faithfulness"] = 0.5 * (sent_supported / n_sent) + 0.5 * cov
    else:
        result["faithfulness"] = 0.0

    # 4) Answer Relevance：问句 ↔ 答案 token 重叠（Dice）
    q_set, a_set = set(_content_tokens(q)), set(answer_toks)
    if q_set and a_set:
        inter = len(q_set & a_set)
        result["answer_relevance"] = 2 * inter / (len(q_set) + len(a_set))
    else:
        result["answer_relevance"] = 0.0

    return result


def _llm_judge(item, rag_abs, root, top):
    """可选：ORNITH 接通时用 kb_claim._chat 对忠实度/相关性判 0-1。失败→None(降级确定性)。"""
    hits, _ = run_query(rag_abs, item["question"], root, top)
    context = "\n".join(h.get("snippet", "") for h in hits)
    prompt = (
        "你是 RAG 质量评测员，严格只依据【检索上下文】判断，不要用自己的知识。\n"
        "【问题】%s\n【检索上下文】\n%s\n【参考答案】\n%s\n"
        "请只输出两行：第一行 'faithfulness: x.xxx'，第二行 'relevance: x.xxx'，"
        "0.0-1.0，可各附一句理由。faithfulness=答案是否被上下文支撑(无编造)；"
        "relevance=答案是否直接回答问句。" % (item["question"], context,
                                             item.get("reference_answer", ""))
    )
    try:
        content, _reason = kb_claim._chat(
            [{"role": "user", "content": prompt}])
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
    if not content:
        return None
    faith = re.search(r"faithfulness:\s*([0-9.]+)", content, re.I)
    rel = re.search(r"relevance:\s*([0-9.]+)", content, re.I)
    if not faith:
        return None
    val = float(faith.group(1))
    out = {"faithfulness": min(1.0, max(0.0, val))}
    if rel:
        out["answer_relevance"] = min(1.0, max(0.0, float(rel.group(1))))
    out["judge_reason"] = content.strip()
    return out


# ── 聚合 ───────────────────────────────────────────────────
def _mean(xs):
    xs = list(xs)
    return (sum(xs) / len(xs)) if xs else 0.0


def run_eval(root: Union[str, Path], rag_abs: str, top: int, limit: int,
             judge: str, strict: bool) -> int:
    data = load_dataset(root)
    if data is None:
        print(f"标注集不存在: {DATASET_PATH}\n先跑: python3 pipeline/kb_eval.py sample "
              f"--dest {DATASET_PATH}", file=sys.stderr)
        return 2
    items, schema = data
    if limit:
        items = items[:limit]
    ensure_index(rag_abs, root)

    rows = [compute_item(it, rag_abs, root, top) for it in items]
    if judge == "on":
        for r in rows:
            j = _llm_judge(next(i for i in items if i.get("id") == r["id"]),
                           rag_abs, root, top)
            if j:
                if "faithfulness" in j:
                    r["faithfulness"] = j["faithfulness"]
                if "answer_relevance" in j:
                    r["answer_relevance"] = j["answer_relevance"]
                r["judge_reason"] = j.get("judge_reason")

    n = len(rows) or 1
    agg = {
        "context_recall": _mean(r["context_recall"] for r in rows),
        "faithfulness": _mean(r["faithfulness"] for r in rows),
        "answer_relevance": _mean(r["answer_relevance"] for r in rows),
        "golden_hit": _mean(r["golden_hit"] for r in rows),
        "composite": _mean([
            _mean(r["context_recall"] for r in rows),
            _mean(r["faithfulness"] for r in rows),
            _mean(r["answer_relevance"] for r in rows),
            _mean(r["golden_hit"] for r in rows),
        ]),
    }
    agg["n_items"] = n
    agg["rag"] = os.path.basename(rag_abs)
    agg["judge"] = judge
    agg["run_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    _print_report(rows, agg, strict)

    # 落回归基线
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    baseline_path = TEST_DIR / f"eval_baseline_{stamp}.json"
    baseline_path.write_text(json.dumps({"schema": schema, "aggregate": agg,
                                          "items": rows}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"\n基线已落: {baseline_path}")

    # --strict：低于阈值 → 退出码 1（门禁可接）
    if strict:
        cfg = load_config()
        mins = _STRICT_MIN
        for k, v in mins.items():
            if agg.get(k, 0) < v:
                print(f"[strict] {k}={agg[k]:.3f} < 阈值 {v} → 未过门禁", file=sys.stderr)
                return 1
    return 0


def _print_report(rows, agg, strict):
    print("=" * 72)
    print("kb_eval · RAG 检索质量基线")
    print("=" * 72)
    hdr = f"{'#':>2}  {'召回':>5}  {'忠实':>5}  {'相关':>5}  {'黄金':>4}  题目标签"
    print(hdr)
    for i, r in enumerate(rows, 1):
        tag = r.get("id", "?")
        err = " !!"+r["error"] if r.get("error") else ""
        print(f"{i:>2}  {r['context_recall']:>5.3f}  {r['faithfulness']:>5.3f}  "
              f"{r['answer_relevance']:>5.3f}  {r['golden_hit']:>4.0f}  {tag}{err}")
    print("-" * 72)
    print(f"均值  {agg['context_recall']:>5.3f}  {agg['faithfulness']:>5.3f}  "
          f"{agg['answer_relevance']:>5.3f}  {agg['golden_hit']:>4.0f}  "
          f"composite={agg['composite']:.3f}  (n={agg['n_items']})")
    if strict:
        print("阈值  " + "  ".join(f"{k}≥{v}" for k, v in _STRICT_MIN.items()))
    print("=" * 72)


# ── 入门级标注集生成（真实笔记，可据实扩充）──────────────────
SAMPLE_DATASET = {
    "schema": "kb_eval/1",
    "note": "从真实笔记提取的入门标注集；按 vault 增长持续补充，目标 ≥20 条覆盖多 domain。",
    "items": [
        {
            "id": "dec-tfidf",
            "question": "为什么本地检索方案最终选 TF-IDF/BM25 而不是嵌入模型？",
            "reference_note": "30-决策日志 Decisions/00-决策示例-选检索方案.md",
            "reference_answer": "因为零依赖、跨平台、可离线、一键部署优先；放弃云端 API（隐私+成本）与本地模型（环境重）。",
            "key_terms": ["零依赖", "跨平台", "可离线", "一键部署", "云端 API", "本地模型", "隐私", "成本"]
        },
        {
            "id": "tech-nojieba",
            "question": "本地知识库为什么故意不用 jieba 分词？",
            "reference_note": "20-技术 Technology/01-技术示例-本地知识库.md",
            "reference_answer": "故意不依赖 jieba：用 unigram+bigram 兜底中文，免安装、跨平台、零依赖。",
            "key_terms": ["jieba", "unigram", "bigram", "零依赖", "跨平台", "免安装"]
        },
        {
            "id": "metric-deadlink",
            "question": "知识库死链率的健康阈值是多少？用什么命令检查？",
            "reference_note": "70-知识治理 Governance/04-质量与指标.md",
            "reference_answer": "死链率阈值 < 2%，用 kb healthcheck deadlinks 检查。",
            "key_terms": ["死链率", "2%", "healthcheck", "deadlinks"]
        },
        {
            "id": "validate-gate",
            "question": "kb validate --strict 的门禁行为是什么？",
            "reference_note": "70-知识治理 Governance/04-质量与指标.md",
            "reference_answer": "存在 frontmatter 必填缺失/取值非法等硬错误即退出码 1，可作入库或定时任务的前置门禁。",
            "key_terms": ["硬错误", "退出码 1", "前置门禁", "validate", "strict"]
        },
        {
            "id": "chunk-long",
            "question": "超长笔记怎么处理？",
            "reference_note": "70-知识治理 Governance/04-质量与指标.md",
            "reference_answer": "按 ## 标题用 kb clean --chunk 分块。",
            "key_terms": ["kb clean", "chunk", "分块", "标题"]
        },
        {
            "id": "project-rel",
            "question": "技术示例笔记链接到哪个项目示例？",
            "reference_note": "20-技术 Technology/01-技术示例-本地知识库.md",
            "reference_answer": "链接到 10-项目 Projects/00-项目示例-职业转型.md。",
            "key_terms": ["职业转型", "项目示例", "10-项目"]
        },
        {
            "id": "backup-verify",
            "question": "怎么验证向量索引在笔记变化后仍生效？",
            "reference_note": "20-技术 Technology/01-技术示例-本地知识库.md",
            "reference_answer": "笔记变化后重跑 kb rag index 重建索引，再跑 kb query 确认返回带出处答案。",
            "key_terms": ["rag index", "重建索引", "kb query", "出处"]
        },
        {
            "id": "governance-loop",
            "question": "知识库治理闭环由哪些规范构成？",
            "reference_note": "70-知识治理 Governance/04-质量与指标.md",
            "reference_answer": "由 01-SOP-新增入库、02-SOP-流转归档、03-SOP-健康巡检、04-质量与指标、命名约定与标签词库共同构成。",
            "key_terms": ["新增入库", "流转归档", "健康巡检", "质量与指标", "命名约定"]
        },
    ]
}


def main() -> int:
    ap = argparse.ArgumentParser(description="kb_eval — RAG 检索质量评估网")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval")
    e.add_argument("--root", default=str(VAULT_BY_DEFAULT))
    e.add_argument("--rag", default=str(PIPE_DIR / "rag.py"))
    e.add_argument("--top", type=int, default=3)
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--judge", choices=["on", "off"], default="off")
    e.add_argument("--strict", action="store_true")

    d = sub.add_parser("dataset")
    d.add_argument("--root", default=str(VAULT_BY_DEFAULT))

    s = sub.add_parser("sample")
    s.add_argument("--dest", default=str(DATASET_PATH))

    cf = sub.add_parser("config")
    cf.add_argument("--root", default=str(VAULT_BY_DEFAULT))

    args = ap.parse_args()
    if args.cmd == "eval":
        rag_abs = os.path.abspath(args.rag)
        if not os.path.exists(rag_abs):
            print(f"rag.py 不存在: {rag_abs}", file=sys.stderr)
            return 2
        return run_eval(args.root, rag_abs, args.top, args.limit, args.judge, args.strict)
    if args.cmd == "dataset":
        data = load_dataset(args.root)
        if data:
            items, schema = data
            print(f"schema={schema}  items={len(items)}")
            for it in items:
                print(f"  [{it.get('id')}] {it['question'][:40]}")
        else:
            print(f"标注集不存在: {DATASET_PATH}")
        return 0
    if args.cmd == "sample":
        Path(args.dest).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dest).write_text(json.dumps(SAMPLE_DATASET, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"入门标注集已写: {args.dest}（{len(SAMPLE_DATASET['items'])} 条）")
        return 0
    if args.cmd == "config":
        print(json.dumps({"thresholds_strict": _STRICT_MIN,
                          "faithful_support": _FAITHFUL_SUPPORT},
                         ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
