# E 组：治理 / LLM 层 / 成长引擎 —— 逐文件全文精读报告

> 范围：`template-vault/pipeline/` 下 13 个文件（curate.py、curate_prompt.py、kb_claim.py、kb_confidence.py、kb_contradiction.py、kb_contradiction2.py、kb_consistency.py、kb_calibrate.py、kb_drift.py、kb_l4.py、kb_l5.py、kb_rsi.py、kb_engine.py）。
> 阅读方式：全部文件用 read 工具完整读完（curate.py 分 4 段读至 1241 行末）。实测行数与本组任务书给的行数一致（curate.py=1241）。
> 所有结论均给出 `文件:行号`。无法确证之处显式标注「未确认」。
> 本文档只读产出，未修改任何代码。

---

## 一、本组职责与相互关系图

### 1.1 定位

本组是 kb-kit 的**「写入侧」治理层**：唯一负责把 `raw/` 记忆（证据层）与日志型笔记「编译」成策展笔记（`kb_layer: canon`），并对编译结果做置信度、矛盾、一致性、漂移、成熟度（L4/L5）、自我改进（RSI）的度量与约束。

### 1.2 模块清单（职责 / 读写属性）

| 文件 | 行数 | 职责 | 写入文件（有则列出） |
|---|---|---|---|
| `curate.py` | 1241 | **结晶器**：选批 → LLM 出提案 → 8 道确定性校验 → 写回正典 | 笔记本体、`raw/_curated/*`、`.kb/curate_state.json`、`.kb/curate_ledger.jsonl`、`.kb/curate_review.jsonl`、`.kb/curate_proposals.jsonl`、`70-知识治理 Governance/_curate/b*.md`、`REVIEW-NEEDED.md`、git 提交 |
| `curate_prompt.py` | 342 | 提示词层（四层结构 + baseline 两档），**不调 LLM**，只组装 messages | 无（纯读 schema/正典） |
| `kb_claim.py` | 423 | 把笔记抽成「可证伪声明」，用后来的 raw 数据判 confirm/falsify → 预测误差型 fitness | `.kb/state/claims.json` |
| `kb_confidence.py` | 392 | 四因子复合置信分 + 分档 + 原则性遗忘审计 | `--apply` 时写笔记 `confidence`/`confidence_tier` |
| `kb_contradiction.py` | 490 | 矛盾 lint（否定密度启发式 + 可选 LLM 极性）→ 候选对 + 非零分；人工 `apply` retire 一侧 | `pipeline/.kb_contradictions.json`、`pipeline/kb_contradictions_report.md`、`apply` 时改笔记 frontmatter |
| `kb_contradiction2.py` | 237 | 「矛盾在环」：正典间主题重合 + 变更词 → 冲突标注 + 人工队列 | `--apply` 时写 `conflicts_with` + callout、`.kb/contradiction_queue.jsonl`、`.kb/duplicate_canon_queue.jsonl` |
| `kb_consistency.py` | 281 | 晋升记录一致性审计（漂移/出处/覆盖召回 + 可选 LLM 忠实度） | `--report` 时写 `.kb/consistency-report-<date>.md` |
| `kb_calibrate.py` | 271 | 在**仿真 vault** 上标定 DUP/MERGE/RETIRE 阈值 | `synth` 会**递归删除**并重建目标目录；`--csv` 写 CSV |
| `kb_drift.py` | 280 | 晋升记录漂移/长度/出处保留度量 + 时间窗趋势 + decay 视图 | 无（纯只读打印） |
| `kb_l4.py` | 529 | L4 宪法：真人逐条裁决 L0/L1/L2/L3/lc，连续错 → 冻结 rung | `.kb_l4_verdicts.jsonl`、`.kb_l4_state.json`、`.kb_l4_config.json`、git 提交 |
| `kb_l5.py` | 473 | L5 元策略：按「外部结果趋势」给 rung 打 running score → 激活权重 | `.kb_l5_state.json`、`.kb_l5_policy.json`、git 提交 |
| `kb_rsi.py` | 473 | RSI 只读探针：全库信号采集（`collect`/`metrics`）+ 提议 + 接地阀 | `pipeline/.kb_rsi_proposals.jsonl` |
| `kb_engine.py` | 705 | RSI 执行编排器 T1/T2/T3（T3 默认关） | 笔记 frontmatter、`pipeline/.kb_rsi_config.json`、`pipeline/.kb_engine_proposals.jsonl`、git 提交 |

共享常量中心：`kb_constants.py:11-88`（本组重度依赖 `CONTRA_SIM`、`MAX_NOTES_CONTRA`、`L4_*`、`L5_*`）。
共享工具库：`kb_common.py`（`iter_notes:112`、`load_note_full:1019`、`content_fingerprint:161`、`root_of:196`、`is_raw_path:406`、`is_generated_report:423`、`candidate_pairs:920`、`model_config:221`）。

### 1.3 调用关系图

```
            ┌──────────────────────── 外部触发(cron) ────────────────────────┐
            │ scripts/growth_cron.sh:132   curate.py run --apply  (⑧, 有闸门)  │
            │ scripts/kb-curate.sh         curate.py run --apply  (独立批处理) │
            └───────────────────────────────┬───────────────────────────────┘
                                            ▼
   curate_prompt.py ──build_messages_baseline(333)/build_messages(190)──▶ curate.py
        ▲                                                                  │
        └── _merge_candidates(224) ──import curate──▶ _existing_canon(412) ┘
                                                                           │
                                LLM HTTP /chat/completions (curate.LLM:141) │
                                                                           ▼
                              校验 verify_note(515) → apply_canon(895) → record_lineage
                                                                           │
                                          curate_review.jsonl ──▶ kb_l4._docket_lc(219)
                                                                           │
  kb_rsi.collect(158)/metrics(189) ◀── grounding 四处重复实现 ── kb_l4.grounding(144)
        ▲                                                          kb_l5.grounding(134)
        │                                                          kb_engine._grounded(191)
        │                                                          kb_rsi.proposals(378)
        ├── kb_engine.tier_t1(214)/tier_t3(474) ── kb_fitness.fitness(101)
        │        ▲                                      │
        │        │                                      ├─ kb_contradiction.detect(154)
        │        │                                      ├─ feedback_loop (external_feedback)
        │        │                                      └─ kb_usage.analyze(273)
        ├── kb_contradiction.py (import kb_rsi:30)
        ├── kb_l4.py (import kb_rsi:54)
        └── kb_l5.py (import kb_rsi:51)

  kb_claim.py ──collect_notes(220)──▶ kb_rsi.collect(158)   [复用采集，避免重复实现]
  kb_confidence.py ──iter_notes/kb_common，独立四因子，无 kb_rsi 依赖
  kb_consistency.py / kb_drift.py ──读 .kb/promotions.jsonl（memory_sync 写的账本）
  kb_calibrate.py ──kb_rsi.collect（_load_notes:125）
```

### 1.4 共享信号函数（重复实现清单，详见第八节）

| 信号 | 定义位置 | 阈值来源 |
|---|---|---|
| 接地阀 `grounded = external_inflow>=5.0 and cross_ratio>=0.15` | `kb_rsi.py:378-383`、`kb_engine.py:191-198`、`kb_l4.py:144-154`、`kb_l5.py:134-144` | 前三处**硬编码 5.0/0.15**，仅 kb_rsi 用 `EXTERNAL_FRESH_MIN_PCT/EXTERNAL_MIN_CROSS`（`kb_constants.py:32-33`） |
| `_parse_d`（严格日期解析，返回 None） | `kb_claim.py:168`、`kb_calibrate.py:209`（返回 date）、`kb_drift.py:75` | 注释自陈与 `kb_common.parse_date_safe:777` 行为不同 |
| 向量/分词 | `kb_rsi.tokenize:59`、`kb_contradiction._vec:75`、`kb_calibrate._tokenize/_vec:33/37`、`kb_common.char_vec:721` | 四种实现，注释均声明「刻意保持不同」 |
| jsonl 容错 JSON 解析 | `curate._extract_json:207`、`kb_claim._parse_json:71`、`kb_usage._parse_json:243` | 三份近似实现 |

---

## 二、curate.py 深挖

### 2.1 完整流水线（`文件:行号`）

**① 候选筛选（只读）** `select_batch(root, domain, limit, include_done=False)` — `curate.py:270-331`

排除顺序：
1. `is_raw_path(rel)` → 跳过证据层（`curate.py:277`）
2. `is_generated_report(rel)` → 跳过运行期产物（`:279`）
3. `SKIP_DIRS`（`:56-62`：归档/模板/收件箱/`_curated`/`stale-reports`/`_curate`/`backups`/`memory`/`docs`）（`:281-283`）
4. `SKIP_NAME_RE`（`:63`）匹配 `_`/README/索引/index/使用说明 的 stem（`:284`）
5. `is_index_stub(fm)` → 目录页（`:287`）
6. `kb_layer in ("canon","noise")` → 已结晶/已判噪声（`:289-291`）
7. `curate_verdict in ("raw-only","noise")` → 已判定过，不重复送模型（`:294-295`，注释记录「实测第三批 250 篇里 223 篇是重复劳动」）
8. `kb_index in (false,no,0)`（`:296`）
9. 正文去换行后长度 `< SMALL_BODY(80)`（`:298-300`）
10. `--domain` 过滤（`:302`）
11. 未含 `include_done` 时：`_already_done`（指纹一致，`:259-264`）跳过；`attempts >= MAX_ATTEMPTS(2)` 跳过（`:304-310`）

打分（`:311-325`）：`BIG_BODY(2000) <= n <= HUGE_BODY(16000)` +3.0；`n>HUGE_BODY` +0.2；`n>=600` +1.5；有 `chunk_of` +1.2；日志味正则（`\d{2}:\d{2}`/活动记录/会话/`- **`）+1.0；有 `kb_source` +0.3；`is_pointer_page` −1.5。按 `(score, body_chars)` 降序取 `limit`（`:330-331`）。

**② 组装提示词** `_build_prompt_messages`（`:360-369`）→ 由 `PROMPT_STYLE`（`:357`，默认 `"baseline"`）二选一。

**③ 调 LLM** `curate_one`（`:730-793`）：`llm.ask_json(msgs, max_tokens=4096)`（`:736`）；若 `prop is None and "截断" in note` → **就地紧凑重试一次**（`:738-747`，把 sections 每节限 2 条、facts ≤6、JSON ≤800 字）。

**④ 解析** `_extract_json`（`:207-255`）：fence 剥离 → `json.loads` → 找首个 `{` 后按平衡括号截取 → **截断修复**：在 `[,}\]]` 元素边界回退（最多 400 个候选）补 `]`/`}`，且要求结果含 `verdict` 或 `sections`（`:251`）。

**⑤ verdict 分流**（`:753-760`）：`noise`/`raw-only` → `mark_evidence_verdict`（写 frontmatter），**不生成正典、不写提案文件**；其余走校验。

**⑥ 校验** `verify_note`（`:515-633`），见 2.4/2.5。

**⑦ 置信闸门**（`:766-784`）：`prop.confidence < CONF_MIN(0.6)` → `rec["ok"]=True`（判定本身没错）但 `action="review:low-confidence"`，写 `.kb/curate_review.jsonl`（`:778-783`）后 **return，不写回**。

**⑧ 写回** `apply_canon`（`:895-917`）（仅 `--apply`）：
- 先查近乎重复正典 `find_dup_canon`（`:787`，阈值 `DUP_CANON_SIM=0.90`:373），命中则 `skip:duplicate-of(<rel>)`（`:789`）
- 原文按 ID 保号移入 `raw/_curated/<rel>`，重名加 `-1/-2` 后缀（`:900-906`）
- `_canon_fm`（`:703-726`）生成正典 frontmatter，正文 = `# 标题` + `render_canon`（`:636-655`，节序 `结论/依据/操作/坑与边界`）+ 回溯源尾部（`:913-914`）
- `record_lineage(root, rel, archive, "curate-archive-original")`（`:916`）

