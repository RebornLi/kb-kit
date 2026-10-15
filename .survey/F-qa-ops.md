# F 组精读报告：质量 / 评测 / 运维 / 反馈

> 范围：`template-vault/pipeline/{kb-healthcheck.py, kb_health.py, kb_audit.py, kb_eval.py, kb_eval_judge.py, kb_usage.py, feedback_loop.py, dashboard.py, backup.py, kb_verify.py}`、`template-vault/scripts/*`、根 `gen_kb_arch.py`，外加 `template-vault/70-知识治理 Governance/04-质量与指标.md`。
> 方式：全部文件用 read 工具逐行读完（大文件分段读到底），结论均标 `文件:行号`；无法确证的一律写「未确认」。
> 只读约束：本报告未修改任何代码；仅执行过只读命令（`kb-healthcheck.py deadlinks/overlong/summary`、`dashboard.check_healthcheck()` 的解析函数、`kb_eval_judge.py run ... judge` 的参数校验、以及对**不存在目录**的 `open()` 探针）。`dashboard.main()`、`kb_audit fix`、`feedback_loop apply` 等写操作**未执行**。
> 运行时环境事实（影响多处结论）：本机 `KB_ROOT=/home/mushan/kb-kit-pure`，且该目录存在（活的 vault），`template-vault/` 是分发的模板；`.codegraph/`、`arch/`、`/**/logs/`、`/**/backups/`、`/**/.kb/` 均在 `.gitignore` 内。

---

## 一、逐文件详解

### 1. `template-vault/pipeline/kb-healthcheck.py`（619 行）

| 项 | 内容 |
|---|---|
| 职责 | SOP-03 健康巡检：9 项检查 + 文本/JSON/HTML 三种消费 + `--log` 落日志。仅检测，不改笔记（`kb-healthcheck.py:20`）|
| 关键函数 | `collect_md(root)` `:71`；`collect_titles(root)` `:80`；`cached_stems(root)` `:104`；`link_path(m)` `:120`；`is_absent(raw_path, stems, titles, root)` `:138`；`check_deadlinks(root)` `:190`；`check_skeletons(root)` `:227`；`check_tags(root)` `:241`；`check_empty(root)` `:277`；`check_frontmatter(root)` `:307`；`check_discipline(root)` `:325`；`check_orphans(root)` `:347`；`check_overlong(root)` `:404`；`check_freshness(root)` `:441`；`run_checks(root, names)` `:479`；`_aggregate(res)` `:493`；`_exit_code(agg)` `:501`；`_render_summary(root, names)` `:505`；`_render_html(summary, out_path)` `:519`；`main()` `:541` |
| 关键常量 | `VAULT_DIRS :31`；`SYSTEM_HINTS :39`；`FRONTMATTER_FIELDS = {tags,status,domain,importance} :42`；`TAG_WHITELIST_SYS :47`；`TAG_WHITELIST_TOPIC :52`（含大量历史自由 tag，实为「白名单漂移」的记账）；`NOISE_TAGS :59`；`REPORT_NAMES :63`（含 `_INDEX.md`）；`NAV_FILES = {_MOC.md,_INDEX.md} :68`；`BODY_LIMIT = 2000 :409`；`CHECKS :471`；`CHECK_ORDER :476` |
| CLI | 位置参数 `check`（`choices=["all","summary","deadlinks","skeletons","tags","empty","frontmatter","discipline","overlong","orphans","freshness"]`，默认 `all`）`:548`；`--root` `:551`；`--log` `:552`；`--checks`（仅 summary 用）`:554`；`--html OUT`（仅 summary 用）`:556` |
| 读写 | 读：`ROOT_DEFAULT`(=env `KB_ROOT` 或 `pipeline/..`) 下所有 `.md`、`tag-taxonomy.json`（经 `kb_common.load_tag_taxonomy :245`）。写：`--log` → `<root>/logs/health-<YYYY-MM-DD>.log`（追加）`:572-573`；`--html OUT` → 覆盖写 OUT `:538` |
| 依赖 | 仅标准库 + `kb_common`（`ROOT_DEFAULT, EXCLUDE, parse_frontmatter, is_source_note, freshness, retrievable_status, is_generated_report, load_tag_taxonomy`）`:27,245` |
| 是否写操作 | 巡检本身只读；两个显式开关（`--log`/`--html`）才写文件 |
| 退出码 | `all`：`total>0 → 1` `:593`；单项：`total>0 → 1` `:608`；`summary`：`summary["exit_code"]` `:599`（= 任一 check `count>0` → 1，`:498,502,507`）；`--root` 不存在 → 3 `:564`；兜底 → 2 `:611` |

### 2. `template-vault/pipeline/kb_health.py`（378 行）

| 项 | 内容 |
|---|---|
| 职责 | 「成长性」六维度量（连接度/时效/新鲜/沉淀/涌现/去重）+ 0-100 分与 3 档标签；输出人读报告或 `--json` 供 dashboard。另有 FR-3.3.14 制度过期检测（**未接线**）|
| 关键函数 | `outbound_links(text)` `:31`；`parse_date(v, root_mtime_default=None)` `:37`；`metrics(root)` `:43`；`datetime_now()` `:178`；`first_line(body)` `:183`；`tags_of(fm)` `:191`；`counter_vec(s)` `:198`；`is_archive(rel)` `:203`；`in_inbox(rel)` `:207`；`is_moc_name(rel)` `:213`；`score_and_alerts(avg_out, orphan_n, orphan_pct, stale_n, stale_pct, backlog, dups, emergence, total=0)` `:218`；`report(m)` `:256`；`check_policy_stale(root)` `:299`；`mark_policy_stale(root)` `:342`；`main()` `:363` |
| 关键常量/阈值 | 去重相似度硬编码 `sc > 0.9` `:115,:134`（**未用** `kb_constants.DUP_SIM_HEALTH=0.90`）；孤儿告警 `orphan_pct>5` `:229`；平均出链 `<1.5 → -20` `:231`；陈旧 `stale_pct>10 → -min(30,int(stale_pct))` `:235`；收件箱 `-min(15,n)` `:241`；去重 `-min(10,n)` `:245`；涌现 `+min(10,n)` `:248`；分数夹取 0..100 `:249`；档位 80/55 `:250-252`；陈旧判据用 `ARCHIVE_DAYS=90` `:23,65`；性能告警 `PERF_WARN_THRESHOLD=400` `:23,225` |
| CLI | `--root` `:365`；`--json` `:366`；无子命令；**恒返回 0** `:374` |
| 读写 | 读：`iter_notes(root)` 全部笔记 + `stat().st_mtime`。写：仅 `mark_policy_stale()`（改 frontmatter 插 `review_needed: true`）`:355-356`，但**无任何 CLI 调用它**（全仓 grep：只被自身 `:344` 调用）→ 死代码 |
| 依赖 | `kb_common`（`tokenize/norm/cos/load_note/iter_notes/build_inverted_index/candidate_pairs/parse_date`）`:22`、`kb_constants` `:23`；`EXCLUDE`/`FM`/`DECAY_DAYS` 导入未使用 |

### 3. `template-vault/pipeline/kb_audit.py`（535 行）

| 项 | 内容 |
|---|---|
| 职责 | 结构化审计（溯源漂移 / 标签簇内聚 / 索引一致性 / 孤儿 / 死链 / 陈旧 / 缺 summary）+ 「确认后修复」（只做安全动作，绝不合并删除）|
| 关键函数 | `all_aliases(root)` `:40`；`all_stems(root)` `:61`；`sample(root)` `:75`；`_tags(fm)` `:103`；`_stem_index(notes)` `:112`；`check_provenance(notes)` `:123`；`check_tag_cohesion(notes)` `:138`；`check_index_consistency(root, notes)` `:169`；`check_structure(root, notes)` `:189`；`cmd_scan(root, as_json, limit)` `:268`；`_link_block(parent, siblings, me)` `:311`；`_safe_fix_actions_orphans(root, notes)` `:322`；`_safe_fix_actions(root, notes)` `:367`；`cmd_fix(root, confirm, only)` `:421`；`cmd_log(root)` `:498`；`main()` `:515` |
| 关键常量 | `STALE_DAYS = 120 :32`；`COHESION_MIN = 0.15 :33`；`COHESION_MIN_N = 5 :34`；`INFERRED_MAX = 0.20 :35`；`AUDIT_DIR = 70-知识治理 Governance/_audit :36`；`PLACEHOLDER` 集合 `:194` |
| CLI | `scan [--root] [--json] [--limit N(默认20)]` `:518-519`；`fix [--root] [--confirm] [--only <kind>]` `:520-521`；`log [--root]` `:522` |
| 读写 | 写：`scan` → `<root>/70-知识治理 Governance/_audit/audit-<今日>.json`（**同日重跑覆盖**）`:279-282`；`fix --confirm` → 改笔记正文/frontmatter `:484`、`git -C root add -- <touched>` `:487`、追加 `_audit/log.md` `:489-493`。`log` 只读 |
| 依赖 | `kb_common`（`iter_notes, load_note_full, tokenize, is_raw_path, is_generated_report, kb_layer_of, is_index_stub, EXCLUDE, parent_of, root_of`）`:29,332`；`git` 二进制（fix）`:487` |
| 是否写操作 | `scan` 只读（但写审计 JSON）；`fix --confirm` 是**改写笔记 + git add** 的写操作 |

### 4. `template-vault/pipeline/kb_eval.py`（424 行）

| 项 | 内容 |
|---|---|
| 职责 | 「RAG 检索质量评估网」：调用真实 `pipeline/rag.py query --json` 做 oracle，算 4 项确定性指标 + 可选 LLM judge，落回归基线。**与 `kb_eval_judge.py` 功能重复，且经 `kb` 不可达**（见 §三、§七）|
| 关键函数 | `_tokenize(s)` `:58`；`_content_tokens(s)` `:63`；`_sentences(text)` `:69`；`load_dataset(root=None)` `:76`；`load_config()` `:83`；`run_query(rag_abs, question, root, top, as_json=True)` `:90`；`ensure_index(rag_abs, root)` `:118`；`_norm_rel(rel)` `:127`；`compute_item(item, rag_abs, root, top)` `:133`；`_llm_judge(item, rag_abs, root, top)` `:187`；`_mean(xs)` `:219`；`run_eval(root, rag_abs, top, limit, judge, strict)` `:224`；`_print_report(rows, agg, strict)` `:287`；`main()` `:372` |
| 关键常量 | `PIPE_DIR :39`；`VAULT_BY_DEFAULT = parents[1]` `:40`；`TEST_DIR = pipeline/test_eval :41`；`DATASET_PATH :42`；`CONFIG_PATH :43`；`_FAITHFUL_SUPPORT = 0.50 :50`；`_STRICT_MIN = {context_recall:0.55, golden_hit:0.50, faithfulness:0.60} :51-55`；`SAMPLE_DATASET`（8 条，schema `kb_eval/1`）`:308-369` |
| CLI | `eval [--root] [--rag PATH] [--top 3] [--limit 0] [--judge on|off] [--strict]` `:375-381`；`dataset [--root]` `:383`；`sample [--dest]` `:386`；`config [--root]` `:389` |
| 读写 | 读：`pipeline/test_eval/eval_dataset.json`、`eval_config.json`（**本仓不存在**，实测 `ls pipeline/test_eval` → No such file）。写：`ensure_index` 会跑 `rag.py index`（**写向量索引**）`:122`；`test_eval/eval_baseline_<YYYYmmdd_HHMMSS>.json` `:269-273` |
| 依赖 | `kb_common`（tokenize）、`kb_claim._chat`（LLM judge）`:45-46`；子进程 `rag.py` |
| 是否写操作 | `eval` 写索引 + 基线；`sample` 写文件；`dataset/config` 只读 |

