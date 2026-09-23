# 需求简报：kb_query 召回/耐久性 测试套件（kb-kit RSI 引擎首个自动化测试）

## 问题定义
`pipeline/kb_query.py` 是 RSI「查询→知识」正回路的核心（检索→规则综合→耐久性五门→人工在环提案）。
该模块及其依赖 `kb_rsi` **至今无任何自动化测试**（qa-testing-strategy/TDD 发现的硬缺口）。
回归/新贡献无法被机器门禁捕捉，只能靠人工跑 CLI。本次覆盖其**纯逻辑、与 embedding/LLM 无关**的部分。

## 用户故事 + 验收（Given-When-Then）
- US1 向量契约：_given 中文字/英文混合输入，_when 做字符向量，_then 相同文本余弦≈1.0、无关文本≈0.0，且用 L2 范数（非 sum=1 词频）。
- US2 长度门：_given 答案字符 < 60，_when 调 durability，_then pass=False 且 reason 含「过短」。
- US3 多样性门：_given TTR < 0.45 的重复文本，_when 调 durability，_then pass=False 且 reason 含「词汇重复」。
- US4 新异度门：_given 答案与某篇编译笔记几乎全同（sim≥0.90），_when 调 durability，_then pass=False 且 reason 含「复述」。
- US5 综合通过：_given 够长、词汇多样、与现有笔记不高度相似的文本，_when 调 durability（无注或低相似注），_then pass=True。
- US6 检索排序：_given 若干编译笔记，_when 调 search()，_then 返回余弦降序 top-k 且仅含 s>0。

## 非功能需求（NFR）
- 零新依赖：仅用 stdlib + 已存在的 pytest。
- 确定性：不触 embedding/LLM；fixture 用临时目录 + 手写编译笔记。
- 快速：全绿 < 5s；可 `python3 -m pytest` 在 CI 跑。
- 无副作用：不写提案文件、不写库、不 git commit（纯断言）。

## 范围外（明确不测）
- `query()`/`apply()` 的提案/写库副作用（涉人工在环 + git，留作后续）。
- `_grounded()` 外接阀（依赖 kb_rsi 全局 metrics，需真实 vault）。
- embedding 语义路径（kb_embed，未 probe 接通）。

## 待确认
- 边界阈值取等：长度 `>=60` 是否过？TTR `>=0.45`？novelty `<0.90`？→ 按源码常量精确测。
