# B 组精读报告 — 核心公共层 / 数据模型 / 状态

> 范围：`template-vault/pipeline/` 下 `kb_common.py`、`kb_constants.py`、`kb_types.py`、`kb_schema.py`、`kb_schema_migrate.py`、`kb_meta.py`、`state_manager.py`、`taxonomy.py`、`evolution_log.py`、`user_manager.py`、`agent_registry.py`，外加对照文件 `template-vault/reference/kb-schema.json`。
> 方法：全部文件用 `read` 分段读完（`kb_common.py` 1213 行分 3 段、`kb_meta.py` 507 行分 2 段、`agent_registry.py` 441 行分 2 段）；行号引用均来自实际读到的内容。跨文件引用关系用 grep 辅助确认，未读的第三方模块只标注"引用点行号"，不臆测其内部实现。
> 只读模式：**本次未修改任何代码**（除任务指定的新建报告文件 `.survey/B-core.md`）。

---

## 一、kb_common.py 逐函数详解（1213 行）

### 1.1 模块级常量

| 行号 | 名称 | 值 / 说明 |
|---|---|---|
| 25 | `ROOT_DEFAULT` | `os.environ.get("KB_ROOT") or Path(__file__).resolve().parents[1]` → 即 `template-vault/`。环境变量优先。 |
| 28-30 | `EXCLUDE` | 目录排除集：`.git`、`backups`、`logs`、`vector index`、`pipeline`、`_SELF_OPT`、`.obsidian`、`__pycache__`、`.pytest_cache`、`.agents`、`.trash`、`.codeartsdoer`、`_curate`。**注意不含 `raw`、`reference`、`_audit`**。 |
| 33 | `DOMAIN_WHITELIST` | `{"运维","开发","安全","产品","数据","管理","综合"}`（7 个一级 domain） |
| 36 | `FM` | `re.compile(r"^---\s*$", re.M)` —— frontmatter 分隔符正则 |
| 39-40 | `CJK` / `LAT` | `[\u4e00-\u9fff]` / `[A-Za-z0-9]+` |
| 170 | `_CHUNK_SUFFIX` | `-(?:p\|c)\d+$`（私有）分块文件名后缀 |
| 218 | `_MODEL_CFG_CACHE` | 私有进程级缓存 `Dict[str, Any]`，键为 `str(root)` |
| 262 | `RETRIEVABLE_STATUSES` | `{"active","stable","legacy"}` |
| 263 | `DEFAULT_STALE_DAYS` | `180` |
| 314 | `STUB_MARKERS` | `("{name} · 块{i}", "已按 §3.3 颗粒度分块为")` |
| 316-326 | `_POINTER_LINE` | 指针行正则（标题 / 列表+wikilink / 列表+箭头 / 纯 wikilink / `A → B.md` / 表格行 / 表格分隔） |
| 375 | `STUB_WORDS` | `("未过滤", "原始记录")` —— **定义后全库无引用（死常量）** |
| 376-383 | `LAYER_MULTIPLIER` | `{"canon":1.25, "":1.00, "page":1.00, "raw":0.72, "index":0.60, "noise":0.60}` |
| 384 | `DEFAULT_LAYER` | `"page"` |
| 416-420 | `GENERATED_REPORTS` | **10 个**运行期产物文件名：`intake_triage.md`、`feedback_hits.md`、`recall_deck.md`、`recall_schedule.md`、`link_suggestions.md`、`_INDEX.md`、`clean_suggestions.md`、`_MOC.md`、`_graph.md`、`_graph_kg.md`（注释 415 行说"运行期生成的报告文件"，未标数量） |
| 508 | `_config_cache` | 私有配置缓存，**键仅为 name，不含 root** |
| 581 | `RELATION_FIELDS` | `("related_to","prerequisite","supersedes","superseded_by")` |
| 954-955 | `import datetime as _dt` / `import os as _os` | 模块中段才导入（顶部第 18-22 行已 import 过 `os`/`datetime`），属于"clean.py 迁入段"的重复导入 |
| 958-960 | `AGGREGATE_DIR` / `AGGREGATE_LIMIT` / `AGGREGATE_IMPORTANCE` | `"00-收件箱 Inbox/_aggregated"` / `2000` / `0.3`。`AGGREGATE_DIR`+`AGGREGATE_IMPORTANCE` 在 `aggregate_bucket`(1139,1154) 使用；**`AGGREGATE_LIMIT` 被 `clean.py:35` import 但在 clean.py 内从未使用（死导入），且全仓无其它引用** |
| 963-971 | `FOLDER_DOMAIN` | 目录名 → domain 映射（约 24 条，如 `"AI与LLM AI":"开发"`、`"安全 Security":"安全"`） |
| 972-976 | `DOMAIN_MAP` | 旧 domain 词 → 新一级 domain（`"技术"→"开发"` 等 16 条） |
| 977-978 | `KEYWORD_DOMAIN` | `{"test":"开发","deploy":"运维","database":"数据","prompt":"产品","career":"综合","security":"安全"}` |
| 981-983 | `_FM_RE` / `_CJK_RE` / `_LAT_RE` | 与第 36/39/40 行 `FM`/`CJK`/`LAT` **完全同义的第二套正则**（迁移遗留） |
| 986 | `STATUS_SET` | `{"draft","active","stable","legacy","archived"}` |
| 1168-1174 | `_DEDUP_DEFAULTS` | `{"dup_sim":0.92,"novel_sim":0.70,"sync_sim":0.85,"replace_sim":0.85,"merge_sim":0.70}` |

### 1.2 文本处理

**`tokenize(s)` ── 43-54**
- 语义：`LAT.findall` 拉丁词转小写 + CJK 逐字 unigram + 相邻字 bigram。目的（注释 46 行）是让"去重判定与 RAG 索引同构"。
- 边界：`s` 为 `None` 会在 `LAT.findall(None)` 抛 `TypeError`（无防御）；空串返回 `[]`；CJK 单字（如"的"）也会成为 token → 噪声 token 靠上层 idf 过滤（kb_constants `L0_MIN_IDF`）。

**`parse_frontmatter(text)` ── 57-86**
- 签名：`(text) -> (fm: dict, body: str)`。
- 语义：`FM.search` 找首个 `^---$`，再找下一个 `^---$`；块内逐行解析。`#` 开头行跳过；行尾为 `:` 时 `fm[key] = None`（嵌套键降级）；含 `:` 的行切第一个 `:`，值 `strip().strip('"\'')`。
- 边界行为：
  - 无 frontmatter / 只有一个 `---` → 返回 `({}, 原文)`（70-71 行）。
  - **值类型恒为 `str` 或 `None`**：`tags: [a, b]` 会得到字符串 `"[a, b]"`（与 `load_note_full` 返回 list 的行为不一致，见 §2.4）。
  - 正文里若出现一行恰为 `---`（水平分割线），且有第二个 `---`，会被误判为 frontmatter 边界。
  - **不处理多行块列表**（`aliases:\n  - a`）：`aliases:` 行判为 `None`，`- a` 行含 `:` 无关则被忽略（`- a` 无冒号 → 丢弃）。

**`count_tokens(text)` ── 130-140**
- 语义：`CJK 字数 + 拉丁字符数 // 4`。`rag.py --context-budget` 用（注释 134 行）。
- 边界：`None`/空 → 0；标点、空白**不计入**；拉丁 <4 字符 → 0 token（英文短文本会被显著低估）。

**`text_entropy(text)` ── 143-158**
- 语义：Shannon 熵再除以 `log2(字符种类数)` 做 0-1 归一。空文本 → 0.0；单字符重复（`num_types<=1`）→ 0.0；每字符唯一 → 1.0。
- 调用方：`classify.py:190`（延迟导入）、`classify.py:198`。

**`content_fingerprint(body)` ── 161-166**
- 语义：`sha256(body.encode("utf-8")).hexdigest()`（64 位 hex）。
- 调用方（多）：`rag.py:221,261`、`kb_ingest.py:35,109,124,132`、`sync.py:182,211`、`memory_ingest.py:243-244`、`memory_sync.py:22,96`、`curate.py:264,958,1168`、`clean.py:237`。
- 边界：`body=None` → `AttributeError`（无防御）。

### 1.3 文件 I/O

**`load_meta(path)` ── 89-98**
- 签名：`(path) -> (fm: dict, body: str)`。读失败（`OSError`/`UnicodeDecodeError`）→ `({}, "")`。
- 调用方：`rag.py:16`、`dashboard.py:84`、`sync.py:221`、`rerank.py:12`、`llm_rerank.py:25`、`root_of` 内部（208 行）。

**`load_note(path)` ── 101-109**
- 签名：`(path) -> (fm, text, body)` 三元组。与 `load_meta` 的差别是额外返回原始 `text`。
- 边界：**读失败会直接抛异常**（107 行裸 `read_text`，无 try）。
- 调用方：`feedback_loop.py:18`、`kb_ingest.py:35`、`link_engine.py:16`、`kb_health.py:22`（`load` 别名）、`graph.py:12`。

**`iter_notes(root)` ── 112-127**
- 语义：`os.walk` 收集 `*.md`；就地裁剪 `EXCLUDE` 目录；遇到子目录含 `.git` 时剪枝（122-124 行）。
- 关键边界（注释 115-117 行）：**必须先把 `root` 转成 `str`**（119 行 `os.fspath`），否则某些沙箱下首元组 `dp` 为 str、`dp != root` 误判为真 → **整库遍历静默返回 0**。这是一处已修复的历史静默失败。
- 性能：**每次调用全盘 `os.walk`**，无缓存、无 mtime 索引；全仓 43 处调用点（`clean.py` 10 次、`kb_confidence.py`/`curate.py` 各 4 次…），同进程内多次调用会重复扫描 (§7-9)。

**`safe_read_text(path)` ── 838-852**：`read_text(utf-8)`，失败 → `None`。**全仓无外部调用方（死代码）**。
**`safe_load_note(path)` ── 855-870**：→ `(fm, text, body)` 或 `None`。**全仓无外部调用方（死代码）**。
**`safe_json_load(path)` ── 873-890**：→ dict 或 `None`。**全仓无外部调用方（死代码）**。