### 5. `template-vault/pipeline/kb_eval_judge.py`（355 行）

| 项 | 内容 |
|---|---|
| 职责 | RAGAS 式四指标（faithfulness / answer_relevancy / context_precision / context_recall），**本地模型当裁判**；ground truth 走「生成候选 → 人工确认」 |
| 关键函数 | `class Judge` `:45`（`__init__` `:46`、`available()` `:53`、`ask(prompt, max_tokens=1500)` `:59`、`ask_json(prompt, max_tokens=1500)` `:73`）；`retrieve(root, q, top=6)` `:91`；`answer(root, q, hits, judge)` `:103`；`score_faithfulness(judge, ctx, ans)` `:136`；`score_relevancy(judge, q, ans)` `:140`；`score_precision(judge, q, hits)` `:144`；`_precision_at_k(scores)` `:149`；`cmd_run(root, limit, as_json)` `:158`；`_avg(rows, key)` `:208`；`_recall_against_gt(gt, hits)` `:213`；`cmd_gold(root, generate, review)` `:224`；`_extract_points(judge, q, ans)` `:292`；`_load_gold(root)` `:298`；`cmd_compare(root, baseline)` `:314`；`main()` `:334` |
| 关键常量 | `EVAL_DIR = .kb/eval :31`；`GOLD = .kb/eval/gold_set.jsonl :32`；`GOLD_CAND = .kb/eval/gold_candidates.jsonl :33`；`LAST_RUN = .kb/eval/last_run.json :34`；30 题来自 `scripts/eval_retrieval.py` 的 `GOLD`（动态 exec 加载）`:37-41`；裁判默认端点 `http://127.0.0.1:8000/v1`、模型 `ornith1.5-35b` `:49-51` |
| CLI | `run [--root] [--limit N] [--json]` `:337-338`；`gold [--root] [--generate] [--review]` `:339-340`；`compare [--root] [--baseline F]` `:341-342` |
| 读写 | 读：`gold_set.jsonl`、`gold_candidates.jsonl`、`last_run.json`。写：`last_run.json`（每次 run 覆盖）`:198`、`gold_candidates.jsonl` `:241`、`gold_set.jsonl` `:274`；另通过子进程 `rag.py query`（只读）与 vLLM `/chat/completions` |
| 是否写操作 | `gold --generate/--review` 写文件；`run` 写 `last_run.json`；`compare` 只读 |

### 6. `template-vault/pipeline/kb_usage.py`（467 行）

| 项 | 内容 |
|---|---|
| 职责 | Layer 2 真实使用结果捕获：从 `feedback_state.json.agent_hits` 挖「再问率 / 查询成功率代理 / 疑似失败」，可选 LLM 判定；`mark` 是 opt-in 的**写**路径 |
| 关键函数 | `_load_state(root)` `:59`；`_to_epoch(iso)` `:80`；`_events_from_agent_hits(agent_hits)` `:97`；`_vec(text)` `:131`；`_cos(a,b)` `:150`；`_norm(q)` `:161`；`_load_snippet(root, rel, cap=200)` `:178`；`_claim_chat(messages)` `:190`；`_judge_requery(root, prev_query, prev_paths, requery, sim)` `:200`；`_parse_json(s)` `:243`；`_llm_available()` `:263`；`analyze(root, window=3600, sim_threshold=0.55, use_embedding=True, use_llm=True)` `:273`；`report(root)` `:364`；`stats(root)` `:392`；`_already_useful(root)` `:403`；`mark(root, window=3600)` `:413`；`main()` `:444` |
| 关键常量 | `STATE_FILE = feedback_state.json :43,:56`；`_WINDOW_SECONDS = 3600 :46`；`_SIM_THRESHOLD = 0.55 :47`；`_MAX_EVENTS = 200 :48`；`_TS_FMTS :51`；`_LLM_REQUERY_CACHE` 上限 128 `:238` |
| CLI | `analyze|report|stats|mark`，**四个子命令都带** `--root --window --sim` `:448-452`（`report/stats/mark` 实际忽略 `--sim`）|
| 读写 | 读：`.kb/state/feedback_state.json`（StateStore，缺失时降级直读同一路径 `:71`）。写：仅 `mark` → 经 `feedback_loop.record_hit` 改 importance + git commit `:430` |
| 依赖 | `kb_embed`（embedding 可选）`:40`；`state_manager.StateStore` `:62`；`kb_claim._chat` `:194`；被 `kb_fitness.py:92`、`kb_selfweight.py:139`、`kb_meta.py:172` 引用 |

### 7. `template-vault/pipeline/feedback_loop.py`（502 行）

| 项 | 内容 |
|---|---|
| 职责 | 反馈回路：把 RAG 命中当「需求信号」累积，产出 importance 提升建议（`ingest`）或写回 frontmatter（`apply`/`hit`，写前 git checkpoint）|
| 关键函数 | `load_index(root)` `:74`；`hits_for(idx, query, topk)` `:84`；`load_state(root)` `:95`；`save_state(root, st)` `:105`；`ingest(root)` `:115`；`weighted_signal(external_count, internal_hits)` `:156`；`compute_signals(state)` `:168`；`bumps(signals, external_count, internal_hits, root=None)` `:179`；`_char_vec(s)` `:224`；`_rcos(a,b)` `:234`；`_candidate_context(root, rels)` `:240`；`_enhance_with_novelty(root, cand, maxs)` `:253`；`_apply_related_floor(root, cand)` `:270`；`report(hits, sources, suggested, zero_n)` `:292`；`apply_bumps(root)` `:309`；`is_raw_rel(rel)` `:365`；`_is_raw_path(p)` `:374`；`_read(p)` `:379`；`_write_importance(p, new)` `:383`；`record_hit(root, query, hit_path, useful)` `:404`；`_accumulate_importance(root, rel, weight, norm_factor=None)` `:432`；`main()` `:465` |
| 关键常量 | `STATE = ".feedback_state.json" :22`；`IDX_DIR = "vector index" :23`；`EXT_WEIGHT=1.0 / INT_WEIGHT=0.15 / HIT_CAP=5 / HIT_TOPK=5 / DIVERSITY_CAP=8 / DIVERSITY_FLOOR=0.05 / HIT_DECAY=0.6` `:33-39`；`NOVELTY_BONUS=0.10 / NOVELY_DENSITY_CAP=0.60 / NOVELTY_SIM=0.55 / NOVELTY_FLOOR=0.06` `:47-50`；`HIT_WEIGHTS` `:58-64`；`EXPLICIT_NORM_FACTOR=0.05 :66`；`MIN_HITS=3 / BUMP_CAP=0.30 / MIN_BUMP=0.05`（`kb_constants.py:18-20`）|
| CLI | `ingest [--root]` `:468`；`apply [--root]` `:469`；`hit --query Q --hit PATH --useful true|false [--root]` `:471-475` |
| 读写 | 读：`vector index/df_idf.json` `:75`、state、笔记。写：`ingest` → `<root>/feedback_hits.md` `:483` + state + `evolution_log` `:148`；`apply` → 改笔记 importance `:338`、git add/commit `:348-353`、state；`hit` → state + importance + 两次 git commit `:455-461` |
| 是否写操作 | `ingest` 写报告与状态；`apply`/`hit` 写笔记并提交 git（**写操作**）|

### 8. `template-vault/pipeline/dashboard.py`（243 行）

| 项 | 内容 |
|---|---|
| 职责 | 生成治理仪表盘 `70-知识治理 Governance/_INDEX.md`（告警块 + 概况 + 备份健康 + 成长性六维 + 分布 + 治理入口）|
| 关键函数 | `run_validate(root)` `:15`；`check_backup(root, daily_dir="backups/daily")` `:23`；`check_pipeline(root)` `:42`；`check_growth(root)` `:57`；`check_healthcheck(root)` `:66`；`check_freshness(root)` `:82`；`main()` `:101` |
| 关键常量/阈值 | 备份 `<30h` 视为新鲜 `:38`；成长性告警只在 `g_score < 55` 时展开 `:131`；sha 校验用 `sha256sum -c` `:32` |
| CLI | `--root` `:103`；`--out`（默认 `<root>/70-知识治理 Governance/_INDEX.md`）`:104,235` |
| 读写 | 读：跑 `validate.py --json`、`kb_health.py --json`、`kb-healthcheck.py deadlinks|overlong`、`git log --grep=^kb: sync`、`backups/daily/*-vault.zip` + `.sha256`、`70-知识治理/_review/*.md`。写：覆盖 `_INDEX.md` `:236-237` |
| 依赖 | `kb_common`（iter_notes/load_meta/freshness/retrievable_status/is_source_note/is_generated_report）`:84`；`git`、`sha256sum`、`validate.py`、`kb_health.py`、`kb-healthcheck.py` |
| 是否写操作 | 是（写仪表盘）。且它**间接调用了只读的其它工具**，本身不改笔记 |

### 9. `template-vault/pipeline/backup.py`（83 行）

| 项 | 内容 |
|---|---|
| 职责 | 跨平台全量快照（zip + `.sha256`），替代 bash-only 的 `backup_now.sh` |
| 关键函数 | `make_backup(vault, kind="daily")` `:30`；`main()` `:68` |
| 关键常量 | `EXCLUDE_DIRS = {".git","backups"} :26`；`KINDS = ("daily","weekly") :27` |
| CLI | `--root`（默认 `KB_ROOT` 或脚本上级）`:70`；`--kind daily|weekly` `:73`；位置参数 `kind`（兼容旧用法）`:75` |
| 读写 | 写：`backups/<kind>/<YYYY-MM-DD>-vault.zip` + `.zip.sha256`（标准 `hash  文件名` 两空格）`:41-61` |
| 依赖 | 纯标准库（argparse/hashlib/os/sys/zipfile/datetime/pathlib）|
| 是否写操作 | 是（备份文件）|

### 10. `template-vault/pipeline/kb_verify.py`（185 行）

