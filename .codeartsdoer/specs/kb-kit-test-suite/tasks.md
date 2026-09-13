# kb-kit 测试套件编码任务规划

## 任务概览

- **总主任务数**：9
- **总子任务数**：42
- **覆盖需求数**：~100 项 FR（全部覆盖）
- **执行顺序**：任务 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9（严格按序执行）

---

## 任务 1：测试基础设施搭建

**描述**：创建测试目录结构、全局 conftest.py、pytest 配置文件和测试数据工厂，为所有后续测试任务提供基础支撑。

**输入**：design.md 第 2 节（目录结构）、第 3 节（Fixture 设计）、第 8 节（隔离策略）
**输出**：`tests/conftest.py`、`tests/fixtures/`、`pytest.ini`、`tests/__init__.py`

### 子任务 1.1：创建测试目录骨架
- **描述**：创建 `tests/` 根目录及 `unit/`、`integration/`、`e2e/`、`fixtures/` 四个子目录，每个子目录含 `__init__.py`
- **验收标准**：目录结构符合 design.md 第 2 节定义，所有 `__init__.py` 存在

### 子任务 1.2：编写 pytest.ini 配置文件
- **描述**：在项目根目录创建 `pytest.ini`，配置 testpaths、python_files、markers（unit/integration/e2e/slow）、addopts
- **验收标准**：`pytest --collect-only` 能发现 tests/ 下的测试文件

### 子任务 1.3：编写 conftest.py 全局 Fixture
- **描述**：实现 `pipeline_path`、`tmp_vault`、`tmp_vault_with_git`、`sample_note_text`、`sample_notes`、`pipeline_modules`、`mock_env`、`clean_env`（autouse）、`reset_config_cache`（autouse）等 fixture。`tmp_vault` 需创建完整 PARA 分区 + reference 配置文件 + pipeline symlink + vector index 目录。`tmp_vault_with_git` 在 tmp_vault 基础上执行 `git init` + 配置 user + 首次 commit
- **验收标准**：任意测试函数可请求 `tmp_vault` fixture 并获得含完整 PARA 结构的临时目录；`tmp_vault_with_git` 返回的目录中 `.git` 存在；`pipeline_modules` 返回的字典含 25 个模块

### 子任务 1.4：编写 note_factory.py 测试笔记工厂
- **描述**：实现 `make_note()`、`make_minimal_note()`、`make_retired_note()` 函数，支持自定义 frontmatter 所有字段 + body + extra_fm
- **验收标准**：`make_note()` 返回的文本以 `---` 开头和结尾，含 title/tags/status/domain/importance/created/updated/kb_target/kb_action/kb_summary/content_type 字段

### 子任务 1.5：编写 vault_factory.py 和 config_factory.py
- **描述**：`vault_factory.py` 实现 `create_vault_structure()`、`create_note()`、`create_config()`。`config_factory.py` 实现 `domain_taxonomy()`、`tag_taxonomy()`、`dedup_config()`、`synonyms()`、`content_type_signals()`，返回与项目 `template-vault/reference/` 下对应文件一致的内容
- **验收标准**：`create_vault_structure(root)` 后 root 下含 9 个 PARA 分区目录 + reference/ 目录；config_factory 各函数返回值与项目实际配置文件 JSON 解析结果一致

---

## 任务 2：核心工具模块单元测试（kb_common + validate + classify）

**描述**：为三个基础模块编写单元测试，这些模块被其他模块依赖，需优先完成。

**输入**：spec.md FR-3.1.x、FR-3.11.x、FR-3.14.x；design.md 4.2.1、4.2.11、4.2.14
**输出**：`tests/unit/test_kb_common.py`、`tests/unit/test_validate.py`、`tests/unit/test_classify.py`