**`load_note_full(path)` ── 1019-1053**
- 签名：`(path) -> (fm, text, block, body)` 四元组（`block` = frontmatter 原始文本块）。
- 语义：**独立重写了 frontmatter 解析**（1031-1052 行），与 `parse_frontmatter` 的差异：
  - 跳过 `{`/`}` 行（1043）；
  - 键名也做 `strip('"\'')`（1046）；
  - **`[a, b]` 会解析成 Python list**（1049-1050 行）。
- 边界：无 frontmatter → `block=""`, `body=text`；读失败抛异常（1031 行无 try）。
- 调用方（`as load` 别名，最广泛）：`clean.py:31-40`、`kb_schema.py:26`、`curate.py:33`、`kb_audit.py:29`、`contextual.py:27`、`tag_fix.py:29`、`kb_confidence.py:33`、`kb_contradiction2.py:29`、`memory_sync.py:19`、`curate_prompt.py:92`、`kb_raw.py:29`。

**`model_config(root=None)` ── 221-249**
- 语义：环境变量 `ORNITH_API_KEY`/`OPENAI_API_KEY`、`ORNITH_BASE_URL`、`ORNITH_CHAT_MODEL`、`ORNITH_EMBED_URL`、`ORNITH_EMBED_MODEL` 优先；否则读 `<root>/.kb/model.json`，再退 `<root>/kb-model.json`（234 行）。返回 5 键 dict。
- 设计动机（216-217 行注释）：cron/systemd 读不到 `~/.bashrc`。
- 边界：**进程级缓存键 = `str(root)`**（230-232、248 行）→ 同进程内首次读不到配置后即便创建了 `model.json` 也不会重新加载；环境变量改动同样不生效。
- 调用方：`contextual.py:84`、`kb_eval_judge.py:47`、`embed_rerank.py:55,73`、`llm_rerank.py:25,72,113,174`、`template-vault/scripts/bench_llm_tps.py:24`。

### 1.4 纳入判定 / 空壳识别

**`is_source_note(fm)` ── 253-258**：`is_source` 为 `True` 或 `"true"/"yes"/"1"`，或 `kind == "source"` → `True`。被 `kb-healthcheck.py:27`、`clean.py:36`、`dashboard.py:84`、`index_excluded`、`kb_layer_of` 使用。

**`retrievable_status(fm)` ── 266-269**
- 语义：无 `status` → 宽松放行 `True`；有则 `status in {"active","stable","legacy"}`。
- **文档与实现不一致**：docstring 写"仅 active/stable 可检索"，实际含 `legacy`（§7-18）。
- 调用方：`rag.py:16`、`kb-healthcheck.py:27`、`dashboard.py:84`。

**`_parse_day(v)` ── 272-277**（私有）：`str(v or "")[:10]` → `date.fromisoformat`，失败 `None`。`"2026-1-5"` 这类非补零日期解析失败。

**`freshness(fm, today=None)` ── 280-289**
- 返回 `{"age_days","review_after","review_due","stale"}`。
- `upd = updated or created`；`age = (today-upd).days`；`review_due = review_after <= today`；`stale = review_due or (age is not None and age > 180)`。
- 边界：无 `updated`/`created` → `age_days=None`，且未被 review 命中时 `stale=False`（**旧笔记默认不陈旧**）。
- 调用方：`kb-healthcheck.py:27`、`dashboard.py:84`、`rag.py`（经 `is_stale`）。

**`is_stale(fm, today=None)` ── 292-294**：`freshness(...)["stale"]` 的薄封装。

**`index_excluded(fm)` ── 297-310**
- 顺序判据：`kb_action == "retire"` → 排除；`kb_index in ("false","no","0")` → 排除；`not retrievable_status` → 排除；`is_source_note` → 排除；否则 `is_index_stub(fm)`。
- **注意：调用 `is_index_stub(fm)` 时未传 `body`**（310 行）→ `is_index_stub` 第 341 行的"正文 <200 字"分支**在此路径永不生效**，与 `curate.py:324`（传了 body）判定不一致（§7-17）。
- 调用方：`rag.py:16`。

**`is_index_stub(fm, body="")` ── 329-345**
- 判据：① `is_chunk_index` 为真值；② `chunk_of` 存在且 **无** `chunk`（父页被改写成目录）；③ `chunk_of` 存在且正文去换行后 <200 字；④ `kb_layer == "index"`。
- 边界：子块（`chunk: 1..N`）**不**算 stub（注释 333 行）。

**`is_pointer_page(body, rel="", min_lines=3)` ── 348-368**
- 判据：`-index.md` 结尾 → 直接 `True`；空正文 → `True`；前 6 行含 `STUB_MARKERS` → `True`；否则按"指针行占比 ≥0.6"，行数 `< min_lines` 时要求**全部**行都是指针行。
- 调用方：`curate.py:324`、`clean.py:466`、`template-vault/scripts/verify_curate.py:48`。

### 1.5 分层与生成物

**`kb_layer_of(fm, rel="", body="")` ── 387-398**
- 显式 `kb_layer` 小写优先 → `is_source_note` → `raw` → `is_index_stub` → `index` → `rel` 在 raw/ → `raw` → 默认 `page`。
- 调用方：`rag.py:16`、`kb_schema.py:26`、`curate.py:34`、`verify_curate.py:29`。

**`layer_multiplier(fm, rel="")` ── 401-403**：`float(LAYER_MULTIPLIER.get(kb_layer_of(fm, rel), 1.0))`。
- 边界：`kb_layer_of` 内部可返回 `"raw"`（`is_source_note` 路径）；但若显式 `kb_layer` 写成 LAYER_MULTIPLIER 里没有的值（如 `"evidence"`），`.get(..., 1.0)` → 中性 1.0（**未知层不降级、不报错**）。
- 调用方：`rag.py:16`。

**`is_raw_path(rel)` ── 406-412**：`Path(rel).parts[0].lower() == "raw"`，即**只看顶层目录**（`a/raw/b` 不算）；异常 → `False`。

**`is_generated_report(rel)` ── 423-436**
- 文件名命中 `GENERATED_REPORTS`；或路径上级含 `_curate` / `_audit`。
- 文档注（426-428 行）：`_MOC.md`/`_INDEX.md` 仍属生成物，死链巡检另在 `kb-healthcheck` 的 `NAV_FILES` 单独纳入。
- 调用方极广：`kb_schema.py`、`rag.py:16`、`curate.py:35`、`clean.py:36`、`tag_fix.py`、`validate.py:13`、`contextual.py:27`、`kb_confidence.py:33`、`feedback_loop.py:18`、`dashboard.py:84`、`kb_audit.py`、`kb_raw.py`、`kb-healthcheck.py:27` 等。

### 1.6 别名 / 血缘 / 合并

**`_alias_items(val)` ── 440-442**（私有）：`strip().strip("[]")` 后按 `[,\s]+` 切分。
- 边界风险：传入 YAML 块列表项 `"- a"` 会被切成 `["-", "a"]`（§7-27）。

**`add_alias_text(text, alias)` ── 445-464**
- 语义：在 frontmatter 里追加 `aliases`（幂等）；**无 frontmatter 返回 `None`**（调用方需判空）。
- 实现：只匹配单行 `^aliases:.*$`；不存在则把 `aliases: [x]` **插到 block 最前**（463 行）。
- 边界：`aliases` 为多行块列表时，`^aliases:.*$` 匹配到空值行 → 被替换为 `aliases: [new]`，**原块列表项残留为孤儿行**（数据损坏风险）。
- 返回：`"---\n" + block + "\n---\n" + body` —— 统一用 `\n` 拼（Windows CRLF 环境下会混合换行）。
- 调用方：`intake_triage.py:21,324`、`clean.py:37,254`、`tests/test_p2.py:28`。

**`record_lineage(root, src, dst, action)` ── 467-477**
- 语义：追加一行 JSON 到 `<root>/.kb/state/lineage.jsonl`（`{ts, action, from, to}`）。
- 边界：**`OSError` 被静默 `pass`**（476-477）→ 血缘丢失无任何提示（静默失败）。
- 调用方：`intake_triage.py:340`、`curate.py:35,916`、`clean.py:38,258`。

### 1.7 domain / 配置加载

**`parse_domain(domain_str)` ── 481-494**：`"开发/后端" → ("开发","后端")`；空/None → `("综合", None)`；用 `split("/", 1)`（只切一层）。
**`domain_primary(s)` ── 497-499**：`parse_domain(...)[0]`。调用方：`sync.py:175`、`graph.py:12`、`rerank.py:12`、`rag.py:511`。
**`domain_secondary(s)` ── 502-504**：`parse_domain(...)[1]`。**全仓无调用方（死代码）**。

**`load_config(name, root=None)` ── 511-533**
- 语义：读 `<root>/reference/<name>`（JSON），**`pop("_meta")` 后**缓存返回；文件不存在 → 缓存 `{}` 并返回 `{}`。
- 边界：**缓存键只用 `name`（521-522 行），不含 `root`** → 多库/测试场景串味（先用 root A 加载，再用 root B 取同名配置会拿到 A 的）。
- 细节：第 529 行函数内 `import json`（模块顶部第 18 行已导入）→ 冗余。
- `_meta` 被剔除 → 调用方无法读到配置版本号。

**`load_domain_taxonomy(root)` ── 536-538** → `reference/domain-taxonomy.json`。
**`load_tag_taxonomy(root)` ── 541-543** → `reference/tag-taxonomy.json`。
**`load_dedup_config(root)` ── 546-548** → `reference/dedup-config.json`。调用方 `memory_ingest.py:369`。

**`is_valid_domain(domain_str, root=None)` ── 551-561**
- **配置缺失 → 返回 `True`（fail-open，556 行）**；否则一级必须在 taxonomy，二级若非空也必须在对应列表。
- 调用方：`validate.py:103`。

**`is_valid_tag(tag, root=None)` ── 564-577**：同样 fail-open（568 行）；支持 `"一级/子级"`。**全仓无调用方**（`kb-healthcheck.py:245` 只用 `load_tag_taxonomy` 自行判断）。

### 1.8 关系 / 去重

**`extract_relations(fm)` ── 584-590**：返回 `RELATION_FIELDS` 中非空的 `{field: value}`。调用方：`graph.py:12,33`。

