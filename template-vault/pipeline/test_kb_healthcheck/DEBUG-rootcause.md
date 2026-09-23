# 故障复盘（debugging-root-cause）— summary 开发中的三个真 bug

## Bug 1：`import kb_healthcheck` ModuleNotFoundError
- **复现**：pytest collection 报错 `ModuleNotFoundError: No module named 'kb_healthcheck'`。
- **假设**：sys.path 没加上 pipeline/（概率高）→ 实测路径解析正确（parent.parent=pipeline），排除。
- **受控实验**：直接 `python3 -c "import sys...; import kb_healthcheck"` → 仍报同样的错。
- **根因**：文件名是 `kb-healthcheck.py`（连字符），Python 模块名不能含连字符，`import kb_healthcheck` 找的是 `kb_healthcheck.py`（下划线），二者不同名。
- **修复**：用 `importlib.util.spec_from_file_location` 按路径装载为 `kb_healthcheck` 模块。
- **预防**：测试夹具统一用 importlib 装载连字符文件；已固化进 test_summary.py 顶部。

## Bug 2：green 测试变 red（frontmatter + orphan 误报）
- **复现**：`test_aggregate_green` 断言失败，aggregate 不是 green。
- **假设**：aggregate 算法错 → 单测 _aggregate 通过，排除。
- **受控实验**：逐个 check 跑单笔记 vault → frontmatter 缺 tags/status 报 1；单笔记无出链 → orphan 报 1。
- **根因**：测试 fixture 本身不"干净"（缺 frontmatter 字段 + 无入链），而非代码 bug。测试在正确地执行契约。
- **修复**：green fixture 改为 4 字段齐全 + 两条互链的真实干净 vault。
- **预防**：clean-vault fixture 模板化，新增 check 时同步更新。

## Bug 3：HTML 测试断言字面量 `aggregate`
- **根因**：HTML 用中文标签 `状态` 渲染，测试断言英文词 `aggregate`，是测试过于字面。
- **修复**：断言 `状态` in body（实际渲染的聚合标签）。

> 结论：3 个 bug 中 1 个是真代码问题（import 装载），2 个是测试契约修正。全部用「一次一变量」定位，未凭感觉改。
