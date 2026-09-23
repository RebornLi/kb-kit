# FR-001：kb-healthcheck 机器可读汇总子命令（JSON + HTML）

## 需求简报（product-requirements）
**问题定义**：kb-healthcheck 只能打印人类文本，CI/仪表盘/告警无法程序化消费巡检结果；运维要盯健康度趋势却无结构化输入。成功标准：新增 `summary` 子命令，输出稳定的 JSON 指标（可被 dashboard/CI 解析），且 5 分钟内零回归。护栏指标：不引入新 bug、不增加巡检总耗时。

**用户故事 + 验收**
- US-1 作为 CI 流水线，我想要 `kb-healthcheck summary --json` 返回结构化指标 + 退出码，以便健康度可作为门禁判绿/红。
  - Given 一个有 3 个死链的 vault，When 运行 `summary`（默认 exit-code），Then JSON 含 per-check 计数且 `aggregate.status=="red"`，进程退出码非 0。
  - Given 一个全通的 vault，When 运行 `summary`，Then `aggregate.status=="green"`，退出码 0。
- US-2 作为运维，我想要一个 HTML 健康页，以便在浏览器里一眼看趋势（不依赖 SSH 文本）。
  - Given `--html out.html`，When 运行，Then 生成自包含 HTML 文件且含 aggregate 与各项计数。
- US-3 作为开发者，想要 `summary` 不重复扫描 vault（复用 `all` 已算出的结果），以免 `all` 模式从 8 遍变成 9 遍。

**NFR**
- 性能：复用已有结果，`summary` 不额外遍历 vault（复用 run_checks 单次结果）。适用：单机本地扫描，非高并发。
- 可用性/兼容：Python 3.9+，零新依赖；JSON schema 稳定（向后兼容）。
- 安全：`--root` 只做路径解析，不执行 vault 内容；编码用 utf-8 + errors=replace。
- 可维护：抽取 `run_checks()` 供文本/JSON/HTML 共用（见 ADR-002）。

## 待确认（Open Questions）
- Q1：aggregate 阈值口径（多少问题算 red）？→ 决定：任一 check 计数 >0 即 red，全 0 即 green（简单可判）。
- Q2：JSON 是否含时间戳？→ 含 `generated_at`（ISO），便于趋势图。

## RTM（需求追溯）
| 需求 | 设计点 | 测试用例 | 版本 |
|---|---|---|---|
| US-1 JSON+exit | summary 子命令, schema | test_summary_json_exit_code | v1.1 |
| US-2 HTML | --html 渲染 | test_html_output | v1.1 |
| US-3 复用 | run_checks 抽取 | test_run_checks_reuse | v1.1 |