**⑨ 记账**（`cmd_run:940-983`）：
- 每篇写 `.kb/curate_state.json` 的 `done[rel] = {fp, ts, batch, attempts(+1), ok, action}`（`:957-961`）
- 账本 `.kb/curate_ledger.jsonl` 追加 `{k:v for k,v in rec.items() if k != "proposal"}`（`:963`）
- 完整提案（含 canon 草稿）另写 `.kb/curate_proposals.jsonl`（`:964-965` → `_props_append:119-126`，只保留 rel/sid/ts/batch/verdict/action/model/confidence + proposal）
- `batches[batch_id] = {ts, n, apply, model}`（`:972-973`）
- 批次提案 markdown `70-知识治理 Governance/_curate/<batch_id>.md`（`:975` → `_write_proposals:986-1010`）

**回滚锚**：`--apply` 且未冻结时在**批次开始前**调 `checkpoint(root)`（`:947-948` → `:133-137`，`git add -u` + commit，返回 HEAD）。

### 2.2 使用的 LLM 接口

| 项 | 值 | 位置 |
|---|---|---|
| 方式 | **纯 HTTP**，`urllib.request`，OpenAI 兼容 `/chat/completions`（零 SDK 依赖） | `curate.py:174-179` |
| base | `param > ORNITH_BASE_URL > OPENAI_BASE_URL > "http://127.0.0.1:8000/v1"` | `:146-147` |
| key | `param > ORNITH_API_KEY > OPENAI_API_KEY`（空则不可用） | `:148` |
| model | `param > ORNITH_CHAT_MODEL > "ornith1.5-35b"` | `:149` |
| 可用性探测 | `GET {base}/models`，timeout **8s** | `:152-163` |
| 生成超时 | **180s**（`__init__` 默认参数 timeout=180） | `:145`, `:178` |
| max_tokens | `ask_json` 默认 2048；结晶实际传 **4096** | `:189`, `:736`, `:743` |
| temperature | 默认 **0.2** | `:166`, `:169` |
| stop | 支持（`stop` 参数），结晶路径未使用 | `:166`, `:171-172` |
| 截断识别 | `finish_reason == "length"` → note `truncated(finish=...)`，修复成功则改 `truncated-repaired` | `:182-183`, `:196-203` |
| **重试** | 无 HTTP 层重试（无退避、无 5xx 重试）；只有「截断→紧凑重试 1 次」（`:738-747`）。`MAX_ATTEMPTS=2`（`:267`）是**跨批次**的重选上限，不是单次调用重试 | `:267`, `:309` |
| 失败记录 | `rec["error"]=note`、`ok=False`，仍写账本（`:748-752`, `:963`）；HTTPError 把前 200 字节响应体带进 note（`:184-185`） | — |
| 降级 | 模型不可用 → `cmd_run` 直接打印 plan 清单后 `return 0`（**退出码 0**，`:924-931`） | — |

> 注意：`curate.LLM` **不读 `.kb/model.json`**（只有 `contextual.py:84-91`、`llm_rerank.py:72/113`、`kb_eval_judge.py:47-51`、`embed_rerank.py:55-74` 走 `kb_common.model_config:221`）。而 cron 脚本 `growth_cron.sh:126`、`kb-curate.sh:49` 也用 `ORNITH_API_KEY` 环境变量做闸门。即：**「库自带模型配置」这条 P2 修复没有落到 E 组任何模块**。

### 2.3 提示词分层与 token 预算

`PROMPT_STYLE` 默认 `baseline`（`:357`），可由 `--prompt layered` 切换（`:1208-1209`, `:1225-1226`）。`curate_prompt.py:277-281` 的注释说明：三轮同批 A/B 中四层结构在「JSON 合法率 / 三关通过率 / 耗时」三项均未胜出，故默认 baseline。

**layered（四层）** `build_messages`（`curate_prompt.py:190-221`）：
| 层 | 内容 | 位置 |
|---|---|---|
| L1 契约 | `schema_block`：从 `reference/kb-schema.json` 投影 `page_type` 的必填/可选节；schema 缺失 → 兜底 `canon-generic` | `:73-86`, `:203` |
| L2 规则 | `SYSTEM_HEADER`(31) + `RULES`(34, 9 条，含 facts 三分型 verbatim/extracted/inferred) + `LENGTH_RULES`(48) 拼成 system | `:200` |
| L3 任务 | 任务句 + 「好内容的判据（逐条自检）」5 条 + `## 原文`（`body[:max_body]`，**max_body=12000**）+ `## 已有正典候选` + `json_contract` | `:204-216` |
| L4 范文 | `exemplars(k=2)` → `exemplar_block`，**字符预算 EXEMPLAR_BUDGET=600**，每篇最多 4 节、每节 2 条、每条 44 字，超预算即停 | `:125-168`, `:214-215` |

附加：`len(body) > 5000` 时追加「再次强调」段（`:217-219`）。
`json_contract`（`:172-187`）要求 `verdict/page_type/title(≤30)/summary(≤60)/sections/facts/sources/dropped/confidence/merge_into`。
token 预算无显式 token 计算（未用 `kb_common.count_tokens:130`），只有**字符数**控制（12000 正文 + 600 范文 + 固定规则），model 侧由 `max_tokens=4096` 兜底。

**baseline（默认）** `build_messages_baseline`（`:333-342`）：
- system = `BASELINE_SYSTEM`（`:281-301`，8 条铁律 + 长度纪律 + 「只输出一个 JSON」）
- user = `BASELINE_USER`（`:303-331`）填入 `sid/rel/body[:max_body=12000]/cands`，末尾追加「【页型】<pt>」
- **不使用范文**（k_examples=0 形参未使用）；JSON 模板固定 4 节（结论/依据/操作/坑与边界），**模板里没有 `page_type` 字段**。
- `cands` 来自 `_merge_candidates`（`:224-244`）：先试 `curate._existing_canon`（相似度 ≥ `REUSE_WINDOW=0.35`，`curate.py:423`），全无则兜底给「库内质量最高的 3 条正典标题」。

### 2.4 尺寸感知守恒校验

`verify_note`（`curate.py:515-633`）：

```
ratio = len(render_canon(prop)) / len(body)                       # :521
n_src = len(body)
n_src <= 600   → min_ratio=0.03, min_chars=max(60, int(n_src*0.10))     # :527-528
n_src <= 3000  → min_ratio=HARD_MIN_RATIO(0.04), min_chars=min(300, n*0.10)  # :529-530
else           → min_ratio=0.04, min_chars=HARD_MIN_CHARS(300)          # :531-532
ratio < min_ratio 或 len(canon) < min_chars → 硬失败「过度压缩」       # :535-537
elif ratio < MIN_RATIO(0.12) → ⚠️压缩偏狠（仅警告）                     # :538-539
elif ratio > MAX_RATIO(0.65) → ⚠️压缩偏轻（仅警告）                     # :540-542
```
设计动机写在注释里：短碎片不能套长原文的绝对字数门槛（`:524-526`）。
`hard_kw = ("过度压缩", "未给出 sources", "全部 facts", "缺页型必填节")`（`:629-630`）→ 只有这 4 类算 hard fail；其余入 `warnings`。

### 2.5 其余判据（同一次 `verify_note` 内）

| 关 | 逻辑 | 位置 |
|---|---|---|
| 接地关 | facts 逐条去空白后在 `norm_body` 里 `in` 校验；`inferred` 型不入分母；`vault:/memory//openclaw//raw/` 或含 `::` 的值视为溯源 ID 跳过；`[[X]]` 去包裹；全不接地且总数 ≥3 → 「疑似编造」 | `:544-577` |
| 推断标记 | 有 inferred 时要求正文含 `^[推断]`，缺失则警告 | `:578-582` |
| 页型关 | 读 `reference/kb-schema.json`；`missing_sections` → 硬失败；`extra_sections` → 警告 | `:584-598`, `load_schema:507-512` |
| 蒸馏关 | `_boilerplate_ratio`（寒暄行 + 指令性开头 + 重复短行）≥0.05 且行数 ≥8 → 警告 | `:600-607`, `_boilerplate_ratio:486-504`, `is_filler_line:475-482` |
| 覆盖关 | `_key_identifiers`（6 类正则：反引号串/路径/版本/主机端口/量纲/文件名，cap=40，`_plausible_id:443` 过滤表格与纯标点）覆盖率 <60% 且总数 ≥8 → 警告 | `:609-621`, `_ID_PATTERNS:430-437` |
| 引用关 | `sources` 为空 → 硬失败「未给出 sources」 | `:623-627` |

> **页型关在默认（baseline）档位下永不触发**：baseline 的 JSON 模板不含 `page_type`（`curate_prompt.py:314-329`），而 `verify_note` 只在 `prop.get("page_type")` 非空时校验（`curate.py:585-586`）→ `canon_type` 恒为 `canon-generic`（`:709`）。见问题 #6。

### 2.6 recheck 重审

`cmd_recheck(root, apply_pass, export)` — `curate.py:1110-1191`：
- 数据源：`.kb/curate_proposals.jsonl`（完整提案旁路，`:1117`；缺失则打印指引并 `return 3`，`:1118-1122`）
- 按 `rel` 取 `ts` 最大者作最终提案（`:1124-1135`）
- 仅对 `action in ("propose", "review:low-confidence")` 的笔记重审（`:1139-1140`）；低置信项**原样保留人工闸门**，不因规则放宽而绕过（`:1146-1149`）
- `--apply-pass`：`checkpoint(root)` 后逐篇 `apply_canon(..., model="recheck")`，并回写 `done[rel]`（batch=`recheck`）（`:1156-1171`）
- `--export`：写 `70-知识治理 Governance/_curate/REVIEW-NEEDED.md`，含未过原因、模型标题/摘要、`kb raw show` 取原文命令、canon 草稿（`:1173-1190`）

### 2.7 提案旁路

两级存储设计（`:41`, `:112-126`）：
- `LEDGER=.kb/curate_ledger.jsonl`：**精简**，`proposal` 字段被剥掉（`:963`），供 `report`/`verify` 统计
- `PROPS=.kb/curate_proposals.jsonl`：**完整**，含 canon 草稿，供 `recheck` 与人工复核（`:119-126`）

`cmd_verify`（`:1013-1048`）离线重校验：从精简账本读 `proposal`……**但账本里根本没有 proposal 字段**（被 `:963` 剥掉），因此过滤条件 `if not r.get("proposal"): continue`（`:1027-1028`）会过滤掉所有行 → 只要走新流程写的账本，`cmd_verify` 恒输出「（无提案记录）」。见问题 #5。

### 2.8 状态文件与账本（字段含义）

| 文件 | 路径常量 | 写入者 | 字段 |
|---|---|---|---|
| `curate_state.json` | `STATE` `:39` | `_save_state:106`, `cmd_run:957-974`, `cmd_recheck:1165-1170` | `done{ rel: {fp(正文 sha256 指纹), ts, batch, attempts, ok, action} }`；`batches{ batch_id: {ts, n, apply, model} }` |
| `curate_ledger.jsonl` | `LEDGER` `:38` | `_ledger_append:112` | 每条：`rel, sid, model, ts, body_chars, ok, note, error?, verdict?, confidence?, verified_ok?, action?, verify{ok,issues,warnings,checks,grounded_facts,dropped_facts}, dup_of?, batch, secs` |
| `curate_proposals.jsonl` | `PROPS` `:41` | `_props_append:119` | `rel, sid, ts, batch, verdict, action, model, confidence, proposal{verdict,page_type,title,summary,sections,facts,sources,dropped,confidence,merge_into}` |
| `curate_review.jsonl` | `REVIEW` `:40` | `_review_append:860` | `ts, rel, sid, reason("low-confidence"), confidence, model, verify_ok, issues[≤5], title, canon[≤4000]` |
| 批次提案 md | `PROPOSAL_DIR` `:37` | `_write_proposals:986` | `70-知识治理 Governance/_curate/<batch_id>.md` |
| git checkpoint | `checkpoint:133` | `cmd_run:947`, `cmd_recheck:1157` | `pre-curate checkpoint @ <时间>` |

### 2.9 闸门与开关

- **L4 冻结闸门**：`l4_frozen(root)`（`:796-810`）读 `.kb_l4_config.json` 或 `.kb/state/l4_config.json` 的 `frozen.lc`；`cmd_run:943-946` 若冻结则把 `apply` 强制降为 False（只出提案）。读不到/损坏 → 不冻结（best-effort）。
- **时间预算**：`--max-seconds`（`:1206`），每篇开始前检查（`:950-952`），到时 break，未处理者不写 state。
- **默认只出提案**：`--apply` 才写回（`:1204`）。

---

## 三、逐文件详解

