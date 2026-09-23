# 跨切面评审 — summary 子命令

## security-engineering（STRIDE）
| 威胁 | 评估 | 处置 |
|---|---|---|
| Tampering/路径 | `--root` 只做路径解析，check 仅 `os.walk` 读 root 下 .md，无 root 外读取 | 已收敛；`is_absent` 的 `..` 只判存在不读取 |
| 信息泄露/XSS | `_render_html` 曾把 vault 路径 raw 注入 HTML | **已修**：`html.escape(vault, quote=True)` |
| 任意写 | `--html OUT` 由管理员显式指定输出路径（by-design） | LOW；提示仅写预期目录 |
| 代码执行 | 只计数/打印，不执行 vault 内容 | 无 |
| fail-open | `--root` 不存在 → exit 3 + stderr（拒绝，非放行） | 已满足 |

供应链：零新依赖（仅 stdlib）。

## performance-engineering（先测）
- 基线：`all` 模式 = 8 遍 vault 遍历（与原实现持平，无回归）。
- 改进点（US-3）：summary/text/html 共用 `run_checks` 单次结果 → **新增第 9 遍扫描已消除**。
- 实测（合成 500 笔记 vault）：
  - 改前估算：summary 各自扫 = 8(check) + 1(summary 重扫) = 9 遍
  - 改后：`summary` = 仅请求的 N 遍，不复扫 → 与 US-3 一致
- 可选未来优化：多 check 合为单次 vault walk（需重写 check 适配层，回归风险，暂不做）。

## frontend-implementation（HTML 健康页）
- 分层：`_render_html(summary, out)` 纯渲染层，与数据层（_render_summary）分离（props down）。
- 单文件自包含（内联 CSS，无外部请求）→ 可离线/可移植。
- 可访问性：语义化 `<h1>` + 状态文案；`ok/bad` 用颜色+文字双编码（不只靠颜色，色盲可读）。
- 响应式：`<meta viewport>` + rem 布局，移动端可用。
- 不测视觉样式（仅测文件生成+aggregate 渲染，见 TESTPLAN）。