| 项 | 内容 |
|---|---|
| 职责 | 一行验收：契约 / 校验 / 巡检 / 结晶探针 / 单测 五项 PASS-FAIL 汇总，退出码 0/1；`--with-index` 加检索质量 |
| 关键函数 | `_find_tests_dir(root)` `:42`；`_run(argv, cwd, timeout)` `:52`；`_parse_schema(res, limit=4)` `:64`；`_parse_validate(res)` `:74`；`_parse_healthcheck(res)` `:87`；`_parse_probe(res)` `:99`；`_parse_unittest(res)` `:108`；`main()` `:122` |
| 关键常量 | `CHECKS` `:30-39`（`kb schema check`／`kb validate`／`kb healthcheck summary`／`scripts/verify_curate.py`／`unittest discover`）；检索质量阈值 `canon >= 0.85` `:163` |
| CLI | `--root` `:124`；`--json` `:125`；`--with-index` `:126`；`--timeout 600` `:127` |
| 读写 | 只读；但要求 `kb` 在 PATH `:32-34`，`--with-index` 走相对路径 `scripts/eval_retrieval.py`（cwd=root）`:157` |
| 依赖 | `kb_common.ROOT_DEFAULT`；外部命令 `kb`、`python -m unittest`、`scripts/verify_curate.py`、`scripts/eval_retrieval.py` |
| 是否写操作 | 否 |

### 11. `template-vault/scripts/kb-curate.sh`（105 行）

- 职责：知识结晶空闲批处理（三重闸门：`CURATE_ENABLE` / `ORNITH_API_KEY` / 1 分钟负载 < `CURATE_MAX_LOAD`），跑完重建索引并出简报（`:3-24`）。
- 关键点：默认 `VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"` `:28`；`CURATE_ENABLE` 默认 **1**（本脚本）`:30`；`CURATE_LIMIT=12 :31`；`CURATE_SECONDS=3600 :32`；`CURATE_MAX_LOAD=4.0 :33`；负载取自 `/proc/loadavg`（失败回退 0）`:50`；`timeout $((CURATE_SECONDS+300)) python3 curate.py run --apply --limit --max-seconds` `:68-70`；`WROTE` 由 grep `canon-written` 统计 `:73`；`FAILS` 统计 ⚠️/error `:74`；**退出码始终 0** `:105`；`note_problem()` 汇总 ❌/⚠️ `:38-40`。
- CLI：`kb-curate.sh [vault_root]`；环境变量 `KB_ROOT/CURATE_ENABLE/CURATE_LIMIT/CURATE_SECONDS/CURATE_MAX_LOAD/ORNITH_*`。
- 读写：写 `logs`? 否（用 mktemp 临时目录 `:36`）；间接写：`curate.py run --apply` 改笔记、`rag.py index` 写索引；读 `curate.py report --json`。
- 依赖：bash、`cut`、`awk`、`timeout`、`grep`、python3、`/proc/loadavg`。**是否写操作**：是（经 curate.py）。

### 12. `template-vault/scripts/kb-daily-supervisor.sh`（183 行）

- 职责：每日「维护(growth_cron) + 巡检(healthcheck_cron) + 可用性自检」，输出可投微信的简报（`:3-22`）；**退出码始终 0**（`:183`，理由见 `:17-19`）。
- 关键点：`VAULT` 默认硬编码 `/home/mushan/kb-kit-pure` `:26`；`EMBED_URL=http://127.0.0.1:8081/health :27`；`QUERY_TIMEOUT=30/GROWTH_TIMEOUT=900/HC_TIMEOUT=300` `:28-30`；`note_problem` `:38-43`；growth 摘要解析 `节奏汇总` `:65-67`；只把 `RC=124` 当超时 `:68`；巡检数字用 sed 从 `logs/health-<date>.log` 抽取 `:88-107`；死链绝对量告警 `>20` `:110`；标签越界 `>0` 告警 `:113`；嵌入服务探测**重试 3 次**（`:129-138`，附 2026-10-01 假警报复盘）；实跑 `kb query '知识库'` `:147`；索引新鲜度用 `stat -c %Y`，>48h 告警 `:158-165`。
- CLI：`kb-daily-supervisor.sh [vault_root]`；环境变量 `KB_ROOT/EMBED_URL/QUERY_TIMEOUT/GROWTH_TIMEOUT/HC_TIMEOUT`。
- 读写：写 mktemp 临时目录；间接写：growth_cron 全流水线。`curl`、`stat -c`、`bash -lc`。**是否写操作**：是（间接）。

### 13. `template-vault/scripts/growth_cron.sh`（145 行）

- 职责：成长引擎定时节拍（`rag index → intake review → feedback ingest → recall deck → link suggestions → dashboard → 记忆写入侧 → 注册表摄取 → memory promote → (可选) curate`），单步隔离（`set -uo pipefail` 不设 `-e`，`:18`）。
- 关键点：`VAULT` 必填（空则 exit 2）`:19-23`；`LOGDIR=$VAULT/logs`、`LOG=logs/growth-<date>.log` `:29-30`；`step()` 用 `PIPESTATUS[0]` 取真实退出码 `:34-44`；每步 `tee -a "$LOG" | tail -6` `:37`；dashboard 有变化才 `git add -- <_INDEX.md>` 并 commit `:64-74`；记忆写入侧需 `AGENT_MEMORY` `:82-89`；注册表摄取 `kb-agent.json` `:97-104`；`memory_sync promote --apply --semantic` `:110`；内联结晶闸门（这里 `CURATE_ENABLE` 默认 **0**、`CURATE_LIMIT=8`、`CURATE_SECONDS=1800`）`:119-136`；日志保留 30 天 `:140`；**有失败则退出非 0** `:145`。
- CLI：`growth_cron.sh <vault_root>`；环境变量 `PYTHON/AGENT_ROOT/AGENT_MEMORY/CURATE_*`。
- 读写：写 `logs/growth-<date>.log`、`<_INDEX.md>`（git commit）、经各步写笔记/索引/状态。**是否写操作**：是。

### 14. `template-vault/scripts/healthcheck_cron.sh`（25 行）

- 职责：跑 `kb-healthcheck.py all` 并把输出重定向到 `logs/health-<date>.log`（`:3-8`）。
- 关键点：`VAULT` 必填 `:11-15`；`python3 "$HC" all > "$LOG" 2>&1` `:19`（**用 `>` 覆盖当天日志**，且**未传 `--root`**）；`RC` 只写进日志 `:21-23`；末尾 `tail -4` `:25` → **脚本退出码恒为 0**（tail 的状态），且 `set -uo pipefail` 无 `-e`。
- CLI：`healthcheck_cron.sh <vault_root>`。读写：写日志。**是否写操作**：写日志。

### 15. `template-vault/scripts/kb-verify-cron.sh`（20 行）

- 职责：定期验收，失败时打印失败项（`:1-2`）。
- 关键点：`VAULT` 默认硬编码 `/home/mushan/kb-kit-pure` `:3`；`timeout 900 kb verify --json` `:4`（依赖 PATH 里的 `kb`）；失败分支用 `python3 -c` 解析 JSON 并打印未过项 `:8-16`，异常分支 `print(sys.stdin.read()[:800])` 但 stdin 已被 `json.load` 读尽 `:15`；**成功时仍打印 `OK ... 五项全绿`** `:20`（与 `:2` 注释「成功静默」矛盾）。
- 读写：只读（含子进程 unittest，会写 `__pycache__`）。退出码：失败 1，成功 0。

### 16. `template-vault/scripts/backup_now.sh`（25 行）

- 职责：兼容包装，转调 `pipeline/backup.py`（`:3-8`）。
- 关键点：`VAULT="${1:-$SELF_DIR/..}"`、`KIND="${2:-daily}"` `:13-14`；探测 `python3|python|py` `:17-20`；找不到解释器 exit 3 `:22`；`exec` 转调 `:25`。**是否写操作**：是。

### 17. `template-vault/scripts/drill_now.sh`（67 行）

- 职责：恢复演练（抽最近备份 → sha256 校验 → 解压还原 → 计数抽查 → git 回滚能力核验 → 写演练记录）。
- 关键点：`DST="$VAULT/70-知识治理 Governance/audit"` `:11`（注意与 kb_audit 的 `_audit` 不同目录）；`mapfile -t ZS < <(ls -t backups/daily/*-vault.zip)` `:14`；`sha256sum -c` `:22`；python 内联解压 `:25-28`；`.md` 计数 `:29-30`；回滚核验用 `git rev-list --count HEAD` `:36-42`；SLA ≤600s `:45-46`；写 `audit/rollout-<date>.md` `:48-63`。
- 依赖：bash≥4（mapfile）、`ls -t`、`sha256sum`（GNU）、`find|sort`、python3、git。**是否写操作**：是（写演练记录；并在临时目录解压）。

### 18. `template-vault/scripts/bench_llm_tps.py`（55 行）

- 职责：本地 chat 模型吞吐基准，区分「总输出 tok/s」与「正文 tok/s」并找并发饱和点（`:1-19`）。
- 关键点：`sys.path.insert(0,'pipeline')` `:23`（要求 cwd=vault 根）；`model_config()` 取端点 `:24-26`；请求 `max_tokens=2048` 硬编码 `:41`；并发序列 `(1,2,4,8,16,24)` 硬编码 `:45`；无 argparse（**文档写的 `--max-tokens/--concurrency` 不存在** `:18`）；`completion_tokens_details.reasoning_tokens` 区分思考 `:37`；输出含「总输出吞吐最优」`:55`。
- 依赖：`kb_common.model_config`、vLLM `/chat/completions`。**是否写操作**：否（纯读/压测；仅消耗推理）。

### 19. `template-vault/scripts/eval_retrieval.py`（189 行）

- 职责：检索质量评测（黄金 30 题 + 「现在 / 关分层 / 开重排」三模式对比，只读）。
- 关键函数/常量：`GOLD`（30 题，4 类）`:25-60`；`run_query(root, q, top, no_layer, no_rerank=False)` `:63`（非 0 退出**大声抛错** `:75-77`）；`classify(root, rel, meta)` `:84`；`measure(root, top, no_layer, no_rerank=False)` `:91`；`_is_stub_meta(meta_all, rel)` `:143`（**恒 False 的占位**）；`main()` `:147`。指标名：`canon_share / stub_share / raw_share / page_share / avg_body_chars / avg_snippet_chars / top1_canon_rate / top3_canon_rate`（`:128-140`）。
- CLI：`--root` `:149`；`--top 10` `:150`；`--json` `:151`。
- 读写：读 `vector index/df_idf.json` `:92`；跑 `rag.py query --json --context 400 --exclude-stale` `:64-74`。无持久化（结果只到 stdout）。**是否写操作**：否。

### 20. `template-vault/scripts/verify_curate.py`（168 行）

- 职责：结晶层端到端验收探针（P0 止血 / P1 骨架 / P2 写回溯源调度 / P3 人工闸门），只读（`:1-12`）。
- 关键函数：`check(name, ok, detail, warn=False)` `:32`；采集段 `:37-61`；P0 断言 `:64-67`；P1 断言（正典≥20、覆盖率≥1%、压缩比 ≥90% 落在 `[4%,120%]`）`:71-101`；P2 断言（`raw/_curated` 数 ≥ canon、lineage 条数 ≥ canon、调度脚本存在、追溯闭环 100%）`:106-122`；P3 断言（`kb_l4 docket` 含 `lc`、`def l4_frozen`、`def mark_evidence_verdict` 存在，**用字符串匹配源码**）`:125-135`；汇总与退出码 `:138-153`；`_raw_ok(root, sid)` `:156`。
- CLI：`--root` `:22`；`--json` `:23`。写：无。退出码：有 FAIL → 1。
- 依赖：`kb_common`、子进程 `kb_l4.py docket`、`kb_raw.py show --json`（每个 canon 篇一次，`_RAW_OK` 逐篇调用 → 30 题级成本）。常量 `EX :16` **未被使用**。