### 3.1 `curate_prompt.py`（342 行）

- **职责**：结晶提示词层（四层 + baseline），页型猜测、契约投影、范文库、merge 候选；不调 LLM。
- **关键函数/类**
  - `load_schema(root: Path) -> Optional[dict]` `:56`
  - `guess_page_type(body: str, fm: Dict[str, Any]) -> str` `:63`（`TYPE_HINTS:24-29` 关键词打分，<2 分 → `canon-generic`）
  - `schema_block(schema: Optional[dict], page_type: str) -> str` `:73`
  - `_canon_rows(root: Path) -> List[Dict[str, Any]]` `:90`（质量分 `:118`：`curate_confidence + min(len(grounded),10)*0.01 + min(len(sections),4)*0.02`）
  - `exemplars(root: Path, body: str, fm: Dict[str, Any], k: int = 2) -> List[Dict[str, Any]]` `:125`
  - `exemplar_block(exs: List[Dict[str, Any]], budget: int = EXEMPLAR_BUDGET) -> str` `:148`
  - `json_contract(page_type: str, required: List[str], optional: List[str]) -> str` `:172`
  - `build_messages(root, body, fm, sid, rel, page_type=None, k_examples=2, max_body=12000) -> List[Dict[str,str]]` `:190`
  - `_merge_candidates(root: Path, body: str, topk: int = 3) -> str` `:224`
  - `build_messages_baseline(body, fm=None, sid="", rel="", page_type=None, k_examples=0, max_body=12000, merge_text="（无）")` `:333`
  - `main() -> int` `:248`（自检 CLI：`show` / `bank`）
- **关键常量**：`TYPE_HINTS:24`、`EXEMPLAR_BUDGET=600:145`、`SYSTEM_HEADER:31`、`RULES:34`、`LENGTH_RULES:48`、`BASELINE_SYSTEM:281`、`BASELINE_USER:303`
- **CLI**：`show --root --text`、`bank --root --limit`（`:248-271`）
- **读写**：读 `reference/kb-schema.json`、全库 canon（`_canon_rows`）；**不写任何文件**
- **依赖**：`kb_common.iter_notes/load_note_full/tokenize`；反向 `import curate`（`:231`，循环依赖，靠函数内延迟导入规避）
- **默认开关**：`k_examples=2`（layered 用）；baseline 忽略

### 3.2 `kb_claim.py`（423 行）

- **职责**：声明验证循环（方案 C/Layer 1）——把编译笔记抽成可证伪声明，用「更晚的 raw 数据」判 confirm/falsify/irrelevant，得 `verified_ratio` / `falsified_ratio`＝预测误差。
- **关键函数/类**
  - `_chat(messages)` `:50` → `(content|None, reason)`；读 **ORNITH_BASE_URL + ORNITH_API_KEY 两者都必须存在**（`:52-56`）
  - `_parse_json(s)` `:71`；`_extract_claims(text)` `:90`；`_judge(claim_text, raw_text)` `:118`
  - `_signature(text)` `:142`；`_jaccard(a, b)` `:147`；`_dot(a, b)` `:154`；`_num(v, default=0.0)` `:158`；`_parse_d(v)` `:168`
  - `_store(root)` `:177`（StateStore，失败降级直读文件）；`_claims_path(root)` `:186`；`_load_claims(root)` `:190`；`_save_claims(root, data)` `:206`
  - `collect_notes(root: Union[str, Path]) -> List[Dict[str, Any]]` `:220`（复用 `kb_rsi.collect`，失败走内联 rglob 兜底）
  - `sync_claims(root, max_per_note=_MAX_PER_NOTE, max_total=_MAX_TOTAL) -> Dict[str, Any]` `:240`（`ThreadPoolExecutor`，`workers=min(8, max(2, len(notes)))` `:254-256`）
  - `_candidate_pairs(root, claims, sim_threshold=_SIM_THRESHOLD, jaccard_min=_JACCARD_MIN)` `:287`
  - `validate(root, max_pairs=_MAX_PAIRS, sim_threshold=_SIM_THRESHOLD, jaccard_min=_JACCARD_MIN) -> Dict[str, Any]` `:316`
  - `fitness_from_claims(claims: List[Dict[str, Any]]) -> Dict[str, Any]` `:363`
  - `report(root) -> str` `:381`；`stats(root) -> str` `:395`；`main() -> int` `:403`
- **关键常量**（`:34-42`）：`CLAIMS_FILE="claims.json"`、`CHAT_MODEL=ORNITH_CHAT_MODEL 或 "ornith1.5-35b"`、`_MAX_PER_NOTE=5`、`_MAX_TOTAL=300`、`_MAX_PAIRS=60`、`_LLM_TIMEOUT=40`、`_SIM_THRESHOLD=0.55`、`_JACCARD_MIN=0.12`、`_RAW_TEXT_CAP=2000`
- **CLI**：`sync|validate|report|stats [--root]`（`:403-419`）
- **读写**：读全库笔记；写 `.kb/state/claims.json`（经 StateStore 或直写）
- **依赖**：`kb_rsi`、`state_manager.StateStore`、`kb_embed`（`_candidate_pairs:289`，局部导入）
- **默认开关**：LLM/embedding 缺失即静默 pending；无任何自动写笔记

### 3.3 `kb_confidence.py`（392 行）

- **职责**：四因子复合置信分 + 绝对/相对分档 + 原则性遗忘审计。
- **关键函数/类**
  - `_score_abs(fm, body, today=None) -> float` `:42`
  - `_basis_field(body: str) -> Tuple[float, Optional[str]]` `:53`
  - `_corroboration(n_sources: int) -> Tuple[float, str]` `:67`
  - `_recency(updated: Any, today: Optional[date] = None) -> Tuple[float, str]` `:77`
  - `_verification(fm: Dict[str, Any]) -> Tuple[float, str]` `:94`
  - `population_cutoffs(root: Path, refresh: bool = False) -> Tuple[float, float]` `:123`
  - `score(fm, body, today=None, pop_p10=None, pop_p3=None) -> Dict[str, Any]` `:147`
  - `explain(root: Path, rel: str) -> Dict[str, Any]` `:177`
  - `_upsert_fm(text: str, updates: Dict[str, Any]) -> Optional[str]` `:189`
  - `cmd_audit(root, limit, as_json) -> int` `:213`；`cmd_apply(root, dry, limit) -> int` `:255`
  - `decay_verdict(root, rel, fm, body, hits=0, today=None) -> Dict[str, Any]` `:287`
  - `_read_hits(root: Path) -> Dict[str, int]` `:323`；`cmd_decay(root, as_json, limit) -> int` `:338`；`main() -> int` `:364`
- **关键常量**：`W={"extraction":0.50,"corroboration":0.25,"recency":0.15,"verification":0.10}` `:35`；`CONF_PUBLISH=0.55` `:36`；`CONF_EVIDENCE_ONLY=0.35` `:37`；`RECENCY_FULL_DAYS=90` `:38`；`RECENCY_ZERO_DAYS=365` `:39`；`DECAY_MIN_ACCESS=1` `:283`；`DECAY_STALE_DAYS=120` `:284`
- **CLI**：`show --root --rel [--json]`、`audit --limit`、`apply [--dry-run] [--limit]`、`decay --limit`（`:364-388`）
- **读写**：读全库；`apply` 非 dry 时写笔记 `confidence`/`confidence_tier`（`:267-273`）
- **依赖**：`kb_common.iter_notes/load_note_full/is_raw_path/is_generated_report`；被 `curate._composite_confidence:685` 导入
- **默认开关**：`apply` 的 `--dry-run` 默认 **False**（`:372`），即 **不传 `--dry-run` 就真写**；但模块 docstring（`:20`）与 `cmd_apply` docstring（`:256`）都写「默认干跑」→ 文档与实现相反（见问题 #20）。`--limit 0` 表示无上限（`:274`）

### 3.4 `kb_contradiction.py`（490 行）

- **职责**：矛盾 lint（P1-C）。启发式否定密度 + 倒排索引预过滤 + 可选 LLM 极性；只读产出清单，人工 `apply` 才 retire。
- **关键函数/类**
  - `_vec(s)` `:75`（CJK 单字 + 拉丁词）；`neg_density(text: str) -> float` `:110`；`_sample_with_neg(text)` `:116`
  - `collect(root: Union[str, Path]) -> List[Dict[str, Any]]` `:129`
  - `detect(root: Union[str, Path]) -> List[Dict[str, Any]]` `:154`
  - `_maybe(notes, i, j, pairs, root=None)` `:215`
  - `_llm_polarity(root, text_a: str, text_b: str) -> Optional[bool]` `:246`
  - `contradiction_zero_score(n_pairs: int) -> float` `:280`（`1/(1+n)`）
  - `render(pairs: List[Dict[str, Any]]) -> str` `:285`
  - `_parse_retain(specs: Optional[List[str]]) -> Dict[int, str]` `:310`；`_git_commit(root, rel, msg)` `:326`
  - `apply(root, ids=None, retain=None, force=False) -> Dict[str, Any]` `:335`
  - `main() -> int` `:435`
- **关键常量**：`MIN_BODY_CHARS=40` `:87`；`MIN_NEG_DENSE=0.020` `:88`；`MAX_NEG_DENSE=0.015` `:89`；`_NEG_CN` `:94-97`；`_NEG_EN` `:100-105`；`CONTRA_SIM=0.40`（`kb_constants.py:14`）；`MAX_NOTES_CONTRA=400`（`kb_constants.py:29`）
- **CLI**：默认（无子命令）= 检测；`apply [--root] [--json] --id ... --retain 1:a 2:b [--force]`（`:436-459`）
- **读写**：写 `pipeline/.kb_contradictions.json`（含稳定 id：`:464`）、`pipeline/kb_contradictions_report.md`（`:477-479`）、`apply` 时改冲突一侧 frontmatter（`kb_action/status/category/contradiction_pair_id/contradiction_survivor`，`:391-397`）+ 保留侧 `contradiction_survived`（`:402`）+ 单文件 git commit（`:403`）+ `evolution_log`（`:408-410`）
- **依赖**：`kb_rsi`、`kb_common(norm/cos/build_inverted_index/candidate_pairs)`、`kb_constants`、`kb_engine.git_backup/set_fm_field`（`:39`，失败降级本地实现）、`rag.llm_answer`（`:267`）
- **默认开关**：扫描只读；`apply` 需人工指定 retain 侧
- **⚠️ 无插件包装**：`plugins/` 下无对应 plugin，CLI 只有 `plugins/contradict_plugin.py` → `kb_contradiction2.py`

### 3.5 `kb_contradiction2.py`（237 行）

- **职责**：矛盾在环（P3）：正典间 section 文本 Jaccard + 变更词 → 冲突/近似重复；默认 dry-run。
- **关键函数/类**
  - `_sections(body: str) -> Dict[str, str]` `:49`；`_emphasis(body: str) -> str` `:61`；`_jacc(a: str, b: str) -> float` `:68`
  - `_change_hits(text: str) -> List[str]` `:75`；`_day(fm: Dict[str, Any]) -> str` `:80`
  - `scan(root: Path, threshold: float = OVERLAP_MIN, limit: int = 0) -> Dict[str, Any]` `:84`
  - `_callout_text(other_rel: str, day: str) -> str` `:135`；`_upsert_fm_simple(text: str, key: str, value: str) -> Optional[str]` `:140`
  - `apply_marks(root: Path, pairs: List[Dict[str, Any]], dry: bool, limit: int) -> int` `:160`
  - `main() -> int` `:193`
- **关键常量**：`CHANGE_KW`（24 个变更词）`:33-38`；`OVERLAP_STRICT=0.35` `:40`；`QUEUE=".kb/contradiction_queue.jsonl"` `:42`；`DUP_QUEUE=".kb/duplicate_canon_queue.jsonl"` `:43`；`OVERLAP_MIN=0.25` `:44`；`DUP_LIKE=0.85` `:45`；`SECTIONS` `:46`
- **CLI**：`scan --root --threshold --json`、`apply --root --threshold --dry-run --limit`（`:193-233`）
- **读写**：`apply` 非 dry 时写笔记 `conflicts_with` + callout（`:172-177`）、两个 queue jsonl（`:181-186`, `:222-228`）、`git add`（**不 commit**，`:188-189`）
- **依赖**：`kb_common(ROOT_DEFAULT/iter_notes/load_note_full/is_raw_path/is_generated_report/tokenize/root_of)`
- **默认开关**：文档称「默认 dry-run；`--apply` 才写」（`:17`），但 `apply` 子命令的 `--dry-run` 默认 False（`:201`）→ **`kb contradict apply` 不传 `--dry-run` 即真写**（见问题 #46）
- **被暴露为**：`plugins/contradict_plugin.py` → `kb contradict scan|apply`

