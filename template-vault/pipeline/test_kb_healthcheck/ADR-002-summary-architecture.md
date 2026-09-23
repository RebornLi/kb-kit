# ADR-002：summary 子命令架构（复用单次巡检结果）

- 状态：已实施
- 背景：kb-healthcheck 现有 8 个 check 函数各自独立遍历 vault；`all` 模式已跑 8 遍。新增 JSON/HTML 汇总若各自再扫一遍 → 第 9 遍 + 输出不一致风险。
- 决策：抽取 `run_checks(root, names)` 返回结构化结果列表 `[(name, count, detail)]`，文本/JSON/HTML 三类消费同一份结果。新增 `summary` 子命令消费 JSON，`--html` 消费同结果渲染。
- 后果（利）：单一数据源、无重复扫描、三类输出始终一致；（代价）改动 main() 结构，需回归文本输出。
- 备选：
  - A 让 summary 独立再扫一遍：简单但多 1 遍 + 可能不一致 → 弃。
  - B 新增统一 result 层（本方案）：一次性改 main，后续所有输出复用 → 选。
- 边界：若未来 check 函数签名/返回值变化，需同步 run_checks 的适配层。

## C4（容器层，一句话）
用户/CI → `kb-healthcheck.py`(CLI 容器) → 读取 vault .md → run_checks → 三类输出(文本/stdout / JSON / HTML文件)。无外部依赖容器。

## fitness function（护栏）
- 自动化：`pytest pipeline/test_kb_healthcheck` 全绿 = 架构不变价（文本输出 + 新汇总输出同层）。
- CI 门禁：`kb-healthcheck summary` 退出码纳入流水线健康检查。
