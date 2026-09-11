---
tags: [reference, 综合]
status: stable
domain: 综合
created: 2026-09-07
updated: 2026-09-07
importance: 0.3
kb_target: reference
kb_action: new
kb_summary: [[codex]] 双链锚点页（Codex 会话记录来源标识）
kb_source: kb-managed
aliases: [codex]
---

# Codex 会话记录锚点

> 来源标识锚点：Codex（OpenAI Codex）会话记录统一带
> `kb_source: codex` 与 `[[codex]]` 双链，落到本知识库对应分区。
> 本页作为该双链的目标页，承接所有来源为 Codex 的会话记录。

- 记忆形态：SQLite（`~/.codex/*.sqlite` 的 `thread_items` 表）
- 摄入方式：`pipeline/memory_ingest_sqlite.py`（SQLite adapter）
- 相关：[[dsh]]（DSH agent-memory 记录锚点）
