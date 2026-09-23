# ADR-001：kb_query 测试套件布局（2026-09-19）

## 背景
kb_query 无自动化测试；需要一种可 CI、零副作用、与 embedding 无关的测试布局。

## 非功能需求
- 可导入 `kb_query`（同目录 `kb_rsi` 依赖）；零新运行时依赖；确定性；CI 友好。

## 备选方案
| 方案 | 优点 | 缺点 |
|---|---|---|
| A. `unittest`（stdlib） | 零依赖 | 断言/parametrize 弱，可读性差 |
| B. `pytest` 单文件 `pipeline/test_kb_query.py` | 简单 | 同目录 import 靠 `sys.path`，多文件难组织 |
| **C. `pipeline/test_kb_query/` 子目录 + conftest 注入 sys.path** | pytest 能力齐全、fixture 清晰、可移植 | 多一个目录 |

## 决策：方案 C
-  runner：pytest（已安装 9.1.1），用 `parametrize` 覆盖阈值边界。
-  布局：`pipeline/test_kb_query/`，`conftest.py` 把 `pipeline/` 注入 `sys.path`（解决 `import kb_query`）。
-  fixture：`tmp_path_factory` 每测试独立临时 vault，手写 `reference/query-*.md` 编译笔记作 `compiled_notes` 数据源。
-  运行：`cd pipeline/test_kb_query && pytest -q`。

## 风险与缓解
-  导入 `kb_query` 会 `import kb_rsi`：仅 import 期无害（embedding 在调用期且 best-effort 降级）。已验证 green baseline 可导入。