**`add_relation(fm, field, target_rel)` ── 593-611**
- 幂等追加：`None` → 直接赋值；`list` → 不存在才 append；`str` 且不同 → 转成 `[old, new]`。
- 边界：`field` 不在 `RELATION_FIELDS` → **静默 return**（601-602 行，无告警）。
- 调用方：`sync.py:221,252`。

**`dedup_check(body, existing_notes, root=None)` ── 615-672**
- 三级：① sha256 精确重复 → `skip`；② TF-IDF 余弦 ≥ `semantic_dedup.threshold`（默认 0.92）→ `skip`；③ `[threshold_min, threshold_max)`（默认 0.70-0.92）→ `mark_related`；否则 `new`。
- 返回 `{"action","reason","matched_rel","similarity"}`。
- **全仓无调用方（死代码）**；且第 645-661 行在函数内重复实现了 `norm()` 的逻辑（§8）。

### 1.9 向量运算

**`norm(vec)` ── 676-693**：L2 归一；空向量原样返回；零向量用 `or 1.0` 防除零（692 行）。
**`cos(a, b)` ── 696-718**：空 → 0.0；自动 swap 用较短向量遍历（716-717）；**返回点积**（假定已归一化，docstring 明确）。
**`char_vec(text)` ── 721-738**：`norm(dict(Counter(tokenize(text))))`；空文本 → `{}`。
- 调用方：`kb_rsi.py:21`（`norm`/`char_vec`）、`memory_ingest.py:49`、`link_engine.py:16`、`kb_health.py:22`、`kb_contradiction.py:31`、`kb_calibrate.py:24`、`kb_query.py:28`。

### 1.10 日期 / 数值解析

**`parse_date(v)` ── 745-774**
- 先 `strptime(str(v).strip()[:10], "%Y-%m-%d")`；失败则用 `_DATE_RE`（742 行 `(\d{4})-(\d{1,2})-(\d{1,2})`）从串中提取。
- 边界：`None`/空 → `None`；`"invalid"` → `None`；日期非法（如 2026-02-30）→ `None`。
- 调用方：`kb_rsi.py:21`、`kb_health.py:22`（别名 `_kb_parse_date`，见 kb_health.py:37-40 兼容包装）。

**`parse_date_safe(v, default=None)` ── 777-798**：失败返回 `default`，`default is None` 时返回 `datetime(1970,1,1)`；**永不返回 None**。**全仓无调用方**（`kb_claim.py:165` 与 `kb_calibrate.py:208` 仅在注释里讨论其行为差异）。

**`parse_importance(s)` ── 805-834**
- `None` → 0.0；`int/float` → `float`；否则用 `_IMPORTANCE_RE = [-+]?\d*\.?\d+` 抓第一个数字。
- 边界：**`"P0"` → 0.0**（docstring 示例自证，819-820 行）；`"0.85 高"` → 0.85；`"v2"` → 2.0（可能非预期）。
- 调用方：`feedback_loop.py:18`、`kb_selfweight.py:37`（均以 `_parse_importance` 别名）。

### 1.11 倒排索引

**`build_inverted_index(vecs)` ── 894-917**：`{token: {rel,...}}`。
**`candidate_pairs(index, rels=None)` ── 920-950`**
- 生成共享 ≥1 token 的有序对（`a < b` 字典序），用 `seen` 集合去重；`rels` 限制范围。
- 返回生成器；**边界**：`seen` 全量驻留内存（大库 O(对数) 内存）。
- 调用方：`kb_rsi.py:21`、`kb_health.py:22`、`kb_contradiction.py:31`。

### 1.12 从 clean.py 迁入的复用族（953 行后）

**`_tokens(s)` ── 989-991**（私有）：拉丁词 + CJK unigram。**与 `tokenize` 不同（无 bigram）**，仅 `infer_domain` 用 → 同模块内两套分词（§8）。
**`fmt_value(v)` ── 994-998**：list → `"[a, b]"`，其他 `str(v)`。调用方：`clean.py:32`、`memory_sync.py:20`、`curate.py:35`、`sync.py:181`。
**`parse_date_str(v, default)` ── 1001-1016**：字符串日期 → `YYYY-MM-DD`，否则 `default.strftime("%F")`。检查条件是 `v[4]=='-' and v[7]=='-'`（1014 行），非严格校验。别名 `parse_date` 用于 `clean.py:39`、`memory_sync.py:21`。
**`infer_domain(rel, fm, title, body)` ── 1056-1071**：优先级 `fm.domain(白名单)` → `DOMAIN_MAP` → 逐级目录名（`rel.split(os.sep)`，1063 行）→ 关键词（标题 + body 前 400 字）→ `"综合"`。
**`infer_status(rel, fm, mtime)` ── 1074-1084**：`"归档"/"trash"` → archived；`fm.status` 合法则用之；否则按 mtime 年龄分档 **>180 archived / >90 legacy / >30 stable / else active**。
**`infer_tags(folder)` ── 1087-1092**：`[folder, slug]` 或 `[folder]`，空 → `["knowledge"]`。
**`list_of(fm, *more)` ── 1095-1103**：tags 支持 list / 逗号空格分隔字符串。
**`base_fm(rel, fm, extra)` ── 1106-1112**：合并 + tags 字符串转 list。
**`infer_summary(body)` ── 1115-1121**：首个非空行（strip ` \t>*#`）截 120 字；否则 `"（待补充摘要）"`。
**`aggregate_bucket(root, body, content_type, author=None)` ── 1124-1163**
- 追加到 `00-收件箱 Inbox/_aggregated/<YYYY-MM>-{chat|todo}.md`，条目格式 `\n---\n\n**[HH:MM] author:** body\n`。
- 新建时写 frontmatter（tags/status/domain/created/updated/importance=0.3/kb_target/kb_action/kb_summary/content_type）。
- 边界：`content_type` 不在 chat 族且不是 todo → 一律归 `todo`（1137 行）；**读-改-写整个桶文件，非原子、无锁**（1145-1147）；返回相对路径 `str(bucket_path.relative_to(root))`。
- 调用方：`sync.py:181,190`、`clean.py:32`。

**`get_dedup_thresholds(domain=None, root=None)` ── 1177-1213**
- 先取 `_DEDUP_DEFAULTS`，用 `config["global"]` 覆盖；再兼容旧格式 `semantic_dedup.threshold → dup_sim`、`semantic_related.threshold_min → novel_sim`；最后 `by_domain[domain]` 覆盖。
- 返回 5 键：`dup_sim / novel_sim / sync_sim / replace_sim / merge_sim`。
- 边界：`by_domain` 只对传入 domain 生效；`domain=None` 时只走 global。
- 调用方：`memory_ingest.py:19,400,526`、`memory_sync.py:21,176`。

---

## 二、note 数据模型

### 2.1 契约来源
`reference/kb-schema.json`（340 行，`_meta.version = 1`，1-8 行）自称"提示词、校验器、导出器的单一事实源"。它声明了 `layers`(9-15)、`page_types`(16-73)、`fields`(74-298)、`canon_required`(299-304)、`noise_must_have`(305-308)、`raw_forbidden`(309-311)、`confidence_rubric`(312-334)、`stale_policy`(335-340)。

### 2.2 字段清单（契约字段 → 类型 → 含义 → 谁写 / 谁读）

