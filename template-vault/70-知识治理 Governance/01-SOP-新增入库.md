---
tags: [template, sop]
status: active
domain: 管理
created: 2026-08-05
updated: 2026-08-05
importance: 1
kb_target: 70-知识治理 Governance
kb_action: new
kb_summary: SOP-01 新增入库（一条知识从收件箱到落库的流程）
category: meta
---
# 01-SOP：新增入库

1. 新建知识 → `00-收件箱 Inbox/`（带 frontmatter 或先无）。
2. `intake_triage.py review` 看四维打分。
3. 达标 → `intake_triage.py apply --move` 路由到 PARA 对应区。
4. 缺字段 → 拒绝入库，进待审队列（`validate.py` 会标红）。
5. 落库后补链（独立操作）：跑 `kb link` 看建议链接清单 → `kb link apply` 写入建议区块。

---
<!-- RSI补链 -->
[[70-知识治理 Governance/04-质量与指标.md]]