### 21. 根 `gen_kb_arch.py`（160 行）

- 职责：生成 draw.io(mxGraph) 原生 XML 架构图（`:1-2`）。
- 关键函数：`nx()` `:10`；`box(x,y,w,h,style,label,parent="1")` `:15`；`rounded(...)` `:26`；`layer_bg(...)` `:33`；`arrow(src,tgt,style=...)` `:41`；`hline(x,y,w,style=...)` `:50`。
- 常量/硬编码：标题 `:60-62`；6 个层壳 `:68-73`；第 ④ 层标签写死 **「25 个插件」**`:71`（实测 `pipeline/plugins/*.py` 38 个文件 / 排除 `__init__`、`_subprocess` 后 36 个）；右列 vault/成长引擎/kb-context-dsh `:108-139`；页宽高 1500×900 `:149`；输出路径 `<repo>/arch/kb-kit-pure-architecture.drawio` `:157`。
- CLI：**无**（无 argparse，无 `main()`，import 即执行）。
- 产物与依赖：仅标准库 `xml.sax.saxutils`；产物目录 `arch/` 被 `.gitignore` 忽略（`.gitignore` 末段「架构图产物」），且脚本**没有 `mkdir`** → 新克隆运行必然 `FileNotFoundError`（已用等价 `open()` 探针验证：目标目录不存在时报 `FileNotFoundError` 且不创建文件）。**是否写操作**：是（写 drawio）。

---

## 二、指标口径表（healthcheck 每项判据）与文档一致性

### 2.1 九项检查的实际判据

| 检查 | 命中判据（公式） | 阈值/常量 | 代码位置 | 退出码/汇总口径 |
|---|---|---|---|---|
| `deadlinks` | 遍历非系统/非模板/非 raw/非归档/非 `kb-kit`/非生成物的笔记（`NAV_FILES` 例外）里的 `[[...]]`；去噪（`reply_to:`、`双向链接`、`…`、`, `、日期等）；`is_absent()` 判缺失（标题/别名匹配 → 系统目录 → 归一 `../` → `.md` 文件 → 无扩展名 → 目录 → **stem 全域兜底**） | 打印「阈值: <2%」，判据 `rate = 100*dead/max(total,1)`，**仅用于展示** | `:190-224`（`is_absent :138`）| 返回 `len(dead)`；`_render_summary` 用 `threshold: 0, pass = count<=0` `:507`；`all` 模式 `total>0→1` `:593` |
| `skeletons` | `size < 500` **且** `(utcnow - st_ctime) > 7天`，排除系统目录与 `50-模板` | 打印「阈值: 0」 | `:227-238` | 返回个数 → 同上 |
| `tags` | 从 `^tags: [...]` 取逗号项；`t in NOISE_TAGS` → 违规「无意义」；`t ∉ TAG_WHITELIST_SYS ∪ TAG_WHITELIST_TOPIC ∪ tag-taxonomy.json 展开` → 违规「待归入白名单」 | 阈值 0 | `:241-274`（白名单加载 `:245-249`）| 返回违规数 |
| `empty` | 对 9 个 `NN-` 目录 `os.walk`（剪掉以 `.` 开头与 `system_dir_parts`），`not any(sub.iterdir())` → 空目录 | 阈值 0 | `:277-304` | 返回个数 |
| `frontmatter` | **仅当文件以 `---` 开头**时，把 `---` 之间文本做**子串**检查 `tags/status/domain/importance` 4 字段 | 阈值 0 | `:307-322` | 返回缺字段笔记数 |
| `discipline` | 顶层条目非 `.`/`_` 开头、不在 `sys_dirs` 白名单、是目录且不匹配 `^\d{2}-` → 违规 | 阈值 0 | `:325-344` | 返回个数 |
| `orphans` | 知识层笔记（排除系统/`raw`/`memory`/`_curated`/生成物/模板；名含 `首页|索引|INDEX|toc` 跳过；`status ∈ {legacy,archived,draft}` 跳过）中，`无 [[...]] 出链` **且** `stem ∉ 入链集合` | 阈值 0 | `:347-401` | 返回个数 |
| `overlong` | `len(body.replace("\n","")) > 2000`；排除模板/raw/归档/生成物/`NAV_FILES`/`is_source_note`/已分块（`chunk_of`/`chunk`/`-pN|cN|index.md`/存在 `-index.md` 兄弟） | `BODY_LIMIT=2000` | `:404-437` | 返回个数 |
| `freshness` | `kb_common.freshness(fm)["stale"]`（= `review_after <= today` 或 `updated/created` 超过 `DEFAULT_STALE_DAYS=180`）记「陈旧」；`not retrievable_status(fm)`（`status ∉ {active,stable,legacy}`）记「非可检索」 | 阈值 0 | `:441-468`；`kb_common.py:262-289` | 返回 `len(stale)+len(blocked)` |
| 汇总 | `_aggregate`：`total_issues=Σcount`、`checks_failed=count>0 的项数`、`status = red if checks_failed>0 else green` | 每项阈值**硬编码 0** | `:493-516` | `exit_code = 0 if green else 1` `:502` |

补充：`summary` 模式产出 `checks[name] = {count, threshold:0, pass}`（`:507`）、`aggregate`、`exit_code`、`generated_at`（**带本地时区偏移** `:511`）、`vault`（`resolve()` 后的绝对路径 `:512`）。

### 2.2 `04-质量与指标.md` 的宣称 vs 实际实现

| 文档行 | 文档宣称 | 实际实现 | 一致？ |
|---|---|---|---|
| `04-质量与指标.md:22` | 死链率 `kb healthcheck deadlinks` 健康阈值 **< 2%** | 打印 <2%（`kb-healthcheck.py:219`）但**门禁阈值为 0**：`_render_summary :507` 与 `:593`/`:608` 都是「有 1 条死链即 red/exit 1」 | ✗ **不一致**：文档说 1.9% 属健康，代码判失败 |
| `:23` | 标签越界阈值 0 | `:265-268` 违规即计入，阈值 0 | ✓ |
| `:24` | 空目录 0 | `:299-301` | ✓ |
| `:25` | 骨架笔记 0 | `:233` 判据 `<500B 且 >7天`；**但未排除生成物** → live 日志把 `_audit/log.md`、`_graph_kg-c1.md` 计入（`logs/health-2026-10-15.log`）| 阈值一致，口径有噪声 |
| `:26` | frontmatter 缺失 0 | `:313` **只检查「以 `---` 开头」的文件** → 完全无 frontmatter 的笔记被跳过，指标可虚低；字段检查是子串匹配（注释里出现字段名也算有）| ✗ 口径不严 |
| `:27` | 顶层纪律 0 | `:335-340`（`sys_dirs` 白名单放过 raw/docs/pipeline 等）| ✓ |
| `:28` | 超长未分块 0 | `:409-431` `BODY_LIMIT=2000` | ✓ |
| `:29` | 孤儿笔记「无入/出链」0 | `:393-396`（出链或入链任一存在即非孤儿）| ✓（比文档更精确） |
| `:30` | 校验硬错误 `kb validate --strict` 0 | 不在本组文件（`validate.py`）| 未确认 |
| `:31` | 陈旧/不可检索 = `review_after` 到期 或 **非 active/stable** | `kb_common.freshness` 用 180 天 + `review_after`；`retrievable_status` 的集合是 `{active,stable,legacy}`（`:262`，**legacy 也算可检索**）→ 与文档「非 active/stable」不符；而 `check_orphans :391` 又把 legacy 当「已退出检索」跳过 | ✗ 口径自相矛盾（legacy 三处三种处理）|
| `:32` | 近重复「相似度 **≥0.7**」用 `kb clean report` | `kb_health.py:115,134` 用 **>0.9**；`kb_constants.py:11-12` 定义 `DUP_SIM=0.85`（clean/rsi）与 `DUP_SIM_HEALTH=0.90`（**声明给 kb_health 却未被 import，kb_health 直接硬编码 0.9**）| ✗ 三套数值（0.7/0.85/0.9）|
| `:33` | 复习负债「少」 | 无量化阈值（「少」不可作为门禁）| ✗ 不可测 |
| `:37-39` | `kb validate --strict` 硬错误退出码 1；各巡检子命令 >0 退出码 1；`kb dashboard` 汇总告警 | 巡检退出码 ✓（`:608`）；dashboard 汇总 ✓ 但含假警报（见 §七 P1）| 部分 ✓ |

**同一概念「陈旧」的三套阈值**（可直接对比）：

| 用途 | 阈值 | 位置 |
|---|---|---|
| 成长性 stale_pct（`kb health-metrics`）| `ARCHIVE_DAYS = 90`（按 `updated` 或 mtime）| `kb_constants.py:23`，`kb_health.py:23,65` |
| 审计 stale（`kb audit scan`，仅 canon，按 `updated`）| `STALE_DAYS = 120` | `kb_audit.py:32,257` |
| 巡检/仪表盘 freshness（`kb healthcheck freshness`、`kb dashboard`）| `DEFAULT_STALE_DAYS = 180` 或 `review_after` 到期 | `kb_common.py:263,287`；`kb-healthcheck.py:457`；`dashboard.py:94` |

---

## 三、评测体系（kb_eval / kb_eval_judge / eval_retrieval / verify_curate）

### 3.1 四条路线、各自方法

