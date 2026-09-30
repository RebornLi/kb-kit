---
tags: [报告, 评测, P4]
status: active
domain: 管理
category: meta
created: 2026-09-30
updated: 2026-09-30
importance: 0.8
kb_target: docs
kb_action: new
kb_summary: P4 报告：RAGAS 式四指标评测（本地裁判）基线 faithfulness 1.0 / relevancy 0.69 / precision 0.62 / canon 0.95；暴露 6 题"KB 自我描述"检索缺失；ground truth 30 题草稿待人确认。
---

# P4 交付报告：RAGAS 式四指标评测（本地模型当裁判）

## 一、基线（30 题 · 27 分钟 · 裁判 ornith1.5-35b）

| 指标 | 分数 | 含义 |
|---|---|---|
| **faithfulness** | **1.0** | 答案里每条断言都能在检索上下文找到 —— **零幻觉** |
| answer_relevancy | 0.69 | 是否答在问题上 |
| context_precision | 0.62 | 召回上下文的相关度（位置加权） |
| **canon_share** | **0.95** | 召回里正典占比 —— 检索质量 |
| context_recall | — | **需要 ground truth**（草稿已生成，待你确认） |

## 二、最有价值的发现：低分题全是「KB 自我描述」类

低相关性（<0.5）的题几乎同一类型，而模型的行为是**正确地拒答**：

```
问题：知识结晶 正典 是什么
答案：资料未覆盖。资料中仅提到「结晶」…但未出现「正典」一词

问题：知识契约 schema 检查什么
答案：资料未覆盖。资料中仅提及「统一契约」（外部业务系统网关代理）…

问题：RAG 索引怎么增量更新
答案：资料未覆盖。资料中仅提到「kb rag index」用于重建向量索引
```

这**不是模型的问题，是检索的问题**：
1. KB 里确实有这些答案（本会话写的报告、P3 报告、契约文件都在库里）
2. 但检索没把它们排到前面 —— 因为这些问题问的是**系统自身**，而库里同类内容太多（几十篇都在讲 kb-kit），TF-IDF/BM25 分不开"讲 kb-kit 的"与"讲 kb-kit 的某个具体机制的"
3. 于是上下文里塞满"相关但不对题"的片段，模型按纪律拒答

**结论**：这类"自指问题"是当前检索的真实盲区，应作为 P5 的重点（可能要用**标题/路径加权**或**问题类型路由**）。

## 三、评测工具本身踩的三个坑（都修了）

### 1. 作答为空 → 相关性全 0（最隐蔽）
`ornith` 是**带 reasoning 的模型**：实测 277 completion_tokens 里 **218 是思考**。
我最初给作答 300 token → 思考完就没额度输出 → `content` 为空字符串，`finish_reason` 却是 `stop`。
**看起来像"模型不可用"，其实是预算不够。** 已把作答/裁判预算提到 1200–1500，并对"空答案"显式告警。

### 2. 裁判把换行写成字面 `\n` → JSON 解析失败
`json.loads` 直接报错，faithfulness 变 `None`。已加三级容错（原样 → 转义归一 → 引号归一）。

### 3. 子进程崩溃被当成"0 命中"
P2 也踩过同类。评测脚本现在对非零退出**大声报错**——否则"崩溃"会被读成"检索质量暴跌"。

## 四、ground truth（需要你确认的一环）

已用本地模型生成 30 题草稿（候选答案 + 关键点），并导出人读清单：

```
70-知识治理 Governance/_curate/P4-gold-review.md
.kb/eval/gold_candidates.jsonl          # 机器读
```

**其中 6 题 AI 判定「资料未覆盖」**（即上面说的自指题），建议改写为 KB 真能回答的问法，或直接弃用。

确认方式（在你的终端里跑，交互式）：

```bash
cd /home/mushan/kb-kit-pure
python3 pipeline/kb_eval_judge.py gold --root . --review
# 每题：回车=接受 · 直接输入新答案=改写 · n=丢弃 · s=跳过
```

确认后再跑一次 `kb eval judge run`，`context_recall` 就有值了：

```bash
python3 pipeline/kb_eval_judge.py run --root .
python3 pipeline/kb_eval_judge.py compare --baseline .kb/eval/baseline-p4.json
```
