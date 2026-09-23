---
name: kb-query
description: "Search the local kb-kit knowledge base with `kb query` for grounded answers. Use BEFORE answering questions about personal knowledge, past decisions, project details, or anything that might be in the KB vault."
metadata: { "openclaw": { "emoji": "🔍", "requires": { "bins": ["kb"] } } }
---

# KB-Query — 本地知识库检索

Use `kb query` to search the local kb-kit knowledge base **before** answering questions that
might benefit from personal/project knowledge. This grounds replies in what you already know
instead of guessing.

## When to use

Trigger a `kb query` when the user asks about:

- Past decisions, lessons, or project history
- How something was set up / deployed / fixed (look for SOPs)
- Definitions or concepts that live in the vault
- Anything that sounds like "do we have notes on X?" / "what did I decide about Y?"

Do **not** use it for general knowledge, small talk, or questions clearly outside the vault.

## Requirements

- `kb` on PATH (kb-kit installed; `~/.local/bin/kb` or vault-local `kb`).
- Vector index built (`kb rag index`). If query returns `{"error": "index not found"}`,
  tell the user to run `kb rag index` once.

## Command pattern

```bash
kb query "<question>" --json --top 5
```

- `--json` returns machine-readable JSON (use this in skill flow).
- `--top N` controls how many hits (default 3; use 5–8 for broader recall).
- Omit `--json` for human-readable formatted output.

## JSON response shape

```json
{
  "query": "备份",
  "hits": [
    {
      "path": "📖-知识库管理方案.md",
      "title": "📖-知识库管理方案",
      "domain": "管理",
      "score": 0.28,
      "snippet": "- 备份/演练：scripts/ 里 backup_now.sh、drill_now.sh",
      "tags": ["meta", "guide"],
      "importance": 1.0,
      "updated": "2026-08-05"
    }
  ]
}
```

- `score` — TF-IDF cosine similarity (higher = more relevant).
- `snippet` — best matching text fragment from the note.
- `importance` — user-curated weight (0.0–1.0).
- Empty `hits` means nothing relevant found; say so and answer from general knowledge.

## Operating flow

1. **Extract keywords** from the user's question (Chinese or English; the index handles both).
2. **Run the query**:
   ```bash
   kb query "<keywords>" --json --top 5
   ```
3. **Read the hits**. If `hits` is empty, try a broader query (fewer keywords, or a synonym).
4. **Ground your answer**:
   - Cite the note path(s) you used: "根据 `20-技术 Technology/xxx.md` …"
   - Quote or paraphrase the relevant `snippet`(s).
   - If the hits don't fully answer, combine them with general knowledge and say which part is from the KB vs. general.
5. **Do not fabricate**. If the KB has nothing relevant, say "知识库里没有相关内容" and answer from general knowledge with that caveat.

## Filters (optional)

Narrow results with flags after the query:

```bash
kb query "部署" --json --domain 运维              # 只搜运维领域
kb query "决策" --json --content-type decision    # 只搜决策类
kb query "Nginx" --json --tags "运维,部署"        # 按标签过滤
kb query "近况" --json --date-from 2026-09-01     # 按日期范围
```

Available filters: `--domain`, `--content-type`, `--author`, `--min-importance`,
`--enforce-level`, `--tags`, `--date-from`, `--date-to`.

## Other useful kb commands

| 命令 | 作用 |
|------|------|
| `kb rag index` | 重建索引（写新笔记后跑一次） |
| `kb list` | 列出所有插件及状态 |
| `kb healthcheck` | 健康巡检（死链/缺字段） |
| `kb ingest agent` | 把 OpenClaw 记忆灌进 KB |

## Tips

- The vault is plain `.md` files; you can also `cat` or `grep` directly if `kb query` misses.
- `kb query` does TF-IDF retrieval, not semantic embedding — use the user's exact words plus 1–2 synonyms for best recall.
- If `kb` points to the wrong vault, use the full path: `/path/to/vault/kb query "..."`.