| 字段 | 类型 | 含义 | 写方（实证） | 读方（实证） |
|---|---|---|---|---|
| `tags` | list（契约）/ str（`parse_frontmatter` 实际） | 受控标签 | `memory_sync.py:259,265`、`clean.py:195`、`aggregate_bucket`(1150) | `rag.py:367,657`、`rerank.py`、`tag_fix.py:68,98`、`kb-retriage.py:198` |
| `status` | enum(draft/active/stable/legacy/archived)，required | 生命周期状态 | `clean.py:190`(infer_status)、`kb_schema_migrate`(124) | `retrievable_status`(268)、`validate.py:96`、`kb_schema.py:84` |
| `domain` | enum(7 值)，required | 领域，可两级 `一级/二级` | `clean.py:189`、`memory_sync.py:255`、`kb_schema_migrate`(117-122) | `parse_domain`(481)、`rag.py:511`、`graph.py`、`rerank.py` |
| `category` | enum，`_maturity: aspirational`，非必填 | 页型/分区（project/experience/reference/sop/meta/knowledge） | `kb_schema_migrate.py:113-114`（唯一写方） | `kb_schema.py:84`（枚举校验） |
| `created` / `updated` | date，required | 时间戳 | `clean.py`、`aggregate_bucket`(1152-1153) | `freshness`(283)、`kb_health.py:62,67` |
| `importance` | number [0,1]，required | 重要性 | `feedback_loop.py`(BUMP_CAP/MIN_BUMP)、`clean.py`、`memory_ingest.py` | `parse_importance`(805)、`recall_schedule`、`rag.py` 排序 |
| `kb_target` | string | 目标相对路径 | `memory_sync.py:264`、`memory_ingest.py:324,465`、`memory_ingest_json.py:107`、`memory_ingest_sqlite.py:178` | `memory_ingest.py:373`、`memory_ingest_json.py:115`（含越界校验） |
| `kb_action` | enum(new/append/update/retire) | 动作 | `memory_sync.py:264`、`memory_ingest_json.py:108`、`kb_query.py:251` | `index_excluded`(300)、`kb_selfweight.py:161,186`、`validate.py:119` |
| `kb_summary` | string，max_len 200 | 检索表层摘要 | `memory_sync.py:265`、`memory_ingest.py:467,539`、`memory_ingest_json.py:109`、`memory_ingest_sqlite.py:178` | `kb_schema.py:124`、`kb_audit.py:97`、`memory_ingest.py:374`、`rag.py` |
| `kb_layer` | enum(canon/page/raw/index/noise)，`enforced` | 知识分层 | `clean.py:205`(index)、`curate.py:848`(noise)、`curate` canon 路径 | `kb_layer_of`(389)、`kb_schema.py:120`、`curate_prompt.py:99`、`layer_multiplier` |
| `kb_index` | bool | 是否入 RAG 索引 | `curate.py:848`（写字符串 `"false"`） | `index_excluded`(302)、`kb_schema.py:116`、`curate.py:296` |
| `source_ref` | list | canon 溯源 ID（`canon_required`） | `curate.py:689,717` | `kb_confidence.py:44,150`、`kb_contradiction2.py:99` |
| `archived_original` | string | 保号原文路径（`canon_required`） | `curate.py:909` | `kb_schema.py:110-112`（存在性校验） |
| `source_chars` / `canon_ratio` | number | 原文规模 / 结晶比 | `curate.py` | `kb_schema.py:95` 范围检查、`kb_confidence.py` |
| `grounded_facts` | list | 回原文核对通过的事实 | `curate.py`（经 prompt 契约） | `kb_confidence.py`、`curate_prompt.py` |
| `curate_confidence` | number [0,1] | 结晶置信 | `curate.py` | `kb_schema.py:95,102` |
| `curate_verdict` | enum(canon/raw-only/noise/merge)，`enforced` | 结晶裁决 | `curate.py:851` | `kb_schema.py:115`、`curate.py:294` |
| `curated_by` / `curated_at` | string / datetime | 结晶者与时间 | `curate.py:851` | 审计/报告 |
| `event_at` / `derived_at` | date / datetime | P3 双时态 | `curate.py` | 文档/契约 |
| `confidence` | number [0,1] | 复合置信分 | `kb_confidence.py:267` | `rag.py` 排序、`kb_selfweight` |
| `confidence_tier` | enum(publish/review/evidence-only) | 相对分档 | `kb_confidence.py:267` | 报表 |
| `conflicts_with` | list | 矛盾在环 | `kb_contradiction2.py` | `kb_contradiction2.py` |
| `superseded_by` / `supersedes` | string | 替代指向 | 人工/`curate` | `graph.py`、`extract_relations`(590) |
| `lifecycle` | enum，`aspirational` | P3 才启用 | **无写方** | `kb_schema.py:88-89` |
| `aliases` | list | 别名（合并重定向） | `add_alias_text`(445)、`intake_triage.py` | 未在 pipeline 读到消费方（"未确认"） |
| `redirect_to` | string | 墓碑重定向 | `intake_triage.py:270` | **未读到消费方（未确认）** |
| `chunk` / `chunk_of` | number / string | 分块关系 | `clean.py:489`、`semantic_chunk.py` | `parent_of`(176)、`is_index_stub`(338-341)、`root_of` |
| `is_source` | bool | 原始来源 | `memory_ingest`（镜像语义） | `is_source_note`(255)、`kb_schema.py:78` |
| `coverage` | number [0,1] | 关键标识符覆盖率 | `curate.py:692` | `kb_scale.py`、`kb_engine.py` |
| `canon_type` | string | 页型（对应 `page_types`） | `curate.py:693,709` | `kb_confidence.py:113` |
| `review_after` | （**契约未声明**） | 复习到期日 | **无写方**（仅 `freshness` 285 读取） | `kb_common.py:285`、间接 `rag.py:836`、`kb-healthcheck.py:442`、`dashboard.py:137` |
| `kind` | （契约未声明） | `"source"` / `"compiled"` / `"raw"` | `kb_query.py:201` | `is_source_note`(258)、`validate.py:126`、`kb_fitness.py:60,110` |
| `is_chunk_index` | （契约未声明） | 分块索引页标记 | `clean.py:206,484,522`、`semantic_chunk.py:269` | `is_index_stub`(335)、`validate.py:146` |
| `kb_source` / `kb_synced_from` / `memory_source` / `source_hash` / `ingest_time` / `author` / `source_format` | string | 摄入溯源（`validate.py:20` 的 `OPTIONAL_SOURCE`） | `memory_ingest.py:250,253`、`memory_sync.py:266`、`memory_ingest_json.py:110`、`memory_ingest_sqlite.py:179`、`sync.py:211` | `kb_raw.py:33`（`FM_ID_KEYS`）、`kb_drift.py:124`、`validate.py:20` |
| `content_type` | （契约未声明） | chat/chat_todo/… | `ingest_chat.py:277`、`clean.py` | `rerank.py:52`、`rag.py:218,294`、`validate.py:21,156` |
| `merged_from` | （契约未声明） | 合并来源 | `intake_triage.py:270` | `intake_triage` |
| `enforce_level` / `chunk_index` | （契约未声明） | 校验/分块 | — | `validate.py:21` |

### 2.3 `kb_layer` 取值
`canon`（1.25×） / `page`（1.00×，`DEFAULT_LAYER`） / `raw`（0.72×） / `index`（0.60×） / `noise`（0.60×）—— 定义在 `kb_common.py:376-384`，与 `kb-schema.json:146-157` 的枚举一致（5 值全对齐）。

### 2.4 status 取值与"可检索"语义
枚举 `draft/active/stable/legacy/archived` 三处重复：`kb-schema.json:80-91`、`kb_common.py:986`（`STATUS_SET`）、`taxonomy.py:16`（`DEFAULT["statuses"]`）。
可检索判定 `kb_common.py:262`（`{active, stable, legacy}`）**与 `infer_status` 的归档语义（kb_common.py:1081，>180 天 → `archived`）叠加**：超过 180 天且由 `infer_status` 写过的笔记会自动退出检索。

### 2.5 **载入器差异（重要）**
同一个 `tags: [a, b]`：
- `parse_frontmatter`（57-86）/`load_meta`/`load_note` → `"[a, b]"`（**字符串**）；
- `load_note_full`（1049-1050）→ `["a", "b"]`（**list**）。

因此 `rag.py`（用 `load_meta`，`rag.py:16`）看到的 tags 是字符串，而 `tag_fix.py`（用 `load_note_full`，`tag_fix.py:29`）看到的是 list——两者各自带 `_parse_tags` (rag.py:485) / `taxonomy.parse_tags` (taxonomy.py:52) 兜底，才没有直接崩，但**同一笔记在不同模块下字段类型不同**，是数据模型层面的隐性契约缺失。

### 2.6 lineage / 别名 / 墓碑
- 血缘：`<root>/.kb/state/lineage.jsonl`，字段 `{ts, action, from, to}`（`kb_common.py:467-477`）。action 实证取值：`"merge"`（intake_triage.py:340）、`"curate-archive-original"`（curate.py:916）、clean.py:258。
- 别名：`aliases` frontmatter 由 `add_alias_text`(445) 幂等追加；`taxonomy.json` 另有同名 `aliases` 概念（**指标签别名，语义完全不同**，taxonomy.py:75）。
- 墓碑：`redirect_to`（intake_triage.py:270 写入）；**pipeline 内未读到消费方**——`kb_common` 只提供 `add_alias_text`/`record_lineage`，没有读取重定向的逻辑（"未确认"是否有外部/插件消费）。

---

## 三、常量与阈值完整清单（kb_constants.py，88 行）

| 行 | 常量 | 值 | 用途 | 被谁引用（import 点） |
|---|---|---|---|---|
| 11 | `DUP_SIM` | 0.85 | 去重相似度阈值 | `kb_rsi.py:23`（用于 262/269/341） |
| 12 | `DUP_SIM_HEALTH` | 0.90 | 健康检查去重阈值 | **无引用（死常量）** |
| 13 | `MERGE_MIN_SIM` | 0.97 | 自动合并门槛 | `kb_engine.py:30` |
| 14 | `CONTRA_SIM` | 0.40 | 矛盾检测下限 | `kb_contradiction.py:32` |
| 17 | `LINK_THRESHOLD` | 0.75 | 补链建议阈值 | **无引用（死常量）** |
| 18 | `MIN_HITS` | 3 | 反馈命中下限 | `feedback_loop.py:19` |
| 19 | `BUMP_CAP` | 0.30 | 重要性上调上限 | `feedback_loop.py:19` |
| 20 | `MIN_BUMP` | 0.05 | 最小上调步长 | `feedback_loop.py:19` |
| 23 | `ARCHIVE_DAYS` | 90 | 归档天数 | `kb_health.py:23` |
| 24 | `RETIRE_AGE_DAYS` | 180 | 退役年龄 | `kb_engine.py:30` |
| 25 | `DECAY_DAYS` | 30 | 衰减周期 | `kb_health.py:23` **import 后未使用（死导入）** |
| 28 | `PERF_WARN_THRESHOLD` | 400 | O(n²) 告警阈值 | `link_engine.py:19`、`kb_health.py:23` |
| 29 | `MAX_NOTES_CONTRA` | 400 | 矛盾检测比较上限 | `kb_contradiction.py:32`（别名 `MAX_NOTES`，用于 175 行） |
| 32-33 | `EXTERNAL_MIN_CROSS` / `EXTERNAL_FRESH_MIN_PCT` | 0.15 / 5.0 | 外部接地门 | `kb_rsi.py:22`、`kb_retriage.py:47`、`kb_selfweight.py:38` |
| 38-40 | `L2_SIGNAL_MIN` / `L2_LINK_BONUS` / `L2_L1_MISS_BONUS` | 0.30 / 0.10 / 0.15 | L2 自我加权 | `kb_selfweight.py:38`、`kb_meta.py:55` |
| 48-53 | `L0_GAP_TOKEN_MIN_LEN`…`L0_TOTAL_PROPOSALS` | 2 / 2.5 / 12 / 8 / 4 / 50 | L0 检索表层修正 | `kb_retriage.py:47` |
| 59-63 | `L3_KNOB_MIN` / `L3_KNOB_MAX` / `L3_KNOB_STEP` / `L3_ROLLBACK_CONSEC` / `L3_MISS_HIGH` | 见 59-61 行三 dict；2；3 | L3 元调参 | `kb_meta.py:55` |
| 68-72 | `L4_RUNGS` / `L4_FREEZE_CONSEC` / `L4_MIN_SAMPLE` / `L4_TRUST_FLOOR` | `("l0","l1","l2","l3","lc")` / 3 / 5 / 0.5 | L4 宪法 | `kb_l4.py:58`（**`L4_TRUST_FLOOR` import 后未使用**） |
| 80-88 | `L5_RUNGS`…`L5_MIN_SAMPLE` | 9 个 | L5 元策略 | `kb_l5.py:54` |

**常量体系的三套并行阈值（同一"陈旧"概念）**：
- `kb_common.DEFAULT_STALE_DAYS = 180`（263）
- `kb_constants.ARCHIVE_DAYS = 90` / `RETIRE_AGE_DAYS = 180`（23-24）
- `kb-schema.json stale_policy: stale_after_days=90 / verified_stale_after_days=180 / archive_after_days=90`（335-340）

---

## 四、schema 校验与迁移

### 4.1 `kb_schema.py`（193 行，只读校验器）

