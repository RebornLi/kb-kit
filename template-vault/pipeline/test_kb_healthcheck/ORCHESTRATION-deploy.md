# 编排与部署 — summary 子命令（software-team-orchestration + cloud-iaac）

## SDLC 全流程回顾（本任务实际走的路线，模式 A：单 agent 顺序角色扮演）
| 阶段 | 技能 | 产出 | 门禁 |
|---|---|---|---|
| P1 需求 | product-requirements | FR-001 + RTM | 每条验收可判真/假 ✓ |
| P2 理解 | codebase-onboarding | kb-healthcheck 心智模型 ✓ | 影响面明确（main/个别check） |
| P3 架构 | architecture-decision | ADR-002（复用单次结果） | 有备选+边界 ✓ |
| P4 开发 | test-driven-development | 7 测试 + run_checks/summary | red-green 真跑 ✓ |
| P5 测试 | qa-testing-strategy | TESTPLAN + 23 用例全绿 | 准入线绿 ✓ |
| 横切 | security/perf/frontend | REVIEW-crosscutting（XSS 修复） | fail-secure ✓ |
| Refine | refactoring | run_checks 抽取（行为不变） | 回归绿 ✓ |
| Debug | debugging-root-cause | DEBUG-rootcause（3 bug 归因） | 根因+预防 ✓ |
| P6 发布 | release-and-rollback | USAGE + 4a51edf（可回滚） | 分钟级回滚 ✓ |
| 整体 | software-team-orchestration | 本表（RTM 脊椎） | 全链路 Done ✓ |

## CI/CD 卡点（供后续接入；kb-kit 目前是手动 git + pytest）
一道闸 = 明确通过/失败含义：
1. **build**：`python3 -c "import py_compile; ..."`（语法，确定性）
2. **test（门禁）**：`pytest pipeline/test_kb_healthcheck/ pipeline/test_kb_query/` — 绿才进下一步
3. **security**：`pyproject`/stdlib 零依赖 → SBOM  trivial；`--root` 路径校验已代码级覆盖
4. **package**：脚本即产物，git tag = 不可互构件
5. **deploy**：`install.sh` 已把 kb-healthcheck.py 打包进 vault；summary 子命令随脚本一并部署
6. **verify（冒烟）**：`kb-healthcheck.py summary` 返回合法 JSON + 退出码 ∈ {0,1,3}

## 部署/回滚（cloud-iaac 本地化：脚本即 IaC）
- **部署**：kb-kit `install.sh` 复制 pipeline/ 脚本到目标 vault；summary 子命令无外部依赖，零配置上线。
- **爆炸半径**：纯本地只读巡检，失败不污染 vault（仅 stdout/可选 HTML 文件）。
- **回滚**：`git reset --hard 4a51edf^` 一键回退到改前；无数据迁移（纯检测），回滚=零数据风险。
- **观测**：summary JSON 的 `generated_at` + 历史输出 = 健康度趋势基线；退出码可接入告警（red 持续 = 告警）。
- **FinOps**：本地运行，成本 = 0（CPU 秒级）。

## 全链路 Definition of Done
✓ RTM 全连通（FR-001→ADR-002→7 测试→v1.1 发布） · ✓ 23 测试绿 · ✓ 无逃逸缺陷（文本输出零回归） · ✓ 回滚已试跑（git reset） · ✓ 观测就位（JSON/退出码/趋势）。