### 子任务 2.1：编写 test_kb_common.py（15 项 FR）
- **描述**：测试 tokenize（纯拉丁/纯CJK/混合/空串）、parse_frontmatter（无fm/有fm/嵌套键/注释行）、load_meta（存在/不存在）、load_note（存在/不可读）、iter_notes（含.md/EXCLUDE目录/嵌套git）、count_tokens（空/CJK/拉丁）、text_entropy（空/单字符重复/全不同/一般）、content_fingerprint（相同body/不同body）、parse_domain（单级/两级/None/空）、load_config（存在/不存在/缓存）、is_valid_domain（白名单内/外/无taxonomy）、is_valid_tag（白名单内/层级/外）、extract_relations（有/无）、add_relation（不存在/字符串转列表/列表追加/幂等）、dedup_check（exact skip/semantic skip/mark_related/new）
- **验收标准**：覆盖 FR-3.1.1 至 FR-3.1.15 全部 WHEN-THEN 条件；纯函数测试不依赖文件系统；文件系统测试使用 tmp_path

### 子任务 2.2：编写 test_validate.py（2 项 FR）
- **描述**：测试 parse_frontmatter（有fm返回dict+start_line/无fm返回None+1）、validate_file 参数化测试（缺必填字段→硬错误、status非法→硬错误、domain不在白名单→硬错误、importance越界→硬错误、正文>2000字→软告警、kb_action=retire+status=active→软告警、enforce_level非标准→软告警、全部合法→无错误）
- **验收标准**：覆盖 FR-3.11.1 至 FR-3.11.2 全部 WHEN-THEN 条件

### 子任务 2.3：编写 test_classify.py（3 项 FR）
- **描述**：测试 classify（正文<15字→noise、密度<0.05且<200字→noise、聊天+决策信号→chat_decision、非聊天+policy信号→policy、无匹配→兜底）、classify_quality（<15字→不摄取、密度<0.05→不摄取、<200字+聊天样→chat、密度≥0.15+熵≥0.7→knowledge、其他→reference）、_detect_chat_context（fm含participants/chat_time/channel→True、body匹配聊天行≥3→True、无特征→False）
- **验收标准**：覆盖 FR-3.14.1 至 FR-3.14.3 全部 WHEN-THEN 条件

---

## 任务 3：检索引擎模块单元测试（rag + rerank + semantic_chunk）

**描述**：为 TF-IDF 检索、重排序和语义分块模块编写单元测试。

**输入**：spec.md FR-3.2.x、FR-3.16.x、FR-3.17.x；design.md 4.2.2、4.2.16、4.2.17
**输出**：`tests/unit/test_rag.py`、`tests/unit/test_rerank.py`、`tests/unit/test_semantic_chunk.py`

### 子任务 3.1：编写 test_rag.py（13 项 FR）
- **描述**：测试 cmd_index（全量构建验证df_idf.json结构、kb_action=retire不入索引、无token不入索引）、_incremental_update（mtime未变跳过、hash不变只更新mtime、内容变化重新计算、文件删除移除）、load_index（不存在/损坏/无version/有version）、_vec（归一化验证）、_cos（正常/无交集→0.0）、_expand_query（含同义词扩展/无配置返回原始）、best_sentence（高匹配句子/结论词加成/空body）、_dedup_chunks（同笔记多chunk保留最高分/top截断）、_apply_filters（8种过滤器逐一参数化测试）、enforce_level加成（policy+must×1.10/reference×0.95/chat×0.90）、cmd_query --json（输出结构/context-budget）、_build_inverted_index（5字段倒排映射）、_inverted_lookup（tag/summary命中加成）
- **验收标准**：覆盖 FR-3.2.1 至 FR-3.2.13 全部 WHEN-THEN 条件；索引测试在 tmp_vault 中创建笔记后调用 cmd_index；不测试 llm_answer（排除范围）

### 子任务 3.2：编写 test_rerank.py（3 项 FR）
- **描述**：测试 _token_overlap（有交集Jaccard/无交集→0.0）、_frontmatter_match（含domain+0.4/含tag+0.3/含content_type+0.3）、rerank（5维度加权降序排列/enforce_level=must→1.0/近30天更新recency线性递减）
- **验收标准**：覆盖 FR-3.16.1 至 FR-3.16.3 全部 WHEN-THEN 条件

### 子任务 3.3：编写 test_semantic_chunk.py（2 项 FR）
- **描述**：测试 _sliding_window（正文<chunk_size→单块/正文>chunk_size→多块overlap/空文本→空列表）、chunk（分块≤1→空列表/分块>1→列表+父索引页/frontmatter含chunk_of/chunk_index/chunk_total）
- **验收标准**：覆盖 FR-3.17.1 至 FR-3.17.2 全部 WHEN-THEN 条件

