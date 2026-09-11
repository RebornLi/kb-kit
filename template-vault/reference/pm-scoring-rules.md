---
tags: [reference]
status: legacy
domain: 综合
created: 2026-05-20
updated: 2026-09-03
importance: 0.5
kb_target: reference
kb_action: new
kb_summary: PM 扣分规则
---

# PM 扣分规则

```yaml
- timestamp: 2026-05-20T10:10:00+08:00
- type: rule
- importance: 10
- source: user
- tags: [pm-workflow, scoring, discipline]
```

## 评分机制
- 基础分 10 分
- 每次违反 PM 工作流铁律 → 扣 1 分
- 木木 = PM，只做规划/分发/汇总/交付，不写代码

## 违规记录
1. 2026-05-20 09:09 — 边干边汇报，打断节奏（开发中途列表问"接下来推哪个"）
2. 2026-05-20 10:11 — 替子 Agent 干活（木界超时，我上手写脚本替它改代码，应重新分发）

## 铁律
- 子 Agent 超时/失败 → 重新分发，不是自己干
- 开发进行中 → 闷头推完，不要停下来汇报
- 始终记得 8088 是部署地址（官网/管理端）
- **所有开发必须符合项目文档**：BRD + HLD + LLD + INTEGRATION + ARCHITECTURE + ARCHITECTURE-V2。表结构/API/模块命名必须对齐 LLD 规范