### 3.6 `kb_consistency.py`（281 行）

- **职责**：晋升记录（`.kb/promotions.jsonl`）语义一致性审计：精确漂移 / 出处保留 / 覆盖召回（Tier1）+ 可选 LLM 忠实度（Tier2）。
- **关键函数/类**
  - `_tokens(text)` `:37`；`_split_body(text)` `:42`；`_sha(text)` `:50`；`_jaccard_recall(note_toks, orig_toks)` `:54`；`_has_git(root)` `:62`
  - `_endpoint_reachable(timeout=5)` `:72`
  - `_check_faithfulness(root, original_text, note_body, timeout=LLM_TIMEOUT)` `:92`（线程 + `th.join(timeout)` 硬超时）
  - `_severity(reasons)` `:130`；`audit_record(rec, root, cov_thr)` `:146`；`main()` `:201`
- **关键常量**：`LEDGER_NAME="promotions.jsonl"` `:32`；`DEFAULT_COV_THR=0.30` `:33`；`LLM_TIMEOUT=15` `:34`；`_severity` 映射 `:132-134`
- **CLI**：`--root --cov-thr --report --json`（`:201-211`）
- **读写**：读 `.kb/promotions.jsonl` + 目标笔记；写 `.kb/consistency-report-<date>.md`（`--report`，`:259-275`）
- **依赖**：**模块级 `import rag`**（`:29`）
- **默认开关**：Tier2 由 `_endpoint_reachable` 决定（`:99`），不可达即静默跳过

### 3.7 `kb_calibrate.py`（271 行）

- **职责**：在仿真语料上 A/B 标定 DUP/MERGE/RETIRE 阈值（precision/recall/F1 + 推荐值）。
- **关键函数/类**
  - `_tokenize(s)` `:33`；`_vec(s)` `:37`；`_synth_text(rng, base, keep_ratio, vocab)` `:58`
  - `synth(root, n=300, pairs=40, seed=7, today=None) -> Dict[str, Any]` `:70`
  - `subprocess_run(root)` `:120`；`_load_notes(root)` `:125`；`_read_gt(root, name)` `:130`
  - `calibrate(root, what: str, csv_out=None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]` `:144`
  - `_parse_d(s)` `:209`；`_maybe_csv(csv_out, what, rows)` `:217`；`_print_report(what, rows, best, default, unit="")` `:228`；`main() -> int` `:240`
- **关键常量**：`_SEED_TEXTS:45-54`；`_DEFAULTS={"dup":0.85,"merge":0.97,"retire":180}` `:225`；扫描区间 dup `[0.60,0.99]` / merge `[0.90,0.99]` / retire `range(30,370,10)`（`:159`, `:190-191`）
- **CLI**：`synth --root --n --pairs --seed`、`dup|merge|retire --root --csv`、`all --root`（`:240-267`）
- **读写**：`synth` **删除并重建 root**（`:74-75`）后写仿真笔记 + `gt_dup.json`/`gt_over.json`；`--csv` 写 CSV
- **依赖**：`kb_common.cos`、`kb_rsi.collect`
- **默认开关**：不修改任何生产阈值（标定结果只打印）

### 3.8 `kb_drift.py`（280 行）

- **职责**：漂移度量（只读）：内容漂移率、平均长度变化、出处保留率、时间窗趋势 + 突变告警、新鲜度/decay 视图。
- **关键函数/类**
  - `_split_body(text)` `:20`；`_sha(text)` `:28`；`_read_frontmatter_keys(target_path)` `:32`；`_has_git(root)` `:48`
  - `_window_key(ts_str, window)` `:59`；`_parse_age(ts_str, now)` `:75`；`_human_age(seconds)` `:84`
  - `_analyze_records(records, now, root, window)` `:95`
  - `_print_summary(per_rec)` `:132`；`_print_trend(per_rec, window, alert)` `:154`；`_print_decay(per_rec)` `:192`；`main()` `:227`
- **关键常量**：`LEDGER_NAME="promotions.jsonl"` `:15`；`DEFAULT_ALERT_PPTS=15` `:16`；`DECAY_AGE_DAYS=60` `:17`
- **CLI**：`--root --window {day,week,month} --decay --alert`（`:228-237`）
- **读写**：只读；**不写任何文件**
- **依赖**：纯标准库
- **默认开关**：全部只读

### 3.9 `kb_l4.py`（529 行）

- **职责**：L4 宪法（人工裁判层）：docket 汇编各 rung「已应用待裁决」→ 真人 `judge` → 连续错 ≥3 且样本 ≥5 冻结 rung（接地阀不自动冻）；`unfreeze` 由人工解除。
- **关键函数/类**
  - `_verdict_path/_state_path/_config_path(root)` `:81/85/89`
  - `_read_state(root)` `:93`；`_write_state(root, state)` `:106`；`_read_config(root)` `:111`；`_write_config(root, config)` `:124`
  - `_checkpoint(root, message, paths) -> bool` `:129`
  - `grounding(root) -> Tuple[bool, str, Dict[str, Any]]` `:144`
  - `_read_verdicts(root)` `:158`；`_write_verdict(root, rec)` `:168`
  - `_docket_l0/_docket_l1/_docket_l2/_docket_l3/_docket_lc(root)` `:177/186/201/210/219`；`_DOCKET_READERS` `:240`
  - `docket(root) -> Dict[str, Any]` `:244`
  - `build_key(rung, rel=None, knob=None, action="boost") -> Optional[str]` `:265`
  - `_verdict_stats(verdicts)` `:280`；`_tail_consecutive_wrong(per_rung)` `:293`
  - `judge(root, rung, key=None, rel=None, knob=None, verb="correct", action="boost", judge_by="anon") -> Dict[str, Any]` `:309`
  - `freeze(root) -> Dict[str, Any]` `:328`；`unfreeze(root, rung) -> Dict[str, Any]` `:381`
  - `status(root) -> Dict[str, Any]` `:397`；`render(root, summary=None) -> str` `:426`；`main() -> int` `:468`
- **关键常量**：`L4_RUNGS=("l0","l1","l2","l3","lc")`（`kb_constants.py:68`）；`L4_FREEZE_CONSEC=3`（`:70`）；`L4_MIN_SAMPLE=5`（`:71`）；`L4_TRUST_FLOOR=0.5`（`:72`，仅陈述未使用）；账本路径 `:69-73`
- **CLI**：`docket|report --root`；`judge --rung --key/--rel/--knob --verb --action --judge`；`freeze --root`；`unfreeze --rung`；`status --json`（`:468-525`）
- **读写**：写 `.kb_l4_verdicts.jsonl`、`.kb_l4_state.json`、`.kb_l4_config.json`（+ git commit）；读四个 rung 账本 + `.kb/curate_review.jsonl`
- **依赖**：`kb_rsi`、`kb_constants`
- **默认开关**：无裁决 → 不冻结；接地不足 → 不自动冻

### 3.10 `kb_l5.py`（473 行）

- **职责**：L5 元策略：按「rung 活跃期 ↔ 外部结果趋势」给 running score ±0.25，归一到 `[0.05,1.0]` 激活权重，供调度器决定跑哪些层。
- **关键函数/类**
  - `_state_path/_config_path(root)` `:75/79`；`_read_state(root)` `:83`；`_write_state` `:96`；`_read_config` `:101`；`_write_config` `:114`；`_checkpoint(root, message, paths) -> bool` `:119`
  - `grounding(root)` `:134`；`_outcome(root) -> Optional[Dict[str, Any]]` `:147`；`_rung_activity(root, rung) -> int` `:157`
  - `_classify(prev: Optional[Dict[str, Any]], cur: Dict[str, Any]) -> str` `:177`
  - `train(root) -> Dict[str, Any]` `:197`
  - `_weights_from_scores(scores, n_cycles) -> Tuple[Dict[str, float], bool]` `:245`
  - `propose(root) -> Dict[str, Any]` `:256`；`apply(root) -> Dict[str, Any]` `:269`；`status(root)` `:284`；`render(root, summary=None) -> str` `:296`；`main() -> int` `:329`
- **关键常量**（`kb_constants.py:80-88`）：`L5_RUNGS=("l0","l1","l2","l3")`、`L5_SCORE_MIN/MAX=±1.0`、`L5_STEP=0.25`、`L5_IMPROVE_MIN=0.5`、`L5_DEGRADE_MIN=1.0`、`L5_WEIGHT_FLOOR=0.05`、`L5_WEIGHT_CEIL=1.0`、`L5_MIN_SAMPLE=3`；活动来源 `_L5_ACTIVITY:66-71`；cycle 上限 64（`:231`）
- **CLI**：`report|train|propose|apply|status --root [--json]`（`:329-363`）
- **读写**：写 `.kb_l5_state.json`；`apply` 写 `.kb_l5_policy.json` + git commit
- **依赖**：`kb_rsi`
- **默认开关**：`train` 才改分；`apply` 才发布

### 3.11 `kb_rsi.py`（473 行）

- **职责**：RSI 只读探针：全库采集 + 扁平指标 + 提议生成 + 外部接地防塌阀 + 集中度护栏。
- **关键函数/类**
  - `load_note(p: Path) -> Tuple[Dict[str, Any], str, str]` `:38`；`tokenize(s: str) -> List[str]` `:59`；`_use_embed()` `:78`；`_safe_embed(text)` `:86`；`vec(s: str)` `:95`；`cos(a, b)` `:107`
  - `parse_f(v: Any, default: float = 0.0) -> float` `:127`；`slug(rel)` `:132`；`is_archive(rel)` `:136`；`in_inbox(rel)` `:140`；`is_moc(rel, root=None)` `:145`；`is_raw(rel)` `:153`
  - `collect(root: Union[str, Path]) -> List[Dict[str, Any]]` `:158`；`compiled_notes(root)` `:179`；`sources(root)` `:184`
  - `metrics(notes: List[Dict[str, Any]], root: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]` `:189`
  - `_concentration(notes)` `:294`
  - `proposals(m: Dict[str, Any], root=None) -> Tuple[List[Dict[str, Any]], str, bool]` `:330`
  - `render(proposals, m, grounded_note) -> str` `:391`；`main() -> int` `:419`
- **关键常量**：`EXCLUDE_DIRS={".obsidian",".git","vector index",".kb",".backups"}` `:29`；`RAW_TOP="raw"` `:31`；陈旧阈值 `age > 90`（`metrics:219`）；`DUP_SIM=0.85`（`kb_constants.py:11`）；集中度告警 `top_share>0.5` / `simpson<0.5`（`:319-323`）
- **CLI**：`--root --json --verbose`（`:419-424`）
- **读写**：唯一写入 `pipeline/.kb_rsi_proposals.jsonl` 追加一行（`:445-449`）
- **依赖**：`kb_common`、`kb_constants`、可选 `kb_embed`（**默认关闭**，需 `USE_EMBEDDING=1`，`:69-83`）
- **默认开关**：只读

### 3.12 `kb_engine.py`（705 行）

- **职责**：RSI 执行编排器 T1（整理：合并/retire/补链）/ T2（外部锚定加权）/ T3（自调参，默认关）；质量门 + git checkpoint。
- **关键函数/类**
  - `_resolve_health_weights(cfg)` `:61`；`load_config() -> Dict[str, Any]` `:72`；`save_config(cfg: Dict[str, Any]) -> None` `:83`
  - `git_add_commit(root, rels: List[str], msg: str) -> bool` `:90`；`git_backup(root, msg: str) -> None` `:104`
  - `is_raw_rel(root, rel: str) -> bool` `:115`；`_raw_guard(root, p)` `:124`；`read_note(p: Path)` `:134`；`set_fm_field(p: Path, key: str, value: Any) -> bool` `:138`
  - `log_decision(cfg, root, tier, action, target, confidence, before, after, extra=None) -> Dict[str, Any]` `:167`
  - `_grounded(m)` `:191`；`health_score(m: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> float` `:201`
  - `tier_t1(root, cfg: Dict[str, Any], do_apply: bool) -> List[Dict[str, Any]]` `:214`
  - `tier_t2(root, cfg, do_apply) -> List[Dict[str, Any]]` `:325`
  - `_load_last_ab_snapshot(root: Path) -> Optional[dict]` `:385`；`_save_ab_snapshot(root: Path, snapshot: dict) -> None` `:404`；`_count_consecutive_neg_delta(root: Path) -> int` `:412`；`_build_ab_report(root: Path) -> str` `:436`
  - `tier_t3(root, cfg, do_apply) -> List[Dict[str, Any]]` `:474`
  - `_log_ev(root, event, summary, detail=None)` `:607`；`main() -> int` `:616`