---

## 任务 4：成长引擎模块单元测试（intake_triage + feedback_loop + link_engine + recall_schedule + kb_health）

**描述**：为四个核心成长引擎和健康度量模块编写单元测试。

**输入**：spec.md FR-3.3.x 至 FR-3.7.x；design.md 4.2.3 至 4.2.7
**输出**：`tests/unit/test_intake_triage.py`、`tests/unit/test_feedback_loop.py`、`tests/unit/test_link_engine.py`、`tests/unit/test_recall_schedule.py`、`tests/unit/test_kb_health.py`

### 子任务 4.1：编写 test_intake_triage.py（5 项 FR）
- **描述**：测试 density（高密度文本/纯空白→接近0）、evaluate（信号<15→trash/缺字段→结构分降/高相似→合并建议/价值关键词→价值分增）、triage（有笔记→结果列表/空收件箱→空列表）、triage_by_content_type（policy/decision/lesson/knowledge/reference→direct、chat类→aggregate、noise→trash、未知→兜底）、_domain_to_para（开发/安全→Technology、产品/运营/管理→Projects、综合→Resources、未知→Resources兜底）
- **验收标准**：覆盖 FR-3.3.1 至 FR-3.3.5 全部 WHEN-THEN 条件

### 子任务 4.2：编写 test_feedback_loop.py（5 项 FR）
- **描述**：测试 hits_for（有效索引→top-k命中/None或空查询→空列表）、bumps（≥MIN_HITS→建议/<MIN_HITS→不生成/<MIN_BUMP→不生成/超BUMP_CAP→截断）、_write_importance（有字段→替换/无字段→开头添加）、record_hit（useful=True→权重1.0/useful=False→权重0.0/state含explicit_feedback）、_accumulate_importance（≤1.0→写入/>1.0→截断/笔记不存在→False）
- **验收标准**：覆盖 FR-3.4.1 至 FR-3.4.5 全部 WHEN-THEN 条件；_accumulate_importance 使用 tmp_vault_with_git

