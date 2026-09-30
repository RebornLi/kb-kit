---
tags: [方案, 模型部署, 优化, 检索]
status: active
domain: 管理
category: meta
created: 2026-10-01
updated: 2026-10-01
importance: 0.9
title: 三模型优化方案：嵌入降级、重排默认关、chat留reasoning预算
kb_target: docs
kb_action: new
kb_summary: 基于实测的三模型显存与检索优化：嵌入模型降级省55GB给chat，重排器实测有害默认关，chat须给reasoning留额度。
kb_layer: canon
canon_type: canon-generic
curated_by: ornith1.5-35b
curated_at: 2026-10-01T03:31:17
derived_at: 2026-10-01T03:31:17
event_at: 2026-10-01
source_ref: [vault:docs/model-optimization-plan.md]
source_chars: 4737
canon_ratio: 0.121
grounded_facts: [8000, 8081, 8082, /home/mushan/models/Qwen3-Embedding-8B-NVFP4, 61.9 GB, 125.6 GB, KB_RERANK=0, 36ms]
curate_confidence: 0.9
confidence: 0.744
archived_original: raw/_curated/docs/model-optimization-plan.md
---

# 三模型优化方案：嵌入降级、重排默认关、chat留reasoning预算

## 结论
- 嵌入模型(61.9GB)只在可选/离线路径，不在查询热路径，应降级或按需启停省~55GB
- cross-encoder重排器对检索系统性有害，排序质量全低于基线，默认关、机制保留
- chat模型配置已优，真实故障源是调用方未给reasoning留预算

## 依据
- 三模型合计125.6GB，GB10统一内存128GB已顶天花板
- kb_embed仅5个调用点，全可选/离线，查询热路径不依赖它
- 30题黄金集实测：不重排top1 90% > cross-encoder top1 63%

## 操作
- 换0.6B嵌入改EMBED_MODEL，保持8081端口与served-model-name不变
- chat max_tokens按reasoning+正文分配，短答案≥1200、JSON裁判≥1500
- KB_RERANK=0关闭重排；KB_TITLE_BOOST=0、KB_SYNONYM_EXPAND=0关闭加权

## 坑与边界
- chat未留reasoning预算时finish_reason=stop但content为空
- gpu-memory-utilization 0.75+max-num-seqs 48为推算值未压测
- 检索结论仅基于30题黄金集(偏系统自身主题)，需扩大后复测

---

> 原记忆（原文保真）：`kb raw show "vault:docs/model-optimization-plan.md"`　·　本地副本 `raw/_curated/docs/model-optimization-plan.md`

## 八、执行中发现的两个"静默失效"（已修）

### 1. `contextual.py` 的 LLM 上下文**一直没生效**（缺配置接线）
`_LLM.__init__` 只读环境变量 → cron/systemd 读不到 `~/.bashrc` → key 为空 →
`available()` 返回 False → `--llm` 被静默降级成纯 CCH。**与 P2 修过的 `kb_embed` 是同一根因，这里漏了一处。**
修：走统一 `model_config()`。

### 2. reasoning 预算的"断崖"，不是"不够"
实测同一个 CCH 提示词：

| max_tokens | reasoning | 正文 | 结果 |
|---|---|---|---|
| 64 | 64 | 0 | 空 |
| 160（旧值） | ~390 | — | 临界/空 |
| 600 | 390 | 25 | ✅ 正常 |
| **1200** | **1200（吃光）** | **0** | ❌ 空（长片段触发超长思考） |
| 2048 + 收紧重试 | ~390 | 25 | ✅ |

**关键结论**：预算不是"越大越好"——长片段会让模型陷入超长思考，
`max_tokens=1200` 反而比 600 更容易失败。正确做法是**提高预算 + 检测到"有 token 无正文"时收紧输入重试**
（已实现）。这也是当初 `kb_eval_judge` 空答案 bug 的同源问题。

修后实测（`kb contextual build --llm --limit 5`）：**LLM 5 / CCH 0**，生成质量良好，例如：
```
01-如何开始.md → 本文讲解一键套件知识库建好后的5步上手流程，属入门操作指南，涵盖建索引、检索、摄入等命令速查。
```

## 九、定论：**嵌入与重排两个模型都不在热路径上**（实测证据）

