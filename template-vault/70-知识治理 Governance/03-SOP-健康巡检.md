---
tags: [template, sop]
status: active
domain: 管理
created: 2026-08-05
updated: 2026-09-27
importance: 0.96
kb_target: 70-知识治理 Governance
kb_action: new
kb_summary: SOP-03 健康巡检（死链/空目录/缺字段/超长）
category: meta
---
# 03-SOP：健康巡检

每周跑一次，或让 cron 自动跑：

```bash
kb healthcheck            # 等价于 all
kb healthcheck all        # 全部巡检
kb healthcheck deadlinks  # 仅死链
kb healthcheck tags       # 仅标签越界
kb healthcheck skeletons  # 仅骨架笔记
kb healthcheck frontmatter
kb healthcheck empty
kb healthcheck discipline
kb healthcheck overlong
kb healthcheck orphans
kb healthcheck freshness   # 陈旧 + 非可检索状态（draft/archived/legacy）
kb healthcheck summary
```

巡检项：缺 frontmatter、空分区、超长未分块笔记、孤儿笔记、坏链、标签越界。
有发现项时退出码非 0（供定时任务/监控察觉）。`kb dashboard` 把告警汇总到
`70-知识治理 Governance/_INDEX.md`。

---
<!-- RSI补链 -->
[[70-知识治理 Governance/04-质量与指标.md]]