- **关键常量**：`CONFIG_PATH=pipeline/.kb_rsi_config.json`（**模块目录硬编码**，`:32`）；`CONFIG_DEFAULT` `:33-42`（`confidence_floor=0.60`、`tiers_enabled=["t1","t2"]`、`t3_enabled=False`、`t3_step=0.10`）；`MERGE_MAX_IMP=0.70` `:43`；`HEALTH_SCORE_WEIGHTS` `:53-58`；`MERGE_MIN_SIM=0.97`/`RETIRE_AGE_DAYS=180`（`kb_constants.py:13/24`）
- **CLI**：`--root --run t1,t2[,t3] --auto --status --reset-config --ab-report`（`:616-624`）
- **读写**：写笔记 frontmatter（retire/link/importance）、`pipeline/.kb_rsi_config.json`、`pipeline/.kb_engine_proposals.jsonl`、git commit
- **依赖**：`kb_rsi`、`kb_fitness`、`kb_scale`、`feedback_loop`
- **默认开关**：**默认只提议**（`--auto` 才写），T3 默认关（`:38`, `:650-653`）

### 3.13 `kb_fitness.py`（168 行，本组关键外部依赖）

- **职责**：外部锚定 fitness（P1-A），给 T3 一个「T1/T2 刷不动」的调参依据。
- **关键函数/类**：`_shannon_norm(counts)` `:49`；`source_diversity(root, notes)` `:58`；`external_feedback_rate(root, compiled_rels)` `:69`；`contradiction_component(root)` `:82`；`query_success_component(root, window=3600)` `:88`；`fitness(root) -> Dict[str, Any]` `:101`；`main()` `:143`
- **关键常量**：`W_SOURCE=0.28`、`W_FEEDBACK=0.28`、`W_CONTRAD=0.24`、`W_QUERY=0.20`（`:38-41`，和为 1.0）；`FEEDBACK_TARGET=0.10` `:44`
- **CLI**：`--root --json`（`:143-147`）
- **读写**：只读（经 `kb_contradiction.detect` 会写矛盾快照？——不会：`contradiction_component:84` 只调 `detect()`，不写快照，快照由 `kb_contradiction.main:477` 写）
- **依赖**：`kb_rsi`、`feedback_loop`、`kb_contradiction`、`kb_usage`
- **默认开关**：只读

---

## 四、LLM 调用点全景表

> 「方式」= 实际网络调用形式；「配置来源」= key/base/model 从哪读；「超时/重试」= 单次调用参数。

| # | 模块（文件:行号） | 用途 | 方式 | 模型名来源 | 配置来源 | 超时 | 重试 | 降级/离线兜底 |
|---|---|---|---|---|---|---|---|---|
| 1 | `curate.py:141-204`（`LLM` 类）| 结晶：出 canon 提案 | HTTP `urllib` `/chat/completions` | `--model > ORNITH_CHAT_MODEL > "ornith1.5-35b"`（`:149`） | **仅环境变量**（`ORNITH_/OPENAI_`，`:146-149`） | 180s（生成）/ 8s（`/models` 探测） | 无 HTTP 重试；截断→紧凑重试 1 次（`:738-747`） | 不可用 → 只出 plan，退出码 0（`:924-931`）；facts 未接地丢弃 |
| 2 | `kb_claim.py:50-67`（`_chat`）| 声明抽取 + 真假判定 | HTTP `/chat/completions` | `CHAT_MODEL`（`ORNITH_CHAT_MODEL` 或 `ornith1.5-35b`，`:35`） | **仅环境变量**（key 与 url 都必须存在，`:52-56`） | 40s（`:39`） | 无 | 失败返回 `(None, reason)` → 声明 `[]` / pending（`:102-103`, `:130-131`） |
| 3 | `kb_contradiction.py:246-277`（`_llm_polarity`）| 启发式未定时的极性复核 | **间接** HTTP：`rag.llm_answer`（`:267`） | `rag.py:1136` **硬编码 `"ornith1.5-35b"`** | 仅环境变量（`rag.py:1118-1120`）+ `ORNITH_API_KEY` 前置检查（`:257`） | 40s（`rag.py:1138`） | 无 | 返回 None = 不判矛盾（`:268-269`）；异常吞掉返回 None（`:276-277`） |
| 4 | `kb_consistency.py:92-127`（`_check_faithfulness`）| 语义忠实度 faithful/partial/unfaithful | **间接** HTTP：`rag.llm_answer`（`:112`） | 同 #3（硬编码） | `_endpoint_reachable:72-89` 只读环境变量 | 线程 join 15s（`LLM_TIMEOUT:34`），实际 HTTP 40s | 无 | 端点不可达/超时 → `ok=False`，跳过 Tier2（`:99-101`, `:120-122`） |
| 5 | `kb_eval_judge.py:45-87`（`Judge`）| 评测裁判（忠实/相关/精确/要点） | HTTP `/chat/completions` | `model_config()["chat_model"]` 或 `ornith1.5-35b`（`:51`） | **`kb_common.model_config`（环境变量 > `.kb/model.json`）**（`:47-51`） | 180s（`:67`） | 无（`ask_json:73-87` 有 3 种 JSON 容错） | 无 key → `available()=False`，提示「先配 .kb/model.json」（`:161`） |
| 6 | `contextual.py:78-143`（`_LLM`）| 片段定位语（CCH 增强） | HTTP `/chat/completions` | `model_config()["chat_model"]` 或 `ornith1.5-35b`（`:91`） | **`model_config`**（`:83-91`） | 120s（`:116`, `:130`） | 有：content 空且消耗 token → 收紧输入重试 1 次（`:134-139`） | `--llm` 未请求或不可用 → 退回纯 CCH（`:186-187`） |
| 7 | `llm_rerank.py:41-143`（`LLMReranker`）+ `expand_query:159` | 检索重排 / 查询改写 | HTTP `/chat/completions` | `model_config()["chat_model"]` 或 `ornith1.5-35b`（`:114`） | **`model_config`**（`:113`） | `budget_s`（`timeout=self.budget_s`，`:122`） | 无；失败原样返回（`:84-85`） | 无 key → `available()=False`；异常全吞（`:127-128`） |
| 8 | `kb_eval.py:187-215`（`_llm_judge`）| 评测 faithfulness/relevance | **复用** `kb_claim._chat`（`:200`） | 同 #2（`ornith1.5-35b`） | 仅环境变量 | 40s | 无 | 异常/无内容/无 `faithfulness:` → None（`:202-209`） |
| 9 | `kb_usage.py:190-240`（`_claim_chat`/`_judge_requery`）| 「重问是否因前次失败」判定 | **复用** `kb_claim._chat`（`:197`） | 同 #2 | 仅环境变量 | 40s | 无（有 128 条内存缓存 `:237-239`） | content None → `(None, None, reason)`（`:218-219`） |
| 10 | `rag.py:1112-1142`（`llm_answer`）| 问答 / 被 #3#4#memory_sync 复用 | HTTP `/chat/completions` | **硬编码 `"ornith1.5-35b"`**（`:1136`） | 仅环境变量 | 40s（`:1138`） | 无 | 双缺 → `(None,"")`；缺一 → 精准提示文案（`:1121-1129`） |
| 11 | `memory_sync.py:203-235`、`:305-306` | 语义五问 | **间接** `rag.llm_answer`（`:235`） | 同 #10 | 仅环境变量（`ORNITH_API_KEY` 前置判断 `:203`, `:305`） | 40s | 无 | 未设 key → 明确提示并退回关键词门禁（`:305-306`） |
| 12 | `kb_embed.py`（embeddings）/ `embed_rerank.py:55-74` | 向量嵌入（非 chat） | HTTP `/embeddings` | `ORNITH_EMBED_MODEL`（`:49`） | 环境变量（`embed_rerank` 走 `model_config`） | 见模块 | 无 | 缺 key/url → None → 字面向量降级（`:152-156`） |

**关键结论**
- 全项目**没有任何模块使用官方 SDK**，一律 `urllib.request` 裸 HTTP（`curate.py:174`、`kb_claim.py:59`、`rag.py:1135`、`contextual.py:112/126`、`llm_rerank.py:117/183`、`kb_eval_judge.py:63`）。
- **模型名配置不统一**：`kb_common.model_config:221-249`（env > `.kb/model.json`）只被 4 处使用（`contextual.py:84`、`llm_rerank.py:72/113/174`、`kb_eval_judge.py:47`、`embed_rerank.py:55/74`）；**E 组 4 个真实调 LLM 的模块（curate、kb_claim、kb_contradiction、kb_consistency）以及 rag/memory_sync 全部只读环境变量**，`rag.py:1136` 还把模型名写死为 `ornith1.5-35b`。
- **重试策略缺失**：没有任何模块做网络层重试/退避；唯一的「重试」是 curate 的截断紧凑重试（`curate.py:738-747`）与 contextual 的 reasoning 耗尽重试（`contextual.py:134-139`）。
- **超时值散落**：8 / 15 / 40 / 120 / 180 秒五种，且 `kb_consistency` 用线程 join 实现「软超时」，被放弃的 daemon 线程仍在跑（`kb_consistency.py:117-122`）。
- **静默降级普遍**：`kb_contradiction`（返回 None）、`kb_consistency`（`ok=False`）、`kb_claim`（`[]`）在 LLM 不可用时与「真的没有结论」不可区分；`kb_claim.sync_claims` 只有 `llm_available: llm_used`（`:283-284`）一个弱信号。

---

## 五、判据与指标（阈值 / 公式 / 反馈关系）

### 5.1 阈值常量总表

