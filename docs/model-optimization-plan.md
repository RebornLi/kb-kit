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