### 证据链

**1. 两条热路径零依赖（代码级）**

```
$ grep -n "kb_embed" pipeline/rag.py        →  无输出   （检索：纯 TF-IDF + BM25 + RRF + 元数据）
$ grep -n "kb_embed" pipeline/curate.py     →  无输出   （结晶：纯 chat 模型）
```

**2. 代码里引用嵌入的 4 处，没有一处接进生产**

| 引用点 | 实际状态 |
|---|---|
| `kb_claim.py` | **没接进 CLI launcher、没进结晶流程、没有 cron**（只能手动 `python3 pipeline/kb_claim.py sync`）；且内有签名 Jaccard 回退 |
| `kb_rsi.py` | P3 已改**显式 opt-in**（`USE_EMBEDDING=1`），默认关 |
| `kb_usage.py` | 被 `kb_adaptretrieve` 调用时**显式传 `use_embedding=False`**；且无 cron |
| `embed_rerank.py` | 默认关（A/B 实测负增益） |

**3. 服务端日志：正常运行时段调用量为 0**

```bash
journalctl --user -u vllm-embed --since "24 hours ago" | grep -c "POST /v1/embeddings"
→ 9667
```

但按时间分布看：

```
9月30 07时  1277 次   ← 我在跑 RAGAS 评测（embed-rerank 对照）
9月30 08时  8363 次   ← 同上
10月1 01时     3 次   ← 我测 seed
10月1 02时    23 次   ← 我测 contextual
（其余时段）    0 次   ← **正常运行时段，一次都没有**
```

**9667 次全部来自我自己的实验，不是生产调用。**

### 所以答案是

| 服务 | 是否在被使用 | 判定 | 显存 |
|---|---|---|---|
| `:8000` chat 35B | ✅ 结晶每次都在用 | **必须常驻** | 61.9 GB |
| `:8081` embed 8B | ❌ 无生产调用方（只有可选/离线路径） | **可停/可按需启停** | **61.9 GB** |
| `:8082` reranker | ❌ 默认流水线不用；A/B 实测有害 | **可停**（留开关） | 1.8 GB |

**净收益：停掉后释放约 62 GB**（几乎等于 chat 模型自己的显存占用），
可把 chat 从 `--gpu-memory-utilization 0.55 / --max-num-seqs 24`
提到 `0.75 / 48`，或支持更长上下文的并发会话。

**安全性**：三个模块（`kb_usage` / `kb_claim` / `kb_rsi`）在嵌入不可用时导入与降级路径全部正常
（已实测），各自有回退（签名 Jaccard / 精确匹配 / 字面 char-vec）。

### 执行建议（分两步，先验证再动手）

```bash
# 第一步：停服务，验证热路径（可随时启回，零风险）
systemctl --user stop vllm-embed vllm-rerank
./kb query "vLLM 部署有哪些坑" --top 3          # 检索照常
./kb curate plan --limit 3                      # 结晶选批照常
./kb validate && ./kb schema check              # 校验照常

# 第二步：确认无影响后，改成按需启停（不留常驻显存）
systemctl --user disable vllm-embed vllm-rerank
# 需要跑 RSI / kb_claim / embed-rerank 对照实验时再手动 start

# 第三步：把省下的显存投给 chat
#   vllm-chat-b12x.service: --gpu-memory-utilization 0.55 → 0.75
#                           --max-num-seqs 24 → 48
```

**注意**：第二步会改动服务自启状态。若 cron 里有依赖嵌入的任务（当前没有），需一并调整。

## 十、方案修订（2026-10-01，按用户指示与实测校正）

### 修订 1：reranker **已卸载** ✅

```bash
systemctl --user stop vllm-rerank && systemctl --user disable vllm-rerank
# → inactive + disabled，端口 8082 下线，释放 1.8 GB
```

依据：默认流水线不用它（`KB_RERANK=0`），且 30 题受控实测对排序**系统性有害**
（top1 90%→63%）。更早的 cross-encoder 实验（`Qwen3-Reranker-4B`/bge）也从未改善过。

**决定**：卸载服务与开机自启；模型文件暂留（重装成本低，`vllm-rerank-serve.sh` 仍在），
需要做对照实验时手动 `systemctl --user start vllm-rerank` 即可。

