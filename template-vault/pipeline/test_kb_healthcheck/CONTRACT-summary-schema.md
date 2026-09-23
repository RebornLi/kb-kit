# summary 子命令契约（api-design + database-design + observability）

## CLI 契约（api-design）
```
kb-healthcheck.py summary            # 默认 JSON 到 stdout，退出码按 aggregate
kb-healthcheck.py summary --json     # 显式 JSON（= 默认）
kb-healthcheck.py summary --html OUT # 另生成 HTML 健康页
kb-healthcheck.py summary --root R   # 指定 vault
kb-healthcheck.py summary --checks deadlinks,orphans   # 只算指定项（可选）
```
- 消费者：CI（stdout JSON + exit code）、仪表盘（拉 JSON）、运维（HTML 页）。
- 选型：CLI 子命令即「资源」`summary`，无网络 API，契约=JSON schema + 退出码语义。
- 错误：非法 --root → exit 2 + stderr 提示；vault 不存在 → exit 3。
- 向后兼容：新增子命令/标志不影响既有 `deadlinks` 等文本输出。

## JSON schema（数据模型 = 轻量反范式：扁平 per-check 计数）
```json
{
  "generated_at": "2026-09-19T04:40:00+08:00",
  "vault": "/abs/path/to/vault",
  "checks": {
    "deadlinks":   {"count": 3, "threshold": 0, "pass": false},
    "orphans":     {"count": 1, "threshold": 0, "pass": false},
    "skeletons":   {"count": 0, "threshold": 0, "pass": true},
    "tags":        {"count": 0, "threshold": 0, "pass": true},
    "empty":       {"count": 0, "threshold": 0, "pass": true},
    "frontmatter": {"count": 0, "threshold": 0, "pass": true},
    "discipline":  {"count": 0, "threshold": 0, "pass": true},
    "overlong":    {"count": 0, "threshold": 0, "pass": true}
  },
  "aggregate": {
    "total_issues": 4,
    "checks_failed": 2,
    "status": "red"
  },
  "exit_code": 1
}
```
- 设计理由（database-design）：每个 check 的 count 是高频查询字段（仪表盘按 check 聚合），阈值内嵌便于独立判 pass，无需 JOIN。
- `pass = count <= threshold`（此处阈值恒 0）。
- 反范式取舍：冗余 threshold 字段，换取「单条 check 即可独立判绿/红」，避免下游聚合计算。

## observability 映射（health checks = 健康 Golden-Signal 代理）
- deadlinks/orphans/empty/frontmatter/discipline/skeletons/overlong/tags = 8 个「错误/完整性」信号。
- aggregate.status（green/red）= 系统级健康信号，可告警（告症状：red 而非逐个 count）。
- `generated_at` + 历史 JSON = 趋势图的时间序列基线（上线前后/周对比 = 健康回归检测）。
- 退出码 = 可行动信号（CI 门禁直接判绿红）。