**入口**：`check`(50-150) / `fields`(153-173) / `main`(176-189)。
**契约加载** `load_schema`(32-37)：读 `<root>/reference/kb-schema.json`，`OSError`/`JSONDecodeError` → `None`，`check` 打印错误并返回 2（52-54）。

**检查规则条目（共 8 类，docstring 8-16 行与实现逐条对应）**：

| 类别 | 实现行 | 规则 |
|---|---|---|
| 1 缺必填字段 | 78-81 | 非 raw/非 `is_source` 时，`schema.fields[*].required == true` 的键缺失或为空 → 报 |
| 2 枚举越界 | 84-92 | 对 `type == "enum"` 且有 `values` 的字段；空值跳过；`kb_layer/curate_verdict/lifecycle` 大小写不敏感；`_maturity == "aspirational"` 的字段归入"自由标签（建议归一）"而非"枚举越界" |
| 3 类型/范围 | 95-103 | `importance/confidence/curate_confidence/canon_ratio` 用正则抓数字，抓不到 → "类型错误"；前三者不在 [0,1] → "范围越界"（**`canon_ratio` 虽声明 range 但未做范围检查**） |
| 4 canon 溯源 | 106-112 | `kb_layer == "canon"` 时 `canon_required`（= `source_ref/archived_original/kb_layer/kb_summary`，schema 299-304）缺项 → "canon 缺溯源"；`archived_original` 文件不存在 → "canon 溯源断裂" |
| 5 noise 契约 | 115-117 | `kb_layer == "noise"` 或 `curate_verdict == "noise"` 而 `kb_index` 不为假值 → "noise 未退出检索" |
| 6 证据层越界 | 120-121 | `raw/` 下 `kb_layer ∈ (canon, noise, index)` → 报（对应 `raw_forbidden: ["kb_layer"]`，schema 309-311） |
| 7 summary 超长 | 124-126 | `kb_summary` > 200 字符且非 raw → 报（对应 `max_len: 200`） |
| 8 陈旧度 | — | docstring 8-16 行声称检查"陈旧度（计算值，不存储）"，但 **`check()` 中无任何 freshness/is_stale 调用**（66-127 行全文未见）→ 文档与实现不符 |

**输出**：`--json` 输出 `notes/layers/problems/problem_total/schema_version/field_usage`(130-134)；文本模式按问题数降序打印(143-148)。`fields` 子命令统计字段使用率并标"✔ 已声明 / ＋ 未声明"（167-172）。
**注意**：`F = schema["fields"]`（55 行）无防护——契约文件存在但缺 `fields` 键时抛 `KeyError`。

### 4.2 `kb_schema_migrate.py`（189 行，dry 默认 + `--apply` 才写）

**迁移目标**（3-9 行注释）：一维 domain → domain × category 二维。
**版本号**：无 schema version 概念；只在 `_meta.version`（kb-schema.json:3 = 1）之外自带映射表。

**映射表**
- `FOLDER_CATEGORY`(19-23)：目录数字前缀 → category（`00→inbox, 01→knowledge, 10→project, 20→experience, 30→decision, 40→resource, 50→template, 60→knowledge, 70→meta, 90→archive`）
- `ROOT_CATEGORY`(24-27)：3 个根文件名 → category
- `DOMAIN_BY_CATEGORY`(29-33)：`project/experience/reference → 开发`；`sop/meta → 管理`

**步骤**
1. `category_for(rel)`(36-43)：单段（根文件）查 `ROOT_CATEGORY`，默认 `knowledge`；`reference/` 子树 → `reference`；否则取首段数字前缀查 `FOLDER_CATEGORY`，无匹配 → `knowledge`。
2. `domain_refine(current, category)`(46-50)：仅当 `current == "综合"` 且 category 在映射表内才改。
3. `analyze(root)`(101-127)：逐文件产 `changes`（category / domain 收敛 / 必填补 `domain=综合`、`status=active`）。
4. `git_backup`(130-136)：`git add -u` + 有变更则 `git commit -m "pre-schema-migrate checkpoint"`。
5. 应用：对每条 change `set_fm`(71-88) 重写整个文件（`"---\n" + new_fm + "\n---\n" + body`），成功则追加一行 JSON 到 `pipeline/.kb_schema_migrate.jsonl`(165,172-175)。
6. 写后再次 `git add -u` + commit（176-183）。

**版本/日志格式**：`{"ts","rel","field","from","to"}`（173-175）。
**已知偏差**：usage 声明 `--dry`（第 12 行）但 `main` 只注册了 `--apply`（142 行）——**默认即 dry，不存在 `--dry` 开关**。
**独立 `iter_notes`**(94-98)：排除集为 `(.git, .obsidian, vector, .kb)`，**比 `kb_common.iter_notes` 宽**（注释 91-93 声明是有意的，要扫 `pipeline/` 下模板笔记）→ 会改写 `logs/`、`backups/`、`_curate/` 内的 md。
**独立 `read_note`**(53-68)：又一套 frontmatter 解析（正则 `^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$`，只认 ASCII 键名）。

---

## 五、状态与元数据

### 5.1 `state_manager.py`（167 行）

**路径约定**：所有状态文件在 `<vault_root>/.kb/state/`（32 行），构造时 `mkdir(parents=True, exist_ok=True)`（33）。

**API**
| 方法 | 行 | 语义 | 边界 |
|---|---|---|---|
| `_atomic_write(p, text)` | 38-55 | 写 `p.name + ".tmp"` → `os.replace` → finally 清残留 tmp | 注释 41-43 记录历史 bug：`Path.rename` 在 Windows 目标存在时抛 `FileExistsError`，导致第二次起状态写入**静默失败**；改用 `os.replace` |
| `_lock(path, exclusive=True)` | 57-68 | `fcntl.flock`（`LOCK_EX`/`LOCK_SH`），返回 `(fd, True)` 或 `(None, None)` | `fcntl` 不可用（Windows）→ 直接 `(None,None)`，**降级为无锁**（15-20 行） |
| `_unlock(fd)` | 70-79 | `LOCK_UN` + close，异常吞掉 | — |
| `load(name, default=None)` | 81-92 | 不存在 → default/`{}`；`JSONDecodeError/OSError/UnicodeDecodeError` → default/`{}` | **文件损坏时静默返回空状态，无告警** |
| `save(name, data)` | 94-101 | 加排他锁 + 原子写（`ensure_ascii=False, indent=2`） | — |
| `update(name, fn, default=None)` | 103-129 | 单锁内 `load → fn(data) → save`（原子读改写） | 解析失败 → 丢弃全部历史、回退 default 并覆盖写回；`fn` 返回 `None` 时不写（123） |
| `exists` / `remove` | 131-139 | — | `remove` 不走锁 |

**旧路径兼容**：`LEGACY_PATHS`(143-149) 5 个文件（`feedback_state/memory_state/sync_state/ingest_state/query_cache` → `.kb/*.json`）；`migrate_legacy_state(vault_root)`(152-167) 读旧写新、**不删旧文件**、失败静默 `pass`。**`migrate_legacy_state` 全仓无调用方（死代码）**。

**实证存在的状态文件**：`.kb/state/feedback_state.json`(6195B)、`.kb/state/query_cache.json`(12871B)。

**调用方（12+ 处）**：`feedback_loop.py:98-108`、`memory_ingest.py:144-168`、`memory_sync.py:117-142`、`recall_schedule.py:89-105`、`kb_usage.py:62-63`、`kb_claim.py:180-181`、`kb_selfweight.py:58-59`、`kb_adaptretrieve.py:84-114`、`rag.py:720,760,929,950`、`plugin_base.py:30`、`tests/test_rag_cache.py:29`、`tests/test_state_manager.py:29`。

### 5.2 `kb_meta.py`（507 行，L3 元调参）

**文件位置（全部在 vault 根，非 `.kb/state/`）**
- `PROPOSAL_FILE = ".kb_meta_proposals.jsonl"`(63)
- `STATE_FILE = ".kb_meta_state.json"`(64)
- `CONFIG_FILE = ".kb_meta_config.json"`(65)
- 三者均标注 gitignore（.gitignore:59-62 实证确认）

**状态 JSON 结构**（`_read_state` 95-102 / `_write_state` 105-107）
```json
{"knovals": {"L0_MIN_IDF":2.5,"L2_SIGNAL_MIN":0.3,"L2_L1_MISS_BONUS":0.15},
 "cycles": [{"knob":…, "old":…, "new":…, "outcome_before":…, "applied":"ISO",
             "outcome_after":…, "delta":…, "consecutive_negative":…,
             "rolled_back":true, "reason":"连续负delta"}],
 "last_good": {…}}
```
（键名 `knovals` 为拼写简写，全局一致使用。）

**配置 JSON**（`_write_config` 120-124）：`{"knovals": {...}, "updated": "ISO"}`。

**主流程**
- `current_knobs(root)`(127-136)：运行时配置覆盖默认（`DEFAULT_KNOBS` 77 行取自 `kb_constants`）。
- `grounding(root)`(154-164)：复用 `kb_rsi.metrics`，门限 `external_inflow ≥ 5.0 and cross_ratio ≥ 0.15`；不通过则**冻结内部元调参**（连 `apply` 也拒绝，318-319）。
- `outcome(root)`(168-180)：best-effort 读 `kb_usage.analyze` 的 `suspect_failed_count`；异常 → `miss=0` + note。
- `_rung_signals`(194-199)：`miss_high = miss ≥ L3_MISS_HIGH`；`l0_idle/l2_idle = proposal jsonl 行数 == 0`。
- `_candidate`(203-224)：gate 类下调一步、weight 类上调一步，触界返回 `None`。
- `propose`(228-261)：接地不足/漏检偏低 → 写空提案并返回解释；否则产提案并编号 `id` 从 1 起。
- `tail_consecutive_negative`(274-282)：纯函数，尾部连续负 delta 计数。
- `apply(root, ids=None, force=False)`(286-340)：写配置 + 状态 + `_checkpoint`（`git add --force` 后 commit，139-150）。
- `calibrate`(344-394)：算 `delta = outcome_before - miss`，连续负 ≥ `L3_ROLLBACK_CONSEC(2)` → 回滚到 `last_good`；再 `propose`。
- `status`(398-418) / `render`(422-453) / `main`(457-503)。

