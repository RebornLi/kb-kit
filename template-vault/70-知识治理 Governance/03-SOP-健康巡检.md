---
tags: [template, sop]
status: active
domain: 管理
created: 2026-08-05
updated: 2026-08-05
importance: 0.6
kb_target: 70-知识治理 Governance
kb_action: new
kb_summary: SOP-03 健康巡检（死链/空目录/缺字段/超长）
---

# 03-SOP：健康巡检

每周跑一次，或让 cron 自动跑：

```bash
kb healthcheck all      # 全部巡检
kb healthcheck deadlinks  # 仅死链
```

巡检项：缺 frontmatter、空分区、超长未分块笔记、孤儿笔记、坏链。
结果写 `logs/health-<date>.log`；`kb dashboard` 汇总巡检告警到治理仪表盘（`70-知识治理 Governance/_INDEX.md`）。