| 工具 | 方法 | 指标名 | gold/题集在哪 | 结果存哪 | 可达性 |
|---|---|---|---|---|---|
| `kb_eval.py` | 调真实 `rag.py query --json` 作 oracle；确定性 token 重叠（`kb_common.tokenize` 同构）+ 可选 LLM judge（`kb_claim._chat`）| `context_recall`（关键术语子串覆盖率 `:154-155`）、`faithfulness`（0.5×句支撑率 + 0.5×token 覆盖率 `:161-172`）、`answer_relevance`（问/答 token Dice `:177-180`）、`golden_hit`（top-k 是否含 `reference_note` `:158`）、`composite`（4 项均值 `:254-259`）| `pipeline/test_eval/eval_dataset.json`（含 `reference_note/reference_answer/key_terms`），不存在时给 8 条 `SAMPLE_DATASET` `:308-369` | `pipeline/test_eval/eval_baseline_<YYYYmmdd_HHMMSS>.json` `:269-273` | **无 CLI 入口**：`kb` 的 `eval` 插件指向 `kb_eval_judge.py`（`plugins/eval_plugin.py:9`）；`kb_eval.py` 在 `tests/test_reachability.py:32` 被声明为「研究层」。**实测 `pipeline/test_eval/` 不存在** → `eval` 直接 `return 2` |
| `kb_eval_judge.py` | RAGAS 式，**本地模型当裁判**（默认 `http://127.0.0.1:8000/v1` + `ornith1.5-35b`）；检索 → 作答 → 三项裁判打分；recall 需人工确认的 ground truth | `faithfulness`（judge JSON `supported/claims` `:180`）、`answer_relevancy`（judge 0-1 `:185`）、`context_precision`（RAGAS 位置加权 `_precision_at_k :149-155`）、`context_recall`（关键点前 12 字子串命中率 `:213-220`）、`canon_share`（命中里非 `raw/` 占比 `:182`）| 题集：**复用 `scripts/eval_retrieval.py` 的 30 题**（动态 exec 加载 `:37-41`）；gold：`.kb/eval/gold_set.jsonl`（人确认）+ `gold_candidates.jsonl`（模型草稿）| `.kb/eval/last_run.json`（每次 run 覆盖）`:198`；gold 文件 `:241,274` | **可达**：`kb eval run/gold/compare` |
| `scripts/eval_retrieval.py` | 同一 30 题跑三模式（`now` / `no-layer(--include-raw --include-stubs)` / `no-rerank(KB_EMBED_RERANK=1)`），按索引 `meta.layer` 分类命中 | `canon_share / stub_share / raw_share / page_share / avg_body_chars / avg_snippet_chars / top1_canon_rate / top3_canon_rate` | 题集内联 `GOLD :25-60`（30 题，**问题全部关于 KB 自身**）| **无持久化**，仅 stdout（`--json`）| 被 `kb_verify.py:157`（`--with-index`）调用，阈值 `canon_share ≥ 0.85` `:163` |
| `scripts/verify_curate.py` | 结晶层端到端断言（不是 RAG 指标，是产物/契约断言）| `PASS/WARN/FAIL` 计数 + 具体断言（正典篇数、覆盖率、压缩比中位与区间占比、归档数、lineage 条数、追溯闭环率）| 无题集；直接扫库 | stdout / `--json` | 被 `kb_verify.py:35` 作为第 ④ 项调用 |

### 3.2 可复现性评估（有证据）

1. **裁判即被测对象**：`kb_eval_judge.Judge` 用的就是本地 `ornith1.5-35b`（`:49-51`），faithfulness/relevancy 由同一模型自评。实测基线（`.kb/eval/baseline-p4.json`）`faithfulness` 取值集合 **只有 `{1.0, None}`** → 该指标无区分度（30 题里 4 题 precision=0，仍拿满 faith）。`docs/p4-report.md` 据此写「**faithfulness 1.0 … 零幻觉**」，属指标虚高。
2. **`context_recall` 实际不可得**：live vault 的 `.kb/eval/` 只有 `baseline-p4.json`、`gold_candidates.jsonl`、`last_run.json`，**没有 `gold_set.jsonl`**；30 题 `context_recall` 全为 `null`（实测统计 30/30）。p4-report 也承认「需要 ground truth」。
3. **裁判模型版本未落盘**：`cmd_run` 写出的 `out` 只有 `ts/n/secs/rows/avg`（`:193-198`），不含 judge model/base_url → 换模型后 `compare` 无法察觉。
4. **无随机种子/无温度记录**：`Judge.ask` 固定 `temperature 0.0` `:61`，但 vLLM 批量推理仍可能非确定；无 seed 字段。
5. **题集自指**：`eval_retrieval.GOLD` 30 题几乎全是「KB 自身机制」（`:25-60`），而语料随结晶/清理变化 → 基线跨时间不可比；p4-report 也把该现象记为「自指问题盲区」。
6. **重复实现带来的口径分裂**：`kb_eval` 与 `kb_eval_judge` 都做 RAGAS 式四指标，但 `context_recall`/`faithfulness` 定义不同（token 子串 vs 裁判 JSON；句支撑 vs 模型自评），两者基线无法互相校验。
7. **文档命令跑不通**：`docs/p4-report.md:83` 与 `:76` 写 `kb eval judge run` / `kb eval judge compare`。实测 `python3 pipeline/kb_eval_judge.py run --root . judge` → `error: unrecognized arguments: judge`，`RC=2`（因为 `kb_launcher.COMPAT_MAP` 无 `("eval","judge")`，多出的位置参数被透传给 argparse）。正确形式是 `kb eval run`。
8. **检索质量的"正文长度"口径错误**（见 §七 P12）：`eval_retrieval.py:114-115` 里 `body_lens` 与 `snip_lens` 都取 `len(h["snippet"])`，而查询带 `--context 400`（`:65`）→ `avg_body_chars` 实为平均 snippet 长度（被 400 截断），报告却打印「平均正文 N 字」`:167`。

---

## 四、反馈回路 `feedback_loop.py`

### 4.1 命中信号如何记录

1. **写入口**：`rag.py:_record_hits()`（`rag.py:717-731`）在**每次 query** 时写 `feedback_state.json.agent_hits[<session>] = {query, hits[paths], timestamp, weight:0.3}`；`cmd_query` 在无 `--session` 时**自动生成** session（`rag.py:744-746`）→ 事件无限累积。live 状态实测：`agent_hits = 2009` 条、文件 **2,089,919 字节**，且 `query_cache.json` 另有 927 KB。
2. **信号汇聚**：`ingest(root)`（`:115-153`）对**全库每篇**「`importance ≥ 0.1`、非 `retire`、有 `kb_summary` 或首行」的笔记，用其摘要当 query 做**进程内 TF-IDF 检索**（`hits_for :84-92`，`HIT_TOPK=5`），命中即 `hits[h] += 1`（`:139-140`）。live 日志实测 `4153 查询源`、`累计命中笔记 1407 个`（`logs/growth-2026-10-15.log`）。
3. **历史衰减**：每轮 ingest 先把历史命中乘 `HIT_DECAY=0.6`（`round(...,4)`），`< 0.1` 的键被删除（`:120-122`）。
4. **外部显式反馈**：`record_hit(root, query, hit_path, useful)`（`:404-429`）把 `{query, hit, useful, timestamp, weight}` 追加进 `state["explicit_feedback"]`，并当 `useful` 时调 `_accumulate_importance`（`:432-462`）按 `weight × EXPLICIT_NORM_FACTOR(0.05)` 上调 importance（**只升不降**），写前先 `git add/commit` 做 checkpoint（`:453-457`），写后再 commit（`:459-461`）。**`record_hit` 的调用者只有两处**：`feedback_loop.main()` 的 `hit` 子命令（`:491`）与 `kb_usage.mark()`（`kb_usage.py:430`），都是人工/opt-in 触发。

### 4.2 如何反哺打分/排序/权重

- **不反哺检索**：`rag.py` 的打分只用 token（见 `kb_constants.py:42-47` 注释：wikilinks/入链不影响打分）。反馈回路只改 **frontmatter `importance`**，而 importance 是否进检索排序未在本组文件内确认（`rag.py` 有 `--min-importance` 过滤参数，`rag.py:738`）→ 影响面止于「元数据 + 过滤」。
- **加权模型**：`weighted_signal()`（`:156-165`）`score = EXT_WEIGHT(1.0)×外部次数 + INT_WEIGHT(0.15)×min(RAG命中, HIT_CAP=5)`；`compute_signals()`（`:168-176`）把 `explicit_feedback` 里 `useful=True` 计数为 `external_count`。
- **归一化提升**：`bumps()`（`:179-215`）先按 `maxs = max(signals.values())` 归一：`delta = min(BUMP_CAP, (sc/maxs)×BUMP_CAP)`（`:202`，`BUMP_CAP=0.30`）→ **只与相对排名有关，与绝对信号强弱无关**；纯冷门（`hits < MIN_HITS=3` 且 `ext=0`）只计数不提升（`:199-200`）；`ext>0 且 delta<MIN_BUMP` 时给 `DIVERSITY_FLOOR=0.05` 地板（`:203-204`）。
- **多样性感知（P1-B）**：`_enhance_with_novelty()`（`:253-267`）给低密度领域加 `NOVELTY_BONUS=0.10×(1 - share/0.60)`；`_apply_related_floor()`（`:270-290`）给「与热门余弦 ≥0.55」但自身弱的笔记 `NOVELTY_FLOOR=0.06` 保底；随后 `delta >= MIN_BUMP` 过滤、按 `(external, score)` 排序、`DIVERSITY_CAP=8` 截断（`:212-214`）。
- **写回**：`apply_bumps()`（`:309-357`）对每个建议：跳过生成物（`:321`）与 `raw/`（`:326`，`is_raw_rel :365`）、校验文件存在、`remaining = BUMP_CAP - applied[rel]`、`actual = min(delta, remaining, 1.0-old)`、`actual < MIN_BUMP` 则跳过（`:333-336`）；写 `importance`（`_write_importance :383-400`，重写整个 frontmatter 块）后 `git add` + 有暂存才 `git commit`（`:348-353`）。

### 4.3 状态文件结构与上限

| 键 | 内容 | 上限/清理 | 代码 |
|---|---|---|---|
| `hits` | `{rel: 累积命中(float)}` | 每轮 ×0.6 衰减，`<0.1` 删除 | `:118-122` |
| `agent_hits` | `{session: {query,hits,timestamp,weight}}` | **无上限、无清理**（live 2009 条）| `rag.py:723-729` |
| `explicit_feedback` | 追加式 list | **无上限**（live 0 条）| `:418-424` |
| `applied` | `{rel: 累计提升(≤BUMP_CAP)}` | 每篇 ≤ `BUMP_CAP=0.30` | `:333,339` |
| `last_ingest` / `run_queries` | 日期 / 源篇数 | — | `:142-143` |

**状态文件存放位置有两套**：`load_state/save_state`（`:95-112`）优先用 `state_manager.StateStore` → `.kb/state/feedback_state.json`；`ImportError` 时回退到 `pipeline/.feedback_state.json`（常量 `STATE = ".feedback_state.json"` `:22`，而 `:356` 的提示语说「累计提升记于 `pipeline/.feedback_state.json`」）。`kb_usage` 的回退直读路径是 `.kb/state/feedback_state.json`（`kb_usage.py:71`）→ 两模块回退路径**不一致**，一旦 `state_manager` 不可导入就会各写一份、互相看不见。

### 4.4 是否会漂移（有证据）

- **EXT_WEIGHT 主锚实际为空**：live `feedback_state.json` 的 `explicit_feedback = []`、`applied = {}`（实测），而 `hits` 有 1407 条 → 所有信号都来自**内部自查询代理**（每篇摘要检索全库），即设计文档 `:26-32` 明确想避免的「富者更富 / 自闭环塌缩」。`growth_cron.sh:53` 只跑 `feedback ingest`，从不跑 `apply`，也从不产生显式反馈。
- **每轮必有赢家**：只要存在 ≥1 个候选，`maxs>0` 就令最高分者拿 `delta=BUMP_CAP=0.30`（`:202`），live 日志每天稳定给出「建议提升 8 篇 … +0.30」。虽然 `applied` 上限 0.30 阻止单篇无限涨，但**"每轮 8 篇"的选择完全由内部命中决定**，且 `_enhance_with_novelty` 的 novelty 项（`:264`）会随领域占比变化而漂移（领域分布一变换，同样的笔记得分就变）。
- **无效建议混入**：live 日志的 8 条建议里 5 条是 `raw/_curated/...`，而 `apply_bumps` 对 `raw/` **硬隔离跳过**（`:326`）→ 「建议提升 8 篇」这一数字约有 62% 永远无法执行（指标虚高 + 误导）。
- **命中计数与展示口径不一致**：`weighted_signal` 里命中被 `min(hits, HIT_CAP=5)` 封顶，但 `report()` 直接打印原始 `b['hits']`（`:303`）→ live 报告出现「命中 9.9992 次 / 42.493300000000005 次」这种既超出封顶值、又带浮点尾数的数字（`round(v*0.6,4)` 后再连续 `+1` 产生的二进制尾数，`:121,140`）。

