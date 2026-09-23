---

tags: [reference, 综合]
status: stable
domain: 开发
created: 2026-09-07
updated: 2026-09-07
importance: 0.3
kb_target: reference
kb_action: new
kb_summary: [[dsh]] 双链锚点页（DSH agent-memory 来源标识）
kb_source: kb-managed
aliases: [dsh]
category: reference

---

# DSH 会话记录锚点

> 来源标识锚点：DSH（DeepSeek Harness）agent-memory 摄入的记录统一带
> `kb_source: dsh` 与 `[[dsh]]` 双链，落到本知识库对应分区。本页作为该双链
> 的目标页，承接所有来源为 DSH 的记录。

- 记忆形态：纯 JSON（`~/.dsh/storages/memory.json`）
- 摄入方式：`pipeline/memory_ingest_json.py`（JSON adapter）
- 相关：[[codex]]（Codex 会话记录锚点）
