# D 组逐文件精读报告 —— 检索 / RAG / 重排 / 索引 / 回忆调度 / 自适应检索

- 项目根：`/home/mushan/workspace/kb-kit-pure`
- 代码主体：`template-vault/pipeline/`
- 模式：只读精读（未修改任何被审代码）。为验证行为，把 `template-vault/` **整体复制**到 `/tmp/kbvault-d` 做实验，所有复现命令均在副本上执行，原仓库状态文件未被写入。
- 覆盖范围：`kb_query.py`、`rag.py`(1145 行)、`rerank.py`、`llm_rerank.py`、`embed_rerank.py`、`kb_embed.py`、`kb_adaptretrieve.py`、`recall_schedule.py`、`kb_selfweight.py`、`kb_fitness.py`，另读 `vector index/df_idf.json`（仅结构/规模）、`reference/synonyms.json`、`reference/dedup-config.json`。
- 为把调用链讲全，另读了 **调用方/被依赖方**：`kb`(bash 入口)、`kb_launcher.py`、`plugins/rag_plugin.py`、`plugins/_subprocess_plugin.py`、`plugin_registry.execute`、`kb_common.py`（tokenize/分层/新鲜度/指纹等公式源）、`kb_constants.py`、`feedback_loop.py`（权重常量源）、`state_manager.py`、`kb_rsi.py`（接地阀）、`kb_usage.py`（再问率）。
- 所有结论均带 `文件:行号`。凡未实测、未读到证据的，明确写「未确认」。

---

## 一、检索总链路：`kb query "…"` 完整函数级调用链

### 1.1 进程与分发层（三次进程切换）

| 跳 | 位置 | 说明 |
|---|---|---|
| 1 | `template-vault/kb:42-43` | `SELF_DIR`/`VAULT` = 脚本所在目录（跟随软链） |
| 2 | `template-vault/kb:59` | `export KB_ROOT="${KB_ROOT:-$VAULT}"` |
| 3 | `template-vault/kb:60` | `exec python3 $VAULT/pipeline/kb_launcher.py "$@"` → **进程①** |
| 4 | `kb_launcher.py:468-473` | `main()` → `_extract_root()`(450-465，剥离 `--root`) → `KBLauncher(vault_root).run(args)` |
| 5 | `kb_launcher.py:266-267` | `tool="query"`, `rest=["…问题…"]` |
| 6 | `kb_launcher.py:276-285` | `rest[0]` 非 `--` 开头且 `("query","…")` 不在 COMPAT_MAP → `sub=None`, `params_args=rest`（**问题串当作位置参数**） |
| 7 | `kb_launcher.py:288` → `_resolve`(248-259) → `kb_launcher.py:28` | `("query", None) → ("rag","query")` |
| 8 | `kb_launcher.py:324` → `208-214` | `_ensure_initialized()`：`registry.discover()` + `initialize_all()`（扫描 `pipeline/plugins/*.py`） |
| 9 | `kb_launcher.py:346-348` | `_parse_params`(217-245，`--json/--answer/--full…` 见 `BOOL_FLAGS` 190-194) → `params.setdefault("root", vault_root)` → `registry.execute("rag","query",params)` |
| 10 | `plugin_registry.py:233` | `PluginRegistry.execute()` |
| 11 | `plugins/rag_plugin.py:8-10` | `RAGPlugin(SubprocessPlugin)`，`SCRIPT_NAME="rag.py"`，`ACTION_MAP={"index":"index","query":"query"}` |
| 12 | `plugins/_subprocess_plugin.py:30-48` | `execute()`：`cmd=[sys.executable, <root>/pipeline/rag.py, "query", "--root", root]` |
| 13 | `plugins/_subprocess_plugin.py:51-65` | 追加其余参数；`_positional` 被跳过循环后**追加到末尾**（问题串最后入参） |
| 14 | `plugins/_subprocess_plugin.py:69` | `subprocess.run(cmd)` → **进程②（rag.py）** |

> 注意：**每次 `kb query` 都是一次全新 Python 进程**，进程内缓存（`kb_embed._cache`、`rag._synonyms_cache`）在查询之间**不存活**。这一点决定了第九节的性能结论。

### 1.2 rag.py 内层链路（`main` → `cmd_query`）

| 跳 | 位置 | 说明 |
|---|---|---|
| 15 | `rag.py:55-83` | `main()`：`argparse`。`index` 子命令 58-60（`--full`）；`query` 子命令 61-82（`q` 位置参数、`--top/--answer/--json(as_json)/--context/--context-budget/--session` + 8 个过滤器 + `--exclude-stale/--include-raw/--include-stubs`） |
| 16 | `rag.py:87-96` | 分发到 `cmd_query(...)` |
| 17 | `rag.py:743` | `t0 = time.time()` |
| 18 | `rag.py:745-746` | 无 `--session` → `_gen_session()`(711-714) 生成 `q-<12hex>`（**保证每次查询都写反馈事件**） |
| 19 | `rag.py:748-757` | 计算 `cache_key = sha256(json{q,top,domain,content_type,author,min_importance,enforce_level,tags,date_from,date_to,context,answer,exclude_stale,include_raw,include_stubs})`（**不含 `as_json`**——见问题 D-1） |
| 20 | `rag.py:758-774` | 缓存读：`StateStore(root).load("query_cache.json")`(759-760)；`ts` 距今 `<300s` 命中 → 打印 `entry["result"]`(764) 或 `entry["text"]`(766) → 命中缓存仍 `_record_hits`(769-771) → `return 0` |
| 21 | `rag.py:776-777` | `idx_path = <root>/vector index/df_idf.json` → `load_index()`(31-45) |
| 22 | `rag.py:778-783` | `payload is None` → `stderr` 打印「索引不存在，先跑: rag.py index」→ `return 2` |
| 23 | `rag.py:784-789` | 仅做**版本**检查：`ver<4` / `ver>4` 各打一条 `stderr` 警告，**仍继续查询** |
| 24 | `rag.py:790` | 解包 `vocab, idf, tf_all, meta_all`（`vocab` 实为死参数，见 D-11） |
| 25 | `rag.py:791` | `rel2vec = {r: _vec(tf_all[r], vocab, idf) for r in tf_all}` — **每查询重建全库文档向量** |
| 26 | `rag.py:793` | 查询扩展 `_expand_query(q)`(1002-1042)，默认直通返回 `q` |
| 27 | `rag.py:794` | `qv = _vec(Counter(tokenize(q_expanded)), vocab, idf)` |
| 28 | `rag.py:795-797` | 第一路召回：`_cos()`(323-325) 全库打分 → 降序 → 丢 `s<=0` |
| 29 | `rag.py:802-805` | 第二路：`_bm25_scores(tf_all, tokenize(q_expanded))`(332-354) → `_rrf_fuse()`(397-403, k=60, cap=300) |
| 30 | `rag.py:808-818` | 倒排加成：`_inverted_lookup(tokenize(q), payload["inverted_index"])`(380-387) → `score += 0.15*count`（**新增不在 `scored` 里的文档**）→ 重排 |
| 31 | `rag.py:826-827` | 标题加权（`KB_TITLE_BOOST=1` 才跑）：`_title_boost()`(617-637) |
| 32 | `rag.py:830-834` | 8 维过滤（有任一过滤器才跑）：`_apply_filters()`(501-539)，每候选一次 `load_meta`(506) |
| 33 | `rag.py:837-843` | `--exclude-stale`：每候选 `load_meta`(840) + `retrievable_status()`/`is_stale()` |
| 34 | `rag.py:847-865` | 分层乘数 + 正典置信分（见第三节公式） |
| 35 | `rag.py:868-870` | 去重/家族截断：`_dedup_chunks()`(429-463)，内部 `_root_of()`(406-426)；产出 `chunk_siblings_map` |
| 36 | `rag.py:880-899` | 语义重排（`KB_RERANK=cross|embed`，**默认 `0` 关闭**） |
| 37 | `rag.py:901-908` | 启发式重排（`KB_HEURISTIC_RERANK=1` 才跑）→ `rerank.rerank()` |
| 38 | `rag.py:909` | `latency_ms` |
| 39a | `rag.py:911-935` | **`--json` 分支**：逐命中 `load_meta`(915) → `_build_hit()`(640-663) → `_parent_block()`(560-603) 父块回填 → `_build_json_output`(666-672) → `_context_budget`(691-708，仅当 `--context-budget`) → `print(json.dumps)`(924) → 写缓存(926-935) |
| 39b | `rag.py:936-963` | **人类可读分支**：`strip_body()`(47-53) + `best_sentence()`(1067-1110) → 打印 → 写缓存(946-955) → `--answer` 时 `llm_answer()`(1112-1142) |
| 40 | `rag.py:965-971` | 命中记录：逐命中 `load_meta`(969) + `_record_hits()`(717-731) 写 `feedback_state.json` 的 `agent_hits`（`weight=0.3`） |
| 41 | `rag.py:973` | `return 0` |

### 1.3 `--json` 与 `--answer` 的行为差异（重要）

- `--json` 走 39a，**完全不走** `llm_answer`：`--answer --json` 的 LLM 答案会被静默丢弃（`answer` 只进了 cache_key，`rag.py:754`），却仍参与缓存键 → 生成一个与纯 `--json` 不同的缓存条目。属静默失败。
- `--context-budget` 只在 `as_json` 分支生效（`rag.py:922-923`），文本模式下传入被忽略。
- 文本分支每条命中用 `(Path(root)/rel).read_text()` 直接读原文（940），JSON 分支用 `load_meta`（915）+ `_parent_block` 再读（575）+ `_record_hits` 再读（969）→ 同一命中文件在一次查询里被读 2–3 次。

---

## 二、索引格式

### 2.1 磁盘位置与文件

- 目录常量：`IDX_DIR = "vector index"`（`rag.py:26`）；文件 `df_idf.json`（写：`rag.py:231`、`rag.py:314`；读：`rag.py:178`、`rag.py:776`）。
- 当前工作区实际件：`template-vault/vector index/df_idf.json`，**304,891 字节**，mtime 2025-10-15 21:53。同目录无其它文件（无分片、无二进制向量文件；**纯 JSON 单文件全量**）。

### 2.2 顶层结构（v4）

`payload` 的键（`rag.py:227-230` 全量、`310-313` 增量）：

```
version: 4                      # IDX_VERSION，rag.py:27
n_docs: 30                      # 收录文档数
vocab: [ … ]                    # sorted(df)，3819 个 token
idf:  { token: log((N+1)/(df+1))+1 }        # rag.py:225 / 308
tf:   { rel: { token: count } }             # 每文档词频（Counter 直出）
meta: { rel: {…} }                          # 见 2.3
doc_hashes: { rel: sha256(body)[:64] }      # rag.py:221 / content_fingerprint
doc_mtimes: { rel: float(st_mtime) }        # rag.py:222
inverted_index: { tag:{}, domain:{}, content_type:{}, author:{}, kb_summary_token:{} }
```

