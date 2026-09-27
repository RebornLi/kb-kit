---
tags: [reference, 综合]
status: stable
domain: 开发
created: 2026-09-28
updated: 2026-09-28
importance: 0.3
kb_target: reference
kb_action: new
kb_summary: [[opencode]] 双链锚点页（OpenCode 会话记录来源标识）
kb_source: kb-managed
aliases: [opencode]
category: reference
---
# OpenCode 会话记录锚点

> 来源标识锚点：OpenCode 会话记录统一带
> `kb_source: opencode` 与 `[[opencode]]` 双链，落到本知识库对应分区。
> 本页作为该双链的目标页，承接所有来源为 OpenCode 的会话记录。

- 记忆形态：SQLite（`~/.local/share/opencode/opencode.db` 的 `session`/`message`/`part` 表）
- 摄入方式：`pipeline/memory_ingest_opencode.py`（opencode adapter）
- 相关：[[codex]]、[[dsh]]（其他 SQLite/JSON 记忆源锚点）