---

## 五、运维与调度

### 5.1 调度频率、日志路径、告警路径

| 脚本 | 频率（依据） | 日志/产出 | 失败告警路径 | 退出码语义 |
|---|---|---|---|---|
| `growth_cron.sh` | README/quickstart 示例 **每天 04:40** `README.md:278`、`quickstart.md:78`、`如何使用.md:66`；live 实跑在 **02:00**（`logs/growth-2026-10-15.log:1` `[growth 02:00:00]`）| `logs/growth-<date>.log` `:29-30`（每步 `tee`，末尾 `节奏汇总`）| 脚本自身 `exit 非0`（`:145`）→ 由 cron 邮件或上游 supervisor 捕获；无内建推送 | `FAIL>0 → 1`，否则 0 |
| `healthcheck_cron.sh` | 由 supervisor 在 02:00 调用（live `logs/health-2026-10-15.log:1` 02:04:02）；SOP 文档说「每周跑一次」`03-SOP-健康巡检.md:17` | `logs/health-<date>.log`（**`>` 覆盖**）`:19` | **无**：脚本退出码 = 末尾 `tail` 的状态 = 0 `:25`；问题只写进日志 `:21-23` | 恒 0 |
| `kb-daily-supervisor.sh` | 注释称 OpenClaw **02:00** 自动化 `:4`；`docs/curate-ops.md:162` | 只输出到 stdout（由 OpenClaw 投递微信）；中间产物在 mktemp | 简报正文 ❌/⚠️ `note_problem :38-43`；**退出码恒 0** `:183`（设计理由 `:17-19`：非 0 会导致任务被判定失败而**跳过投递**）| 恒 0 |
| `kb-curate.sh` | `docs/curate-ops.md:163` 记 **02:30**（`kb-curate-nightly`），也可 crontab `30 2 * * *` `docs/curate-ops.md:175-178` | mktemp；简报 stdout | 简报 ❌/⚠️；**退出码恒 0** `:105` | 恒 0 |
| `kb-verify-cron.sh` | 未见调度声明（仓库内无 crontab/openclaw 配置；`.github` 不存在 → 也无 CI）| stdout（`OK ...` 或失败清单）| 失败 `exit 1` `:18` → cron 邮件；成功也打印 `:20` | 0/1 |
| `backup_now.sh` / `kb backup` | **未见任何调度接线**；live `backups/daily` 仅 3 个包（2026-09-19/24/27），dashboard 告警「备份已 17.7d 未更新」（`logs/growth-2026-10-15.log:108`）| `backups/<kind>/<date>-vault.zip(+.sha256)` | 无 | 0/2/3 |
| `drill_now.sh` | 无调度；文档记为「每季度」`drill_now.sh:62` | `70-知识治理 Governance/audit/rollout-<date>.md` | 无（失败 `exit 1`）| 0/1 |
| `bench_llm_tps.py` | 手动 | stdout | — | — |

**告警通道结论**：真正的「告警」只有两条：① OpenClaw 自动化把 supervisor/curate 的 stdout 简报投递到微信（`docs/curate-ops.md:158-163`，配置在本仓之外，仓库内无配置可审计）；② cron 邮件（只在 `growth_cron.sh`/`kb-verify-cron.sh` 返回非 0 时可能触发）。`healthcheck_cron.sh` 恒 0 且覆盖日志 → 作为独立 cron 时完全静默。

### 5.2 锁 / 并发保护

- **shell 层：全仓 `template-vault/scripts/` 无 `flock`**（grep `flock` 无命中）。`growth_cron.sh`、`healthcheck_cron.sh`、`kb-curate.sh` 均可重入/并发。
- **Python 层：只有状态文件有锁**。`state_manager.StateStore._lock()` 用 `fcntl.flock`（非 Unix 降级为无锁，`state_manager.py:16-20,57-68`），但**只在单次 load 或单次 save 内持锁**；`rag.py:_record_hits`（`rag.py:719-731`）是 `load → 改 → save` 的读改写，锁在两次调用之间释放 → 并发丢更新。
- **索引写入非原子**：`rag.py:231,314` 用 `(idx/"df_idf.json").write_text(...)` 直接覆盖，无临时文件 + `os.replace`；读方（`eval_retrieval.py:92`、`kb_usage`/`feedback_loop.load_index`）直接 `json.loads`。而 `growth_cron.sh` 与 `kb-curate.sh` 都可能重建索引（`growth_cron.sh:47,135`；`kb-curate.sh:85`），02:00 与 02:30 两班在长任务下可重叠 → 可能读到截断 JSON。
- **写笔记的并发**：`feedback_loop.apply_bumps`、`kb_audit fix`、`memory_sync promote --apply` 都会改同一批 `.md` 并各自 `git commit`，彼此无互斥。

### 5.3 跨平台可用性

| 组件 | Windows | macOS | 依据 |
|---|---|---|---|
| `backup.py` / `kb backup` | ✅ 纯标准库（zipfile+hashlib）`backup.py:17-27` | ✅ | `backup.py:3-11` 明确以「kb.cmd 无法调用 bash」为迁移动机 |
| `dashboard.py` | ❌/部分：`sha256sum -c` `:32` + `git` | ❌ 部分：macOS 无 `sha256sum`（有 `shasum`）→ 校验显示「未知」`:31` | `dashboard.py:30-34` |
| `kb-healthcheck.py` | ⚠️ 逻辑可跑，但 `st_ctime` 语义不同（Windows=创建时间、Linux=inode 变更时间）`:233`；健康页/日志用本地时间 | ⚠️ 同 | `:233,511` |
| `*.sh`（growth/supervisor/curate/drill/healthcheck/verify）| ❌ 需 bash（README 已声明 Windows 不适用 `README.md:289`）| ❌/⚠️：`timeout` 默认没有；`mapfile` 需 bash≥4（macOS 3.2）`drill_now.sh:14`；`stat -c` 是 GNU `kb-daily-supervisor.sh:159`；`sha256sum` GNU `drill_now.sh:22` | 各文件行号如上 |
| `/proc/loadavg` 空闲闸门 | ❌ 非 Linux 时 `cut ... || echo 0`（`:50`、`growth_cron.sh:123`）→ `LOAD1=0` **闸门静默失效**（永远「空闲」）| ❌ 同 | `kb-curate.sh:50,59`；`growth_cron.sh:123,128` |
| `kb_verify.py` | ⚠️ 以 `["kb", ...]` 直接 exec（`kb_verify.py:32-34`）：Windows 需解析到 `kb.cmd`（未实测，**未确认**）| ⚠️ 需 `kb` 在 PATH | `:32-34,55` |
| `gen_kb_arch.py` | ✅ 纯标准库，但**需先存在 `arch/`** `:157` | ✅ 同 | `:156-159` |
| `bench_llm_tps.py` | ✅（stdlib urllib）| ✅ | `:21-34` |

### 5.4 `backup.py` 备份策略与恢复（`drill_now.sh`）

- **策略**：全量 zip（`ZIP_DEFLATED`）`backup.py:46`；排除**任意层级**的 `.git` 与 `backups`（避免递归打包）`:26,48`；跳过悬空软链 `:52`；单个文件读失败 continue（best-effort）`:54-57`；文件名 `YYYY-MM-DD-vault.zip`，**同一天重跑覆盖同名文件** `:43-44`；生成标准 `hash  文件名` 两空格 `.sha256` `:59-61`；无保留/轮转策略（无清理逻辑）；`sha256` 用 `out.read_bytes()` **整包读入内存** `:59`。
- **产物范围**：除 `.git`/`backups` 外**全打包** → 包含 `.kb/state/*`（含 `feedback_state.json` 2 MB、`query_cache.json` 927 KB、`lineage.jsonl`）、`vector index/df_idf.json`、`logs/`、`.obsidian/`（`EXCLUDE_DIRS` 只有两项，`:26`）。
- **恢复演练 `drill_now.sh`**：只取 `backups/daily/` 里 **mtime 最新**的一个包（`:14-16`，`backups/` 根下的历史包不参与）；`sha256sum -c` 校验 `:22-23`；python 内联 `extractall` 到 mktemp `:25-28`；统计 `.md` 数与抽查首个文件 `:29-33`；核查主仓 `git rev-list --count HEAD > 0` 作为「可回滚」证据 `:36-42`；SLA ≤600s `:45-46`；写 `70-知识治理 Governance/audit/rollout-<date>.md` `:48-63`。
- **实测现状**：live 最新备份是 **2026-09-27**（`backups/daily`），dashboard 自报「备份已 17.7d 未更新」；`audit/` 下只有 `rollout-2026-09-03.md` → 恢复演练也早已停跑。

---

## 六、`gen_kb_arch.py` 产物与依赖

- **产物**：`<repo>/arch/kb-kit-pure-architecture.drawio`（单文件 draw.io / mxGraph XML，含 `mxfile > diagram > mxGraphModel > root`；页 1500×900；`id="title"/"note"` 固定 + 数字 id 自增）`:145-159`。运行结束打印 `wrote <path> cells: N` `:160`。
- **依赖**：仅标准库 `xml.sax.saxutils.escape` 与 `pathlib` `:3-4`；无第三方、无网络。
- **内容事实**：6 层左侧栈（用户层/CLI/注册中心+插件协议/插件实现/业务逻辑/共享基础设施）`:64-101`；右侧 Vault + 成长引擎 6 步（intake→feedback→link→recall→kb_health→dashboard 自循环）`:126-135`；底部 `kb-context-dsh` 插件说明 `:138-139`；脚注声明依据 `kb-context-dsh/README.md + 📖-知识库管理方案.md + pipeline 插件注册中心源码` `:142-143`。
- **问题**：① `arch/` 在 `.gitignore` 内（仓库内不存在），脚本**不建目录**直接 `open("w")` `:157` → 新克隆运行必失败（已用等价 `open()` 探针验证：`FileNotFoundError`，且不会自动创建目录）；② 第 ④ 层标签写死「25 个插件」`:71`，与实测 36 个插件模块不符（陈旧硬编码）；③ 无 CLI、import 即执行（不可 `--help`/不可选输出路径）；④ `fontFamily=Helvetica` 与中文标签混排，渲染字体依赖 viewer（非代码问题）。
- **验证方式说明**：我未运行该脚本（避免在仓库里创建产物），上述「必失败」结论由静态阅读 + 对不存在目录的等价 `open()` 探针得出。

---

## 七、发现的问题清单