**写入原子性**：`_write_state`(107) 与 `_write_config`(122) 均为**直接 `write_text`，非原子、无锁**，与 `state_manager._atomic_write`（os.replace）策略不一致。

### 5.3 `evolution_log.py`（127 行）

**双写**
1. 机器可读：`<root>/pipeline/.kb_evolution.jsonl`（63 行，**追加**）。
2. 人类可读：`<root>/pipeline/EVOLVED.md`（67 行，`_append_md` 71-95 读-改-写整个文件）。

**事件前缀** `EVENTS`(21)：`INGEST, QUERY, LINT, ENGINE, HEALTH, T1, T2, T3, NOTE, CONTRADICTION`。
**记录格式**（50-58）：`{"ts","date","time","event","category","summary","detail"}`；`category` 默认 `"info"`。
**`_EVENT_LABEL`**(23-28)：10 个前缀的中文标签（与 `EVENTS` 一一对应）。
**`_HEADER`**(29-33)：**定义后未被 `_append_md` 使用**（`_append_md` 只写 `## <date>` 段）→ 头部常量近似死代码。
**其他 API**：`append`(44-68)、`tail(root, n=30)`(98-103)、`render_md(root, limit=40)`(106-115)。
**边界**：非 `EVENTS` 前缀 → `raise ValueError`(47-48)。
**接入点**：`feedback_loop.py:148`(INGEST)、`kb_query.py:214,289`、`kb_retriage.py:363,458`、`kb_adaptretrieve.py:303`、`kb_selfweight.py:262,368`、`kb_contradiction.py:408,469`、`kb_engine.py:611`、`kb_rsi.py:464`（**传了非法前缀 `ALERT`，见 §7-2**）。

### 5.4 `user_manager.py`（177 行）

- 注册表路径：`<root>/reference/users.json`(19)——**在 git 跟踪范围内**（实证文件存在，含 `_meta.roles` + `users.default`）。
- 结构：`{"_meta": {roles: {...}}, "users": {name: {role, display_name, created, inbox}}}`（cmd_add 61-66 写入）。
- `ROLES = ("admin","editor","reader")`(20)；`ROLE_PERMS`(21-25)：admin 6 项、editor 3 项、reader 1 项。
- `INBOX_BASE = "00-收件箱 Inbox"`(26)；`default` 用户的 inbox 就是根收件箱（60 行）。
- 命令：`add`(47-72) / `list`(75-86) / `remove`(89-103，禁删 default) / `inbox`(106-122) / `check-perm`(125-138)。
- **读写**：`load_users`(33-38) 无 try（损坏 JSON 直接抛）；`save_users`(41-44) **非原子、无锁**。
- **权限仅用于打印**：`ROLE_PERMS` 除 `cmd_list`(84) 与 `cmd_check_perm`(135) 外，**无任何执行路径做强制校验**。

### 5.5 `agent_registry.py`（441 行）

- 注册表：`<root>/kb-agent.json`（`STATE_FILE` 第 25 行；`.gitignore:41-42` 实证忽略）。
- 结构：`{"version": 1, "agents": {name: {"type","label","enabled","sources":{...}}}}`（156、195、224-229）。
- `AGENT_TYPES = ("md","json","project-context","sqlite","opencode")`(33)（注释 5 行说支持六种 agent，类型枚举是 5 种——OpenClaw/Hermes 共用 `md`）。
- 探测层：`detect_openclaw`(50-75) / `detect_hermes`(78-88) / `detect_dsh`(91-108) / `detect_codex`(111-120) / `detect_opencode`(123-133) / `detect_agents`(136-145)；环境变量覆盖 `KB_<AGENT>_<FIELD>`（`env_override` 44-47）。
- 读写：`load_registry`(151-156) 容错返回默认；`save_registry`(159-161) **非原子、无锁**。
- 命令：`cmd_detect`(164-173) / `cmd_list`(176-186) / `cmd_add`(189-198) / `cmd_enable`(201-210) / `cmd_setup`(213-234) / `cmd_ingest`(237-269)。
- adapter：`_ingest_md`(272-301，subprocess 调 `memory_ingest.py --agent-name`)、`_ingest_json`(304-320)、`_json_sources`(323-344)、`_ingest_project_context`(347-358)、`_ingest_sqlite`(361-369)、`_ingest_opencode`(372-384)。
- 接线：`kb_launcher.py:38-48,166-170` 把 `kb agent …` / `kb ingest-agent` / `kb user …` 路由到这里与 `user_manager`。

---

## 六、taxonomy.py 与 reference/*.json 的关系

`taxonomy.py`（104 行）是**与 `kb_common` 配置加载并行、互不知晓的第二套受控词表系统**：

| 维度 | `taxonomy.py` | `kb_common` 配置族 |
|---|---|---|
| 配置文件名 | `reference/taxonomy.json`（22-27 行；**回退到包内 `reference/`**） | `domain-taxonomy.json`(538)、`tag-taxonomy.json`(543)、`dedup-config.json`(548) |
| 加载器 | `load_taxonomy`(22-41)：缺失时用内置 `DEFAULT`(13-19) 兜底 | `load_config`(511-533)：缺失返回 `{}`（**fail-open**） |
| domain 枚举 | `DEFAULT["domains"]`(14) 与 `kb_common.DOMAIN_WHITELIST`(33)、`kb-schema.json`(95-103) **三处同值重复** | `domain-taxonomy.json` 只管二级词表 |
| 标签层级 | `taxonomy.json.tag_hierarchy`：分组 `work/tech/knowledge` + 英文/中文别名(aliases) | `tag-taxonomy.json`：分组为 `decision/knowledge/开发/运维/.../知识工程/工具生态/…`，**两套分组名完全不同** |
| 归一 | `normalize_tag`(70-81)：别名 → 规范；已知 leaf → `group/leaf` | `kb_common.is_valid_tag`(564)：只做白名单判断，不归一 |
| 消费方 | `clean.py:74-85`（归一 tags）、`tag_fix.py:31,59-75,98`、`tests/test_taxonomy.py` | `kb-healthcheck.py:245`（load_tag_taxonomy 告警白名单）、`memory_ingest`/`memory_sync`（dedup 阈值） |

**结论**：存在两条并行词表链——
- 链 A（英文分组 + 别名归一）：`taxonomy.json` → `taxonomy.py` → `clean.py`/`tag_fix.py`；
- 链 B（中文分组 + 二级 domain）：`domain-taxonomy.json`/`tag-taxonomy.json` → `kb_common` → `kb-healthcheck`/`memory_ingest`。

两链的 domain 一级枚举都与 `kb-schema.json` 重复声明（共 3 份同值副本），改一处不会自动同步。

---

## 七、发现的问题清单

> 类型标注：bug / 静默失败 / 重复实现 / 死代码 / 性能 / 编码假设 / 状态写入不原子 / 文档不一致。