实测规模（本仓库副本，`python3 -c` 统计，未经改动）：

| 指标 | 值 |
|---|---|
| `n_docs` / `meta` / `doc_hashes` / `doc_mtimes` | 30 |
| `vocab` / `idf` 条目 | 3819 |
| `tf` 文档数 | 30 |
| 索引内 token 总量 | 12,611（12,617，含 my 测试改动前后） |
| 单文档 token | min 68 / median 322 / max 1356 |
| `inverted_index` 键数 | tag 34、domain 3、content_type **0**、author **0**、kb_summary_token 430 |
| `layer` 分布 | `page` 30（**模板库没有 canon/raw/index 层样本**） |
| `parent`/`root` 非空 | 0（模板库无分块） |

### 2.3 每文档 `meta` 字段（`rag.py:209-220`）

| 字段 | 来源 | 查询期是否被消费 |
|---|---|---|
| `title` | `fm.title` / 文件名 | ✗（输出用 frontmatter 现值，`rag.py:652`） |
| `domain` / `tags` | frontmatter | ✗ Meta 仅用于展示；`domain` 实际取现值 |
| `layer` | `kb_layer_of()` | ✓ `rag.py:851-857` |
| `chunk_of` | frontmatter | ✓ `_root_of()`(414) |
| `context` | `contextual.cch()`(201/278) | ✓ 重排正文拼接(887) + 输出(650) |
| `parent` / `root` | `parent_of()`/`root_of()` | ✓ `_parent_block`(568) |
| `confidence` | frontmatter | ✓ canon 分支(860) |
| `importance` | `_imp_of()` | **✗ 排序从不使用**（过滤器读的是现盘 frontmatter，`rag.py:522`） |
| `ctype` | frontmatter | ✗ 死字段 |
| `updated_ts` | `_ts_of()` days-since-epoch | **✗ 死字段（检索无时间衰减）** |
| `title_text` | `_title_text()` | ✓ 仅 `KB_TITLE_BOOST=1`(628) |

> 结论：索引里 `importance`、`updated_ts`、`ctype`、`vocab` 四个字段在查询路径上是**只写不读**的。

### 2.4 增量更新（`rag.py:236-316`）

判定入口 `cmd_index()`(175-187)：
1. `incremental=True` 且 `load_index` 得到 `old_ver == 4` → `_incremental_update()`(236)。
2. 否则（不存在 / 损坏 / 版本不符 / `--full`）→ `_full_rebuild()`(190-233)。

`_incremental_update` 流程：
- `current_files = {rel: Path}`（`iter_notes`，239）；
- 逐文件：`is_generated_report` → `retired`(246-248)；`index_excluded` → `retired`(250-254)；**先 `load_meta(p)` 全量读文件(249)**，再做 `old_mtimes.get(rel)==mtime` 快路径(256-260)；
- mtime 变 → `content_fingerprint(body)`(261) 与 `old_hashes` 比对 → 进 `changed`(264-265)；
- `deleted = 老 rel 不在 current_files 或 ∈ retired`(268)；
- `changed`/`deleted` 皆空 → 直接返回「索引已是最新」(270-272)；
- 否则局部替换 `tf`/`meta`(275-301) → **重算全量 df/idf**(302-308) → **`_build_inverted_index(root, tf_all)` 重建整张倒排**(309) → 覆盖写 `df_idf.json`(314)。

**实测（副本上 monkeypatch `Path.read_text` 计数）**：
- 无任何变更的一次「增量」：`read_text` **35 次**（几乎等于全库文件数 + 索引 + 状态文件）→ 「增量」并不省磁盘 IO，只省了 tokenize。
- 故意改 1 个文件后再跑：**65 次**（35 次扫描 + `_build_inverted_index` 再读 30 个文件）。
- 全量 `_full_rebuild` 同样是「1 遍 iter_notes 读文件(198) + 1 遍 `_build_inverted_index` 再读(366)」。

`_build_inverted_index`(361-377) 对每个 rel 重新 `load_meta(Path(root)/rel)`(366)，即使 `meta_all` 里已有同样信息 → 索引过程固定 2 倍全库读。

### 2.5 失效判断与一致性校验

| 机制 | 位置 | 实际效果 |
|---|---|---|
| 版本失效 | `rag.py:183-184`（`old_ver != IDX_VERSION` → 全量）| 唯一被自动触发的失效判断 |
| 查询期校验 | `rag.py:784-789` | **只比 version**，不比 `doc_hashes`/`mtime`/文件存在性；版本不同也照样查询 |
| 损坏 | `load_index`(43-44) → `(None,0)` | 索引损坏时 `cmd_query` 报「索引不存在」(782)，**措辞误导**；`cmd_index` 能自愈（走全量） |
| 键值对（rel→hash）一致性 | `doc_hashes`（`rag.py:221`） | **rag.py 查询路径从不校验**；只有外部工具校验 |
| 外部校验 1 | `memory_sync.py:83-110`（判据：`96` 与 `99`） | 逐文件比 `doc_hashes[rel] != content_fingerprint(p.read_text())`（`96`/`99`），不等就 `rag.cmd_index(root)`(`101-102`) |
| 外部校验 2 | `kb_scale.py:55-80` `fabric()` | `coverage = n_indexed / n_markdown` + 版本比对（唯一的「索引 vs 文件覆盖」体检） |
| 外部校验 3 | `scripts/verify.py:117-136` | 只读 `n_docs` 判「索引存在 + 查询可用」 |

**实测幽灵命中**（副本上删掉 `70-知识治理 Governance/04-质量与指标.md` 后查询）：

```json
{"path":"70-知识治理 Governance/04-质量与指标.md","score":1.8328,
 "title":"04-质量与指标","snippet":"","domain":"-"}
```

即：文件已删，索引仍以 #1 返回该 rel，`snippet` 为空、`domain` 退化为 `-`，**退出码 0、无任何警告**。`load_meta` 对不存在文件返回 `({},"")`（`kb_common.py:94-97`）→ 静默降级；若同时用 `--answer`，`llm_answer` 里 `read_text` 抛 `FileNotFoundError`，被 `except (OSError,…)`(1140) 捕获并报成「**连接 ORNITH 端点失败**」→ 错误归因。

---

## 三、打分公式与常量（含默认开关）

### 3.1 分词（tokenize）

`kb_common.py:43-54`：`LAT=[A-Za-z0-9]+` → **小写整词**；`CJK=[\u4e00-\u9fff]` → 逐字输出 unigram，并额外输出相邻 **bigram**（`c+cjk[i+1]`）。没有 jieba、没有停用词、没有跨标点的 bigram（bigram 只在「连续汉字序列」内构造）。

特例（同名不同实现，**不要混用**）：
- `rag.py` 用 `kb_common.tokenize`（unigram+bigram）；
- `kb_rsi.tokenize`(`kb_rsi.py:59-65`)：`[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}` → 中文只留 **2+ 连续段**、无 bigram；
- `kb_query._tokenize`(`kb_query.py:44-46`)：中文逐字 + 拉丁词，**无 lower()、无 bigram**（注释 57-58 明确说明是为保持排序行为）；
- `kb_adaptretrieve._ngrams`(`kb_adaptretrieve.py:50-52`)：**字符 bigram（含标点、含空白归一）**。

### 3.2 索引向量与余弦

- `_vec(counter, vocab, idf)`（`rag.py:318-321`）：`v[t]=count(t)*idf[t]`（**`vocab` 参数未使用**，仅以 `t in idf` 过滤），再 L2 归一化 `n = sqrt(Σx²) or 1.0`。
- `_cos(a,b)`（`rag.py:323-325`）：稀疏点积，自动遍历较短向量；因两侧都已 L2 归一化，点积即余弦。
- `idf = log((N+1)/(df+1)) + 1`（`rag.py:225`、`308`）—— 平滑 IDF，取值域 `[1, 1+log(N+1)]`，**恒正**。

### 3.3 BM25（第二路）

`rag.py:332-354`，`k1=1.5`、`b=0.75`（函数默认参数，无环境变量可调）：

```
df   = 每次调用现算（rag.py:334-337，遍历全库 token）
N    = len(tf_all) or 1
avgdl= Σ|d|/N or 1.0
idf_t= log((N - n + 0.5)/(n + 0.5) + 1)          # rag.py:350
score= Σ_t idf_t * f*(k1+1) / (f + k1*(1-b + b*L/avgdl))   # rag.py:351
```
只保留 `s>0` 的文档（352-353）。

### 3.4 RRF 融合

`rag.py:393-403`：`RRF_K = 60`、`RRF_CAP = 300`；`score = Σ 1/(60+rank)` 对两路各取前 300 名。**理论最大分 = 2/(60+1) ≈ 0.0328**（`rag.py:394` 常量 `RRF_CAP` 与 `HYBRID_ALPHA=0.5`(329) —— 后者已是死常量，线性加权被 RRF 取代，注释见 799-801）。

### 3.5 倒排加成（数值量纲冲突，见 D-4）

`INVERTED_HIT_BOOST = 0.15`（`rag.py:358`）；`_inverted_lookup`(380-387) 在 `tag` 与 `kb_summary_token` 两个字段上，对**每个查询 token** 每命中一篇就 +1；`rag.py:812-816` `score += 0.15 * hit_count`，无 IDF、无长度归一、无上限。

**实测分解**（副本、查询「质量门禁与巡检指标」）：

| 阶段 | 头部文档得分 |
|---|---|
| TF-IDF 余弦 | 0.2683 |
| RRF 融合后 | **0.03279**（理论上限 0.03279） |
| 倒排命中计数 | 12 次（`与`→7 篇、`标`→8 篇…单字 unigram 大量命中） |
| **最终分** | **1.8328** |

即：RRF 阶段贡献 ≈1.8%，倒排加成贡献 ≈98%。**混合检索（BM25+RRF）在最终排序里几乎不起作用**，实际决定排序的是「query 的单字/双字与 `kb_summary` token 的重复计数」。

### 3.6 分层乘数 / 优先级 / 置信分

`kb_common.LAYER_MULTIPLIER`(`kb_common.py:376-383`)：`canon 1.25`、`"" 1.00`、`page 1.00`、`raw 0.72`、`index 0.60`、`noise 0.60`。

`rag.py:847-865`：
```
if layer == "raw"  and not include_raw:   s *= 0.72
elif layer in ("index","noise") and not include_stubs: s *= 0.60
elif layer == "canon":
    s *= 1.25
    s *= (1.0 + 0.20 * (confidence - 0.5) * 2)     # CONF_WEIGHT=0.20, rag.py:392
```
- 外层条件 `if not include_raw or not include_stubs`(847)：**只要两个 `--include-*` 都打开，canon 提升也一并失效**（逻辑耦合）。
- 分支是 `elif` 链：canon 文档不会再吃到 raw/stub 降级（设计上无冲突）。