> 类型：bug / 静默失败 / 假警报 / 指标虚高 / 硬编码路径 / 时区 / 重复实现 / 无锁并发 / 死代码。

| # | 位置 | 类型 | 影响 | 建议 |
|---|---|---|---|---|
| P1 | `dashboard.py:74-79`（触发条件 `"0 死链" not in line`）+ `kb-healthcheck.py:219` 输出格式 `0/2146 死链` | **假警报（已在生产验证）** | 死链为 0 时仍把「🔗 死链检测: 0/2146 死链 (0.0%)」列为告警：`logs/growth-2026-10-15.log:108` 原文可见；我复现 `dashboard.check_healthcheck()` 得到同一告警。日常简报长期携带一条假告警 | 改为解析结构化 `summary --json`（`checks.deadlinks.count`），或至少用 `count>0` 判定；顺带修「📏 📏」双图标 |
| P2 | `kb-healthcheck.py:76`（`REPORT_NAMES` 含 `_INDEX.md`）vs `:198-202`（`NAV_FILES` 豁免 `_INDEX.md`） | **bug / 静默漏报** | `collect_md` 已在 `:76` 跳过 `_INDEX.md`，`:198-202` 的 `_INDEX.md` 豁免分支**永不可达** → 注释 `:66-68` 声称已修「_INDEX 死链漏报 15 条」实际只对 `_MOC.md` 生效，`_INDEX.md` 的死链仍不计 | 把 `_INDEX.md` 从 `REPORT_NAMES` 移除，或让 `collect_md` 接受 include 参数 |
| P3 | `kb-healthcheck.py:307-318` | **指标虚低** | 无 frontmatter 的笔记被 `:313` 直接 `continue` → 「frontmatter 缺失 0」不代表全库合规；字段判定是 `in block` 子串匹配（注释/正文含 `tags` 也算有）| 对无 `---` 的文件计「缺整个 frontmatter」，改用 `parse_frontmatter` 的键集合判定 |
| P4 | `kb_usage.py:118-119` | **bug（时间窗反向）** | `evs` 升序后取 `[:200]` → 只分析**最旧的 200 条**事件（live `agent_hits=2009`）→ 再问率/成功率代理长期停留在历史窗口，不反映现状 | 改 `evs[-_MAX_EVENTS:]`（或按窗口过滤后再取尾部）|
| P5 | `feedback_loop.py:22,95-112` vs `kb_usage.py:71` | **重复实现 / bug** | `state_manager` 不可导入时，feedback_loop 回退到 `pipeline/.feedback_state.json`，而 kb_usage 回退到 `.kb/state/feedback_state.json` → 两套状态文件、互相不可见（写入丢失）；`:356` 的提示语也指向旧路径 | 统一回退路径常量，或去掉 `ImportError` 回退直接报错 |
| P6 | `feedback_loop.py:303`（打印 `b['hits']`）+ `:121,140`（`round(...,4)` 后再反复 `+1`） | **指标虚高/展示失真** | live 报告出现「命中 9.9992 次 / 42.493300000000005 次」，而打分时命中被 `HIT_CAP=5` 封顶（`:163`）→ 读者无法据此理解真实权重 | 打印 `min(hits, HIT_CAP)` 与封顶后权重；累积时用 `round(...,2)` 或 Decimal |
| P7 | `feedback_loop.py:33-39,156-165` + live 状态 `explicit_feedback=[]` | **指标虚高（设计未落地）** | `EXT_WEIGHT=1.0` 的「外部真反馈主锚」在生产中恒为空（唯一写入者 `:491`、`kb_usage.mark` 都是 opt-in，`growth_cron.sh:53` 从不调 `apply`）→ 全部信号来自「全库自查询」的内部人气代理，正是注释 `:26-32` 想避免的富者更富/自闭环塌缩 | 若要真接地，需在 agent 回答后自动写 `useful`（例如 kb_query 工具返回后询问/推断），或在无外部反馈时**不输出提升建议**（避免每天稳定「8 篇 +0.30」的空转）|
| P8 | `feedback_loop.py:326` vs live 建议列表 5/8 为 `raw/_curated/...` | **无效建议 / 指标虚高** | `apply_bumps` 硬跳过 `raw/`，但 `ingest` 的建议仍包含它们 → 「建议提升 8 篇」中 62% 永不生效 | `bumps()` 阶段就过滤 `is_raw_rel` |
| P9 | `kb_health.py:218-249` | **指标虚高** | 涌现项 `score += min(10, len(emergence))`（`:248`）可**抵消**陈旧/孤儿/积压的扣分；例如 10 篇 MOC 正好抵掉 10 篇过期笔记（`:236`）| 加分与扣分分离展示，或给加分设上限并禁止抵消硬告警项 |
| P10 | `kb_health.py:299-360` | **死代码 / 假成功** | `check_policy_stale`/`mark_policy_stale`（FR-3.3.14）**无任何调用点**（全仓 grep 仅自引用）；`mark_policy_stale` 在 `text.replace("---\n", ...)` 未命中时仍 `marked += 1` 并重写文件 → 计数假成功；未复核笔记返回 `days_overdue = cycle_days`（`:330`）语义错误 | 接一个 `kb health-metrics --mark-policy` 子命令并校验 replace 是否生效；否则删除该功能 |
| P11 | `kb_constants.py:23`（90）vs `kb_audit.py:32`（120）vs `kb_common.py:263`（180） | **指标口径分裂** | 同一「陈旧」在三处三值，dashboard/healthcheck/audit/health-metrics 报出的陈旧数不可比 | 统一到 `kb_constants` 并区分「归档线/复核线/审计线」的名义 |
| P12 | `04-质量与指标.md:22`（<2%）vs `kb-healthcheck.py:507,593,608`（阈值 0） | **文档口径不符** | 文档说 1.9% 健康，工具判 red/exit 1 → 使用者按文档配置监控会永远收到红 | 要么实现「rate<2% 才算 fail」，要么把文档改成 0 |
| P13 | `04-质量与指标.md:32`（≥0.7）vs `kb_health.py:115,134`（>0.9）vs `kb_constants.py:11-12`（0.85/0.90） | 文档/常量不一致 + 死常量 | `DUP_SIM_HEALTH=0.90` 声明为「kb_health 的健康检查阈值」却从未被 import（kb_health 硬编码 0.9）→ 改常量无效 | `from kb_constants import DUP_SIM_HEALTH` 并统一文档 |
| P14 | `kb_audit.py:268`（`limit` 未用）、`verify_curate.py:16`（`EX` 未用）、`kb_health.py:22-23`（`EXCLUDE/FM/DECAY_DAYS` 未用）、`kb_audit.py:370`（`idx` 未用）| 死代码 | 参数/常量看起来可调，实际无效（`kb audit scan --limit N` 无效果，输出仍硬编码 60/8）| 接线或删除 |
| P15 | `kb_eval.py`（整文件）vs `kb_eval_judge.py`；`plugins/eval_plugin.py:9`；`tests/test_reachability.py:32` | **重复实现 + 不可达工具** | `kb eval` 指向 `kb_eval_judge.py`，`kb_eval.py` 没有 CLI 入口（仅被声明为「研究层」），其数据集目录 `pipeline/test_eval/` 不存在 → `kb_eval.py eval` 必然 `return 2`；两套 RAGAS 口径并存易混 | 二选一：删除 `kb_eval.py` 或把它接成 `kb eval baseline` 子命令并落一份数据集 |
| P16 | `kb_eval_judge.py:180-184` + `.kb/eval/baseline-p4.json` | 指标虚高 | `faithfulness` 由被测的同一本地模型自评，实测取值只有 `{1.0, None}`（30 题 4 题 precision=0 仍满分）；`docs/p4-report.md` 据此宣称「零幻觉」| 引入第二裁判/规则校验，或至少在报告里标注「自评、无区分度」；`faith` 需同时给出 `claims` 分布 |
| P17 | `.kb/eval/gold_set.jsonl` 缺失（live 只有 `gold_candidates.jsonl`）`:32,213-220` | 评测缺口 | 30 题 `context_recall` 全为 `null` → 四指标实际只有三项；p4-report 也承认待人工确认 | 走一次 `gold --review` 人确认，或把 recall 换成不需 gold 的代理并改名 |
| P18 | `docs/p4-report.md:76,83` 的 `kb eval judge run/compare` | **文档命令跑不通** | 实测（`python3 pipeline/kb_eval_judge.py run --root . judge`）`error: unrecognized arguments: judge`、`RC=2`（`COMPAT_MAP` 无 `("eval","judge")`）| 文档改为 `kb eval run` / `kb eval compare`，或在 launcher 加别名 |
| P19 | `kb_eval.py:118-124`（`ensure_index` 不看返回码）、`:278`（`cfg` 未用）、`:76`（`load_dataset` 忽略 `root`）| **静默失败** | 索引重建失败时评测照跑，基线数值无声失真 | 检查 returncode 并中止/标注 |
| P20 | `eval_retrieval.py:114-115,167` | **指标口径错误** | `body_lens` 与 `snip_lens` 同取 `len(snippet)`，而查询带 `--context 400` → 报告里的「平均正文 N 字」其实是**被 400 截断的片段长度**；无法支撑「空壳顶替真内容」的论证 | 读命中笔记真实正文字数（`len(load_note(p)[2])`）另存 `avg_body_chars` |
| P21 | `eval_retrieval.py:143-144`（`_is_stub_meta` 恒 False）、`:70-73`（参数名 `no_rerank` 与语义相反）| bug/命名 | stub 只能靠 `-index.md` 命名识别，索引 meta 缺 `is_stub` 则漏判；`no_rerank=True` 实际是**开启**重排（`KB_EMBED_RERANK=1`），易误读 | 实现 meta 判据或注明；把参数名改为 `use_rerank` |
| P22 | `healthcheck_cron.sh:19-25` | **静默失败** | ① 未传 `--root`，实际 root 由 `kb_common.ROOT_DEFAULT`（env `KB_ROOT` 优先）决定 —— 本机 `KB_ROOT=/home/mushan/kb-kit-pure`，我实测在 `template-vault` 下运行也检查了**另一个 vault**（summary 的 `vault` 字段 = `/home/mushan/kb-kit-pure`）；② `>` 覆盖当天日志，同日多跑丢历史；③ 退出码 = `tail` 状态 = 0 → 独立 cron 下永不告警 | 显式 `--root "$VAULT"`；追加 `>>` 或分轮编号；异常时 `exit 1` |
| P23 | `kb-verify-cron.sh:2 vs :20`、`:15`、`:3` | 注释不符 + 静默失败 + 硬编码 | 注释说「成功静默」但成功也打印（cron 噪声）；异常分支 `sys.stdin.read()` 已无内容 → 解析失败时**详情为空**；`VAULT` 默认 `/home/mushan/kb-kit-pure` | 成功时不输出；用 `OUT="$OUT"` 通过环境变量传入而非管道；参数化 VAULT |
| P24 | `kb-curate.sh:74`（`FAILS` 未使用）、`:73`（靠 grep 日志串 `canon-written`）| 死代码 + 脆弱耦合 | 警告数算了不用；`WROTE>0` 决定是否重建索引，而该判据依赖 `curate.py:970` 的打印格式，一旦改日志就不重建（静默陈旧索引）| 用 `curate.py report --json` 的前后差（脚本已在 `:64,:90` 取 `canon`）来判断，别 grep 文本 |
| P25 | 全仓 `scripts/*.sh` 无 `flock`；`rag.py:231,314` 非原子覆盖索引；`rag.py:719-731` load→save 丢更新 | **无锁并发** | 02:00 `growth_cron`（含 `rag index`、`memory_sync --apply`）与 02:30 `kb-curate.sh`（含 `curate run --apply` + `rag index`）可重叠：索引被截断/读方 `json.loads` 崩、状态文件丢更新、同批笔记并发 git commit | 给长任务加 `flock -n /tmp/kb-cron.lock`；索引改临时文件 + `os.replace`；`_record_hits` 改用 `StateStore.update()` |
| P26 | `kb-curate.sh:28`、`kb-daily-supervisor.sh:26`、`kb-verify-cron.sh:3`、`kb_verify.py:44-45` | **硬编码路径** | 默认 vault 写死作者机器路径（`/home/mushan/kb-kit-pure`），`kb_verify` 还在兄弟目录里猜 `workspace/kb-kit-pure/tests`；换机/分发后行为不可预期 | 统一 `KB_ROOT`/脚本自解路径 |
| P27 | `kb-healthcheck.py:233`（UTC + `st_ctime`）vs 其它检查本地时间；`:511`（带偏移）vs `:573,585`（本地 naive）；`kb_audit.py:274,281`（本地 `date.today()`）| **时区/时间基准不统一** | 同一次运行里 UTC 与本地混用；日志文件名/审计文件名用本地日期，夏令时/改 TZ 时可能覆盖同名文件或漏判「>7 天」 | 统一用 `datetime.now(timezone.utc)`（或统一本地）并落 `tz` 字段；用 mtime 而非 ctime 判「年龄」 |
| P28 | `kb-healthcheck.py:227-238` 未用 `is_generated_report` | 噪声/假告警 | live 有 9 个「骨架笔记」，其中至少 `70-知识治理 Governance/_audit/log.md`、`_graph_kg-c1.md` 是生成物 → 每天固定 2+ 条无效告警，稀释真信号（与 `:347-401`、`:404-437`、`:441-468` 的排除集不一致）| 在 skeletons 里加 `is_generated_report(rel)` 排除 |
| P29 | `dashboard.py:82-98` vs `kb-healthcheck.py:452-453` | **重复实现/口径不一致** | dashboard 自算 freshness（不排除 `50-模板`/`模板`），healthcheck 排除 → `04-质量与指标.md:31` 的两处数字不同；同一指标两个来源 | dashboard 直接调 `kb-healthcheck.py freshness`（或复用 `run_checks`）|
| P30 | `kb_audit.py:36`（`_audit`）vs `drill_now.sh:11`（`audit`）；`kb_audit.py:281`（同日覆盖）| 结构/历史丢失 | 治理目录下并存 `_audit/` 与 `audit/`，人找报告要猜；审计 JSON 同日重跑互相覆盖，无历史序列（`cmd_log` 只能列当天若干）| 统一目录（含 `is_generated_report` 已认 `_audit`，宜保留 `_audit`）；文件名加时间戳 |
| P31 | `gen_kb_arch.py:157`（无 mkdir）+ `.gitignore`（`arch/`）| bug（新克隆必崩）+ 硬编码陈旧 | 目标目录被忽略且不存在 → `FileNotFoundError`（已用等价 open 探针验证）；`:71` 写死「25 个插件」，实测 36 → 图与实现漂移 | 加 `path.parent.mkdir(parents=True, exist_ok=True)`；插件数改为读取 `plugins/*.py` 计数 |
| P32 | `backup.py:43-44`（同名覆盖）、`:26`（仅排除 `.git/backups`）、`:59`（整包入内存）、无保留策略 | 运维缺口 | 同日重跑静默覆盖；备份包含 `.kb/state`（含 2 MB 反馈状态与查询缓存）与可重建的 `vector index` → 体积/隐私与恢复价值错配；无限增长；且**没有任何调度接线**（live 最新 09-27，dashboard 已告警 17.7d）| 文件名加时间戳/轮转保留；补 `EXCLUDE_DIRS`（`vector index`、`logs`、可选 `.kb/state`）；把 `kb backup daily` 接进 `growth_cron.sh` 或独立 cron 并纳入 supervisor 自检 |
| P33 | `drill_now.sh:14`（只看 `backups/daily`）、`:29-32`（空集时 `grep -c '^'` 计 1）| bug/口径 | `backups/` 根下的历史快照不参与演练；无 `.md` 时计数假为 1 | 递归收集 `backups/**/*-vault.zip`；计数前判空 |
| P34 | `kb_verify.py:42-49`（猜 tests 目录）、`:112`（依赖 `^OK` 文本）、`:157`（相对路径）、`:44` | 硬编码/脆弱 + 分发不可用 | 分发库（无 `tests/`）下第 ⑤ 项恒定 FAIL，`kb verify` 永远不绿；`--with-index` 依赖 cwd 布局 | 把「未找到 tests」标为 SKIP 而非 FAIL；用 `root/scripts/eval_retrieval.py` 绝对路径 |
| P35 | `kb_health.py:363-374`（恒 return 0）| 门禁缺失 | `kb health-metrics` 永不失败，无法进 cron 门禁，与 `04-质量与指标.md:35-39` 的「门禁」叙事不符 | 加 `--strict` 与阈值退出码 |
| P36 | `kb-healthcheck.py:299`（`any(sub.iterdir())` 未捕获 OSError）、`:313/315`（同文件读两次）、`:594-599`（`--log` 在 summary 模式被静默忽略）、`:601-603`（单项模式标题打印两次）| 健壮性/小瑕 | 权限异常会中断整轮巡检；多余 I/O；`--log` 静默无效；日志出现重复标题行 | 包 try/except；单次读取；summary 也写日志；去掉重复 emit |
| P37 | `kb_usage.py:14,291,388`（注释/文案）| 文档漂移 | 注释说 `agent_hits` 需 `--session` 才写（`rag.py:744-746` 已自动生成）；`:388` 说「不进 fitness」，实际 `kb_fitness.py:92`、`kb_selfweight.py:139`、`kb_meta.py:172` 都在消费它 | 更新注释，明确它在 L1/L3 回路里的作用 |