### 子任务 4.3：编写 test_link_engine.py（3 项 FR）
- **描述**：测试 outbound（[[Target]]/[[Target|alias]]/[[Target#section]]/无wikilink→空集）、suggestions（相似≥threshold无互链→建议/不同domain→cross=True/同domain→cross=False/相似<threshold→不生成）、apply_links（已有建议区块→跳过幂等/无区块→追加/--limit→取前N对）
- **验收标准**：覆盖 FR-3.5.1 至 FR-3.5.3 全部 WHEN-THEN 条件；apply_links 使用 tmp_vault_with_git

### 子任务 4.4：编写 test_recall_schedule.py（4 项 FR）
- **描述**：测试 base_interval（≥0.9→180天/≥0.5→30天/≥0.3→14天/<0.3→7天）、build（importance<0.1不参与/kb_action=retire不参与/到期进deck/urgency降序）、retrievable（summary命中顶层→True/None或空→False/不在索引→False）、mark（last_seen更新/stability递增×1.4上限180/recalls+1）
- **验收标准**：覆盖 FR-3.6.1 至 FR-3.6.4 全部 WHEN-THEN 条件；retrievable 需先构建 rag 索引

### 子任务 4.5：编写 test_kb_health.py（3 项 FR）
- **描述**：测试 metrics（连接度/时效/新鲜/沉淀/涌现/去重六维度）、score_and_alerts（orphan_pct>5→告警/avg_out<1.5→扣20分/stale_pct>10→扣分/收件箱积压→扣分/涌现→加分/score≥80→良好/55≤score<80→需推动/score<55→需治理）、check_policy_stale（有review_cycle且过期→返回/无last_reviewed→直接过期/非policy→不检测）
- **验收标准**：覆盖 FR-3.7.1 至 FR-3.7.3 全部 WHEN-THEN 条件

---

## 任务 5：运维工具模块单元测试（kb-healthcheck + clean + sync + state_manager + dashboard + graph）

**描述**：为健康巡检、清洗、同步、状态管理、仪表盘和图谱模块编写单元测试。

**输入**：spec.md FR-3.8.x 至 FR-3.13.x、FR-3.15.x；design.md 4.2.8 至 4.2.13、4.2.15
**输出**：`tests/unit/test_kb_healthcheck.py`、`tests/unit/test_clean.py`、`tests/unit/test_sync.py`、`tests/unit/test_state_manager.py`、`tests/unit/test_dashboard.py`、`tests/unit/test_graph.py`

### 子任务 5.1：编写 test_kb_healthcheck.py（8 项 FR）
- **描述**：测试 8 项巡检：check_deadlinks（有效链接/死链/系统目录/模板占位符）、check_skeletons（<500B+>7天→骨架/系统目录不检测）、check_tags（白名单内/外/噪声标签）、check_empty（PARA空目录/系统目录不检测）、check_frontmatter（有fm缺字段/无fm跳过/系统目录跳过）、check_discipline（NN-格式合规/违规）、check_orphans（无出链非MOC→孤儿/有出链/MOC不计）、check_overlong（>2000字→超长/≤2000不报）
- **验收标准**：覆盖 FR-3.9.1 至 FR-3.9.8 全部 WHEN-THEN 条件；骨架测试用 os.utime 调整 mtime

### 子任务 5.2：编写 test_clean.py（5 项 FR）
- **描述**：测试 route_chunk_strategy（<200→aggregate/200-2000→direct/2000-10000→chunk_by_heading/>10000→semantic_chunk）、infer_domain（fm白名单内/文件夹推断/关键词推断/无法推断→综合）、infer_status（路径含归档→archived/fm白名单/mtime>180天→archived/mtime>90天→legacy）、clean_note（frontmatter含全部必填字段/保留非必填字段）、aggregate_bucket（桶不存在→创建/存在→追加/指定author→含前缀）
- **验收标准**：覆盖 FR-3.10.1 至 FR-3.10.5 全部 WHEN-THEN 条件

### 子任务 5.3：编写 test_sync.py（3 项 FR）
- **描述**：测试 route_domain（开发→(Technology,后端开发)/安全→(Technology,安全)/未知→(Resources,domain)）、cmd_dry（有硬错误且未--allow-existing-issues→中止/无硬错误→输出步骤）、cmd_ingest_any（文件不存在→1/质量门禁未通过→丢弃/noise→丢弃/chat类→聚合桶/knowledge等→直达）
- **验收标准**：覆盖 FR-3.12.1 至 FR-3.12.3 全部 WHEN-THEN 条件

### 子任务 5.4：编写 test_state_manager.py（4 项 FR）
- **描述**：测试 StateStore.load（存在/不存在→default/损坏→default）、save（原子写入验证.tmp不存在/JSON格式）、update（load→fn→save/fn返回None不写入）、migrate_legacy_state（旧路径有新路径无→迁移/新路径已有→不迁移）
- **验收标准**：覆盖 FR-3.13.1 至 FR-3.13.4 全部 WHEN-THEN 条件

### 子任务 5.5：编写 test_dashboard.py（3 项 FR）
- **描述**：测试 run_validate（mock subprocess返回JSON/非JSON→{}）、check_backup（无备份→ok=False/有备份sha256校验/超30小时→fresh=False）、main（输出含告警/概况/备份/成长性/分布/有硬错误→告警/无告警→正常）
- **验收标准**：覆盖 FR-3.8.1 至 FR-3.8.3 全部 WHEN-THEN 条件；mock subprocess.run

### 子任务 5.6：编写 test_graph.py（2 项 FR）
- **描述**：测试 collect_nodes（有关系字段→收录/domain_filter过滤/无关系字段→不收录）、build_mermaid（有节点边→合法Mermaid/超max_nodes→截断/policy→方括号/decision→圆括号）
- **验收标准**：覆盖 FR-3.15.1 至 FR-3.15.2 全部 WHEN-THEN 条件

---

## 任务 6：记忆与摄取模块单元测试（memory_ingest + memory_sync + memory_ingest_json + memory_ingest_sqlite + agent_registry + ingest_chat + ingest_convert + user_manager）

**描述**：为记忆写入/同步、多 agent 摄取、对话记录摄入、格式转换和用户管理模块编写单元测试。

**输入**：spec.md FR-3.18.x 至 FR-3.25.x；design.md 4.2.18 至 4.2.25
**输出**：`tests/unit/test_memory_ingest.py`、`tests/unit/test_memory_sync.py`、`tests/unit/test_memory_ingest_json.py`、`tests/unit/test_memory_ingest_sqlite.py`、`tests/unit/test_agent_registry.py`、`tests/unit/test_ingest_chat.py`、`tests/unit/test_ingest_convert.py`、`tests/unit/test_user_manager.py`

### 子任务 6.1：编写 test_memory_ingest.py（4 项 FR）
- **描述**：测试 _text_sim（有交集Jaccard/无token→0.0）、normalize_bucket（简写→标准/标准→原样/未知→原样）、_safe_kb_path（绝对路径→None/../穿越→None/正常→Path）、mirror（hash未变→跳过/hash变化→镜像/指纹已存在→去重跳过）
- **验收标准**：覆盖 FR-3.18.1 至 FR-3.18.4 全部 WHEN-THEN 条件

### 子任务 6.2：编写 test_memory_sync.py（3 项 FR）
- **描述**：测试 signal_density（高信号词比例→高密度/无信号词→0.0）、slug（特殊字符→连字符/空或全特殊→memory-note）、grade（密度<阈值→不通过/长度<min_chars→不通过/sims≥DUP_SIM→不通过/sims≥NOVEL_SIM→不通过/软门未通过→review_needed）
- **验收标准**：覆盖 FR-3.19.1 至 FR-3.19.3 全部 WHEN-THEN 条件；grade 需先构建 rag 索引

### 子任务 6.3：编写 test_memory_ingest_json.py（1 项 FR）
- **描述**：测试 ingest（含tables.records→逐条处理/已处理→跳过/content空→跳过/dry_run=True→不落盘）
- **验收标准**：覆盖 FR-3.20.1 全部 WHEN-THEN 条件

### 子任务 6.4：编写 test_memory_ingest_sqlite.py（2 项 FR）
- **描述**：测试 _extract_turns（含thread_items→按(thread_id,turn_id)聚合/userMessage→content.text/agentMessage→text）、ingest（含.sqlite→逐文件逐turn/已处理→跳过/user或reply空→跳过）
- **验收标准**：覆盖 FR-3.21.1 至 FR-3.21.2 全部 WHEN-THEN 条件；用标准库 sqlite3 创建测试数据库

### 子任务 6.5：编写 test_agent_registry.py（3 项 FR）
- **描述**：测试 detect_agents（mock shutil.which/Path.exists，验证4种agent探测）、load_registry/save_registry（存在→dict/不存在→默认dict/保存→JSON）、cmd_add（已注册→1/未注册→0）
- **验收标准**：覆盖 FR-3.22.1 至 FR-3.22.3 全部 WHEN-THEN 条件

### 子任务 6.6：编写 test_ingest_chat.py（3 项 FR）
- **描述**：测试 _detect_platform（wechat/wework→wechat_work、dingtalk/钉钉→dingtalk、feishu/lark→feishu、slack→slack、.csv→wechat_work兜底、.json→slack兜底）、_segment（间隔>30分钟→切分/单段>5000字→二次切分）、parse_chat（指定platform→用指定/未指定→自动探测/适配器失败→回退通用）
- **验收标准**：覆盖 FR-3.23.1 至 FR-3.23.3 全部 WHEN-THEN 条件

### 子任务 6.7：编写 test_ingest_convert.py（3 项 FR）
- **描述**：测试 convert（文件不存在→success=False/未知格式→success=False/已知格式成功→success=True/外部工具未安装→success=False）、detect_schema（tables.records→dsh/thread_items→codex/嵌套数组→nested_array/其他→generic）、_save_mirror（原样复制/同名冲突→数字后缀）。外部工具测试用 @pytest.mark.skipif 跳过
- **验收标准**：覆盖 FR-3.24.1 至 FR-3.24.3 全部 WHEN-THEN 条件

### 子任务 6.8：编写 test_user_manager.py（4 项 FR）
- **描述**：测试 cmd_add（已存在→1/角色非法→1/不存在且合法→添加+创建收件箱）、cmd_list（无用户→无用户提示/有用户→输出信息）、cmd_remove（不存在→1/default→拒绝/存在且非default→删除）、cmd_check_perm（含权限→0/不含→1）
- **验收标准**：覆盖 FR-3.25.1 至 FR-3.25.4 全部 WHEN-THEN 条件

---

## 任务 7：安装引擎单元测试（create_vault）

**描述**：为 create_vault.py 安装引擎编写单元测试。

**输入**：spec.md FR-3.26.x；design.md 4.2.26
**输出**：`tests/unit/test_create_vault.py`

### 子任务 7.1：编写 test_create_vault.py（1 项 FR）
- **描述**：测试 main（目标已存在且有内容未--force→拒绝返回1/目标不存在或空→创建拷贝/拷贝完成→初始化状态文件+索引目录/demo=True→跑demo/init_git=True→git init+提交）。在 tmp_path 中执行，不触碰真实项目
- **验收标准**：覆盖 FR-3.26.1 全部 WHEN-THEN 条件

---

## 任务 8：集成测试

**描述**：编写模块间协作链路的集成测试，验证多模块组合流程的正确性。

**输入**：spec.md FR-3.2.x 至 FR-3.13.x；design.md 第 5 节
**输出**：`tests/integration/` 下 6 个测试文件

### 子任务 8.1：编写 test_rag_feedback_pipeline.py
- **描述**：在 tmp_vault_with_git 中创建5篇笔记 → rag.cmd_index 构建索引 → feedback_loop.ingest 累计命中 → feedback_loop.bumps 生成提升 → feedback_loop.apply_bumps 写入 → 验证 importance 已提升（只升不降）
- **验收标准**：完整链路执行无异常；笔记 frontmatter importance 值已增加

### 子任务 8.2：编写 test_rag_recall_pipeline.py
- **描述**：在 tmp_vault_with_git 中创建不同 importance 笔记 → rag.cmd_index → recall_schedule.build(deck_n=10) → 验证 deck 按 urgency 降序 → recall_schedule.mark(deck_n=2) → 验证 state 文件 last_seen/stability/recalls 已更新
- **验收标准**：deck 排序正确；state 文件含更新后的回忆记录

### 子任务 8.3：编写 test_clean_validate_pipeline.py
- **描述**：在 tmp_vault_with_git 中创建缺字段笔记 → clean.do_clean(dry=False) → validate.validate_file → 验证清洗后 frontmatter 含全部必填字段且 validate 无硬错误
- **验收标准**：清洗后笔记通过 validate 校验

### 子任务 8.4：编写 test_intake_triage_apply.py
- **描述**：在 tmp_vault_with_git 收件箱创建笔记 → intake_triage.triage(threshold) → intake_triage.apply_moves(move=True, trash=True) → 验证笔记已移到目标 PARA 区/归档区
- **验收标准**：笔记文件已从收件箱移到正确目标目录

### 子任务 8.5：编写 test_classify_sync_pipeline.py
- **描述**：在 tmp_vault_with_git 中创建不同内容类型文件 → classify.classify(body) → sync.cmd_ingest_any → 验证文件已路由到对应 PARA 区且 frontmatter 含正确 content_type
- **验收标准**：文件路由到正确目录；frontmatter content_type 与分类结果一致

### 子任务 8.6：编写 test_state_manager_integration.py
- **描述**：在 tmp_vault 中通过 StateStore 写入 feedback_state.json → feedback_loop.load_state 读取 → 验证数据一致 → migrate_legacy_state 迁移旧路径 → 验证旧路径文件已迁移到 .kb/state/
- **验收标准**：跨模块状态读写一致；旧路径迁移成功

---

## 任务 9：端到端测试

**描述**：编写 CLI 命令端到端测试，通过 subprocess 执行完整命令流程。

**输入**：spec.md FR-3.2.x、FR-3.9.x、FR-3.10.x、FR-3.11.x、FR-3.26.x；design.md 第 6 节
**输出**：`tests/e2e/` 下 5 个测试文件

### 子任务 9.1：编写 test_rag_cli.py
- **描述**：在 tmp_vault_with_git 中创建笔记 → 执行 `python3 rag.py index --root <vault>` → 验证 stdout 含索引就绪信息 + df_idf.json 存在 → 执行 `python3 rag.py query "测试" --json --root <vault>` → 解析 stdout JSON 验证含 query/hits/meta
- **验收标准**：CLI index 和 query 命令执行成功；JSON 输出结构正确

### 子任务 9.2：编写 test_healthcheck_cli.py
- **描述**：在 tmp_vault 中创建含死链/骨架/越界标签的笔记 → 执行 `python3 kb-healthcheck.py all --root <vault>` → 验证 stdout 含各项巡检结果 → 验证退出码非0（有问题时）
- **验收标准**：CLI 巡检命令输出含预期问题报告

### 子任务 9.3：编写 test_clean_cli.py
- **描述**：在 tmp_vault_with_git 中创建缺字段笔记 → 执行 `python3 clean.py dry-run --root <vault>` → 验证 stdout 含拟执行说明 → 执行 `python3 clean.py apply --root <vault>` → 验证笔记 frontmatter 已补全
- **验收标准**：dry-run 输出拟执行步骤；apply 后笔记 frontmatter 完整

### 子任务 9.4：编写 test_validate_cli.py
- **描述**：在 tmp_vault 中创建合法/非法笔记 → 执行 `python3 validate.py --json --root <vault>` → 解析 stdout JSON 验证含 total/valid/hard_issues/warn_issues
- **验收标准**：CLI 输出 JSON 结构正确；合法/非法笔记计数正确

### 子任务 9.5：编写 test_create_vault_cli.py
- **描述**：执行 `python3 create_vault.py --vault <tmp_path/test-vault> --no-git --no-demo` → 验证目标目录含 PARA 分区 + pipeline/ + reference/ + vector index/
- **验收标准**：安装后目录结构完整；含所有预期子目录和文件

---

## 任务依赖关系

```
任务 1（基础设施）
  ├── 任务 2（核心工具单元测试）
  ├── 任务 3（检索引擎单元测试）
  ├── 任务 4（成长引擎单元测试）
  ├── 任务 5（运维工具单元测试）
  ├── 任务 6（记忆摄取单元测试）
  └── 任务 7（安装引擎单元测试）
        │
        ├── 任务 8（集成测试）← 依赖任务 2-7 的模块测试验证
        └── 任务 9（端到端测试）← 依赖任务 2-7 的模块测试验证
```

- 任务 1 是所有后续任务的前置条件
- 任务 2-7 之间无依赖，可并行开发，但建议按序执行（2→3→4→5→6→7）
- 任务 8 依赖任务 2-7 完成后才能验证集成点
- 任务 9 依赖任务 2-7 完成后才能验证 CLI 入口

---

## 代码生成提示

### 通用模板（每个测试文件）

```python
"""测试 <模块名> 的 <功能描述>。"""
import pytest
from pathlib import Path

# 模块通过 conftest.py 的 pipeline_path fixture 导入
# 在测试函数中通过 pipeline_modules fixture 获取模块对象


class TestXxxModule:
    """<模块名> 单元测试。"""

    def test_function_condition_expected(self, tmp_vault, pipeline_modules):
        """验证 FR-3.x.x: <WHEN-THEN 描述>"""
        # Arrange: 构造测试数据
        # Act: 调用被测函数
        # Assert: 验证结果
```

### 参数化测试模板

```python
@pytest.mark.parametrize("input_text,expected", [
    ("hello world", ["hello", "world"]),
    ("你好世界", ["你", "好", "世", "界", "你好", "好世", "世界"]),
    ("", []),
])
def test_tokenize_parametrized(input_text, expected, pipeline_modules):
    result = pipeline_modules["kb_common"].tokenize(input_text)
    assert result == expected
```

### CLI 测试模板

```python
def test_rag_cli_index_query(tmp_vault_with_git, pipeline_path):
    """E2E: rag.py index + query CLI"""
    import subprocess, sys, json
    # index
    result = subprocess.run(
        [sys.executable, str(pipeline_path / "rag.py"), "index", "--root", str(tmp_vault_with_git)],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert (tmp_vault_with_git / "vector index" / "df_idf.json").exists()
    # query
    result = subprocess.run(
        [sys.executable, str(pipeline_path / "rag.py"), "query", "测试", "--json", "--root", str(tmp_vault_with_git)],
        capture_output=True, text=True
    )
    output = json.loads(result.stdout)
    assert "query" in output and "hits" in output
```