`PRIORITY_MULTIPLIER`(`rag.py:466-471`)，在 `_apply_filters` 内乘法（537-538，**仅在启用过滤器时生效**）：
```
(policy,must)=1.10 (policy,should)=1.05 (policy,may)=1.00 (policy,None)=1.05
reference=0.95  chat/chat_decision/chat_knowledge/chat_todo=0.90  其余 1.0
```

### 3.7 freshness / 时间衰减

- 检索打分**没有任何时间衰减**：`updated_ts` 写进索引后从不被读（见 2.3）。
- 只有硬过滤：`--exclude-stale`（`rag.py:837-843`）→ `retrievable_status()`(`kb_common.py:266-269`，仅 `active/stable/legacy`）+ `is_stale()`(`292-294`)；`is_stale = review_after 到期 or age > DEFAULT_STALE_DAYS=180`（`kb_common.py:263`、`280-289`）。
- 唯一的时间项在启发式重排里：`_recency_score`(`rerank.py:78-93`)：`days<=30 → 1.0-(days/30)*0.5`（1.0→0.5）、`>30 → 0.3`、无 `updated` → 0.5。

### 3.8 标题加权（默认关）

`TITLE_BOOST = 0.55`、`TITLE_BOOST_CAP = 200`（`rag.py:613-614`）；开关 `KB_TITLE_BOOST=1`（`rag.py:826`）。
`_title_boost`(617-637)：`q_tokens = {t | len(t)>1}`（619，**单字 token 被排除**）；`cov = 命中数/查询token数`；`s *= 1 + 0.55*cov`。
`title_text` 由 `_title_text`(124-135) 拼成：`title + kb_summary[:120] + tags + 路径各段`。

### 3.9 查询扩展（三档，两档默认关）

`_expand_query`(1002-1042)：
1. `KB_SYNONYM_EXPAND=1`（1015）→ 双向扩展：`reverse`(992-996) 把同义词映射到规范词，再把该条目**全部同义说法** tokenize 后并入(1029-1042)。默认 **off**。
2. 否则 `KB_QUERY_EXPAND=llm`（1019）→ `llm_rerank.expand_query()`(159-201)，EXPAND_PROMPT 要求 ≤12 个词；默认 **off**，代价 +10~20s。
3. 都关 → 原样返回 `q`（`q_expanded == q`，已实测确认）。

扩展只作用于 3.2/3.3 两路（`rag.py:794`、`802`）；**倒排加成(810)与标题加权(827)用未经扩展的原始 `q`** → 扩展范围不一致。

### 3.10 重排三分支的启用条件与默认开关

| 分支 | 位置 | 开关 | 默认 | 公式 |
|---|---|---|---|---|
| 语义/cross-encoder | `rag.py:880-899` | `KB_RERANK`：`0/off/none`(默认) / `embed` / 其他值=cross | **关** | `final = 0.6*norm_minmax(语义分) + 0.4*norm_minmax(首段分)`（`embed_rerank.py:29` ALPHA=0.6、`102-103`；`96-101` min-max 归一） |
| 启发式 5 维 | `rag.py:901-908` | `KB_HEURISTIC_RERANK=1` | **关** | `0.40*Jaccard(query,body) + 0.20*fm匹配 + 0.20*importance + 0.10*enforce + 0.10*recency`（`rerank.py:16-22`、`122-124`），**完全替换**首段分 |
| LLM 重排 | `llm_rerank.py:41-143` | 无 CLI 接线（只有 `probe`/`try` 子命令）→ 实际**未接入 rag 查询路径** | **关** | `final = 0.4*(base/bmax) + 0.6*(1 - pos/(m-1))`（`llm_rerank.py:28`、`94-96`） |

`_ask`(101-143)：候选只喂「标题 + 首条 `-` 列表项前 80 字」(104-109)；要求模型输出 `{"order":[…]}`，用 `re.search(r"\[[\d,\s]+\]")`(129) 解析；`len(valid) < 2` → 放弃(137-138)；失败一律原样返回(84-85)。缓存 `.kb/llm_rerank_cache.json`，键 = `query\0rel1\1rel2…`(80)，保留最后 1500 条(65)。

`embed_rerank.py`：`TOP_N=50`(28)、`ALPHA=0.6`(29)、`BUDGET_S=6.0`(30)、`RERANK_URL=http://127.0.0.1:8082/v1/rerank`(37，**硬编码，无 env 覆盖**)、`RERANK_MODEL=bge-reranker-v2-m3`(38)；缓存 `.kb/embed_cache.json`（相对 `root`，27），保留最后 4000 条(147)。

### 3.11 片段与预算

- `best_sentence`(1067-1110)：先 `_snippet_candidate`(1051-1064) 过滤引用/指针行；再按 `[。！？\n]` 切句；`s_score = 词重叠 + 0.5*(结论词命中) + len_bonus(30~120字 0.3 / >120 0.1)`（1084-1090）；滑窗 1–3 句，`val = total*(1+0.15*(len-1))`(1101)，总长上限 320 字。
- `_context_budget`(691-708)：按序累加 `count_tokens`(kb_common:130-140，CJK 1 字=1 token、拉丁 4 字符=1 token)，超预算时若剩余 >50 则 `_truncate_to_tokens`(675-688) 截断后 `break`。
- `_parent_block`(560-603)：`PARENT_WINDOW=900`(542)；父页是「目录页」时（`_is_index_like` 545-557，指针行占比 ≥0.6）回填同父兄弟中**本次查询得分最高**者(581-598)，并置 `parent_is_sibling="1"`。

---

## 四、逐文件详解

### 4.1 `template-vault/pipeline/rag.py` — 1145 行

| 项 | 内容 |
|---|---|
| 职责 | 本地 TF-IDF+BM25 混合检索的索引与查询；唯一被 `kb query` 调用的检索实现 |
| 关键类/函数 | `load_index(idx_path)`(31)；`strip_body(text)`(47)；`main()`(55)；`_infer_parent_from_name(rel)`(102)；`_title_text(rel,fm)`(124)；`_imp_of(fm)`(138)；`_ts_of(fm)`(146)；`_conf_of(fm)`(158)；`_parent_path/_root_path`(166/170)；`cmd_index(root, incremental=True)`(175)；`_full_rebuild(root, idx)`(190)；`_incremental_update(root, idx, old)`(236)；`_vec(counter, vocab, idf)`(318)；`_cos(a,b)`(323)；`_bm25_scores(tf_all,q_tokens,k1=1.5,b=0.75)`(332)；`_build_inverted_index(root,tf_all)`(361)；`_inverted_lookup(query_tokens,inverted_index)`(380)；`_rrf_fuse(list_a,list_b,k=60,cap=300)`(397)；`_root_of(rel,meta_all)`(406)；`_dedup_chunks(scored,top,meta_all=None)`(429)；`_parse_importance(v)`(476)；`_parse_tags(v)`(485)；`_has_any_tag(tags_value,tags_filter)`(494)；`_apply_filters(...)`(501)；`_is_index_like(root,path)`(545)；`_parent_block(root,rel,meta_all,context,sib_scores=None)`(560)；`_read_head(p,n=900)`(606)；`_title_boost(scored,query,meta_all)`(617)；`_build_hit(...)`(640)；`_build_json_output(...)`(666)；`_truncate_to_tokens(text,max_tokens)`(675)；`_context_budget(hits,budget)`(691)；`_gen_session()`(711)；`_record_hits(query,hits,session,root)`(717)；`cmd_query(root,q,top,answer,as_json=False,context=240,context_budget=None,session=None,domain=None,content_type=None,author=None,min_importance=None,enforce_level=None,tags=None,date_from=None,date_to=None,exclude_stale=False,include_raw=False,include_stubs=False)`(734)；`_load_synonyms(path=None)`(981)；`_expand_query(q)`(1002)；`_snippet_candidate(line)`(1051)；`best_sentence(body,q)`(1067)；`llm_answer(root,q,hits)`(1112) |
| 常量/阈值 | `CONTEXT_IN_INDEX=True`(24)；`IDX_DIR`(26)/`IDX_VERSION=4`(27)；`HYBRID_ALPHA=0.5`(329，死)；`INVERTED_HIT_BOOST=0.15`(358)；`CHUNK_FAMILY_MAX=2`(391)、`CONF_WEIGHT=0.20`(392)、`RRF_K=60`(393)、`RRF_CAP=300`(394)；`PRIORITY_MULTIPLIER`(466)；`PARENT_WINDOW=900`(542)；`TITLE_BOOST=0.55`/`TITLE_BOOST_CAP=200`(613-614)；`CONCLUSION_WORDS`(1046-1048) |
| CLI | `index [--root] [--full]`(58-60)；`query q [--top 3] [--root] [--answer] [--json] [--context 240] [--context-budget] [--session] [--domain] [--content-type] [--author] [--min-importance] [--enforce-level] [--tags] [--date-from] [--date-to] [--exclude-stale] [--include-raw] [--include-stubs]`(61-82) |
| 缓存 | `.kb/state/query_cache.json`（TTL 300s，`rag.py:762`），另写 `.kb/state/feedback_state.json`（717-731） |
| 依赖 | `kb_common`（16）、可选 `contextual.cch`(18-22)、可选 `state_manager.StateStore`(720/759/928/949)、可选 `embed_rerank`(889/894)、可选 `rerank`(904)、可选 `llm_rerank.expand_query`(1021) |
| 默认开关 | `KB_RERANK=0`(880)、`KB_HEURISTIC_RERANK` 关(902)、`KB_TITLE_BOOST=0`(826)、`KB_SYNONYM_EXPAND=0`(1015)、`KB_QUERY_EXPAND` 空(1019)；倒排加成**恒定开启**(808) |

### 4.2 `kb_query.py` — 366 行

| 项 | 内容 |
|---|---|
| 职责 | P2-1「查询→知识」正回路：查询→规则综合→耐久性五道闸→**只写提案**，人工 `apply` 才落库（非检索路径；CLI 为 `kb compile query/apply/status`，见 `plugins/compile_plugin.py:10-11`、`kb_launcher.py:180`） |
| 关键函数 | `_tokenize(s)`(44)；`_vec(s)`(49)；`_slug(q)`(65)；`search(root,query,topk=6)`(72)；`synthesize(scored,query)`(87)；`_grounded(root)`(105)；`durability(answer,root)`(114)；`_prop_path/_state_path`(136/140)；`_daily_count(root)`(144)；`_inc_daily(root)`(154)；`_next_id(root)`(166)；`query(root,q,answer=None)`(172)；`_fm_block(fm)`(222)；`_write_note(root,rec)`(235)；`_git_commit(root,rel,msg)`(261)；`apply(root,ids=None,force=False)`(268)；`status(root)`(297)；`_render_status(root)`(305)；`main()`(320) |
| 常量 | `MIN_ANSWER_CHARS=60`(31)、`MIN_TTR=0.45`(32)、`NOVELTY_MIN_SIM=0.90`(33)、`MAX_PROPOSALS_PER_DAY=10`(36)、`_CN/_LAT`(38-39) |
| CLI | `query --root --query --answer [--json]`(323-326)、`apply --root [--id…] [--all] [--json]`(327-330)、`status --root [--json]`(331-332) |
| 缓存文件 | `pipeline/.kb_query_proposals.jsonl`(137)、`pipeline/.kb_query_state.json`(141，按天计数) |
| 依赖 | `kb_rsi`（collect/compiled_notes/metrics/proposals/load_note）、`kb_common.cos`(28)、`evolution_log`(213/288) |
| 默认 | 只提议、不写库（人工在环）；`apply` 需显式 `--id`/`--all` |

### 4.3 `rerank.py` — 146 行

| 项 | 内容 |
|---|---|
| 职责 | 5 维启发式交叉打分重排（FR-3.2.6），无外部模型 |
| 函数 | `_token_overlap(query_tokens,doc_tokens)`(25)；`_frontmatter_match(query,fm)`(34)；`_importance_score(fm)`(58)；`_enforce_score(fm)`(66)；`_recency_score(fm)`(78)；`rerank(query,hits,root)`(96)；`main()`(131) |
| 常量 | `W_TOKEN=.40/W_FRONTMATTER=.20/W_IMPORTANCE=.20/W_ENFORCE=.10/W_RECENCY=.10`(16-20)；`RECENCY_DAYS=30`(22) |
| CLI | `python3 rerank.py <query> [--hits JSON] [--root]`(132-137)；插件名 `rerank`(`plugins/rerank_plugin.py:10`) |
| 依赖 | `kb_common.tokenize/load_meta/domain_primary/parse_domain`(12) |
| 默认 | 在 rag 路径中**默认关闭**（`KB_HEURISTIC_RERANK=1` 才用） |

### 4.4 `llm_rerank.py` — 241 行

| 项 | 内容 |
|---|---|
| 职责 | 用本地 chat 模型按「能否回答问题」重排（P6 实验）+ LLM 查询改写 |
| 类/函数 | `LLMReranker(root,top_n=12,alpha=0.4,budget_s=60.0,enabled=True)`(42)；`_load/save`(53/59)；`available()`(71，要求 `model_config()["api_key"]`)；`rerank(query,hits,texts=None)`(74)；`_ask(query,head,texts)`(101)；`expand_query(text,root=None)`(159)；`main()`(204) |
| 常量 | `TOP_N=12`(27)、`ALPHA=0.4`(28)、`BUDGET_S=60.0`(29)、`MAX_TOKENS=4096`(30)、`CACHE=.kb/llm_rerank_cache.json`(31)、`PROMPT`(33-38)、`EXPAND_PROMPT`(146-153) |
| CLI | `probe [--root] [--query] [--top]`、`try --query [--top] [--json]`(204-212) |
| 缓存 | 排名缓存 1500 条(65)；`.kb/query_expand_cache.json` 2000 条(156/196) |
| 依赖 | `kb_common.ROOT_DEFAULT/model_config/load_meta`(25)、`urllib.request` |
| 默认 | 重排**未接入 rag**（`rag.py` 无 `LLMReranker` 引用）；`expand_query` 仅在 `KB_QUERY_EXPAND=llm` 时被调用 |

### 4.5 `embed_rerank.py` — 238 行

| 项 | 内容 |
|---|---|
| 职责 | 两段式重排：cross-encoder（首选，`/v1/rerank`）或 bi-encoder 嵌入余弦（`kb_embed`）|
| 类/函数 | `CrossEncoderReranker(url=RERANK_URL,model=RERANK_MODEL,top_n=50,alpha=0.6,budget_s=6.0)`(44)；`.available()`(52)；`.rerank(query,hits,texts)`(65)；`_norm(v)`(108)；`_cos(a,b)`(114)；`_fp(text)`(118)；`EmbedReranker(root,top_n=50,alpha=0.6,budget_s=6.0,enabled=True)`(123)；`_load_cache/save`(135/141)；`_embed(text)`(153)；`available()`(169)；`rerank(query,hits,texts)`(178)；`main()`(209) |
| 常量 | `CACHE=.kb/embed_cache.json`(27)、`TOP_N=50`(28)、`ALPHA=0.6`(29)、`BUDGET_S=6.0`(30)、`RERANK_URL/MODEL`(37-38) |
| CLI | `probe`、`score <q> <text>`(212-214) |
| 默认 | 由 `KB_RERANK` 驱动，**默认 `0` 不启用**；`available()` 每次打 `/v1/models`（timeout 5s）|

### 4.6 `kb_embed.py` — 169 行

| 项 | 内容 |
|---|---|
| 职责 | 可选语义向量地基（Layer 0），OpenAI 兼容 `/embeddings`；不可用即降级 |
| 函数 | `_base_url()`(38)、`_api_key()`(44)、`_model()`(48)、`_post(url,payload)`(52)、`_first_vector(payload)`(64)、`_normalize(vec)`(75)、`available()`(80)、`_probe_once()`(91)、`embed(text)`(108)、`_main(argv)`(134) |
| 常量 | `EMBED_PATH="/embeddings"`(25)、`_TIMEOUT=10`(26)、`_TIMEOUT_PROBE=12`(27)、`_RETRY=2`(28)、`_CACHE_LIMIT=8192`(29)、`_PROBE_INTERVAL=300.0`(35) |
| 环境 | `ORNITH_EMBED_URL` → `ORNITH_BASE_URL`(41)、`ORNITH_API_KEY`(45)、`ORNITH_EMBED_MODEL`(49) |
| 缓存 | **进程内** `_cache`（32，上限 8192）+ `_probe`/`_last_probe_ts`（33-34，300s 节流）；无磁盘缓存 |
| 默认 | 端点未配置 → 一律 `None`（调用方降级）；`kb_rsi._use_embed` 默认 False(`kb_rsi.py:75-83`) |

### 4.7 `kb_adaptretrieve.py` — 386 行

| 项 | 内容 |
|---|---|
| 职责 | L1「查询即反馈」：记录真实查询事件 → 挖「高相似却未命中」的检索缺口 → 只提议 → 人工 apply 落「检索修正契约」 |
| 函数 | `_ngrams(text,n=2)`(50)；`_cosine(a_text,b_text)`(55，**死代码，无调用点**)；`_norm_q(q)`(69)；`_now_iso()`(73)；`record_query(root,query,hits=None,session=None,satisfied=None)`(78)；`_agent_events(root)`(110)；`_suspect_failed_queries(root)`(131)；`_index_notes(root)`(141)；`propose(root,sim_min=0.50,max_proposals=50)`(154)；`_git_checkpoint_force(root,rel,msg)`(247)；`apply(root,ids=None,force=False)`(256)；`status(root)`(313)；`render(root,summary=None)`(321)；`main()`(340) |
| 常量 | `PROP_FILE=".kb_adaptretrieve_proposals.jsonl"`(38)、`ADJUST_FILE=".kb_retrieval_adjust.json"`(39)、`SIM_MIN=0.50`(41)、`MAX_PROPOSALS=50`(42)、`REQUERY_WINDOW=3600`(43)、`_MAX_EVENTS=500`(44) |
| CLI | `capture --root --query [--hits…] [--session] [--satisfied]`(344-347)、`propose [--sim] [--max] [--json]`(349-351)、`apply [--id…] [--all] [--force] [--json]`(353-356)、`status [--json]`(358-359) |
| 依赖 | `kb_rsi.collect/is_raw/is_archive/in_inbox`(34/144-146)、`kb_usage.analyze`(135)、`state_manager.StateStore`(84/113)、`evolution_log`(301) |
| 默认 | 只提议；相似度为离线 char-bigram 余弦；不读同义词表、无概念索引（见第七节） |

### 4.8 `recall_schedule.py` — 273 行

| 项 | 内容 |
|---|---|
| 职责 | 回忆调度（成长引擎④）：按 importance 衰减窗口 + SM-2 式间隔重复生成今日 deck，并用 rag 索引做「检索浮层」自查 |
| 函数 | `review_update(interval,ease,reps,q)`(45)；`load_meta(path)`(68)；`base_interval(imp)`(78)；`load_state(root)`(86)；`save_state(root,st)`(102)；`load_index(root)`(112)；`retrievable(idx,rel,summary)`(130)；`build(root,deck_n)`(143)；`report_deck(deck)`(179)；`report_status(due_list)`(195)；`mark(root,deck_n,rels,grade="good")`(206)；`main()`(243) |
| 常量 | `STATE=".recall_state.json"`(24)、`IDX_DIR`(25)、`INTERVAL_TABLE`(30-35)、`EASE_DEFAULT=2.5`(38)、`EASE_MIN=1.3`(39)、`MAX_INTERVAL=365`(40)、`DAILY_CAP=20`(41)、`GRADE_Q={again:1,hard:3,good:4,easy:5}`(42) |
| CLI | `deck [--n 10] [--root]`(246)、`mark [--deck 10] [--recalls "a,b"] [--grade good] [--root]`(247-250)、`status [--root]`(251) |
| 状态文件 | `.kb/state/recall_state.json`（`StateStore`，86-109），回退旧路径 `pipeline/.recall_state.json`(24/93/98) |
| 输出文件 | `recall_deck.md`(256)、`recall_schedule.md`(265) —— 写入 vault 根（均为 `GENERATED_REPORTS`，`kb_common.py:416-420`，不入索引） |
| 依赖 | `rag`(63，复用 `_vec/_cos/tokenize/load_index/IDX_VERSION`)、`kb_common.parse_frontmatter/iter_notes/parse_importance/ROOT_DEFAULT/EXCLUDE`(21-22)、`state_manager`(89/104) |
| 默认 | 单日上限 20 篇；`--n` 超过 20 会被截到 20（175） |

### 4.9 `kb_selfweight.py` — 458 行

| 项 | 内容 |
|---|---|
| 职责 | L2 自我加权：融合「外部显式有用反馈 + RAG 人气 + 入链 + L1 重问」→ 提议 importance 提升，人工 apply 写回 frontmatter（只升不降、封顶、接地门控） |
| 函数 | `_state(root)`(56)；`_prop_path/_applied_path`(76/80)；`_read_applied(root)`(84)；`grounding(root)`(95)；`_feedback_signals(state)`(112)；`_inbound_links(root)`(122)；`_l1_signal(root)`(134)；`_note_imp(root,rel)`(150)；`signal(root)`(171)；`propose(root)`(211)；`_write_proposals(root,proposals)`(270)；`_write_importance(p,new_imp)`(280)；`apply(root,ids=None,force=False)`(298)；`status(root)`(378)；`render(root,summary=None)`(391)；`main()`(413) |
| 常量（导入） | `EXT_WEIGHT=1.0`、`INT_WEIGHT=0.15`、`HIT_CAP=5`、`BUMP_CAP=0.30`、`MIN_BUMP=0.05`、`DIVERSITY_FLOOR=0.05`（`feedback_loop.py:33-38`）；`L2_SIGNAL_MIN=0.30`、`L2_LINK_BONUS=0.10`、`L2_L1_MISS_BONUS=0.15`（`kb_constants.py:38-40`）；`EXTERNAL_FRESH_MIN_PCT=5.0`、`EXTERNAL_MIN_CROSS=0.15`（`kb_constants.py:32-33`） |
| 文件 | `pipeline/.kb_selfweight_proposals.jsonl`(51)、`pipeline/.kb_selfweight_applied.json`(52，累计账本) |
| CLI | `report/propose/apply/status [--root]`(416-423)，`apply` 支持 `--id…`/`--all`/`--json` |
| 依赖 | `kb_rsi.collect/metrics/compiled_notes/load_note`(97/125/180/156)、`feedback_loop`（常量 + `is_raw_rel`）(44-47)、`kb_usage.analyze`(140) |
| 默认 | 只提议；接地不足时冻结内部人气（193-203） |

### 4.10 `kb_fitness.py` — 168 行

| 项 | 内容 |
|---|---|
| 职责 | 给 T3 元调参一个**外部锚定**的 fitness（四个分量），替代可自抬的 `health_score` |
| 函数 | `_shannon_norm(counts)`(49，**死代码，无调用**)；`source_diversity(root,notes)`(58)；`external_feedback_rate(root,compiled_rels)`(69)；`contradiction_component(root)`(82)；`query_success_component(root,window=3600)`(88)；`fitness(root)`(101)；`main()`(143) |
| 常量 | `W_SOURCE=0.28`、`W_FEEDBACK=0.28`、`W_CONTRAD=0.24`、`W_QUERY=0.20`(38-41)；`FEEDBACK_TARGET=0.10`(44) |
| CLI | `[--root] [--json]`(144-146) |
| 依赖 | `kb_rsi`(32，`collect/metrics/EXCLUDE_DIRS`)、`feedback_loop.load_state`(33/72)、`kb_contradiction.detect/contradiction_zero_score`(83-85)、`kb_usage.analyze`(93) |
| 默认 | 全部只读；无数据时分量给「中性/满分」（见第六节） |

### 4.11 配置文件（只读结构，未贴全文）

**`template-vault/vector index/df_idf.json`**：见第二节（304,891 B，v4，30 docs，3819 vocab，12611 tokens）。
**`template-vault/reference/synonyms.json`**（539 行、7029 B）：`{_meta:{version,description,note}, "<规范词>":[同义词…]}`，共 **88 个规范词条**（`知识库/检索/向量/索引/…/提示词`），每条 2–6 个同义词，覆盖中英缩写（如 `正典 → canon, canonical, 结晶产物, kb_layer canon, 正典层`）。**仅被 `rag._load_synonyms`(981) 读取**，而该函数只在 `KB_SYNONYM_EXPAND=1` 时才被调用 → 默认完全未生效。
**`template-vault/reference/dedup-config.json`**（41 行）：`global.{dup_sim:0.92, novel_sim:0.70, sync_sim:0.85, replace_sim:0.85, merge_sim:0.70}`、`by_domain.{开发:{0.88,0.65}, 综合:{0.94,0.75}}`、`sim_method:"tfidf"`、`exact_dedup(sha256, skip)`、`semantic_dedup(0.92, skip)`、`semantic_related(0.70–0.92, mark_related)`、`jaccard_dedup(0.85)`。**该文件不被 `rag.py`/`rerank.py`/`kb_query.py` 读取**；由 `kb_common.load_dedup_config/get_dedup_thresholds`(`kb_common.py:546/1177`) 供 memory_ingest/memory_sync/link_engine 使用 → 与检索打分**没有接线**（`DUP_SIM=0.85` 在 `kb_constants.py:11` 是另一套值，两处阈值并不一致）。

---

## 五、`recall_schedule.py` 的 SM-2 式间隔重复

### 5.1 状态文件

- 首选 `.kb/state/recall_state.json`（`StateStore`，原子写 + flock，`state_manager.py:38-101`），存在才读，否则回退 `pipeline/.recall_state.json`(93-95)；写入永远走 StateStore(102-109)。
- 单条状态结构（`mark` 写入，231-234）：`{ "<rel>": {"last_seen": "YYYY-MM-DD", "interval": float, "ease": float, "reps": int, "lapses": int?} }`；读取兼容旧字段 `stability`/`recalls`(160-162、225-227)，`mark` 会 `pop` 掉旧字段(229-230)。
- 全局键：无（纯 rel→state）。旧文件含 `per_day`？不适用——那是 `kb_query` 的 state。

### 5.2 grade 取值

`GRADE_Q = {"again":1, "hard":3, "good":4, "easy":5}`(`recall_schedule.py:42`)，CLI `--grade` 选择项即这四个键(249-250)，默认 `good`(250/214)。`q<3`（即 `again`）判为遗忘。

### 5.3 调度公式

`review_update(interval, ease, reps, q)`(45-60)：

```
ease = ease + (0.1 - (5-q)*(0.08 + (5-q)*0.02))        # 50: 标准 SM-2 EF 更新
ease = max(1.3, min(2.5, ease))                        # 51: 下限 1.3、上限 2.5
if q < 3:  reps = 0; interval = max(1.0, interval*0.5) # 52-54: 遗忘 → 间隔减半（非重置为 1）
else:
    reps += 1
    if reps > 1: interval = interval * ease            # 56-58: 首次回忆不乘 ease（保留 importance 种子）
interval = max(1.0, min(interval, 365))                # 59
return round(interval,2), round(ease,3), reps
```

- **种子间隔**：`base_interval(imp)`(78-83) 查 `INTERVAL_TABLE`(30-35)：`imp≥0.9→180d`、`≥0.5→30d`、`≥0.3→14d`、`其余→7d`。
- **到期与紧急度**（`build`，156-165）：
  `due = last_seen(或 created) + max(1, round(interval))`；`overdue = today - due`；
  `urgency = imp * (1 + max(0, overdue)/max(interval,1))`（165）。
- **deck**：只取 `overdue ≥ 0`，按 `-urgency` 排序，截断 `min(deck_n, DAILY_CAP=20)`(174-175)；`status` 用全量 `notes` 按 `due` 升序(173)。
- 过滤：`kb_action=retire` 跳过(151-152)；`importance<0.1` 跳过(154-155)。

### 5.4 deck 生成与「检索浮层」

`retrievable(idx, rel, summary)`(130-140)：以该笔记 `kb_summary`（或正文首行前 60 字，166）为查询，用 `rag._vec`+`rag._cos` 与**全库所有向量**比对，仅当该笔记恰好是 **top-1** 才返回 True。`build` 对**每篇笔记**都调一次(170) → **O(N²)**。

实测（副本，35 篇）：`build(root,10)` 共 **7.5 ms**，`deck` 7 篇、`due` 35 篇；`retrievable=False` 的样本（`01-如何开始.md`，imp 0.97，base 180，due 2027-02-01，overdue -109）说明「检索浮层」自查并不稳定，仅作提示。

`mark(root, deck_n, rels, grade)`(206-240)：无 `--recalls` 时取 `build(root, 999)` 的 deck 前 `deck_n` 篇(212-213)，注意 `build` 内部已按 `DAILY_CAP=20` 截断(175) → `--deck 999` 最多 20 篇。

---

## 六、`kb_selfweight.py` 与 `kb_fitness.py`：自权重 / 适应度机制与自指风险评估

### 6.1 kb_selfweight（L2 自我加权）

**接地阀**（`grounding`，95-108）：`grounded = external_inflow ≥ 5.0% AND cross_ratio ≥ 0.15`（100-101），指标来自 `kb_rsi.metrics`：`external_inflow = 100*raw_fresh/total_all`（`kb_rsi.py:277-282`，分子=raw/ 近 30 天新建数）、`cross_ratio = cross/total_out`（`kb_rsi.py:205-209`）。实测模板库：`external_inflow 8.6%`、`cross_ratio 0.65` → **阀开**。

**四路信号**（171-207）：
```
grounded:  raw = 1.0*ext + 0.15*min(hits,5) + 0.10*inbound + 0.15*l1_miss   # 194-197
!grounded: raw = 1.0*ext（人气冻结），且 ext==0 的笔记直接跳过               # 200-203
```
- `ext` = `feedback_state.explicit_feedback` 中 `useful=true` 的计数(112-119) —— 真外部锚。
- `hits` = `feedback_state.hits`（由 `feedback_loop.ingest` 用**每篇笔记自己的 kb_summary 当查询**、取 top-5 命中累加，`feedback_loop.py:115-144`）—— **纯自指人气代理**。
- `inbound` = 编译笔记里 `[[...]]` 指向它的次数(122-131) —— 结构信号，同样由系统自身（link_engine）生成。
- `l1_miss` = `kb_usage.suspect_failed` 里该笔记作为命中的次数(134-147) —— 真实使用行为。

**提议**（`propose`，211-267）：门槛 `raw ≥ L2_SIGNAL_MIN=0.30`(225)；`delta = round(min(BUMP_CAP, (raw/maxs)*BUMP_CAP), 2)`(237，**相对归一：最高分候选必得满额 +0.30**）；`ext>0 且 delta<DIVERSITY_FLOOR` → 抬到地板(238-239)；`delta<MIN_BUMP` 丢弃(240-241)；`new = min(1.0, old+delta)`(242)。排序 `(-ext, -signal, rel)`(251)，id 顺序编号(252-253)，写 `pipeline/.kb_selfweight_proposals.jsonl`(254/270-276)。

**写回**（`apply`，298-375）：幂等（`applied_ids` 去重，325-326）；拒 raw/(330-331)、拒 `importance` 解析不出(334-336)；累计封顶 `remaining = min(BUMP_CAP - cum, 1.0 - old)`(338)；`_write_importance` 用**正则替换** frontmatter 的 `importance`(280-294)，无则插到块首(292-293)；账本 `pipeline/.kb_selfweight_applied.json`(352-354)；写前 `git add`(358-359，账本 `--force`) + 一次 commit(362-364)。

**自指/自我抬高风险判据（我的评估）**：

| 风险点 | 判据（行号） | 评估 |
|---|---|---|
| 「人气」自指闭环 | `hits` 由 feedback_loop 用自库摘要互查产生(`feedback_loop.py:136-140`)，权重 `INT_WEIGHT=0.15`、`HIT_CAP=5` → 单项最多 0.75 | **真实存在**。阀门是**全库级**而非逐篇级：只要「raw 近 30 天流入 ≥5% 且跨域 ≥15%」成立（实测已成立），任何笔记都能靠纯内部人气超过 `L2_SIGNAL_MIN=0.30`（2 次命中 = 0.30 即达标），并获得 `delta`。文档「外部锚最硬」的表述对**单篇**不成立 |
| 相对归一化放大 | `maxs = max(c["raw"]) or 1.0`(231) + `(raw/maxs)*BUMP_CAP`(237) | 排名第一的候选**恒定**拿 +0.30，与绝对信号强度无关 → 与「外部锚定」的意图不符（建议乘绝对系数或加最小绝对门槛） |
| 只升不降 | 242、338 | 单向棘轮；无衰减路径（衰减在别处：`kb_conconfidence decay`/`kb_health`）。累计封顶 0.30/篇，风险有界 |
| 覆盖范围 | `kb_rsi.compiled_notes`(180) = 全库非 raw | 分母大、分子小；`applied` 账本只在本地，跨库/跨年不可比 |
| 缓解措施确实存在 | 前提`grounded`(202-203)、地板仅给 `ext>0`(238)、人工在环(298)、raw 不改(330)、幂等(325)、git 可回滚(362) | 方向正确；但**没有**：单篇级外部锚要求、时间衰减、「提议-采纳」效果回检 |

### 6.2 kb_fitness（外部锚定适应度）

```
fitness = 0.28*src_div + 0.28*min(1, fb_rate/0.10) + 0.24*cz + 0.20*qs   # kb_fitness.py:114-119
```
- `src_div` = raw/ 各 domain 的 **Simpson 指数 1-Σp²**（58-66，注意 docstring 12 行写「Shannon 熵归一化」，实现是 Simpson；`_shannon_norm`(49-55) 是死代码）。
- `fb_rate` = `|explicit_feedback(useful) ∩ 编译笔记| / |编译笔记|`（69-79）；分母 `or 1` 防除零(77)。
- `cz` = `contradiction_zero_score(#pairs) = 1/(1+#pairs)`（`kb_contradiction.py:280-282`）。
- `qs` = `query_success_proxy = 1 - requery_rate`（`kb_usage.py:344-352`；requery = 3600s 窗口内出现相同/近义查询，`kb_usage.py:303-321`）。无事件 → 中性 **1.0**(`kb_fitness.py:96-98`)。

**实测（副本）**：
```
fitness = 0.460
  source_diversity 0.444 (3 外部源 {开发:2, 综合:1})
  external_feedback_rate 0.000
  contradiction_zero_score 1.000 (0 对矛盾)
  query_success 0.480 (= 1 − 再问率; 25 查询事件)
```

**自指/Goodhart 风险判据**：

| 风险点 | 判据 | 评估 |
|---|---|---|
| 「零矛盾 = 满分 0.24」 | `kb_contradiction.py:282` `1/(1+0)` = 1.0 | **结构性白送分**。空库/小库/不跑矛盾检测/写更含糊的笔记都拿满分；无数据 ≠ 健康 |
| 「无查询 = 满分 0.20」 | `kb_fitness.py:96-98` 无 `agent_hits` → `qs=1.0` | **不测量即满分**：不采集流量永远比采集后被扣分更「健康」→ 反向激励 |
| 两个分量合计 0.44 恒为满分下限 | 实测 0.460 | 空库 fitness ≈ 0.44~0.46，无法体现退化；建议改为「未测量」标记或降低权重 |
| 查询信号被测量工具污染 | `rag.py:745-746` **每次 rag.py 查询都写 agent_hits**；`kb_eval.py:92-113` 以 `rag.py query --json` 当 oracle 跑 30 道黄金题 | **同一批题连跑两次（1h 内）→ 每条都算「重问」→ `requery_rate→1`、`qs→0`**，fitness 直接被评测行为压低 0.20。**实测证据**：副本 25 条事件（含 2 次相同 demo 查询 + 我的 2 次重复）→ requery_rate **0.52**、qs **0.48** |
| 分量自身可被系统间接影响 | `hits`/`inbound` 不进 fitness（好），但 `fb_rate` 依赖 `feedback_loop` 的 `explicit_feedback`（可由 agent 自报 useful） | 需人工纪律；`weight=0.3` 的自动 session 不计入 useful，方向正确 |

结论：fitness 的**方向**（外部锚、四分量、抗自抬）比旧 `health_score` 明显更稳；但当前实现有 **0.44 的「白送地板」** 与 **1 个可被自家评测污染的 0.20 分量**，趋势判读（T3）在「无外部反馈的小库」上几乎不可用；另需注意 `fitness()` 有全局副作用 `kb_rsi.root = root`(104)，且 `metrics(notes=notes)` 未传 root 会触发 `DeprecationWarning`(`kb_rsi.py:190-192`)。

---

## 七、`kb_adaptretrieve.py` 的自适应检索与查询扩展

### 7.1 能力边界（确定性结论）

- **同义词扩展：无。** 该模块只 `import kb_rsi`(34)，不读 `reference/synonyms.json`、不 import `rag`。全库唯一读同义词表的是 `rag._load_synonyms`(981)，且默认关闭。
- **概念索引：无。** 代码里没有 concept/ontology 结构；`reference/` 下的 `domain-taxonomy/tag-taxonomy/content-type-signals` 也不被该模块引用。所谓「概念索引」在本仓库**未确认存在**（`rag.py` 里出现 "concept" 的只有注释语境；`tag_fix.py`/`link_engine.py` 的字段名 `.concept` 亦不构成检索概念索引）。
- 它真正的「扩展」是**候选生成用的字符 bigram 倒排**（162-176）：对每篇编译笔记算 `Counter(bigram)`、模长、并建 `bigram → {rel}` 倒排(166-176)，查询时把与 query 共享 ≥1 bigram 的笔记并成候选集(192-197)。

### 7.2 检索缺口挖掘（`propose`，154-243）

```
events    = _agent_events(root)            # feedback_state.agent_hits，前 500 条
suspect_q = _suspect_failed_queries(root)  # kb_usage.analyze(use_embedding=False)
notes     = _index_notes(root)             # collect() 去掉 raw/归档/收件箱
for ev in events:
    cand = ∪ inv_idx[g] for g in bigrams(query)
    for rel in sorted(cand):
        if rel in 已命中的 paths: continue
        sim = dot/(|q|·|note|)             # 与 _cosine 等价但内联(203-206)
        if sim < sim_min(0.50): continue
        issue = "requery_failure_missed" if 归一化query∈suspect_q else "missed_retrieval"
        追加 {"query","note","sim","issue","suggested":"boost","reason"}
        if len(proposals) >= max_proposals(50): stop
```
- 排序与 id：`(是否重问, -sim, note)`(228)，顺序编号(229-231)，`severity = high/medium`(231) → **跨运行稳定**。
- 写 `pipeline/.kb_adaptretrieve_proposals.jsonl`（全量重写，236-238）。

### 7.3 「契约」的落地情况：**写入后无人消费（L1 回路断开）**

- `apply`(256-309) 把选中项并入 `pipeline/.kb_retrieval_adjust.json` 的 `{"adjustments":[…],"updated":…}`(295-296)，并 force-add 做 git checkpoint(297-298)。
- 全仓库检索该文件名的**所有读者**（`grep -rn "retrieval_adjust" --include=*.py --include=*.json`，排除 `.agents`）：
  - `kb_adaptretrieve.py:39/258`（自己写）
  - `kb_l4.py:70`（`_L1_LEDGER`，仅用于 `docket` 统计）
  - `kb_l5.py:68`（`_L5_ACTIVITY`，仅用于判断该 rung「本 cycle 是否动作过」）
  - `template-vault/.gitignore:52`
- **`rag.py` / `rerank.py` / `recall_schedule.py` / 任何检索入口都不读它。** `suggested="boost"` 这个动作**没有任何执行者**。
- 因此 L1 的真实闭环是：`真实查询 → 记录 → 挖缺口 → 写提议 →（人工）写契约 → **dead end**`。检索行为不会因此改变；「把真实的答不上信号喂回检索」这一使命**当前未实现**。
- 注：与之对照，L0(`kb_retriage`) 写的是 `tags/kb_summary`，重建索引后**确实**改变检索打分（`rag.py:375-376` 的 `kb_summary_token` 倒排）——L1 缺少这一环。

---

## 八、问题清单（`文件:行号` + 类型 + 影响 + 建议）

| # | 位置 | 类型 | 影响 | 建议 |
|---|---|---|---|---|
| D-1 | `rag.py:748-757`（键不含 `as_json`）× `rag.py:763-766`（`entry["result"]`/`entry["text"]`）| **bug（可复现崩溃）** | 先用文本模式查一次、5 分钟内再用 `--json` 查同一问题 → `KeyError: 'result'` 直接 traceback（实测）：`File "rag.py", line 764 … KeyError: 'result'`。反向（先 json 后文本）打印 `None` | 把 `as_json` 计入 cache_key，或读写两侧统一存/取 `{result,text,paths}` 并用 `.get()` 兜底 |
| D-2 | `kb_query.py:286`（`open(_prop_path(root),"w").close()`）+ `kb_query.py:281-284`（`skipped` 只统计不保留）| **bug（数据丢失）** | `kb_query.py apply --id X` 会**清空整个提案文件**，未选中的提案被无声删除，而返回值却写「剩余 N 条待审」（294）。实测：3 条提案 → `apply --id 001` → 文件 0 行，`skipped=['…002','…003']` | 只重写未写入的 `skipped` 列表（或删除已写入行）；并把 `skipped` 语义与消息对齐 |
| D-3 | `kb_query.py:166-169` `_next_id` = 提案文件行数+1 | **缓存/标识失效** | 配合 D-2，文件被清空后同日 id 从 `q-YYYY-MM-DD-001` 重新开始 → **id 复用**，历史提案与账本对不上 | 用时间戳/uuid，或从 state 里维护单调计数 |
| D-4 | `rag.py:358` + `rag.py:812-816` vs `rag.py:393-403` | **数值问题（量纲冲突）** | 倒排加成 `0.15*hit_count`（实测最高 **1.83**）比 RRF 融合分上限 `2/61≈0.0328` 高 **2 个数量级** → 最终排序 98% 由「单字 token 与 kb_summary 的重复计数」决定，**BM25+RRF 形同装饰**；且单字 CJK unigram（如 `与`→7 篇、`标`→8 篇）无 IDF 加权 | 把加成改为「乘性微调 + IDF 加权」（如 `s *= 1+0.05*Σidf(tok)`），或先把 RRF 分数归一到 [0,1] 再叠 |
| D-5 | `rag.py:485-491` `_parse_tags`（不含 `[]` 剥离）+ `rag.py:494-498` `_has_any_tag` | **bug（静默过滤失效）** | 列表式 `tags: [sop, metric, governance]` 被切成 `['[sop','metric','governance]']` → `--tags governance` **静默返回空**（实测 `[]`）；`--tags sop` 只因另一篇写成裸值才命中。`_build_hit` 输出的 `tags` 字段同样是脏的（实测 `["[sop","metric","governance]"]`） | 复用 `rerank.py:45` 的 `strip("[]")` 写法，或统一按 `kb_common` 风格解析 |
| D-6 | 索引只比 `version`：`rag.py:784-789`；`load_meta` 兜底 `kb_common.py:94-97` | **静默失败** | 索引过期（笔记被删/改名）时 `kb query` 仍把幽灵文档当 #1 返回，`snippet=""`、`domain="-"`、**退出码 0、零警告**（实测）。若同时 `--answer`，`read_text` 抛错被 `except OSError`(1140) 捕获并误报为「连接 ORNITH 端点失败」 | 查询期对命中 rel 做存在性检查并剔除/告警；或比对 `doc_hashes` 抽样自检；`--answer` 的错误分支区分「文件缺失」与「网络失败」 |
| D-7 | `rag.py:249`（mtime 快路径在 `load_meta` 之后）+ `rag.py:309`/`361-366`（倒排重建全库读） | **性能（假增量）** | 「增量」索引实测仍读 **35** 个文件；改 1 个文件读 **65** 个（≈2× 全库）；全量与增量的磁盘 IO 基本相同 | 先 `stat()` 再做 mtime 比较（把 249 挪到 256 之后）；`_build_inverted_index` 复用 `meta_all` 而非再读文件；倒排只对 `changed` 做增删 |
| D-8 | `rag.py:921`（JSON 缓存结果里不含 answer）+ `rag.py:754` + `rag.py:956-963` | **静默失败/开关不一致** | `--answer --json` 的 LLM 答案被丢弃，却占用独立缓存键；`--context-budget` 在文本模式下被忽略(922-923) | JSON 输出也走 `llm_answer` 或显式报「--answer 仅文本模式生效」；非 JSON 模式支持 budget 或明确拒绝 |
| D-9 | `rag.py:762`（TTL 300s）+ `rag.py:930-933`/`950-953`（只 `update` 不清理） | **缓存失效/无界增长** | `query_cache.json` 过期条目永不删除，条目含整份 JSON 结果 → 文件只增不减。实测：7 条中 6 条 `ts` 距今约 183 天（>1.5e6 秒），仍在文件里 | 写入时顺手剔除 `ts` 超 TTL 的条目；限制条目上限/文件大小 |
| D-10 | `rag.py:717-731`（`agent_hits` 无清理）+ `kb_usage.py:303-321`（O(n²) 配对） | **性能/无界状态** | 每次查询新增一条事件（745-746 强制生成 session），`feedback_state.json` 单调增长；kb_usage 对事件做双重循环配对 | 按时间/条数裁剪（如保留最近 2000 条）；配对改为按归一化 query 建哈希桶 |
| D-11 | `rag.py:318-321`（`vocab` 形参未用）+ `rag.py:790` + 索引写 `vocab`(227) | **死数据/性能** | `vocab` 是全库 token 排序列表，索引里最大的一块之一（30 篇即 3819 项），加载后被完全忽略；`idf` 的键已等价于 vocab | 删字段或延迟加载；最小修复：`_vec(counter, idf)` |
| D-12 | `rag.py:217/219/218`（`importance`/`updated_ts`/`ctype` 写入）+ 无任何读点 | **死字段/能力缺口** | 检索排序**完全不看 importance 与时间**（旧的新鲜度信号只在 `--exclude-stale` 硬过滤与启发式重排里），与「importance 参与排序」的文档预期不符 | 要么删字段，要么把 `1+α*importance`、`exp(-age/τ)` 接进打分（并同步更新文档） |
| D-13 | `rag.py:124-135` `_title_text` 的 `for t in (fm.get("tags") or [])` | **bug（信号损坏）** | 当 `tags` 是字符串（`kb_common.parse_frontmatter` **只会**产出字符串，`kb_common.py:83-85`）时按**字符**迭代 → 索引里 `title_text` 变成 `"…m o c ,   g u i d e ,   s y s t e m"`（实测）。因 `q_tokens` 过滤了单字(619)，实际把「tags 命中」这一路信号**整条废掉** | `_parse_tags(fm.get("tags"))` 后再 join |
| D-14 | `rag.py:166-172` → `kb_common.parent_of(rel, fm)`（`kb_common.py:175` 用 `Path(__file__).parents[1]`）| **bug（多库错位）** | `--root` 指向别的库时，父块判定仍拿**代码所在库**当基准 → `parent`/`root` 字段错误或为空（`_parent_block` 因而静默不回填） | 显式传 root：`parent_of(rel, fm, root)` / `root_of(rel, fm, root)` |
| D-15 | `rag.py:597-598` | **死代码/隐患** | `return {...} if False else {...}`：`if False` 分支里调用**未定义**的 `best_body`；同时真正返回的 `_read_head`(606-610) 直接 `read_text()[:900]`，**把 frontmatter 也当父块正文**返回 | 删死分支；父块正文用 `load_meta` 后的 body（与本函数 575 行一致） |
| D-16 | `rag.py:1042`(SYN/LLM 扩展) vs `rag.py:810/827`（未扩展） | **一致性问题** | 查询扩展只影响余弦与 BM25 两路；倒排加成与标题加权用原始 query → 扩展开启时各路「看到的问题」不同 | 统一传入 `q_expanded` 或明确注释差异为设计 |
| D-17 | `rag.py:880-899` 默认关（注释 874-879 记录实测负增益）| **默认开关（合理但需文档）** | `KB_RERANK` 默认 `0`、`KB_TITLE_BOOST` 默认 `0`、`KB_SYNONYM_EXPAND` 默认 `0`、`KB_QUERY_EXPAND` 默认空（`rag.py:826/880/1015/1019`）；`kb_rsi` 侧 `USE_EMBEDDING` 默认关（`kb_rsi.py:75-83`）——**该库的「重排/扩展/语义」几乎全部默认关闭**，README/文档若承诺这些能力会与默认行为不符 | 在用户文档中显式列出「默认关闭」清单与开启方法（环境变量名） |
| D-18 | `kb_adaptretrieve.py:39/287-289` 的 `suggested="boost"` | **功能未闭环（最高优先级）** | 「检索修正契约」写入后**无任何消费者**（实测 grep：只有 kb_l4:70 / kb_l5:68 当账本计数）→ L1 的检索纠错**永不影响检索**，自治回路的最后一跳是断的 | 在检索路径加一个小型消费点（如 `query→note` 命中即给 `+boost` 或加 token），或明确把该文件降级为「审计清单」并修正文档 |
| D-19 | `kb_adaptretrieve.py:127` `events[:_MAX_EVENTS]`（`_MAX_EVENTS=500`）| **bug（有界方向反了）** | `agent_hits` 是插入序（≈时间序）dict，取前 500 = **最老的 500 条**；事件超过 500 后，**最新查询的信号被永久忽略** | 反转为 `events[-_MAX_EVENTS:]`（或先按 `ts` 排序再取尾） |
| D-20 | `kb_adaptretrieve.py:221-225`（达 `max_proposals` 就 `break` 外层事件循环）| **偏差** | 一旦凑满 50 条，剩余（更新的）事件整批不分析 → 命题集合永远偏向最早的事件 | 改为按严重度/时间做优先筛选后再截断，或允许「每事件 1 条」 |
| D-21 | `kb_adaptretrieve.py:55-66` `_cosine` 无调用点；`kb_fitness.py:49-55` `_shannon_norm` 无调用点；`rag.py:329` `HYBRID_ALPHA` 无使用；`kb_common.layer_multiplier` 被 `rag.py:16` 导入但未用 | **死代码** | 维护成本与误读风险（如 fitness docstring 说 Shannon，实现是 Simpson） | 删除或标注 deprecated |
| D-22 | `memory_sync.py:96`（`content_fingerprint(p.read_text())` 整文件）比对 `rag.py:221` 写入的 `content_fingerprint(body)`；判据在 `memory_sync.py:99` | **bug（缓存/一致性误判）** | 两者**永不相等** → `memory_sync.load_index` 每次都判定「有变更」并调用 `rag.cmd_index`。实测：该函数内部一次调用触发 **38 次** `read_text`（本可 0 次） | 统一指纹口径（建议都用 body，`content_fingerprint(body)`） |
| D-23 | `rag.py:778-783`「索引不存在」也可由**损坏**触发（`load_index` 43-44 返回 `(None,0)`）| **静默失败/措辞误导** | 索引损坏时提示「先跑 rag.py index」尚可自愈，但用户看不到「损坏」这个事实（无日志区分） | 区分「不存在」/「JSON 解析失败」并分别提示 |
| D-24 | `kb_fitness.py:96-98`（无数据→1.0）、`kb_contradiction.py:282`（0 矛盾→1.0）| **数值/激励（Goodhart 残留）** | fitness 有 **0.44 的白送地板**（实测空库 0.460）；「不测量」永远优于「测量后被发现有问题」 | 未测量分量标 `None` 并从权重里剔除（重新归一化），或下调 `W_CONTRAD`/`W_QUERY` |
| D-25 | `rag.py:745-746` + `kb_eval.py:92-113` | **信号污染（自指测量）** | 评测/探针（`kb eval run`、`llm_rerank try` 的 220 行 subprocess）跑 `rag.py query` 会写真实使用事件；1 小时内重跑同一组黄金题 → `requery_rate→1`、`query_success_proxy→0` → fitness 掉 0.20。实测副本已呈现 `requery_rate 0.52` | 给评测/非用户调用加显式标记（如 `--no-record`/`KB_NO_FEEDBACK=1`）并在 kb_usage 里排除 |
| D-26 | `recall_schedule.py:130-140`（`retrievable` O(N²)）+ `143-176`（每篇一次全库比对）| **性能（O(N²)）** | `build()` 对每篇笔记做一次全库 top-1 判定；`mark` 还会为了取 deck 再跑一次 `build`（212）。实测 35 篇 7.5 ms，外推 1000 篇 ≈ 秒级、10k 篇 ≈ 分钟级 | 预计算矩阵/只对候选做（倒排取候选），或把「检索浮层」降级为抽样（如仅前若干 deck 项） |
| D-27 | `recall_schedule.py:256/265` | **副作用/写盘** | `deck`/`status` 看起来是只读命令，却把 `recall_deck.md`/`recall_schedule.md` 写到 vault 根；`--n` > 20 被 `DAILY_CAP` 静默截断(175)，用户无从得知 | 增加 `--dry-run`/`--out`；超上限时给出提示 |
| D-28 | `recall_schedule.py:51` `ease = max(1.3, min(2.5, ease))` 且 `EASE_DEFAULT=2.5` | **数值（上限即默认）** | ease **只能下降、永不超过 2.5**，`easy` 评分形同虚设；标准 SM-2 无上限 | 上限提到 2.8~3.0（或明确记录这是有意的收敛设计） |
| D-29 | `recall_schedule.py:213` `rels.split(",")` 无 `strip()` | **小 bug** | `--recalls " a.md, b.md"` 带空格会去找带空格的路径 → 该条静默未命中（`state` 里凭空多一条键） | `[r.strip() for r in rels.split(",") if r.strip()]` |
| D-30 | `kb_selfweight.py:104`（`kb_rsi.root = root` 全局副作用）+ `kb_rsi.py:190-192`（重复 `kb_rsi.metrics` 在其它模块未传 root） | **耦合/告警噪声** | 多库/并发场景下模块级 `root` 会被互相覆盖；`metrics` 会打 `DeprecationWarning` | 全面改为显式传 root |
| D-31 | `kb_fitness.py:231`/`kb_selfweight.py:231`(相对归一) 见 6.1 | **数值（相对归一）** | 最强候选恒定拿满额 `BUMP_CAP`，与绝对信号强度脱钩 | 乘绝对系数或加绝对门槛（见 D-x in 6.1） |
| D-32 | `template-vault/.gitignore:34-36` 注释称 `feedback_state.json` 已纳入 git 版本控制，但根 `.gitignore:35` 有 `/**/.kb/` | **文档/配置矛盾** | `git check-ignore -v` 实测：`.kb/state/feedback_state.json` 与 `query_cache.json` 均被 `/**/.kb/` 忽略 → 关键状态**没有版本历史**，与注释及「可 diff/回滚」的设计意图相反 | 修正注释或加 `!template-vault/.kb/state/feedback_state.json` 白名单 |
| D-33 | `reference/synonyms.json`（539 行）+ `rag.py:1015` | **默认关/资产闲置** | 88 条同义词表默认完全不生效（仅 `KB_SYNONYM_EXPAND=1` 时读），且实测注释记录为「轻微负增益」 | 定位其价值（如只用于 L0 缺口修补/检索缺口诊断）或删除以免误导 |

---

## 九、性能关键点（查询路径的时间复杂度与磁盘 IO）

### 9.1 单次 `kb query` 的实测成本（副本，30 篇 / 12,617 tokens / 索引 304,891 B）

| 阶段 | 行号 | 实测 | 复杂度 |
|---|---|---|---|
| 三次进程启动（bash→launcher→rag）| `kb:60`, `_subprocess_plugin.py:69` | ~40 ms（`real 0m0.045s` 含全部） | 常数 |
| `json.load` 整份索引 | `rag.py:42`/`777` | **2.89 ms** | O(索引字节) |
| `rel2vec` 全库重建 | `rag.py:791` | **1.26 ms** | O(全库 token) ≈ **100 ms/1M tokens** |
| `_bm25_scores`（df 现算 + 全库打分）| `rag.py:334-351` | **0.95 ms** | O(全库 token + N·|q|) ≈ **76 ms/1M tokens** |
| 余弦全库比对 | `rag.py:795` | **0.21 ms** | O(N·|q|) |
| 倒排 lookup | `rag.py:810` | 可忽略 | O(|q|·命中) |
| 命中文件读取（输出+记录）| `rag.py:915/940/969` | 每命中 2–3 次全文件读 | O(top·文件大小) |
| 可选：`--exclude-stale`/过滤器 | `rag.py:840`/`506` | 每候选 1 次全文件读 | O(候选) |
| 可选：语义重排 | `rag.py:884-897` | 网络 + 最多 60 次 `load_meta`(886) | O(top_n·文件) |
| 可选：`--answer`（LLM）| `rag.py:1132-1138` | 本地 chat 串行 **10–40 s** | 常数×网络 |

### 9.2 结论与可优化处（按收益排序）

1. **每次查询重建全库向量与 BM25 统计（`rag.py:791`、`334-337`）** —— 这是唯一的**超线性放大点**。索引里已经存了 `tf`（词频）与 `idf`，但每次查询都要重新做「乘 idf + L2 归一化 + 建 df + 算 avgdl」。外推：10k 篇 × ~420 tokens ≈ 4.2M tokens → 仅这两项 ≈ **0.7 s/查询**；且每个查询都是新进程，**进程内缓存不可能生效**。建议：把 idf 加权并归一化后的稀疏向量（或至少 `df`/`avgdl`/`dl`）**预计算并写进索引**（`IDX_VERSION` 递增即可），查询期退化为纯点积。
2. **索引整份 JSON 装载（`rag.py:42`）** —— 索引体积 ≈ 24 B/token（实测 304,891 B / 12,617 tokens），10k 篇约 **~96 MB**，`json.load` 单次即 1–2 s、数百 MB 内存；而查询只需要 `idf` + `tf` + `meta` 的一小部分。建议：拆分（如 `df_idf.json` + 每文档向量文件）+ 内存映射/按需读，或改用 sqlite（README 的「纯标准库」约束下 sqlite3 可用）。
3. **倒排加成 ≠ 候选预筛（`rag.py:795`、`802`）** —— 两路召回都是**全库扫描**；`inverted_index` 只用来加一点分（还抢了 98% 权重，见 D-4）。建议：用 `inverted_index`（或 tf 的 token 倒排）先取候选，再算分，可把 O(N) 降为 O(候选)。
4. **「增量」索引的假增量（`rag.py:249`、`309`/`366`）** —— 实测无变更也读 35 个文件、改 1 个读 65 个；全量与增量磁盘 IO 同级。建议：mtime 判断提到 `load_meta` 之前；倒排复用 `meta_all`；只对 changed 增删倒排。
5. **命中文件的重复读取（`rag.py:915/940/969`、`575`）** —— 同一命中在一次查询中被读 2–3 次。建议：把已读到的 `(fm, body)` 放入本轮 dict 复用。
6. **`--exclude-stale`/过滤器逐候选读盘（`rag.py:840`/`506`）** —— `meta` 里其实已有 `layer/confidence`，但状态/日期/标签只能现读；建议把 `status`/`review_after`/`updated` 也入索引 meta。
7. **`recall_schedule.build` 的 O(N²)（`recall_schedule.py:130-140`、`170`）** —— 每篇一次全库 top-1 判定；`mark` 还要再 `build` 一次(212)。建议：用倒排取候选或只对 top-K deck 做核对。
8. **`kb_adaptretrieve.propose` 的全量 bigram 化（`kb_adaptretrieve.py:166-176`）** —— 每篇正文建 bigram Counter + 全量倒排（O(全库字符)），且**每次 propose 都重算**；叠加 `_suspect_failed_queries`→`kb_usage.analyze`（O(事件²) 配对）。建议：与 rag 索引共用一次扫描，缓存 bigram 倒排。
9. **`kb_query.query()` 一次调用三遍全库（`kb_query.py:75` search、`120-122` novelty、`106` `_grounded`）** —— `kb_rsi.compiled_notes/collect` 每次都 `rglob` + 逐文件 `read_text`（`kb_rsi.py:161-176`）。建议：一次 collect 复用。
10. **状态文件增长（`rag.py:930`/`717-731`、`kb_usage.py:303`）** —— `query_cache.json` 含整份 JSON 结果且从不清理过期项；`feedback_state.agent_hits` 每次查询 +1 条。当前分别 12.9 KB / 6.2 KB，长期运行会持续膨胀并拖慢每次查询的 `load`/`save`（含 flock 与整文件 JSON 解析）。
11. **`--answer` 的 LLM 调用（`rag.py:1135-1138`，timeout 40 s）** 与 `KB_QUERY_EXPAND=llm`（`llm_rerank.py:187`，BUDGET_S 60 s）是仅有的「秒级」项，均默认关闭，属于可接受的取舍（注释里已量化：+10~20 s；canon@K 91.3%→95.3%）。

---

## 附：本次精读的复现命令（均在副本 `/tmp/kbvault-d` 上，不写原仓库）

```bash
cp -a template-vault /tmp/kbvault-d
cd /tmp/kbvault-d
# 1) 缓存跨模式崩溃（D-1）
rm -f .kb/state/query_cache.json
python3 pipeline/rag.py query "质量门禁与巡检指标" --root $PWD          # 文本 → 写 text-only 缓存
python3 pipeline/rag.py query "质量门禁与巡检指标" --root $PWD --json   # → KeyError: 'result' (rag.py:764)
# 2) tags 过滤静默失效（D-5）
python3 pipeline/rag.py query "质量 指标" --top 3 --root $PWD --tags governance --json   # hits: []
# 3) 幽灵命中（D-6）
rm -f "70-知识治理 Governance/04-质量与指标.md"
python3 pipeline/rag.py query "质量门禁与巡检指标" --top 2 --root $PWD --json   # #1 仍是已删文件
# 4) 假增量（D-7）/ 指纹口径（D-22）：monkeypatch Path.read_text 计数
# 5) fitness 白送地板与信号污染（D-24/D-25）
python3 pipeline/kb_fitness.py --root $PWD
for i in 1 2; do python3 pipeline/rag.py query "知识库备份策略" --root $PWD >/dev/null; done
python3 pipeline/kb_usage.py analyze --root $PWD      # requery_rate 0.52 / query_success_proxy 0.48
# 6) apply 清空提案（D-2）：预置 3 条 → apply --id 001 → 文件 0 行
```

### 未确认项（诚实标注）

- `kb_usage.analyze` 的完整实现（我只读了 266-360 行做再问率/代理的定义），未逐行通读 `kb_usage.py`。
- `kb_l4.py`/`kb_l5.py` 对两份账本的**全部**用法只核到「读取计数」层面（`kb_l4.py:188/203`、`kb_l5.py:68-72`），未读它们的完整逻辑。
- `kb_contradiction.detect` 的阈值与 O(n²) 上限（`MAX_NOTES_CONTRA=400`）只从常量与 `contradiction_zero_score` 读到，未通读该模块。
- 「概念索引」在本仓库**未确认存在**；若指 `docs/concepts.md` 一类人工文档，不属代码检索机制。
- `template-vault/vector index/df_idf.json` 的**运行时**数值（304,891 B / 30 docs）是本次精读时的快照，会随后续 `kb rag index` 变化。