| 指标 | 常量（值） | 定义位置 | 判据 |
|---|---|---|---|
| 结晶压缩比 | `MIN_RATIO=0.12` / `MAX_RATIO=0.65` / `HARD_MIN_RATIO=0.04` / `HARD_MIN_CHARS=300` | `curate.py:44-47` | 见 2.4；hard fail 仅「过度压缩」 |
| 候选体量 | `BIG_BODY=2000` / `SMALL_BODY=80` / `HUGE_BODY=16000` | `curate.py:49-51` | 打分加权；`HUGE_BODY` 未真正跳过（仅 +0.2 分） |
| 批大小 / 相似窗 | `BATCH_DEFAULT=8` / `REUSE_WINDOW=0.35` | `curate.py:52-53` | merge 候选、`_existing_canon` |
| 结晶置信闸门 | `CONF_MIN=0.6` | `curate.py:54` | 模型自评 <0.6 → 转人工队列 |
| 重复正典闸门 | `DUP_CANON_SIM=0.90` | `curate.py:373` | 写回前 `find_dup_canon` |
| 跨批次尝试上限 | `MAX_ATTEMPTS=2` | `curate.py:267` | 未过校验者最多被重选 2 次 |
| 标识符覆盖 | 覆盖 <0.6 且 ids ≥8 → 警告 | `curate.py:618` | 覆盖关 |
| 蒸馏（废话） | `boilerplate_ratio >= 0.05` 且行数 ≥8 | `curate.py:603` | 触发条件成立但内部判定恒 False（见 #1） |
| 置信四因子 | `W={0.50,0.25,0.15,0.10}`；`publish≥0.55`、`evidence-only<0.35`；recency 90d/365d | `kb_confidence.py:35-39` | `score():147-174` |
| 相对分档 | 群体 `p10`/`p3`（`int(n*0.10)-1`, `int(n*0.03)-1`） | `kb_confidence.py:143` | 低于 p10 → review；低于 p3 → evidence-only（`:163-164`） |
| 遗忘 | `DECAY_STALE_DAYS=120`、`importance<0.4`、`hits<=0`、tier≠publish；**命中 ≥3 项** | `kb_confidence.py:283-318` | `decay_verdict:318` |
| 矛盾（启发式） | `CONTRA_SIM=0.40`、`MIN_NEG_DENSE=0.020`、`MAX_NEG_DENSE=0.015` | `kb_constants.py:14`、`kb_contradiction.py:87-89` | 余弦 ≥0.40 且（高否定侧 ≥0.02 且低否定侧 ≤0.015） |
| 矛盾零分 | `1/(1+n_pairs)` | `kb_contradiction.py:282` | fitness 分量 |
| 矛盾（在环） | `OVERLAP_MIN=0.25`（**被 `max()` 抬到 0.35**）、`OVERLAP_STRICT=0.35`、`DUP_LIKE=0.85` | `kb_contradiction2.py:40-45`, `:102` | 主题重合 + 至少一方含变更词 |
| 一致性 | `DEFAULT_COV_THR=0.30` | `kb_consistency.py:33` | Jaccard 召回 <0.30 → coverage-drop(High)；provenance 缺 kb_source/memory_source → Critical |
| 标定默认值 | `{"dup":0.85,"merge":0.97,"retire":180}` | `kb_calibrate.py:225` | 与 `kb_constants.py:11/13/24` 对应 |
| 漂移 | `DEFAULT_ALERT_PPTS=15`、`DECAY_AGE_DAYS=60` | `kb_drift.py:16-17` | 相邻窗口 drift% 跳增 ≥15pp 告警 |
| L4 | `L4_FREEZE_CONSEC=3`、`L4_MIN_SAMPLE=5`、`L4_TRUST_FLOOR=0.5`(未用) | `kb_constants.py:68-72` | `trust = correct/(correct+wrong)`；`should = consec≥3 and n≥5`（`kb_l4.py:344`） |
| L5 | `L5_STEP=0.25`、`L5_SCORE±1.0`、`IMPROVE≥0.5%`、`DEGRADE≥1.0%`、`WEIGHT∈[0.05,1.0]`、`MIN_SAMPLE=3` | `kb_constants.py:80-88` | `weight = clamp(0.5 + 0.5*(s/1.0))`（`kb_l5.py:250-251`） |
| 接地阀 | `external_inflow ≥ 5.0 and cross_ratio ≥ 0.15` | `kb_constants.py:32-33`；硬编码副本 `kb_l4.py:148`、`kb_l5.py:138`、`kb_engine.py:197` | 四处 |
| health_score | `100 − min(45, orphan%×3) − min(45, stale%×3) − (15 if 未接地)` | `kb_engine.py:53-58`, `:201-213` | 可被 cfg `health_weights` 覆盖 |
| fitness | `0.28·来源多样性(Simpson) + 0.28·min(1, 反馈覆盖率/0.10) + 0.24·1/(1+矛盾对数) + 0.20·查询成功率` | `kb_fitness.py:38-44`, `:118-119` | 分量 `:112-116` |
| 集中度护栏 | `top_share>0.5` 或 `simpson<0.5` → alert | `kb_rsi.py:319-323` | 只告警 |
| RSI 去重 | `DUP_SIM=0.85`、`RETIRE_AGE_DAYS=180`、陈旧 `>90d` | `kb_constants.py:11/24`、`kb_rsi.py:219` | T1 |

### 5.2 闭环与自指（Goodhart）风险

```
                     ┌──────────────────────────────────────────────┐
                     │  curate 写入 canon（section 含「坑与边界」等）  │
                     └───────┬──────────────────────────────┬───────┘
                             │                              │
       contradiction2 标注冲突│                              │kb_claim 抽声明
                             ▼                              ▼
         矛盾对数 n ──▶ kb_contradiction.detect(154)   claims.json
                             │                              │
                             ▼                              ▼
        fitness.contradiction_component(82)         fitness_from_claims(363)
                             └────────► fitness ◄──────────┘
                                          │
                                    T3 调参(tier_t3:474)   ← fitness_trend
                                          │
                              confidence_floor ↑ / t3_step ↓（单向棘轮）
```

已确认的反馈链与风险点：
1. **fitness → T3 → 质量门 → 写回量 → 库形态 → fitness**：`kb_fitness.fitness:101` 的 4 个分量里，`source_diversity` 只随 raw 变化（真外部锚，抗刷）；但 `contradiction_zero_score` 由**本库笔记文本**决定，而该文本是 curate 写的 → 存在「上游写什么文本，下游就按什么文本量矛盾」的耦合（`kb_fitness.py:82-85`）。**未确认**是否可被定向抬高（需实测），但链条客观存在。
2. **T3 单向棘轮**：`fitness_trend <= 0` 时升 `confidence_floor`（上限 0.80）并缩 `t3_step`（下限 0.02）（`kb_engine.py:571-577`），而「fitness 上升」分支只写 `T3_STABLE` 不回调（`:588-590`）→ 参数只能越调越紧、无法自动放松；无 `last_good` 之外的回滚路径。且 `fitness_trend` 大多为 0（库稳定）→ 稳定即收紧。
3. **A/B 回滚实际不可达**：`_count_consecutive_neg_delta:412-433` 逐行倒扫 `ab_snapshot.delta`，遇到非负/无该字段即 break；而每轮 `main()` 最后都会追加一条 `tier="health"` 记录（`kb_engine.py:685-693`，无 `ab_snapshot`，`delta` 默认 0）→ 倒扫在**最后一行就 break**，`count` 恒 0，`ab_rollback`（`:550`）几乎永不触发。
4. **L5 只看 external_inflow / cross_ratio**（`kb_l5.py:183-192`），二者与 raw 流入、wikilink 跨域数相关；`cross_ratio` 的分母是**编译笔记的出链总数**（`kb_rsi.py:204-209`），而 T1 会**自动补链**（`kb_engine.py:295-306`）→ T1 补链可改变 `cross_ratio` → 进而影响 L5 对 T1 自己的评分（自评风险）。`external_inflow` 的分母含 `total_all = total + raw_total`（`kb_rsi.py:281`），即编译笔记数增加会**压低** external_inflow → L2/T1 增写笔记会连带压低接地指标。**这是可确证的耦合**（公式层面）。
5. **L4 冻结 → curate 只出提案**（`kb_l4.freeze` ↔ `curate.l4_frozen:796`），是唯一的「人工负裁决 → 自动生效」链路；但 lc 的 docket 来源只有低置信提案（`kb_l4._docket_lc:219-237` ← `curate.py:778`），而低置信提案本身依赖模型自评 `confidence`（`curate.py:767`）→ 若模型总给自己高分，`lc` 永无待裁项，`L4_MIN_SAMPLE=5` 永不满足，冻结永不可达。**逻辑上是自指闭环。**

---

## 六、声明/溯源机制、置信分计算、双时态字段

### 6.1 `kb_claim.py` 的「声明 / 溯源」

- **声明（claim）模型**：`_extract_claims:90-115` 让 LLM 从正文抽 1–5 条「可证伪断言」，类型限定 `fact|prediction|policy|relation`，`confidence∈[0,1]`（默认 0.7），`len(claim)>=6` 才收（`:111`）。
- **声明 ID**：`md5(f"{note_rel}\x00{claim}\x00{idx}")[:16]`（`:265`）→ 幂等去重基于 `existing_ids`（`:247`, `:266`）。
- **溯源字段**（每条 claim，`:268-275`）：`claim_id, note_rel, claim, type, confidence, signature(排序 token 集), extracted_at, verdict, verdict_at, evidence, history[]`。
  - `evidence` = `{rel, sim, mode, evidence, verified_at}`（`:344-345`）→ **指向判定所用 raw 文件**，是真溯源。
  - `history` 追加每次判定（`:346-347`），可审计改判。
- **验证配对**：`_candidate_pairs:287-313`
  - 日期硬约束：`rdate is None or cdate is None or rdate <= cdate → skip`（`:301-302`），注释强调「否则用未来数据检验过去声明=泄露」（`:18`）。
  - 两档匹配：有 embedding → 余弦 vs `_SIM_THRESHOLD=0.55`；无 → 签名 Jaccard vs `max(0.55*0.4, 0.12)=0.22`（`:305-311`）。
  - 有界：`pairs[:_MAX_PAIRS]`（`:313`）。
- **判据产出**：`validate:316-360` 返回 `verified_ratio=confirm/judged`、`falsified_ratio=falsify/judged`（`:353-360`）；`report:381-392` 解读为「falsified_ratio 持续走高 = 知识被现实推翻，自我改进应打折」。
- **装配**：StateStore（`state_manager.StateStore`，`_store:177-183`）→ `.kb/state/claims.json`，结构 `{version, updated_at, generated_at, total_claims, claims[]}`（`:280-281`）。
- **已知偏差**：`extracted_at` 一律写「今天」（`:273`），而日期约束用它当「声明日期」→ 只有 `created` 晚于今天的 raw 才能配对（见问题 #9）。

### 6.2 `kb_confidence.py` 置信分计算

```
confidence = 0.50·E + 0.25·C + 0.15·R + 0.10·V                     # :156
E 抽取方式 : 无列表→0.9；有推断标记→ 0.9 − 0.6·min(inferred/lines, 0.5) ∈[0.6,0.9]   # :53-64
C 佐证     : n<=0 → 0.1；否则 min(1.0, 0.35 + 0.65·log2(n)/3)                      # :67-74
R 新近     : 无有效 updated → 0.7；age<=90 → 1.0；age>=365 → 0.15；
             否则 1.0 − 0.85·(age−90)/275                                            # :77-91
V 校验     : 【有接地事实】min(1, len/5)；【grounded_facts 为空列表】0.2；
             【coverage】min(1, cov)；【canon_ratio∈[0.05,0.85]】1.0 else 0.5；
             【canon_type 非空】1.0；最后取各 part 平均；无 part → 0.4               # :94-117
```
- 分档：绝对（≥0.55 publish / ≥0.35 review / else evidence-only，`:158-159`）→ 相对（<p10 → review，<p3 → evidence-only，`:163-164`）。
- `population_cutoffs:123-144` 带 `POP_CACHE` 进程内缓存（`:120-127`）。
- 写回：`cmd_apply:255-279` 写 `confidence` + `confidence_tier`；`_upsert_fm:189-210` 就地改 frontmatter。
- `curate._composite_confidence:678-700` **复用同一套权重与因子函数**，但用「当下可得」的伪 frontmatter（`source_ref=["x"]`、`updated=今天`、`grounded_facts`、`coverage`、`canon_type`）→ 刚结晶的 canon 置信分会被系统性抬高（`source_ref` 恒 ≥1 → C=0.35 起；`updated` 今天 → R=1.0）。**这是可确证的分数虚高路径。**

### 6.3 双时态时间字段

| 字段 | 含义 | 写入位置 |
|---|---|---|
| `event_at` | 该正典描述的**事实何时为真** | `curate._event_at:661-675`（取值优先级：原 fm `event_at` > `created` > `updated` > 正文前 3000 字里最早出现的 `20xx-xx-xx` 日期），写入 `_canon_fm:716` |
| `derived_at` | **何时学到**（结晶时刻） | `_canon_fm:715` = `_now()` |
| `curated_at` | 结晶动作时间 | `_canon_fm:714` |
| `updated` | 被覆写为**今天**（结晶日） | `_canon_fm:712` → **注意：这会同时影响 `kb_confidence._recency`（`:154`）与 `kb_contradiction2._day`（`:80-81`）的语义** |
| `extracted_at` | claim 抽取日（kb_claim） | `kb_claim.py:273` |

> 双时态字段只在 `_canon_fm` 落地；下游使用情况：`kb_confidence` 用 `updated/created`（**不读 `event_at`**，`:154`）；`kb_contradiction2._day` 用 `updated/created`（**不读 `event_at`**，`:80-81`）；`kb_drift` 用 ledger 的 `ts`。即：**写的双时态信息目前基本没人消费**（`grep event_at` 仅命中 curate.py 与原生 fm）。**未确认**是否有其他模块（未在本组范围）读取。

---

## 七、发现的问题清单

> 类型：bug / 静默失败 / 重复实现 / 死代码 / 自指放大 / 性能 / 设计风险

