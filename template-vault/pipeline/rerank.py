#!/usr/bin/env python3
# ============================================================
# rerank.py —— 检索结果重排（FR-3.2.6）
#   5 维度加权交叉编码打分，不依赖外部模型
#   token 重叠度(40%) + frontmatter 匹配(20%) + importance(20%)
#   + enforce_level(10%) + 时效性(10%)
# ============================================================
import sys, os, datetime
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_common import tokenize, load_meta, domain_primary, parse_domain


# 权重配置
W_TOKEN = 0.40      # token 重叠度
W_FRONTMATTER = 0.20  # frontmatter 字段匹配
W_IMPORTANCE = 0.20   # importance 加权
W_ENFORCE = 0.10      # enforce_level 加权
W_RECENCY = 0.10      # 时效性

RECENCY_DAYS = 30  # 近 30 天更新的加分


def _token_overlap(query_tokens, doc_tokens):
    """token 重叠度（Jaccard）。"""
    if not query_tokens or not doc_tokens:
        return 0.0
    intersection = query_tokens & doc_tokens
    union = query_tokens | doc_tokens
    return len(intersection) / len(union) if union else 0.0


def _frontmatter_match(query, fm):
    """frontmatter 字段匹配：domain/tags/content_type 命中加分。"""
    score = 0.0
    q_lower = query.lower()
    # domain 匹配
    domain = fm.get("domain", "")
    if domain and domain in query:
        score += 0.4
    # tags 匹配
    tags = fm.get("tags", "")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.strip("[]").split(",") if t.strip()]
    if isinstance(tags, list):
        for t in tags:
            if t and t in query:
                score += 0.3
                break
    # content_type 匹配
    ct = fm.get("content_type", "")
    if ct and ct in q_lower:
        score += 0.3
    return min(score, 1.0)


def _importance_score(fm):
    """importance 加权。"""
    try:
        return float(fm.get("importance", 0.5))
    except (ValueError, TypeError):
        return 0.5


def _enforce_score(fm):
    """enforce_level 加权：policy/must 优先。"""
    el = fm.get("enforce_level", "")
    if el == "must":
        return 1.0
    if el == "should":
        return 0.7
    if el == "may":
        return 0.4
    return 0.5  # 默认


def _recency_score(fm):
    """时效性：近 30 天更新的加分。"""
    updated = fm.get("updated", "")
    if not updated:
        return 0.5
    try:
        if isinstance(updated, str):
            d = datetime.date.fromisoformat(updated[:10])
        else:
            return 0.5
        days = (datetime.date.today() - d).days
        if days <= RECENCY_DAYS:
            return 1.0 - (days / RECENCY_DAYS) * 0.5  # 1.0 → 0.5
        return 0.3  # 超过 30 天
    except (ValueError, TypeError):
        return 0.5


def rerank(query, hits, root):
    """对 Top-K 检索结果做重排。

    Args:
        query: 查询字符串
        hits: [(rel, score), ...] 检索结果
        root: vault 根路径

    Returns:
        [(rel, new_score, old_score), ...] 重排后的结果
    """
    query_tokens = set(tokenize(query))
    results = []
    for rel, old_score in hits:
        try:
            fm, body = load_meta(Path(root) / rel)
        except (OSError, UnicodeDecodeError):
            fm, body = {}, ""

        doc_tokens = set(tokenize(body))
        s_token = _token_overlap(query_tokens, doc_tokens)
        s_fm = _frontmatter_match(query, fm)
        s_imp = _importance_score(fm)
        s_enf = _enforce_score(fm)
        s_rec = _recency_score(fm)

        new_score = (W_TOKEN * s_token + W_FRONTMATTER * s_fm +
                     W_IMPORTANCE * s_imp + W_ENFORCE * s_enf +
                     W_RECENCY * s_rec)
        results.append((rel, new_score, old_score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="检索结果重排")
    ap.add_argument("--root", default=os.environ.get("KB_ROOT", "."))
    ap.add_argument("query", help="查询字符串")
    ap.add_argument("--hits", help="JSON 格式的 hits 列表")
    args = ap.parse_args()
    hits = json.loads(args.hits) if args.hits else []
    results = rerank(args.query, hits, args.root)
    for rel, new_sc, old_sc in results:
        print(f"{new_sc:.4f}  (was {old_sc:.4f})  ← {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
