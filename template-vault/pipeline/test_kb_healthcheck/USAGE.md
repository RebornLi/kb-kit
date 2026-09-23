# kb-healthcheck.py 使用手册

## 何时用
知识库健康巡检（Obsidian vault 治理）。仅检测 + 报告，**不自动修改**任何笔记。

## 前置条件
- Python 3.9+，零第三方依赖。
- 脚本位于 `pipeline/kb-healthcheck.py`；默认巡检脚本所在 vault（可用 `KB_ROOT`/`--root` 覆盖）。

## 命令

### 单个巡检项
```bash
python3 kb-healthcheck.py deadlinks     # 死链检测
python3 kb-healthcheck.py orphans       # 孤儿笔记（无入链）
python3 kb-healthcheck.py tags          # 标签越界
python3 kb-healthcheck.py all           # 全巡检（默认，等价于全部项）
```
退出码：0 = 无问题；1 = 发现问题。（用于 CI 门禁）

### 新增：`summary`（机器可读 · JSON）
```bash
python3 kb-healthcheck.py summary                 # JSON 到 stdout + 退出码
python3 kb-healthcheck.py summary --checks deadlinks,orphans   # 只算指定项
python3 kb-healthcheck.py summary --html health.html   # 另生成 HTML 健康页
python3 kb-healthcheck.py summary --root /path/to/vault
```
JSON 结构见 `test_kb_healthcheck/CONTRACT-summary-schema.md`。退出码：green=0 / red=1 / 非法 --root=2 / vault 不存在=3。

## 退出码速查
| 码 | 含义 |
|---|---|
| 0 | 无问题（或 green） |
| 1 | 发现问题（red） |
| 2 | 用法错误（argparse） |
| 3 | `--root` 指定的 vault 不存在 |

## 排错
- `ModuleNotFoundError: kb_common` → 从 `pipeline/` 目录运行，或把 pipeline 加到 `PYTHONPATH`。
- summary JSON 解析失败 → 确认用 `python3 -m json.tool` 校验；`generated_at` 为 ISO 本地时间。
- HTML 页打开空白 → 检查 `--html` 输出路径是否可写。

## docs-as-code
本文与代码同 PR 维护；`summary` 变更需同步更新本章与 `CONTRACT-summary-schema.md`。
