# QA 测试计划 — summary 子命令

## 分级（金字塔）
- **单元（大量）**：`run_checks()` 返回结构、`aggregate` 计算、pass/threshold、exit_code 映射、schema 字段完整性。快、孤立、确定性。
- **集成（适中）**：用 tmp vault 合成数据，跑真实 check 函数 → summary 输出完整 JSON；`--root` 解析；非存在 vault → exit 3。
- **端到端（少量）**：`python3 kb-healthcheck.py summary` 真实进程 → stdout 合法 JSON + 退出码；`--html` 生成文件。

## 测什么（等价类 + 边界 + 异常）
- aggregate status：全 0 → green；任一 >0 → red。
- exit_code：green→0，red→1，非法 root→2，缺失 root→3。
- schema：所有 8 个 check 键都在；每条有 count/threshold/pass。
- 复用：run_checks 被 text/summary/html 共用（不改文本输出）。
- 异常：空 vault、无 frontmatter 笔记、--root 指向目录不存在。

## 不测什么
- 每个 check 函数的内部算法（kb-healthcheck 既有逻辑，回归不动）——只测 run_checks 对其适配层与 summary 消费。
- HTML 的视觉样式（只测文件生成 + 含 aggregate）。

## 准出
全量 `pytest` 绿 + 真实 `summary` 进程 JSON 可用 + 文本输出回归绿（8 个 check 不受影响）。
