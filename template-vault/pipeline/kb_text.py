"""kb_text — 文本处理与向量工具（从 kb_common.py 拆分，P1-1）。

提供分词、token 计数、内容指纹、字符向量、余弦相似度、倒排索引等纯文本/数学工具。
不依赖 I/O，可独立测试。
"""
import re
import math
import hashlib
from collections import Counter
from typing import Dict, Iterator, List, Optional, Set, Tuple


# ── 分词正则（CJK unigram+bigram + 拉丁词）────────────────────
CJK = re.compile(r"[\u4e00-\u9fff]")

LAT = re.compile(r"[A-Za-z0-9]+")

def tokenize(s):
    """分词：拉丁词 lower + 中文字符 unigram + bigram（无 jieba 兜底）。

    rag/intake/link/recall/kb_health 共用此实现，保证去重判定与 RAG 索引同构。
    """
    toks = [m.lower() for m in LAT.findall(s)]
    cjk = CJK.findall(s)
    for i, c in enumerate(cjk):
        toks.append(c)
        if i + 1 < len(cjk):
            toks.append(c + cjk[i + 1])
    return toks

def count_tokens(text):
    """粗略 token 计数：CJK 1 字 = 1 token，拉丁 4 字符 = 1 token。

    零依赖（不依赖 tiktoken），用于上下文预算控制。
    rag.py --context-budget 用此函数计算拼接 token 数，确保不超限。
    """
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    lat_chars = sum(len(m) for m in LAT.findall(text))
    return cjk + lat_chars // 4

def text_entropy(text):
    """Shannon 信息熵，0-1 归一化。

    用 Python 标准库 math + collections.Counter 计算。
    空文本返回 0.0，单字符重复返回 0.0，每字符不同返回 1.0。
    高熵 = 信息丰富，低熵 = 重复/模板化内容。
    """
    if not text:
        return 0.0
    freq = Counter(text)
    n = len(text)
    num_types = len(freq)
    if num_types <= 1:
        return 0.0
    h = -sum((c / n) * math.log2(c / n) for c in freq.values())
    return h / math.log2(num_types)

def content_fingerprint(body):
    """内容指纹：body 的 sha256 哈希（64 字符十六进制）。

    用于精确去重和变更检测。零依赖（hashlib 标准库）。
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

# ── P2: 三级去重阈值校准（FR-3.3.11）──────────────────────────
def norm(vec: Dict[str, float]) -> Dict[str, float]:
    """L2 归一化稀疏向量。

    Args:
        vec: 稀疏向量 {token: weight}

    Returns:
        归一化后的稀疏向量，使 L2 范数为 1。
        空向量或零向量返回原向量（不除以 0）。

    Examples:
        >>> norm({"a": 3.0, "b": 4.0})
        {'a': 0.6, 'b': 0.8}
    """
    if not vec:
        return vec
    n = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / n for k, v in vec.items()}

def cos(a: Dict[str, float], b: Dict[str, float]) -> float:
    """计算两个稀疏向量的余弦相似度。

    假设输入已归一化（或仅关心点积相对大小）。
    自动选择较短向量遍历以提升性能。

    Args:
        a: 稀疏向量 {token: weight}
        b: 稀疏向量 {token: weight}

    Returns:
        余弦相似度（点积，若输入已归一化则为余弦值）。

    Examples:
        >>> cos({"a": 0.6, "b": 0.8}, {"a": 0.6, "b": 0.8})
        1.0
    """
    if not a or not b:
        return 0.0
    # 自动 swap 使 a 为较短向量
    if len(b) < len(a):
        a, b = b, a
    return sum(av * b.get(k, 0.0) for k, av in a.items())

def char_vec(text: str) -> Dict[str, float]:
    """从文本构建归一化字符向量（tokenize → Counter → norm）。

    Args:
        text: 输入文本

    Returns:
        归一化稀疏向量 {token: weight}，L2 范数为 1。
        空文本返回 {}。

    Examples:
        >>> v = char_vec("hello world")
        >>> abs(sum(x * x for x in v.values()) - 1.0) < 1e-9
        True
    """
    if not text:
        return {}
    return norm(dict(Counter(tokenize(text))))

# ── 倒排索引（build_inverted_index / candidate_pairs）─────────
def build_inverted_index(vecs: Dict[str, Dict[str, float]]) -> Dict[str, Set[str]]:
    """构建 token → {rel} 倒排索引。

    用于 O(n²) 成对比较的预过滤：仅对共享至少 1 个 token 的笔记对计算余弦相似度。

    Args:
        vecs: {rel: 归一化向量} 字典

    Returns:
        {token: {rel1, rel2, ...}} 倒排索引。

    Examples:
        >>> idx = build_inverted_index({"a.md": {"x": 0.6, "y": 0.8},
        ...                              "b.md": {"y": 0.5, "z": 0.9}})
        >>> idx["y"] == {"a.md", "b.md"}
        True
    """
    index: Dict[str, Set[str]] = {}
    for rel, vec in vecs.items():
        for token in vec:
            if token not in index:
                index[token] = set()
            index[token].add(rel)
    return index

def candidate_pairs(
    index: Dict[str, Set[str]], rels: Optional[List[str]] = None
) -> Iterator[Tuple[str, str]]:
    """生成候选对（共享至少 1 个 token 的笔记对）。

    每对仅生成一次（按 (a, b) 顺序，a < b 字典序）。
    若提供 rels 参数，仅生成 rels 内部的候选对。

    Args:
        index: build_inverted_index 构建的倒排索引
        rels: 限制候选对范围的笔记列表（None 表示全部）

    Yields:
        (rel_a, rel_b) 候选对，rel_a < rel_b（字典序）。
    """
    rel_set = set(rels) if rels is not None else None
    seen: Set[Tuple[str, str]] = set()
    for token_rels in index.values():
        # 过滤到目标范围
        if rel_set is not None:
            token_rels = token_rels & rel_set
        if len(token_rels) < 2:
            continue
        # 生成有序对
        sorted_rels = sorted(token_rels)
        for i, a in enumerate(sorted_rels):
            for b in sorted_rels[i + 1:]:
                pair = (a, b)
                if pair not in seen:
                    seen.add(pair)
                    yield pair