| # | 位置 | 类型 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| 1 | `kb_meta.py:481-483` | **bug（静默失败）** | `ids = a.id if (a.all_ is False and a.id) else None; ids = None if a.all_ else ids; r = apply(root, ids)` —— `apply` 第 295 行要求 `ids is None and force` 才全选，此处 `force` 用默认 `False` | `kb_meta apply --all` **写入 0 个旋钮**却打印"✅ 写入 0 个旋钮"，用户以为已全量应用 | 传 `force=True`：`r = apply(root, ids, force=a.all_)` |
| 2 | `kb_rsi.py:464` + `evolution_log.py:21,47-48` | **bug（崩溃）** | `evolution_log.append(root, "ALERT", …)`，`ALERT` 不在 `EVENTS`；`append` 抛 `ValueError`，而调用方 except 只覆盖 `(ImportError, OSError, AttributeError, TypeError)`（kb_rsi.py:466） | 集中度护栏触发告警时，`kb_rsi` CLI 抛 `ValueError` 中断 | 把 `"ALERT"` 加入 `EVENTS`（并补 `_EVENT_LABEL`），或调用方捕获 `ValueError` |
| 3 | `tag_fix.py:60,75` vs `taxonomy.py:94,103` | **bug（崩溃）** | `taxonomy.known_tags(tax, root)` / `taxonomy.is_known_tag(t, tax, root)` 多传 `root`，而签名分别是 `(tax)` / `(tag, tax)` | `kb tag-fix` 扫描第一轮即 `TypeError`，标签修复功能完全不可用 | 统一签名（建议 `known_tags(tax, root=None)`）或改调用方 |
| 4 | `agent_registry.py:407,432-436` | **bug（危险静默）** | CLI 注册了 `--list`（407）但 `cmd_ingest` 从不检查它；默认 `dry_run=False` | `kb ingest-agent --list` 不会列清单，而是**直接执行写库摄取**（mirror/sync/extract） | 在 main 中处理 `--list`，或未给 `--dry-run` 时拒绝写 |
| 5 | `kb_common.py:310` vs `curate.py:324` | **bug（判定不一致）** | `index_excluded` 调 `is_index_stub(fm)` 不传 body，`is_index_stub` 第 341 行"正文 <200 字"分支在此路径永不生效 | 短小的级联分块目录页会被 `rag.py` 判为可入索引，被 `curate.py` 判为 stub → 同一笔记两套结论 | 让 `index_excluded(fm, body="")` 透传 body |
| 6 | `kb_common.py:445-464` | **bug（数据损坏）** | `add_alias_text` 只认单行 `^aliases:.*$`；遇到多行块列表时把空值行替换成 `aliases: [new]`，原 `  - x` 行残留为孤儿 | 改写后 frontmatter 结构损坏 | 先检测块列表形式，或统一用 `list_of`/YAML 解析后整块重写 |
| 7 | `kb_common.py:440-442` | **bug（脏数据）** | `_alias_items` 用 `[,\s]+` 切分，`"- a"` 切成 `["-","a"]` | 别名列表可能写入 `-` 这种垃圾项 | 先剥离前导 `-` |
| 8 | `user_manager.py:33-38,41-44` | **状态写入不原子 + 静默失败** | `load_users` 无 try；`save_users` 直接 `write_text`，无锁无 temp+replace | JSON 损坏 → 未捕获异常；并发/中断写会截断 `reference/users.json`（**该文件在 git 跟踪内**，且无备份机制） | 复用 `StateStore` 或 `os.replace` 原子写 + 异常降级 |
| 9 | `agent_registry.py:159-161,192` | **状态写入不原子** | `save_registry` 直接 `write_text`；`cmd_add`/`cmd_enable` 用 `reg["agents"]` 直接索引 | 写中断 → 注册表损坏；`kb-agent.json` 合法但缺 `agents` 键 → `KeyError` | 原子写 + `reg.setdefault("agents", {})` |
| 10 | `agent_registry.py:222-229` | **性能（实现层）** | `cmd_setup` 在 for 循环里对每个探测到的 agent 各做一次 `load_registry` + `save_registry` | N 个 agent → 2N 次全量读写 | 循环外 load 一次、循环后 save 一次 |
| 11 | `kb_meta.py:107,122-124` | **状态写入不原子** | `_write_state`/`_write_config` 直接 `write_text`，且写在 vault 根而非 `.kb/state/` | 与 `state_manager` 的 os.replace 原子写策略不一致；中断可损坏旋钮状态 | 改用 `StateStore` 或 `_atomic_write` |
| 12 | `evolution_log.py:71-95` | **状态写入不原子 + 静默丢数据** | `_append_md` 读-改-写整个 `EVOLVED.md`，无锁 | 并发/中断丢事件或损坏人读演进史（该文件入库） | 加文件锁或改为追加式写入 |
| 13 | `kb_common.py:476-477` | **静默失败** | `record_lineage` 捕获 `OSError` 后 `pass` | 血缘写入失败无任何提示，`lineage.jsonl` 缺失不会被发现 | 至少返回布尔值/写 stderr |
| 14 | `state_manager.py:89-90,125-126` | **静默失败（数据丢失）** | `load`/`update` 在 JSON 损坏时静默回退 default，`update` 还会把 default 覆盖写回 | `feedback_state.json` 损坏 → 全部反馈历史被清空且无告警 | 损坏时改名为 `.corrupt` 备份并告警 |
| 15 | `kb_common.py:112-127` + 43 处调用点 | **性能** | `iter_notes` 每次全盘 `os.walk`，无缓存；`clean.py` 单文件 10 次调用 | 大库下重复全盘扫描，O(调用次数 × 文件数) | 增加按 root 的 mtime 缓存或一次性 `list()` 复用 |
| 16 | `kb_common.py:521-522, 230-232` | **bug（缓存串味）** | 两个缓存的键都不含足够上下文：`load_config` 键只有 `name`；`model_config` 键只有 `str(root)` 且不失效 | 多库/测试场景取到别的库的配置；进程内首次读不到 `.kb/model.json` 后永不重试 | `load_config` 键改 `(name, str(root))`；`model_config` 支持失效重载 |
| 17 | `kb_common.py:266-269` | **文档不一致** | docstring 写"仅 active/stable 可检索"，实现允许 `legacy`（262 行） | 读者误判检索范围 | 修文档 |
| 18 | `kb_schema.py:8-16` vs `50-150` | **文档不一致** | docstring 声称检查"8. 陈旧度"，`check()` 内无任何陈旧度检查 | 契约检查的宣称能力大于实现 | 实现或删掉该条 |
| 19 | `kb_schema.py:55` | **bug（未防护）** | `F = schema["fields"]`，`load_schema` 只在文件缺失/JSON 坏时降级 | 契约文件存在但结构异常 → `KeyError` | `F = schema.get("fields") or {}` |
| 20 | `template-vault/reference/kb-schema.json` 权限 `-rw-------`（600） | **编码/部署假设** | 契约文件仅 owner 可读 | 多用户/容器以不同 UID 挂载同一 vault → `load_schema` 返回 `None`，`kb_schema check` 直接 exit 2 | 设为 644 与其它 reference/*.json 一致 |
| 21 | 全仓（`kb_common.py:95,107,236,850,1031,1146-1161`、`state_manager.py:48,88,…`、`evolution_log.py:63,72,…`、`user_manager.py:37,44`、`agent_registry.py:154,161`、`kb_schema.py:35`、`kb_schema_migrate.py:54,73,87`） | **编码假设** | 全部硬编码 `encoding="utf-8"`，**无 GBK/自动探测回退** | 非 UTF-8 来源文件被当作读取失败（`load_meta` 静默返回空；`load_note` 抛异常） | 统一走 `safe_read_text` 并允许 `errors=` 策略或按 BOM 探测 |
| 22 | `kb_schema_migrate.py:87`、`kb_common.py:464`、`evolution_log.py:92-95` | **编码假设（Windows）** | 拼接/写回统一用 `\n`，未用 `newline=""` | Windows 上产生混合换行（CRLF 文件被部分改写为 LF） | 指定 `newline="\n"` 明确策略，或保留原换行 |
| 23 | `kb_common.py:180`、`kb_common.py:1063`、`kb_schema_migrate.py:37` | **路径假设** | `f"{d}/{cand}"` 手工拼 `/`；`rel.split(os.sep)` 依赖分隔符一致 | Windows 下 rel 若用 `/` 分隔，`infer_domain`/`category_for` 的目录回退失效 | 统一用 `Path.parts` |
| 24 | `kb_common.py:1145-1147` | **状态写入不原子** | `aggregate_bucket` 读-改-写整个聚合桶 | 并发摄取丢条目 | 加锁 + 原子写 |
| 25 | `kb_schema_migrate.py:71-88,167-175` | **状态写入不原子** | `set_fm` 逐文件整体重写（只改一个字段也重写全文），无锁；`--apply` 无失败回滚（除 git checkpoint） | 中途失败留下半迁移状态 | 逐文件原子写 + 记录已完成清单 |
| 26 | `kb_schema_migrate.py:12` vs `139-143` | **文档不一致** | usage 写 `--dry`，CLI 未注册该开关（默认即 dry） | 用户按文档传 `--dry` 会 argparse 报错 | 补 `--dry` 或改文档 |
| 27 | `kb_common.py:173-193` | **静默失败（截断）** | `parent_of` 最多回溯 8 层，超出返回 `""` | 深层分块链的父子关系静默丢失 | 超限时记录/告警 |
| 28 | `kb_common.py:272-277` | **边界** | `_parse_day` 用 `date.fromisoformat(str(v)[:10])` | `2026-1-5` 这类非补零日期解析失败 → `age_days=None` → 不判陈旧 | 用 `parse_date` 或宽松正则 |
| 29 | `kb_common.py:130-140` | **边界/精度** | `count_tokens` 拉丁 `//4` 向下取整，空白标点不计 | 英文短文本 token 数被低估为 0，上下文预算控制偏松 | 至少 `ceil` 或设下限 |
| 30 | `kb_common.py:805-834` | **边界** | `parse_importance("P0") → 0.0`；`"v2" → 2.0` | P0 这类"高优先级"标记被读成最低重要性（docstring 自证） | 显式白名单 `P0→1.0` 等，或拒绝非数字 |
| 31 | `kb_common.py:551-561,564-577` | **静默失败（fail-open）** | domain/tag 白名单配置缺失时一律返回 `True` | 配置丢失 → 校验静默失效 | 至少打印一次告警 |
| 32 | `kb_meta.py:63-65` vs `state_manager.py:32` | **约定不一致** | L3 状态文件放 vault 根，而非 `.kb/state/` | 状态文件散落，清理/备份脚本易漏 | 统一到 `.kb/state/` |
| 33 | `user_manager.py:21-25,84,135` | **设计缺陷** | `ROLE_PERMS` 只用于 list/check-perm 输出，无强制校验点 | "权限"是信息性的，任何调用者都能执行任意操作 | 明确文档化，或在命令入口做门禁 |
| 34 | `user_manager.py:89-103` | **残留** | `remove` 不删用户 inbox 目录 | 空目录残留 | 提示或可选删除 |
| 35 | `agent_registry.py:284-291` | **静默失败** | `_run_extra` 忽略 subprocess 返回码 | `memory_ingest` 摄取失败时注册表/用户看不到失败 | 检查 returncode 并在 out 中标注 |
| 36 | `agent_registry.py:138,389` | **死代码** | `detect_agents` 内重复 `import shutil`（顶部 22 行已 import）；`main` 中 `sub = ap.add_parser("sub") if False else ap` | 无功能影响，可读性差 | 清理 |
| 37 | `kb_common.py:375,958-960` | **死代码** | `STUB_WORDS`(375) 全仓无引用；`AGGREGATE_LIMIT`(959) 被 `clean.py:35` import 但从未使用 | 常量中心失真 | 删除或接入 |
| 38 | `kb_constants.py:12,17,25,72` | **死代码** | `DUP_SIM_HEALTH`、`LINK_THRESHOLD` 无任何引用；`DECAY_DAYS`、`L4_TRUST_FLOOR` 被 import 但未使用（`kb_health.py:23`、`kb_l4.py:58`） | 常量中心失真，读者以为阈值生效 | 删除或真正接入 |
| 39 | `kb_constants.py:11` vs `memory_sync.py:27` | **重复实现（数值冲突）** | 同名 `DUP_SIM` 两处不同值：`kb_constants`=0.85（kb_rsi 用），`memory_sync`=0.92（`memory_ingest.py:21` 用） | 两条摄入路径的去重严格度不同且无人察觉 | 统一由配置文件驱动（`get_dedup_thresholds`） |
| 40 | `state_manager.py:152-167` | **死代码** | `migrate_legacy_state` 无调用方 | 旧状态文件永不迁移（只靠各模块自行 fallback） | 在入口脚本调用一次 |
| 41 | `kb_common.py:838-890,502-504,615-672` | **死代码** | `safe_read_text`/`safe_load_note`/`safe_json_load`/`domain_secondary`/`dedup_check` 均无外部调用方 | 公共库表面积虚高 | 删除或用于替换各模块重复的 try-except |
| 42 | `kb_types.py:15-64` | **死代码** | 全模块**未被任何生产代码 import**（仅 `tests/test_reachability.py:42` 作为文件名字符串列出） | 类型契约形同虚设；读者误以为有类型约束 | 接入类型标注或删除 |
| 43 | `kb_schema_migrate.py:150-151` | **统计口径 bug** | `n_extra` 在 `domain` 同时因"收敛"和"补齐"进入 changes 时重复计数 | 预览数字偏高（仅报表，无写影响） | 按实际 changes 差集统计 |
| 44 | `kb_common.py:28-30` | **观察（未确认）** | `EXCLUDE` 不含 `reference/`，故 `reference/*.md`（codex.md/dsh.md/opencode.md/memory-protocol/*.md）会被 `iter_notes` 当作知识笔记纳入检索与契约校验（文件确有 frontmatter，如 `reference/codex.md:1-8`） | 需确认是否有意为之 | 明确"reference 文档是否属语料" |

---

## 八、重复实现清单

| 能力 | 实现位置（行号） | 差异 | 各自被谁使用 |
|---|---|---|---|
| **frontmatter 解析** | ① `kb_common.parse_frontmatter`(57-86)<br>② `kb_common.load_note_full` 内联解析(1031-1052)<br>③ `validate.parse_frontmatter`+`parse_yaml`(40-72)<br>④ `kb_schema_migrate.read_note`(53-68)<br>⑤ `kb_rsi.load_note`(38-56)<br>⑥ `kb_engine.read_note`(134)<br>⑦ `sync.read_note`(58) | ① 返回值全为 str/None；② 额外把 `[...]` 解析成 list、跳过 `{}`；③ 返回 `(fm, 行号)` 且值带 `__line__/__value__` 元数据、`true/false` 转 bool；④ 只认 ASCII 键名、值为 str；⑤ 用 `re.search` 定位（注释 40-41 行记录曾因 `re.match` 导致整库 frontmatter 解析为空的事故） | ① `sync.py`、`kb_contradiction2.py`、`curate.py`…（见 §1.3）<br>② `clean`/`curate`/`kb_schema`/`kb_audit`/`contextual`/`tag_fix`/`kb_confidence`/`memory_sync`/`kb_raw`<br>③ `validate.py`<br>④ `kb_schema_migrate.py`<br>⑤ `kb_rsi.py`<br>⑥ `kb_engine.py`<br>⑦ `sync.py` |
| **分词 tokenize** | ① `kb_common.tokenize`(43-54)：拉丁 + CJK unigram+bigram<br>② `kb_common._tokens`(989-991)：拉丁 + CJK unigram（无 bigram）<br>③ `kb_rsi.tokenize`(59-65)：拉丁 + CJK **2 字以上连续段**（注释明示与 ① 不同）<br>④ `rag._tokenize`/`rag.py:681` 逐字中文<br>⑤ `kb_query._tokenize`(44)<br>⑥ `kb_eval._tokenize`(58)<br>⑦ `kb_calibrate._tokenize`(33)<br>⑧ `kb_contradiction._vec`(75)+`_CN_TOKENS`(106-107)<br>⑨ `curate._tokenize`(78-79)<br>⑩ `feedback_loop._CN_CHARS`(220)<br>⑪ `kb_claim._CN`(44)、`kb_calibrate._CN`(27)、`kb_query._CN`(38) 各自的 CJK 正则 | 至少 5 种切分口径（unigram / unigram+bigram / 2+ 连续段 / 逐字 / 标点清洗） | ① `rag`/`intake_triage`/`link_engine`/`recall_schedule`/`kb_health`/`kb_consistency`/`kb_retriage`/`curate_prompt`/`kb_audit`/`memory_ingest`/`kb_eval`；② 仅 `infer_domain`；③ `kb_rsi` 全套相似度；其余各模块自用 |
| **CJK 字正则** | `kb_common.CJK`(39)、`kb_common._CJK_RE`(982)、`clean.CJK`(116)、`feedback_loop._CN_CHARS`(220)、`kb_contradiction._CN_CHARS`(71)+`_CN_TOKENS`(106)、`kb_claim._CN`(44)、`kb_calibrate._CN`(27)、`kb_query._CN`(38) | 8 份同义正则 | 各自模块 |
| **向量 norm** | ① `kb_common.norm`(676-693)<br>② `kb_common.dedup_check` 内联(650-661)<br>③ `kb_rsi.norm`（kb_rsi 内，`vec` 104 行调用）<br>④ `embed_rerank._norm`(108)<br>⑤ `kb_usage._norm`(161，查询归一，语义不同) | ① 用 `or 1.0` 防零除；② 重复①逻辑两遍；③/④ 面向 embedding list | ① `link_engine`/`kb_health`/`kb_contradiction`/`kb_rsi`/`memory_ingest`；② 仅 `dedup_check`（无调用方）；④ `embed_rerank` |
| **余弦相似度** | ① `kb_common.cos`(696-718，仅 dict)<br>② `kb_rsi.cos`(107-124，兼容 dict + embedding list，注释 110-111 行明示差异)<br>③ `kb_usage._cos`(150)<br>④ `rag._cos`(323)<br>⑤ `embed_rerank._cos`(114)<br>⑥ `kb_query`/`kb_calibrate` 以 `kb_common.cos as _cos` 别名复用(28/24) | ② 是唯一同时支持两种向量类型的实现 | ① `link_engine`/`kb_health`/`kb_contradiction`；② `kb_rsi` 内部；④ `rag` 排序；⑤ 重排 |
| **日期解析** | ① `kb_common.parse_date`(745-774)<br>② `kb_common.parse_date_safe`(777-798)<br>③ `kb_common._parse_day`(272-277)<br>④ `kb_common.parse_date_str`(1001-1016)<br>⑤ `kb_health.parse_date`(37-40，委托①的兼容包装)<br>⑥ `kb_claim._parse_d`(168，注释 165-166 明示返回 None 与②不同)<br>⑦ `kb_calibrate._parse_d`(209) | ① datetime / ② datetime+默认 / ③ date / ④ str / ⑥⑦ 可返回 None | ① `kb_rsi`、`kb_health`(经⑤)；③ `freshness`；④ `memory_sync`/`clean`(别名 `parse_date`)；⑥⑦ 各自模块 |
| **importance 解析** | ① `kb_common.parse_importance`(805-834)<br>② `kb_rsi.parse_f`(127-129) | 同为正则抓数，②返回默认 0.0 | ① `feedback_loop`/`kb_selfweight`(别名 `_parse_importance`)；② `kb_rsi` |
| **tags 解析** | ① `taxonomy.parse_tags`(52-67，支持 JSON 数组字符串)<br>② `kb_common.list_of`(1095-1103)<br>③ `rag._parse_tags`(485)<br>④ `kb_retriage._parse_tags_value`(224) | ① 最健壮 | ① `clean`/`tag_fix`；② `clean`/`memory_sync`；③ `rag`；④ `kb_retriage` |
| **iter_notes 遍历** | ① `kb_common.iter_notes`(112-127)<br>② `kb_schema_migrate.iter_notes`(94-98，排除集不同，注释声明有意) | ② 含 `pipeline/`、`logs/`、`backups/` | ① 全仓 43 处；② 仅迁移脚本 |
| **原子状态写入** | ① `state_manager._atomic_write`(38-55，tmp+os.replace)<br>② `kb_meta._write_state/_write_config`(107/122，直接 write_text)<br>③ `user_manager.save_users`(44)<br>④ `agent_registry.save_registry`(161)<br>⑤ `evolution_log._append_md`(95)<br>⑥ `kb_schema_migrate.set_fm`(87)<br>⑦ `kb_common.aggregate_bucket`(1147) | 只有 ① 是原子的；②③④⑤⑥⑦ 均为直接写 | ① 12+ 个模块；其余各自模块 |
| **配置加载** | ① `kb_common.load_config`(511-533，`reference/*.json`，缓存键仅 name)<br>② `taxonomy.load_taxonomy`(22-41，固定 `taxonomy.json`，包内回退 + 内置 DEFAULT)<br>③ `kb_schema.load_schema`(32-37，`kb-schema.json`，无缓存) | 三套加载语义（缓存策略、回退策略、缺省值策略都不同） | ① `memory_ingest`/`memory_sync`/`validate`/`kb-healthcheck`；② `clean`/`tag_fix`；③ `kb_schema` |
| **状态文件 load/save 包装** | `feedback_loop.load_state/save_state`(95-108)、`memory_sync.load_state/save_state`(113-142)、`memory_ingest.load_state/save_state`(140-168)、`recall_schedule.load_state/save_state`(86-106)、`kb_usage._load_state`(59)、`memory_ingest_sqlite._load_state/_save_state`(196-203)、`memory_ingest_json._load_state/_save_state`(164-171)、`curate._load_state/_save_state`(99-106) | 8 组同构包装，各自"优先 StateStore、回退旧路径" | 各自模块 |
| **domain 解析** | ① `kb_common.parse_domain/domain_primary/domain_secondary`(481-504)<br>② `kb_common.infer_domain`(1056-1071，推断而非解析)<br>③ 各模块直接 `fm.get("domain")` | — | ① `sync`/`graph`/`rerank`/`rag`；② `clean`/`memory_sync` |

---

## 附：本次精读覆盖清单

| 文件 | 行数 | 读取方式 | 状态 |
|---|---|---|---|
| `kb_common.py` | 1213 | 分 3 段读完（1-420 / 421-840 / 841-1213） | ✅ |
| `kb_constants.py` | 88 | 全读 | ✅ |
| `kb_types.py` | 64 | 全读 | ✅ |
| `kb_schema.py` | 193 | 全读 | ✅ |
| `kb_schema_migrate.py` | 189 | 全读 | ✅ |
| `kb_meta.py` | 507 | 分 2 段读完 | ✅ |
| `state_manager.py` | 167 | 全读 | ✅ |
| `taxonomy.py` | 104 | 全读 | ✅ |
| `evolution_log.py` | 127 | 全读 | ✅ |
| `user_manager.py` | 177 | 全读 | ✅ |
| `agent_registry.py` | 441 | 分 2 段读完 | ✅ |
| `reference/kb-schema.json` | 340 | 全读 | ✅ |
| `reference/{dedup-config,taxonomy,domain-taxonomy,tag-taxonomy,users,triage-config}.json` | — | 全读 | ✅ |

**未读但通过 import/调用点引用确认的第三方模块**（仅记录引用关系，未对其内部实现下结论）：`kb_rsi.py`（仅读 30-139/450-469）、`validate.py`（仅读 30-99）、`rag.py`、`clean.py`、`curate.py`、`kb_health.py`（仅读 30-74）、`recall_schedule.py`（仅读 60-99）、`tag_fix.py`（仅读 50-109）、`kb_usage.py`、`kb-healthcheck.py` 等。