| # | 位置 | 类型 | 问题 | 影响 | 建议 |
|---|---|---|---|---|---|
| 1 | `curate.py:603-607` | **死代码/bug** | 条件 `keep > 1.0 - bp["boilerplate_ratio"]*0.8`，其中 `keep=1-r` → 化简为 `r < 0.8r` → 对 `r>0` **恒假** | 「废话未清」警告**永不触发**；蒸馏关（直接度量「没有废话」目标的唯一指标）形同虚设 | 改为与阈值比较，如 `if keep > (1 - 0.20)`（即删得少于 20% 才警告） |
| 2 | `curate.py:756-759`（`mark_evidence_verdict:838-857`） | **越权写入** | 只要 verdict 是 `noise`/`raw-only` 就**立刻写笔记 frontmatter**，且**不看 `apply`**；`checkpoint` 只在 `apply` 时做（`:947-948`） | `kb curate run`（无 `--apply`，名义「只出提案」）会修改 raw 层笔记的 `kb_layer/kb_index/curate_verdict` 且**无 git 回滚锚** → 与「默认只出提案」承诺冲突 | 加 `if apply:` 门控，或在写前也 checkpoint |
| 3 | `kb_l4.py:341-359` | **bug（自锁）** | `freeze()` 循环里先 `frozen[r]=True`（`:347`），`else` 分支（接地不足/未达门槛）仍把**已变异的 `frozen`** 落盘（`:357-359`） | 与文档「未接地 → 不自动冻」直接矛盾：**接地的库在未接地时会照样冻结 rung**，即文档警告的「让闭锁系统更锁死」 | else 分支应写回原始 `config["frozen"]`，或用 `newly` 为空时的快照 |
| 4 | `kb_l4.py:72` vs `kb_meta.py:65/91` | **bug（路径错误）** | `_L3_LEDGER="pipeline/.kb_meta_config.json"`，但 `kb_meta._config_path` 解析为 `<root>/.kb_meta_config.json` | L3 的 docket **恒为空** → 真人永远无法对 L3 裁决 → `L4_MIN_SAMPLE` 不可达 → **l3 永不可冻结** | 改为 `.kb_meta_config.json`（并统一「账本相对 root 还是 pipeline」的约定） |
| 5 | `curate.py:1027-1028`（`cmd_verify`） | **bug（死功能）** | 从 `LEDGER` 读行后要求 `r.get("proposal")`，但写账本时 `proposal` 已被剥离（`:963`） | `kb curate verify` **恒输出「（无提案记录）」**；`--pending` 参数（`:1012`）也从未使用 | 改读 `PROPS`（`curate_proposals.jsonl`）或恢复账本中的 proposal |
| 6 | `curate_prompt.py:314-329` + `curate.py:585-586` | **静默失效** | baseline 模板不含 `page_type` 字段 → `prop.get("page_type")` 恒 None → 页型关整段跳过；`canon_type` 恒 `canon-generic`（`:709`） | 默认档位下**契约（kb-schema.json）形同未接**；`kb_confidence._verification` 的「有页型」恒 +1.0（`:113-114`）→ 置信分被恒定抬高 | baseline 模板补 `"page_type": "<pt>"`，或 verify 时回填 `guess_page_type` |
| 7 | `kb_engine.py:412-433` + `:550` | **死代码/静默** | 倒扫 `ab_snapshot.delta` 时，每轮末尾的 `tier="health"` 记录（`:685-693`）无该字段 → 首行即 break → `count` 恒 0 | T3 的「连续 3 次负 delta 自动回滚」**永不触发**（`:551`） | 跳过非 `ab_snapshot` 行而不是 break；或把 delta 写进同一行 |
| 8 | `kb_claim.py:273` + `:301-302` | **bug（指标恒空）** | `extracted_at` 写「今天」，日期约束要求 `rdate > cdate` → 只有 `created` 在**今天之后**的 raw 能配对 | `validate` 实际上找不到配对（除当日新 raw 外）→ `verified_ratio`/`falsified_ratio` 长期为 `None` → 适应度闸「真·预测误差」失效 | `extracted_at` 应取笔记来源时间（`event_at/created`），或改用「raw 的 created > 笔记 created」 |
| 9 | `kb_claim.py:316-317` vs `:313` | **死参数** | `validate(max_pairs=...)` 形参未使用，内部硬切 `_MAX_PAIRS` | CLI/调用方无法调整上界 | 把 `max_pairs` 传入 `_candidate_pairs` |
| 10 | `kb_claim.py:299-304` + `:46` | **性能** | 每个 claim × 每个 raw 都重新 `kb_embed.embed(rtext)`；`_EMBED_CACHE`（`:46`）声明后从未使用 | O(claims × raws) 次嵌入调用，慢且浪费 | 用 `_EMBED_CACHE` 按 raw 文本缓存向量 |
| 11 | `kb_claim.py:102-103`, `:130-131` | **静默失败** | LLM 不可用与「抽不到声明/判不出」返回同一个值（`[]` / `False`） | 与 curate/kb_confidence 的静默降级同病：无法区分「停机」与「无内容」 | 返回 `(result, reason)` 并落盘 `pending_reason` |
| 12 | `kb_claim.py:276-278` | **自指/数据丢失** | `merged = new_claims + 旧claims[:max_total]`，新声明优先 → 超 `_MAX_TOTAL(300)` 时**先丢最老的声明及其 history** | 长期运行会丢历史证据，RSI 的「预测误差」样本被截断 | 按未判定优先+重要性排序，或增大/分片存储 |
| 13 | `kb_contradiction.py:142-147` | **bug（未捕获异常）** | `load_note` 调用两次（`:142` 在 try 内，`:147` 在 try 外）；且 `fm` 由第二次覆盖 | 第二次读盘抛 `OSError/UnicodeDecodeError` 会直接中断整次 detect；也白读一遍 | 删掉 `:147`，复用 `:142` 的结果 |
| 14 | `kb_contradiction.py:162-167` + `:188/196` | **性能** | `cmp_key` 每次比较都 `kb_rsi.load_note` 读盘，被 `sorted()` 调用 O(n log n) 次 | n>400 时可能数千次磁盘读；是 `kb_l4 docket` 从 10s 变 >300s 事故（`kb_rsi.py:70-74` 注释）同类风险 | 预取 `domain/importance` 到内存再排序 |
| 15 | `kb_contradiction.py:228-232` | **性能/成本** | 启发式未判定的**每一对**都调 LLM（有 key 时），无次数上限 | 大库可触发成千上万次 40s LLM 调用，且是在「只读 lint」路径里 | 加 `--llm` opt-in + 总次数预算；把结果缓存 |
| 16 | `kb_contradiction.py:267` → `rag.py:1136` | **配置漂移** | 经 `rag.llm_answer` 调用，模型名硬编码 `ornith1.5-35b` | 换模型需改码；与 `.kb/model.json` 机制脱节 | 统一走 `kb_common.model_config` |
| 17 | `kb_contradiction2.py:102` | **bug（默认值失效）** | `thr = max(threshold, OVERLAP_STRICT)` → CLI 默认 `--threshold 0.25` 被静默抬到 `0.35` | 帮助文案/默认值与实际行为不符；用户调到 0.25 无效果 | 移除 `max()`，或把默认改成 0.35 并改文档 |
| 18 | `kb_contradiction2.py:188-189` | **不一致** | `apply_marks` 只 `git add` 不 commit（对比 `kb_contradiction._git_commit:326-332` 会 commit） | 改动留在暂存区，后续 cron 的 `git add` 可能把它们卷进不相干的提交 | 补 commit 或复用 `kb_engine.git_add_commit` |
| 19 | `kb_contradiction2.py:172` | **数据丢失** | `_upsert_fm_simple(text,"conflicts_with", f'["{other}"]')` 覆盖式写入 | 一篇与多方冲突时只保留最后一条 | 读现有列表再合并去重 |
| 20 | `kb_confidence.py:255-275`（`cmd_apply`） | **危险默认** | `dry` 只在传 `--dry-run` 时为真；帮助文本说「默认干跑」但**不传参即真写**（`:371-372`, `:384-385`） | 误以为安全的 `kb confidence apply` 会直接改全库 frontmatter（`--limit 0` 表示无上限） | 默认 dry，改 `--write` 才落盘 |
| 21 | `kb_confidence.py:287-320` vs `:266` | **不一致/反馈错位** | `decay_verdict` 调 `score(fm, body)` **不传 p10/p3**（绝对档），而 `cmd_apply` 用相对档写 `confidence_tier` | 同一篇笔记「存盘 tier」与「遗忘判定 tier」可能不同 → 理由栏与落盘值相互矛盾 | 统一传入 `population_cutoffs` |
| 22 | `kb_confidence.py:53-64` | **文档不符** | 头注释声明 `verbatim 1.0 / extracted 0.9 / inferred 0.6`（`:13`），实现最大只有 0.9（无法识别 verbatim） | 「抄的 vs 归纳的」其实未区分；`W=0.50` 的最大项分辨率损失 0.1 | 用 facts 的 `kind` 分布实现三分档 |
| 23 | `kb_confidence.py:67-74` | **文档不符** | 注释写「1→0.4」（`:14`），公式给 0.35（`:73`） | 审计解释与文档不一致 | 改注释或改公式 |
| 24 | `kb_consistency.py:126` | **静默错误判定** | `verdict = "unfaithful" if ... else ("partial" if ... else "faithful")`：中文回答（如「不忠实」）落 `faithful` | 「默认判忠实」→ Critical/High 漏报 | 加中文关键词映射，无法识别时返回 `ok=False` |
| 25 | `kb_consistency.py:243-251` | **伪验证/误导输出** | 仅跑了一次 `git rev-parse` 就打印「已确认仅读审计，未改动任何 live 笔记」 | 给人虚假安全感（该断言并不成立） | 打印真实统计或删除该句 |
| 26 | `kb_consistency.py:191` | **成本/延迟** | 对**每条** ledger 记录无条件尝试 LLM（仅端点可达性预检），无并发/上限 | 大 ledger 时审计极慢（每条最多 15s 后被丢弃但线程仍跑） | 加 `--llm` opt-in + 条数上限 + 线程池 |
| 27 | `kb_calibrate.py:74-75` | **破坏性风险** | `if root.exists(): shutil.rmtree(root)` 无任何「这是仿真库」守卫 | 误传真实 vault 路径 → **整库删除** | 仅在检测到 `gt_dup.json`/目录为空/`--force` 时删除；否则报错退出 |
| 28 | `kb_calibrate.py:180-189` | **方法学缺陷** | `pos_sims` 用注入的**目标相似度** `keep`，而非实测 `cos`；负样本随机对**未排除 GT 对** | 标定出的阈值与实际打分器不对齐（注释自陈 `cos≈keep_ratio`，但仅是近似） | 用 `_cos` 实测正样本；负样本剔除 GT 对 |
| 29 | `kb_drift.py:265-273` + `:279` | **静默失败** | `subprocess` 只在 `__main__` 分支导入（`:279`）；`main()` 被 import 调用时 `subprocess.DEVNULL` 抛 `NameError`，被 `except Exception` 吞掉 | git 历史检查在「被 import 调用」场景静默不执行 | 顶部 `import subprocess` |
| 30 | `kb_drift.py:122` | **指标口径不一致** | `length_ratio` 用 `len(text)`（含 frontmatter）与 `len(orig)` 比 | 与 `kb_consistency` 的 body-only 口径不同，二者数字不可比 | 统一用 `_split_body` |
| 31 | `kb_l4.py:426-427`（`render`） | **死参数 + 重复计算** | `summary` 形参被忽略，内部又调 `status(root)`；`main:491` 还先算了一次 `status(root)` | 每次 `report` 全库扫描 2 次（`grounding`→`kb_rsi.collect`） | 用传入的 summary；report 只算一次 |
| 32 | `kb_l4.py:77-78` | **死代码** | `_p()` 定义后从未调用 | 噪声 | 删除 |
| 33 | `kb_l4.py:162-165` | **静默失败** | 一行 JSON 损坏 → 整个账本返回 `[]` | 裁决历史「凭空消失」，信任度归零 | 逐行 try/except |
| 34 | `kb_l4.py:219-237` | **自指/不可达** | lc 的 docket 只来自低置信提案，而低置信来自模型自评 | 模型自评偏高 → lc 无待裁项 → `L4_MIN_SAMPLE` 不可达 → lc 永不可冻（连带 `curate.l4_frozen` 永不生效） | 引入「抽样送审」或按批限额强制抽样 |
| 35 | `kb_l5.py:70` vs `kb_meta.py:64` | 路径正确但耦合紧 | l3 活跃度读 `.kb_meta_state.json` 的 `cycles` 长度（`:173`），与 kb_meta 的内部字段名强耦合 | kb_meta 改字段名 → L5 静默把 l3 活跃度算成 0 | 加显式契约/版本字段 |
| 36 | `kb_engine.py:32` | **bug（--root 失效）** | `CONFIG_PATH` 硬编码为模块所在目录的 `.kb_rsi_config.json` | `kb_engine.py --root <别的库>` 仍读写**安装目录**的配置：读错开关、写脏源码树 | 改为 `Path(root)/"pipeline"/".kb_rsi_config.json"` |
| 37 | `kb_engine.py:571-590` | **单向棘轮/自指** | `confidence_floor` 只能升（≤0.80）、`t3_step` 只能降（≥0.02），且 `fitness_trend==0`（稳定）也算「停滞」触发收紧 | 长期运行后质量门越来越紧、写回越来越少，无自动恢复路径 | 加对称放宽分支 + `last_good` 回滚 |
| 38 | `kb_engine.py:646` 注释 | **文档漂移** | 注释提到 `--force-t3`，但 CLI 无该参数（`:617-624`） | 误导 | 删注释或实现参数 |
| 39 | `kb_engine.py:138-145`（`set_fm_field`） | **守卫脆弱** | raw 守卫通过「向上找含 `pipeline` 子目录的祖先」实现（`:141-145`），找不到就**跳过守卫** | 若目录结构变化（pipeline 不在此库），守卫静默失效 | 改为显式传 root 并直接比较 |
| 40 | `kb_rsi.py:337-342` | **bug（假门）** | `same_dom = True` 硬编码，但 `gate` 文案宣称「领域一致」（`:341`） | 去重提议的「同域」门是假的；文档/输出误导 | 真算 `info[a]["domain"] == info[b]["domain"]` |
| 41 | `kb_rsi.py:281-282` | **指标口径** | `external_inflow = raw_fresh/(compiled_total + raw_total)`：编译笔记增多会**压低**该百分比 | T1/T2 增写笔记 → 跨过 5% 阈值更难 → 影响所有接地闸与 L5 评分（自指耦合） | 分母改为 `raw_total` 或单列两指标 |
| 42 | `kb_engine.py:295-306`（T1 补链）| **自指 / 守卫不一致** | 自动把 `[[target]]` 追加到笔记正文（`:301-302`），该写入**不经过** `_raw_guard`（同函数内 retire 路径走 `set_fm_field:241/259`，是带守卫的）。当前 `orphans` 仅来自编译笔记（`info`，`:279`），故**未发现可实际越界**；但同一函数内两条写路径守卫强度不同 | 若未来 `orphans` 数据源变化即可能改到证据层；另会推高 `cross_ratio` 反馈给 L5（见 5.2-4） | 写正文前统一走 `_raw_guard`/`is_raw_rel` |
| 43 | 多处 | **重复实现** | 接地阀 4 份、`_parse_d` 3 份、tokenize/vec 4 份、容错 JSON 3 份（见 1.4） | 常量中心 `kb_constants` 未真正收敛 | 抽取到 `kb_common` 单一实现 |
| 44 | `curate.py:943-946` | **顺序/文案** | L4 冻结检查在打印「直接写回」之后才执行 | 输出先宣称写回、再改口只出提案 | 把冻结检查前移 |
| 45 | `plugins/curate_plugin.py:16-22` | **CLI 缺失** | `ACTION_MAP` 无 `recheck`（而 `curate.py:1197` 有该子命令）；`curate.py:890` 提示 `kb curate judge` 但无该命令 | 两个功能只能靠裸 python 调用；用户按提示操作会失败 | 补 `recheck`；删掉或实现 `judge` 提示 |
| 46 | `kb_contradiction2.py:201` + `:17` | **危险默认（文档反了）** | `--dry-run` 默认 False（`:201`），而头注释写「默认 dry-run；`--apply` 才写」（`:17`），且该子命令**没有** `--apply` | 用户按文档理解执行 `kb contradict apply` → **直接改笔记并写队列**（无预演） | 把 `dry` 默认设为 True，改用 `--write` 落盘 |