### 修订 2：embed 8B **保留**（纠正我的判断）

我先前依据"代码级调用点"判断它无生产调用方，**这个判断不完整**：
它服务的是**对话检索链路**（用户确认），而非只看 kb-kit 内部引用。

**实测现状**：`vllm-embed` 实际只占 **9.9 GB**（NVFP4 量化后权重仅 4.9 GB，
`CAP=0.12` 封顶约 14.6 GB）—— 不是我以为的 61.9 GB（那是更早一次快照的读数，已过时）。

**结论改为**：**保留嵌入服务**，不降级、不停。理由：10 GB 换"对话检索可用"很划算，
而降到 0.6B 省下的 8 GB 对整体无意义。

### 修订 3：神经检索的**历史实证**（这是最该记住的一条）

从库里翻到 2026-09-21 的 `Node0.3-C` 记录，与我这两天的实测**完全吻合**：

> **qwen3-embedding-8B 语义重排无法零回归改善检索**。扫遍融合谱：
> 50/50 RRF→0.535（但 project-rel 回归）、60/40→0.460、70/30→0.368、
> 自适应神经注入→0.377（更糟，context_recall 0.604→0.421、faithfulness 0.620→0.491）。
> **根因：qwen3-embedding 把泛用/中心笔记排得偏高（中央倾向偏差），
> 语义重排挤掉覆盖 key_term 的正确笔记。这是模型质量上限，非调参可解。**

**两次独立实验、两套不同指标（用户的 composite@n=8 与我的 canon@K/top1/top3@n=30）、
跨两个月的间隔，指向同一结论。** 这已经不是一个可以靠继续调参解决的问题。

## 十一、重新优化后的结论（当前最优配置）

### 显存重新分配（卸载后）

| 服务 | 实测显存 | 状态 |
|---|---|---|
| `:8000` chat 35B | 62.2 GB | 常驻（`--gpu-memory-utilization 0.55`，可上调） |
| `:8081` embed 8B | 9.9 GB | 常驻（对话检索链路） |
| ~~`:8082` reranker~~ | ~~1.8 GB~~ | **已卸载** |
| **合计** | **~72 GB / 121 GB** | **空闲 ~49 GB** |

**49 GB 空闲怎么用**（按性价比排序）：

1. **chat 并发上调**（最直接、零风险）：
   `--gpu-memory-utilization 0.55 → 0.70`、`--max-num-seqs 24 → 48`
   → 结晶批次吞吐提升、长会话不易触发抢占
2. **暂不投给神经检索**——三次实验证明在本库上它是负收益
3. 留作未来更重任务（本地更大模型试跑）的余量

### 检索层：维持当前确定性配置（实测最优）

```
分层降级（canon ×1.25 / page ×1.0 / raw ×0.72 / index,noise 不入索引）
+ 上下文强化 CCH（只进索引，不改正文）
+ 父子块（命中碎片回填父块）
+ RRF 融合（TF-IDF 余弦 + BM25，Σ1/(60+rank)）
+ 复合置信分参与排序（±10%）
+ 同族去重（每根家族 ≤2 条）
+ 概念索引 docs/concepts.md（解决自指类问题）
```

**默认关闭**（保留开关，换库/换题时复测）：
`KB_RERANK`（重排）· `KB_TITLE_BOOST`（标题加权）· `KB_SYNONYM_EXPAND`（同义词扩展）。

### 下一步可试的方向（附成本与判据）

| 方向 | 成本 | 判据 | 我的预判 |
|---|---|---|---|
| **LLM 当重排器**（chat 模型从 top20 里挑，45 GB 空闲后可行） | +1–3s/查询 | top1/top3 是否 >90%/70% | 有希望（能读懂"能否回答问题"），但延迟代价大 |
| 换**非 Qwen3** 嵌入（如 bge-m3 / Nemotron-Embed） | 需下载+标定 | 用户那套 composite(n=8) 零回归 | 不确定，中央倾向偏差可能是 Qwen3 特有 |
| 语义**只做召回扩列**不重排 | 已在 `NEURAL_MODE=expand` 设计里 | 零回归 | 实测零回归但也零增益 |
| 放弃神经检索，继续强化**元数据信号** | 低 | any 指标提升 | **推荐**：本库的优势一直来自元数据而非语义 |
