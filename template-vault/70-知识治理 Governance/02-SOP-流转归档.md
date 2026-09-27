---
tags: [template, sop]
status: active
domain: 管理
created: 2026-08-05
updated: 2026-09-27
importance: 1
kb_target: 70-知识治理 Governance
kb_action: new
kb_summary: SOP-02 流转归档（项目收尾 / 过期条目下沉）
category: meta
---
# 02-SOP：流转归档

- 项目结束 → 可复用经验留 `20-技术`；项目笔记置 `kb_action: retire`（归档，不再参与检索）。
- 过期条目 → `kb clean report` 列出候选（含 TTL：过期且 importance<0.4）→ 人工确认后置 `kb_action: retire`。
- 合并/去重 → 目标笔记声明 `aliases`（旧 `[[名]]` 重定向）、写**墓碑**（`redirect_to`）保留原文，
  lineage 记入 `.kb/state/lineage.jsonl`（可回溯）。
- 归档进 `90-归档 Archive/`，保留可回滚历史（git）。

> `retire` 的笔记会被增量索引正确移除（不参与 `kb query`），无需手动重建索引。

---
<!-- RSI补链 -->
[[70-知识治理 Governance/03-SOP-健康巡检.md]]