---

## 八、重复与冗余清单

### 8.1 `kb_contradiction.py` ↔ `kb_contradiction2.py`：两套并存的矛盾检测

| 维度 | `kb_contradiction.py`（490 行） | `kb_contradiction2.py`（237 行） |
|---|---|---|
| 定位 | 语料级「否定极性矛盾」lint（P1-C） | 正典级「变更/取代」冲突在环（P3） |
| 数据源 | **所有编译笔记**（排除 raw/归档/收件箱/MOC，`collect:129-151`） | **仅 `kb_layer: canon`**（`scan:92`） |
| 相似度 | 向量余弦（CJK 单字 + 拉丁词，`_vec:75`）+ 倒排索引预过滤（`:171-172`）+ `CONTRA_SIM=0.40` | section 文本 Jaccard（`_jacc:68`）+ `OVERLAP_STRICT=0.35` |
| 判据 | 否定密度差双闸（`:225`），可选 LLM 极性（`:229`） | 主题重合 + 变更词（`:113`） |
| 产出 | `pairs + n_contradictions + zero_score`（`:465`）；`pipeline/.kb_contradictions.json`、`kb_contradictions_report.md` | `pairs/conflicts/duplicates`（`:131-132`）；`.kb/contradiction_queue.jsonl`、`.kb/duplicate_canon_queue.jsonl` |
| 写回 | 人工 `apply --retain <id>:a\|b` → retire 一侧（`kb_action=retire/status=legacy/category=contradicted/contradiction_pair_id/contradiction_survivor`，`:391-397`）+ 单文件 commit | 自动 `apply` → 双方 `conflicts_with` + callout（`:172-175`），只 `git add` 不 commit |
| 谁在用 | **`kb_fitness.contradiction_component:82-85`**（fitness 分量）+ 裸 CLI | **唯一有插件包装者**（`plugins/contradict_plugin.py` → `kb contradict`） |
| 幂等性 | 快照 id 每次重算（`:463-464`），旧 id 可能指向新对 | callout 幂等检测（`:170-171`） |

**结论**：两者不是替代关系而是**两套并行判据**，但存在三处实质冗余与冲突：
1. 都会产出「冲突对」却互不知情（一个 retire 笔记、一个只加标注），同一对可能出现两种互斥处置建议；
2. `kb_contradiction2` 的 `duplicate-like`（`DUP_LIKE=0.85`，`:45/118`）与 `curate.find_dup_canon`（`DUP_CANON_SIM=0.90`，`curate.py:373`）功能重叠，前者只入队列、后者在写回前拦截，**两套阈值（0.85 vs 0.90）**；
3. `kb_contradiction` 的 `contradiction_zero_score:282` 是 fitness 的分量（权重 0.24），而 `kb_contradiction2` 的 `conflicts` **不进入任何指标**——即「已标注的真冲突」对 fitness 没有影响（`kb_fitness.py:82-85` 只调前者）。**这使两条矛盾通道的严重性权重完全不对称。**

### 8.2 `kb_l4` / `kb_l5` / `kb_meta` / `kb_engine` 的重叠信号采集

| 信号 | kb_rsi | kb_engine | kb_l4 | kb_l5 | kb_meta | kb_fitness |
|---|---|---|---|---|---|---|
| `external_inflow` / `cross_ratio` 接地判定 | 定义 `metrics:189`+ 阀 `proposals:378` | `_grounded:191`（**拷贝** 5.0/0.15） | `grounding:144`（**拷贝**） | `grounding:134`（**拷贝**） | `grounding`（kb_meta 内，被 `curate`/L4 引用同理） | 经 `kb_rsi` |
| rung 账本读取 | — | — | `_docket_l0..l3:177-214` | `_rung_activity:157` | `L0_PROPOSAL_REL/L2_PROPOSAL_REL:68-69` | — |
| 全库扫描 | `collect:158` | 每 tier 各一次（`tier_t1:215`、`tier_t3:481`）+ `main:681` | `grounding`→`collect`（`docket`/`status`/`render` 各触发） | 同左（`train`/`propose`/`status`） | `outcome`/`grounding` | `fitness:105` + 内部 4 个分量各自再扫（`contradiction_component`、`kb_usage.analyze`） |

**具体冗余**：
1. **接地判定 4 份**（`kb_rsi.py:378`、`kb_engine.py:191`、`kb_l4.py:144`、`kb_l5.py:134`），只有 kb_rsi 走 `kb_constants`；改一处阈值必然漏改其余三处。
2. **rung 账本路径双份**：`kb_l4.py:69-72` 与 `kb_l5.py:66-71` 各维护一份 `_L0/_L1/_L2` 路径常量；且 L3 在两边**指向不同文件**（`kb_l4` 指 `pipeline/.kb_meta_config.json`（错），`kb_l5` 指 `.kb_meta_state.json`（对））→ 同一概念两种解析。
3. **"是否动作过"的双重定义**：L4 用「账本里有哪些条目」（`_docket_*`），L5 用「账本条目计数」（`_rung_activity`），kb_meta 用「proposal 文件是否存在」（`L0_PROPOSAL_REL:68`）→ 三套口径。
4. **fitness 分量与既有模块重复**：`source_diversity`（`kb_fitness.py:58`）重复实现 Simpson 熵，`kb_rsi._concentration:294-326` 已有同款（且 `kb_rsi._concentration` 另算了 `shannon_norm`/`top_share`）；`kb_fitness` 连 `_shannon_norm:49` 都定义了却**未使用**（只用了 Simpson）。
5. **T3 的 health 与 fitness 并行**：`tier_t3:515-518` 两个都算、只把 fitness 用于决策（`:571` 起），health 仅写日志 → 每轮多一次全库 `metrics` 扫描。
6. **`kb_confidence.population_cutoffs` 与 `cmd_audit`/`cmd_apply`/`cmd_decay`** 各自遍历全库并 `score()`（`:129-137`, `:216-229`, `:259-274`, `:341-348`）→ 四份近似循环，仅靠 `POP_CACHE` 省掉 cutoffs 那一遍。

### 8.3 其他重复

- `curate._existing_canon:412`（curate）与 `curate_prompt._canon_rows:90`（prompt）对 canon 各扫一遍；`_merge_candidates:224` 先调前者、失败再调后者 → 最坏一次两遍全库扫描，且**每个候选笔记都做一次**（`curate._build_prompt_messages:365-368`）。
- `curate.find_dup_canon:376` 在写回前**再扫一次**全库（每篇一次）→ 单批 N 篇约 2N 次全库遍历（性能，与问题 #14/#15 同类）。
- `kb_consistency._sha:50` 与 `kb_drift._sha:28` 逐字相同；`_split_body` 同样两份（`kb_consistency.py:42` / `kb_drift.py:20`）。
- `curate._extract_json:207` / `kb_claim._parse_json:71` / `kb_usage._parse_json:243` 三份容错 JSON 解析。
- `kb_engine.git_backup:104` / `git_add_commit:90` 与 `kb_l4._checkpoint:129` / `kb_l5._checkpoint:119`（后两者逐字相同）/ `curate.checkpoint:133` / `kb_contradiction._git_commit:326` —— **五份 git 包装**，提交范围（`-u` vs `--force` vs 指定路径）与「无变更时的返回值」语义各不相同。

---

## 附：未能确认 / 需实测的点

1. `kb_rsi.proposals` 的接地打折（`:385-387`）是否真的被任何自动流程消费（`kb_rsi` 只写日志，未见读回；**未确认**）。
2. `kb_fitness` 的 `query_success` 分量在真实库中是否恒为默认 1.0（`kb_fitness.py:98` 无数据即中性）→ 0.20 权重是否实际失效（**未确认**，取决于 `kb_usage.analyze` 是否有数据）。
3. `kb_claim` 的 `_candidate_pairs` 在真实库中的实际配对数（受问题 #8 影响，预计≈0）——**未实测**。
4. `event_at` 是否被本组之外的模块（如 `rag.py`、`kb_audit.py`、`kb_verify.py`）消费——**未检查**，本组范围内仅 `curate.py` 写入。
5. `.kb/` 与 `pipeline/` 下账本的真实存在情况（`template-vault/.kb/state` 目录存在但内容未列）——**未实测**运行态。