---

## 八、测试 / 验收缺口

- **测试入口**：`tests/` 共 19 个 `test_*.py`（`README.md:295-296` 称 66 条）；`kb_verify.py:30-39` 把 `python3 -m unittest discover -s tests` 作为第 ⑤ 项；`kb_verify.py:42-49` 说明 `tests/` 只存在于源码仓库。**仓库无 CI 配置**（`.github/` 不存在）→ 五项全绿靠手动/cron 触发。
- **本组（F 组）实际被覆盖的部分**（grep + 读文件确认）：
  - `feedback_loop.py`：`tests/test_growth_fixes.py:76`（`test_hits_decay_on_ingest`，无索引时只衰减）、`tests/test_growth_fixes2.py:62`（`test_feedback_skips_generated_report`，`save_state`+`apply_bumps` 不改生成物）→ 覆盖了「衰减」和「跳过生成物」两条，**未覆盖** `bumps()` 归一化/novelty/floor、`weighted_signal`、`record_hit`、`_write_importance`（frontmatter 重写）、raw/ 隔离。
  - `kb-healthcheck.py`：`tests/test_lifecycle_p0.py:92`（`test_healthcheck_freshness_runs`，仅子进程 smoke）→ **9 项里只 smoke 了 freshness**；死链/标签/骨架/纪律/孤儿/超长/空目录/汇总/HTML/`--log` 全无断言（P1/P2/P3/P28 这些 bug 因此长期存活）。
  - `backup`：仅 `tests/test_help_alignment.py` 通过 `kb help` 文案间接覆盖路由（`help` 里出现 `kb backup` → `COMPAT_MAP` 可解析），**没有** `backup.py` 的产物/排除/覆盖行为测试。
  - 路由/结构：`tests/test_help_alignment.py`（help 与路由对齐）、`tests/test_reachability.py`（模块可达性；把 `kb_eval.py`/`kb_usage.py` 等登记为内部模块）、`tests/test_plugin_registry.py`。
- **完全没有测试的工具/行为**（grep `tests/*.py` 无命中，除上表注明的间接引用）：
  - `kb_audit.py`（scan 的四类检查、fix 的 dry-run/`--confirm`/git add、日志追加）
  - `kb_eval.py`（数据集/指标公式/`--strict` 退出码）
  - `kb_eval_judge.py`（judge JSON 容错、`_precision_at_k`、gold 流程）
  - `scripts/eval_retrieval.py`（`classify/measure` 口径，含 P20 的 body/snippet 混淆）
  - `scripts/verify_curate.py`（探针自身）
  - `dashboard.py`（含 P1 假警报、P29 口径不一致、备份解析）
  - `kb_verify.py` 的 5 个解析器（`_parse_*`）
  - `kb_usage.py`（含 P4 的 200 条窗口反向 bug）
  - `kb_health.py`（`metrics` 六维公式、`score_and_alerts` 加分抵消、`check_policy_stale`）
  - `gen_kb_arch.py`（含 P31 的必崩目录问题）
  - **全部 shell 脚本**（`growth_cron.sh` 的 `step()` 隔离/PIPESTATUS、`healthcheck_cron.sh` 的恒 0 退出码、`kb-curate.sh` 的闸门、`drill_now.sh`、`kb-verify-cron.sh`）
- **验收/观测缺口的净效果**：本报告里 P1（假警报）、P2（漏报）、P3（虚低）、P4（窗口反向）、P20（口径错）、P22（恒 0）、P31（必崩）**都能被一条针对性单测捕获**，但当前 19 个测试文件里没有任何一个覆盖它们；`kb verify` 的「五项全绿」因此只证明了「契约/校验/巡检阈值/探针断言/既有单测」通过，**不能证明质量指标本身正确**。

---

### 附：我没有验证/无法确认的点

1. `kb validate --strict` 的实际判据与 `04-质量与指标.md:30` 是否一致（`validate.py` 不在本组范围）。
2. `kb_verify.py:32-34` 在 Windows 下能否把 `kb` 解析到 `kb.cmd`（未在 Windows 实测）。
3. `kb_audit` 与 `kb-healthcheck` 在当前 live vault 上的死链/孤儿数字是否完全相等（本机 `KB_ROOT` 指向 live vault，跑 `kb_audit scan` 会写审计 JSON，为避免写操作未执行）。
4. OpenClaw 自动化的真实配置（02:00/02:30、微信投递目标）在仓库外，只能依据 `docs/curate-ops.md:158-169` 的自述与 live 日志时间戳（02:00/02:04）推断。
5. `logs/health-2026-10-14/15.log` 权限变为 `0600`（更早为 `0664`）的原因（疑为运行环境 umask 变化，非代码逻辑）。
