---
tags: [template, sop]
status: active
domain: 管理
created: 2026-08-05
updated: 2026-08-05
importance: 0.6
kb_target: 70-知识治理 Governance
kb_action: new
kb_summary: SOP-02 流转归档（项目收尾 / 过期条目下沉）
---

# 02-SOP：流转归档

- 项目结束 → 可复用经验留 `20-技术`，项目笔记 `kb_action=retire`。
- 条目过期 → `clean.py --report` 列出 → 人工确认后 `retire`。
- 归档进 `90-归档 Archive/`，保留可回滚历史（git）。
