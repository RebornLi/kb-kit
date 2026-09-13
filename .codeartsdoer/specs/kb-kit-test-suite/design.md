# kb-kit 测试套件技术设计文档

## 1. 设计概述

### 1.1 设计目标
为 kb-kit 项目的 25 个 pipeline 模块及 `create_vault.py` 安装引擎设计一套完整的 pytest 测试套件，覆盖单元测试、集成测试和端到端测试三个层级，确保所有功能需求（FR-3.x.x.x）可验证、可执行、可隔离。

### 1.2 设计原则
- **零外部依赖**：除 pytest 外不引入任何 pip 包
- **测试隔离**：所有文件系统操作在 `tmp_path` fixture 中进行，不触碰原项目
- **模块可独立测试**：每个模块的测试可单独运行，不依赖其他测试先执行
- **Fixture 复用**：共享测试数据（笔记样本、配置文件、临时 vault）通过 conftest.py 集中管理
- **类型安全**：所有 fixture 和辅助函数有明确类型注解

### 1.3 技术栈
| 组件 | 选型 | 说明 |
|------|------|------|
| 测试框架 | pytest ≥ 7.0 | 原生支持 fixture/parametrize/mark |
| Python 版本 | 3.8+ | 与被测项目一致 |
| 覆盖率工具 | pytest-cov（可选） | 非必须，有则统计 |
| 断言库 | pytest 原生 assert | 不引入 assertpy/hamcrest |
| Mock 库 | unittest.mock（标准库） | mock 外部 subprocess/git |
| 路径管理 | pathlib.Path | 与被测项目一致 |

---

## 2. 目录结构设计

```
tests/
├── conftest.py                          # 全局 fixtures（pipeline_path, tmp_vault, sample_notes...）
├── unit/                                # 单元测试（按模块组织）
│   ├── test_kb_common.py                # kb_common.py 的 15 项 FR
│   ├── test_rag.py                      # rag.py 的 13 项 FR
│   ├── test_intake_triage.py            # intake_triage.py 的 5 项 FR
│   ├── test_feedback_loop.py            # feedback_loop.py 的 5 项 FR
│   ├── test_link_engine.py              # link_engine.py 的 3 项 FR
│   ├── test_recall_schedule.py          # recall_schedule.py 的 4 项 FR
│   ├── test_kb_health.py                # kb_health.py 的 3 项 FR
│   ├── test_dashboard.py                # dashboard.py 的 3 项 FR
│   ├── test_kb_healthcheck.py           # kb-healthcheck.py 的 8 项 FR
│   ├── test_clean.py                    # clean.py 的 5 项 FR
│   ├── test_validate.py                 # validate.py 的 2 项 FR
│   ├── test_sync.py                     # sync.py 的 3 项 FR
│   ├── test_state_manager.py            # state_manager.py 的 4 项 FR
│   ├── test_classify.py                 # classify.py 的 3 项 FR
│   ├── test_graph.py                    # graph.py 的 2 项 FR
│   ├── test_rerank.py                   # rerank.py 的 3 项 FR
│   ├── test_semantic_chunk.py           # semantic_chunk.py 的 2 项 FR
│   ├── test_memory_ingest.py            # memory_ingest.py 的 4 项 FR
│   ├── test_memory_sync.py              # memory_sync.py 的 3 项 FR
│   ├── test_memory_ingest_json.py       # memory_ingest_json.py 的 1 项 FR
│   ├── test_memory_ingest_sqlite.py     # memory_ingest_sqlite.py 的 2 项 FR
│   ├── test_agent_registry.py           # agent_registry.py 的 3 项 FR
│   ├── test_ingest_chat.py              # ingest_chat.py 的 3 项 FR
│   ├── test_ingest_convert.py           # ingest_convert.py 的 3 项 FR
│   ├── test_user_manager.py             # user_manager.py 的 4 项 FR
│   └── test_create_vault.py             # create_vault.py 的 1 项 FR
├── integration/                         # 集成测试（模块间协作）
│   ├── test_rag_feedback_pipeline.py    # rag 索引 → feedback 命中 → importance 提升
│   ├── test_rag_recall_pipeline.py      # rag 索引 → recall deck → mark 回忆
│   ├── test_clean_validate_pipeline.py  # clean 清洗 → validate 校验
│   ├── test_intake_triage_apply.py      # triage review → apply move/trash
│   ├── test_classify_sync_pipeline.py   # classify 分类 → sync 路由写入
│   └── test_state_manager_integration.py # state_manager 跨模块协作
├── e2e/                                 # 端到端测试（CLI 命令）
│   ├── test_rag_cli.py                  # rag.py index + query CLI
│   ├── test_healthcheck_cli.py          # kb-healthcheck.py all CLI
│   ├── test_clean_cli.py                # clean.py dry-run + apply + report CLI
│   ├── test_validate_cli.py             # validate.py --json CLI
│   └── test_create_vault_cli.py         # create_vault.py --vault CLI
└── fixtures/                            # 测试数据工厂
    ├── __init__.py
    ├── note_factory.py                  # 构造测试笔记（frontmatter + body）
    ├── vault_factory.py                 # 构造临时 vault 目录结构
    └── config_factory.py                # 构造临时配置文件
```

---

## 3. 核心架构设计

### 3.1 全局 Fixture 设计 (conftest.py)

```python
# conftest.py 核心接口设计

@pytest.fixture(scope="session")
def pipeline_path() -> Path:
    """返回 pipeline 模块所在目录的绝对路径。
    所有测试通过此路径 import 被测模块。"""

@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """构造一个临时 vault 目录，含 PARA 分区 + reference 配置。
    每个测试函数独立一份，互不干扰。
    目录结构：
      tmp_vault/
        00-收件箱 Inbox/
        10-项目 Projects/
        20-技术 Technology/
        30-决策日志 Decisions/
        40-资源库 Resources/
        50-模板 Templates/
        60-运营 Operations/
        70-知识治理 Governance/
        90-归档 Archive/
        reference/
          domain-taxonomy.json
          tag-taxonomy.json
          dedup-config.json
          synonyms.json
          content-type-signals.json
        pipeline/  → symlink 到真实 pipeline/
        vector index/"""

@pytest.fixture
def tmp_vault_with_git(tmp_vault: Path) -> Path:
    """在 tmp_vault 基础上初始化 git 仓库。
    配置 user.email/user.name，做首次 commit。"""

@pytest.fixture
def sample_note_text() -> str:
    """返回标准测试笔记文本（含完整 frontmatter + body）。"""

@pytest.fixture
def sample_notes(tmp_vault: Path) -> list[Path]:
    """在 tmp_vault 中创建 5 篇测试笔记，覆盖不同 domain/status/content_type。
    返回笔记文件路径列表。"""

@pytest.fixture
def pipeline_modules(pipeline_path: Path) -> dict:
    """动态 import 所有 pipeline 模块，返回 {模块名: 模块对象} 字典。
    供测试直接调用模块函数，不经过 subprocess。"""

@pytest.fixture
def mock_env(monkeypatch) -> None:
    """清除可能干扰测试的环境变量（KB_ROOT/ORNITH_API_KEY 等）。"""
```

### 3.2 测试数据工厂设计

#### 3.2.1 note_factory.py

```python
def make_note(
    title: str = "测试笔记",
    tags: list[str] = ["knowledge"],
    status: str = "active",
    domain: str = "开发",
    importance: float = 0.5,
    created: str = "2025-01-01",
    updated: str = "2025-01-01",
    kb_target: str = "20-技术 Technology",
    kb_action: str = "new",
    kb_summary: str = "测试摘要",
    content_type: str = "knowledge",
    body: str = "这是测试正文内容。",
    extra_fm: dict = None,
) -> str:
    """构造带完整 frontmatter 的笔记文本。"""

def make_minimal_note(body: str = "正文") -> str:
    """构造无 frontmatter 的笔记文本。"""

def make_retired_note(body: str = "已归档内容") -> str:
    """构造 kb_action=retire 的笔记文本。"""
```

#### 3.2.2 vault_factory.py

```python
def create_vault_structure(root: Path) -> None:
    """在 root 下创建完整 PARA 分区目录 + reference/ + pipeline/ symlink。"""

def create_note(root: Path, rel_path: str, content: str) -> Path:
    """在 vault 内指定相对路径创建笔记文件。"""

def create_config(root: Path, name: str, data: dict) -> Path:
    """在 reference/ 下创建配置 JSON 文件。"""
```

#### 3.2.3 config_factory.py

```python
def domain_taxonomy() -> dict:
    """返回标准 domain-taxonomy.json 内容。"""

def tag_taxonomy() -> dict:
    """返回标准 tag-taxonomy.json 内容。"""

def dedup_config() -> dict:
    """返回标准 dedup-config.json 内容。"""

def synonyms() -> dict:
    """返回标准 synonyms.json 内容。"""

def content_type_signals() -> dict:
    """返回标准 content-type-signals.json 内容。"""
```

---

## 4. 模块测试设计

### 4.1 测试分层架构图

```
┌─────────────────────────────────────────────────────────┐
│                    E2E 测试 (e2e/)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │rag CLI   │ │healthchk │ │clean CLI │ │validate  │   │
│  │index+query│ │  all     │ │dry+apply │ │ --json   │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
├─────────────────────────────────────────────────────────┤
│                集成测试 (integration/)                    │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │
│  │rag→feedback │ │rag→recall   │ │clean→validate│      │
│  │→importance  │ │→deck→mark   │ │→校验通过     │      │
│  └─────────────┘ └─────────────┘ └─────────────┘       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │
│  │triage review│ │classify→sync│ │state_manager│      │
│  │→apply move  │ │→路由写入    │ │跨模块协作    │      │
│  └─────────────┘ └─────────────┘ └─────────────┘       │
├─────────────────────────────────────────────────────────┤
│                  单元测试 (unit/)                         │
│  ┌────────┐┌────────┐┌────────┐�!─── 26 模块 ───┐     │
│  │kb_common││  rag   ││validate││classify│clean │     │
│  │15 FRs   ││13 FRs  ││ 2 FRs  ││ 3 FRs  │5 FRs │     │
│  └────────┘└────────┘└────────┘└────────┘──────┘     │
├─────────────────────────────────────────────────────────┤
│              Fixture 层 (conftest.py + fixtures/)        │
│  tmp_vault │ sample_notes │ pipeline_modules │ mock_env │
└─────────────────────────────────────────────────────────┘
```

### 4.2 各模块测试策略

#### 4.2.1 kb_common.py 测试策略
- **导入方式**：`sys.path.insert(0, pipeline_path)` 后 `import kb_common`
- **纯函数测试**：`tokenize` / `parse_frontmatter` / `count_tokens` / `text_entropy` / `content_fingerprint` / `parse_domain` 不需要文件系统
- **文件系统测试**：`load_meta` / `load_note` / `iter_notes` / `load_config` 使用 `tmp_path` 构造临时文件
- **缓存测试**：`load_config` 的缓存行为需通过清除 `kb_common._config_cache` 来重置
- **去重测试**：`dedup_check` 需构造 `existing_notes` 列表，验证三种 action（skip/mark_related/new）

#### 4.2.2 rag.py 测试策略
- **索引构建**：在 `tmp_vault` 中创建笔记，调用 `cmd_index(root)` 后验证 `df_idf.json` 结构
- **增量更新**：首次索引后修改笔记内容/mtime，再次索引验证增量逻辑
- **查询测试**：构建索引后调用 `cmd_query(root, q, top, ...)` 验证返回值和输出
- **过滤测试**：构造含不同 domain/content_type/author/importance 的笔记，验证 8 种过滤器
- **Mock 策略**：`llm_answer` 函数不测试（排除范围），相关测试 mock 环境变量确保不触发
- **内部函数**：`_vec` / `_cos` / `_dedup_chunks` / `_apply_filters` / `_build_inverted_index` / `_inverted_lookup` 直接调用测试

#### 4.2.3 intake_triage.py 测试策略
- **density 函数**：纯函数测试，输入不同文本验证密度值范围
- **evaluate 函数**：构造完整参数 `(rel, path, fm, text, body, vsim, target)`，验证四维打分和 action
- **triage 函数**：在 `tmp_vault` 的收件箱中创建笔记，调用 `triage(root, threshold)` 验证结果列表
- **triage_by_content_type**：参数化测试 12 种 content_type 的路由结果
- **_domain_to_para**：参数化测试所有 domain 映射

#### 4.2.4 feedback_loop.py 测试策略
- **hits_for**：需要先构建 rag 索引，再调用 `hits_for(idx, query, topk)`
- **bumps**：构造 hits 字典验证提升建议计算（含 MIN_HITS/BUMP_CAP/MIN_BUMP 边界）
- **_write_importance**：在 `tmp_vault` 中创建笔记，调用后读回验证 frontmatter
- **record_hit**：调用后验证 state 文件含 explicit_feedback
- **_accumulate_importance**：需 `tmp_vault_with_git` fixture（因内部有 git commit）

#### 4.2.5 link_engine.py 测试策略
- **outbound**：纯函数测试，输入含各种 wikilink 格式的文本
- **suggestions**：在 `tmp_vault` 中创建相似笔记对，调用后验证 recs/cross 列表
- **apply_links**：需 `tmp_vault_with_git`，验证幂等性和建议区块写入

#### 4.2.6 recall_schedule.py 测试策略
- **base_interval**：参数化测试 importance → interval 映射的 4 个区间
- **build**：在 `tmp_vault` 中创建不同 importance 的笔记，验证 deck 排序和过滤
- **retrievable**：需先构建 rag 索引
- **mark**：调用后验证 state 文件的 last_seen/stability/recalls 更新

#### 4.2.7 kb_health.py 测试策略
- **metrics**：在 `tmp_vault` 中创建含出链/无出链/跨域链接的笔记，验证六维度量值
- **score_and_alerts**：参数化测试不同输入组合的评分和告警
- **check_policy_stale**：创建含/不含 review_cycle 的 policy 笔记，验证过期检测

#### 4.2.8 dashboard.py 测试策略
- **run_validate / check_backup / check_pipeline / check_growth**：mock subprocess.run 返回预设结果
- **main**：在 `tmp_vault_with_git` 中运行，验证输出文件含预期区块

#### 4.2.9 kb-healthcheck.py 测试策略
- **8 项巡检**：每项独立测试，构造含/不含问题的 `tmp_vault`
- **check_deadlinks**：构造含有效 wikilink/死链/系统目录链接的笔记
- **check_skeletons**：构造小文件 + 调整 mtime（用 `os.utime`）
- **check_tags**：构造含白名单内/外/噪声标签的笔记
- **check_empty**：在 PARA 分区下创建空目录
- **check_frontmatter**：构造有/无 frontmatter + 缺字段的笔记
- **check_discipline**：在 vault 根创建合规/违规顶层目录
- **check_orphans**：构造有出链/无出链/MOC 笔记
- **check_overlong**：构造正文 > 2000 字 / ≤ 2000 字的笔记

#### 4.2.10 clean.py 测试策略
- **route_chunk_strategy**：参数化测试 4 个字符区间
- **infer_domain**：构造不同 rel/fm/title/body 组合
- **infer_status**：构造不同路径/mtime（用 `os.utime` 模拟旧文件）
- **clean_note**：在 `tmp_vault` 中创建笔记，调用后验证 frontmatter 完整性
- **aggregate_bucket**：在 `tmp_vault` 中调用，验证桶文件创建/追加

#### 4.2.11 validate.py 测试策略
- **parse_frontmatter**：纯函数测试有/无 frontmatter 的文本
- **validate_file**：参数化测试各种 frontmatter 组合（缺字段/非法值/过长正文等），验证 hard/warn 列表

#### 4.2.12 sync.py 测试策略
- **route_domain**：参数化测试所有 domain → (PARA, subdir) 映射
- **cmd_dry**：mock `run_validate` 返回有/无硬错误的结果
- **cmd_ingest_any**：在 `tmp_vault` 中创建不同格式文件，验证路由决策

#### 4.2.13 state_manager.py 测试策略
- **StateStore 类**：在 `tmp_path` 中测试 load/save/update/exists/remove
- **原子写入**：save 后验证 `.tmp` 中间文件不存在
- **文件锁**：Unix 下验证 flock 行为（可选，Windows 跳过）
- **migrate_legacy_state**：在旧路径创建状态文件，调用后验证迁移

#### 4.2.14 classify.py 测试策略
- **classify**：参数化测试噪声/聊天/非聊天/兜底场景
- **classify_quality**：参数化测试噪声/简短对话/高质量/参考资料
- **_detect_chat_context**：构造含 participants/chat_time/channel 的 fm 和含聊天行模式的 body

#### 4.2.15 graph.py 测试策略
- **collect_nodes**：在 `tmp_vault` 中创建含/不含关系字段的笔记
- **build_mermaid**：构造节点字典验证 Mermaid 语法（含节点形状/边样式/截断）

#### 4.2.16 rerank.py 测试策略
- **_token_overlap**：纯函数测试 Jaccard 相似度
- **_frontmatter_match**：构造含 domain/tags/content_type 的 fm
- **rerank**：在 `tmp_vault` 中创建笔记，验证 5 维度加权排序

#### 4.2.17 semantic_chunk.py 测试策略
- **_sliding_window**：构造不同长度文本验证分块数和 overlap
- **chunk**：验证分块笔记的 frontmatter 字段和父索引页

#### 4.2.18 memory_ingest.py 测试策略
- **_text_sim**：纯函数测试 Jaccard 相似度
- **normalize_bucket**：参数化测试简写/标准名/未知 bucket
- **_safe_kb_path**：参数化测试绝对路径/路径穿越/正常路径
- **mirror**：在 `tmp_vault` 中创建 agent 记忆源目录，调用后验证镜像结果

#### 4.2.19 memory_sync.py 测试策略
- **signal_density**：纯函数测试
- **slug**：参数化测试特殊字符/空输入
- **grade**：需先构建 rag 索引，构造不同 body 验证五问门禁

#### 4.2.20 memory_ingest_json.py 测试策略
- **ingest**：在 `tmp_path` 中创建 DSH 格式 JSON 文件，调用后验证摄取结果和幂等性

#### 4.2.21 memory_ingest_sqlite.py 测试策略
- **_extract_turns**：在 `tmp_path` 中创建 SQLite 数据库（含 thread_items 表），调用后验证 turn 聚合
- **ingest**：完整摄取流程测试

#### 4.2.22 agent_registry.py 测试策略
- **detect_agents**：mock `shutil.which` / `Path.exists` 验证探测逻辑
- **load_registry / save_registry**：在 `tmp_path` 中测试读写
- **cmd_add**：测试已存在/未注册场景

#### 4.2.23 ingest_chat.py 测试策略
- **_detect_platform**：参数化测试文件名关键词/扩展名兜底
- **_segment**：构造消息列表验证时间间隔切分和超长段二次切分
- **parse_chat**：在 `tmp_path` 中创建各平台格式文件，验证解析结果

#### 4.2.24 ingest_convert.py 测试策略
- **convert**：在 `tmp_path` 中创建各格式文件（txt/csv/html/eml/json），验证转换结果
- **detect_schema**：参数化测试 DSH/Codex/nested_array/generic
- **_save_mirror**：验证原样复制和同名冲突处理
- **外部工具**：`_convert_pdf` / `_convert_docx` 等用 `@pytest.mark.skipif` 跳过

#### 4.2.25 user_manager.py 测试策略
- **cmd_add / cmd_list / cmd_remove / cmd_check_perm**：在 `tmp_vault` 中测试完整 CRUD 流程
- **权限模型**：参数化测试 admin/editor/reader 三种角色的权限集合

#### 4.2.26 create_vault.py 测试策略
- **main**：在 `tmp_path` 中执行安装，验证目录拷贝/状态文件/索引目录
- **--no-git / --no-demo**：验证跳过 git/demo 的行为
- **--force**：验证覆盖已有目录

---

## 5. 集成测试设计

### 5.1 rag → feedback → importance 提升链路

```
步骤 1: 在 tmp_vault_with_git 中创建 5 篇笔记
步骤 2: 调用 rag.cmd_index(root) 构建索引
步骤 3: 调用 feedback_loop.ingest(root) 累计命中
步骤 4: 调用 feedback_loop.bumps(hits) 生成提升建议
步骤 5: 调用 feedback_loop.apply_bumps(root) 写入 importance
验证: 读取笔记 frontmatter，importance 已提升（只升不降）
```

### 5.2 rag → recall → deck → mark 链路

```
步骤 1: 在 tmp_vault_with_git 中创建不同 importance 的笔记
步骤 2: 调用 rag.cmd_index(root) 构建索引
步骤 3: 调用 recall_schedule.build(root, deck_n=10) 构建 deck
步骤 4: 验证 deck 中笔记按 urgency 降序排列
步骤 5: 调用 recall_schedule.mark(root, deck_n=2, rels=None) 标记已回忆
验证: state 文件中 last_seen/stability/recalls 已更新
```

### 5.3 clean → validate 链路

```
步骤 1: 在 tmp_vault_with_git 中创建缺字段的笔记
步骤 2: 调用 clean.do_clean(root, dry=False) 清洗
步骤 3: 调用 validate.validate_file(path) 校验清洗后的笔记
验证: 清洗后 frontmatter 含全部必填字段，validate 无硬错误
```

### 5.4 intake_triage review → apply 链路

```
步骤 1: 在 tmp_vault_with_git 的收件箱中创建笔记
步骤 2: 调用 intake_triage.triage(root, threshold) �!获取结果
步骤 3: 调用 intake_triage.apply_moves(root, results, move=True, trash=True)
验证: 笔记已移到目标 PARA 区/归档区
```

### 5.5 classify → sync 路由写入链路

```
步骤 1: 在 tmp_vault_with_git 中创建不同内容类型的文件
步骤 2: 调用 classify.classify(body) 获取分类结果
步骤 3: 调用 sync.cmd_ingest_any(args) 摄取
验证: 文件已路由到对应 PARA 区，frontmatter 含正确 content_type
```

### 5.6 state_manager 跨模块协作

```
步骤 1: 在 tmp_vault 中通过 StateStore 写入 feedback_state.json
步骤 2: 通过 feedback_loop.load_state(root) 读取
验证: 数据一致，路径映射正确
步骤 3: 调用 migrate_legacy_state 迁移旧路径
验证: 旧路径文件已迁移到 .kb/state/
```

---

## 6. 端到端测试设计

### 6.1 CLI 测试通用模式

```python
def run_cli(pipeline_path: Path, module: str, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """执行 python3 pipeline/<module>.py <args>，返回结果。
    通用 CLI 调用封装，所有 E2E 测试使用此函数。"""
    cmd = [sys.executable, str(pipeline_path / f"{module}.py"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd))
```

### 6.2 rag CLI 端到端

```
步骤 1: 在 tmp_vault_with_git 中创建笔记
步骤 2: run_cli("rag.py", ["index", "--root", str(root)])
步骤 3: 验证 stdout 含 "全量索引就绪"，df_idf.json 存在
步骤 4: run_cli("rag.py", ["query", "测试问题", "--json", "--root", str(root)])
步骤 5: 解析 stdout JSON，验证含 query/hits/meta
```

### 6.3 kb-healthcheck CLI 端到端

```
步骤 1: 在 tmp_vault 中创建含死链/骨架/越界标签的笔记
步骤 2: run_cli("kb-healthcheck.py", ["all", "--root", str(root)])
步骤 3: 验证 stdout 含各项巡检结果
步骤 4: 验证退出码非 0（有问题时）
```

### 6.4 clean CLI 端到端

```
步骤 1: 在 tmp_vault_with_git 中创建缺字段笔记
步骤 2: run_cli("clean.py", ["dry-run", "--root", str(root)])
步骤 3: 验证 stdout 含拟执行说明
步骤 4: run_cli("clean.py", ["apply", "--root", str(root)])
步骤 5: 验证笔记 frontmatter 已补全
```

### 6.5 validate CLI 端到端

```
步骤 1: 在 tmp_vault 中创建合法/非法笔记
步骤 2: run_cli("validate.py", ["--json", "--root", str(root)])
步骤 3: 解析 stdout JSON，验证 total/valid/hard_issues/warn_issues
```

### 6.6 create_vault CLI 端到端

```
步骤 1: run_cli(create_vault.py, ["--vault", str(tmp_path / "test-vault"), "--no-git", "--no-demo"])
步骤 2: 验证目标目录含 PARA 分区 + pipeline/ + reference/
步骤 3: 验证 vector index/ 目录存在
```

---

## 7. 数据模型设计

### 7.1 测试笔记模型

```python
@dataclass
class TestNote:
    """测试笔记数据模型。"""
    rel_path: str               # vault 内相对路径
    title: str                   # 标题
    tags: list[str]              # 标签列表
    status: str                  # 状态
    domain: str                  # 领域
    importance: float            # 重要度
    content_type: str            #!内容类型
    body: str                    # 正文
    extra_fm: dict               # 额外 frontmatter 字段

    def to_markdown(self) -> str:
        """序列化为带 frontmatter 的 Markdown 文本。"""
```

### 7.2 测试结果模型

```python
@dataclass
class CLITestResult:
    """CLI 测试结果。"""
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def json_output(self) -> dict:
        """解析 stdout 为 JSON。"""
```

---

## 8. Mock 与隔离策略

### 8.1 环境变量隔离
```python
@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """自动清除可能干扰测试的环境变量。"""
    for key in ("KB_ROOT", "ORNITH_API_KEY", "ORNITH_BASE_URL",
                "AGENT_MEMORY", "AGENT_ROOT", "VAULT_ROOT"):
        monkeypatch.delenv(key, raising=False)
```

### 8.2 Git 操作隔离
- 所有需要 git 的测试使用 `tmp_vault_with_git` fixture
- 在临时目录 `git init`，配置占位 user.email/user.name
- 不触碰项目本身的 git 仓库

### 8.3 配置缓存隔离
```python
@pytest.fixture(autouse=True)
def reset_config_cache():
    """每个测试前后清除 kb_common 配置缓存。"""
    import kb_common
    kb_common._config_cache.clear()
    yield
    kb_common._config_cache.clear()
```

### 8.4 外部工具 Mock
```python
# pdftotext/pandoc 等外部工具用 skipif 跳过
@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext 未安装")
def test_convert_pdf():
    ...
```

### 8.5 模块导入策略
```python
# conftest.py 中统一处理 sys.path
import sys
from pathlib import Path

PIPELINE_PATH = Path(__file__).resolve().parents[1 / "template-vault" / "pipeline"
if str(PIPELINE_PATH! not in sys.path:
    sys.path.insert(0, str(PIPELINE_PATH))
```

---

## 9. 错误处理与边界条件

### 9.1 需覆盖的边界条件
| 模块 | 边界条件 |
|------|----------|
| kb_common.tokenize | 空字符串、纯标点、超长文本 |
| kb_common.parse_frontmatter | 无 frontmatter、frontmatter 无闭合 `---`、空 frontmatter |
| rag._cos | 零向量、完全相同向量、正交向量 |
| rag._dedup_chunks | 空 scored、top=0、全部同一笔记 |
| classify.classify | 空正文、纯空白、超长正文 |
| validate.validate_file | 文件不存在、编码错误、空 frontmatter |
| state_manager.StateStore | 文件损坏、并发写入（mock）、空 dict |
| memory_ingest._safe_kb_path | 绝对路径、`../` 穿越、正常路径 |
| semantic_chunk._sliding_window | 空文本、单段落、无段落分隔 |

### 9.2 异常处理测试
- **文件不存在**：所有读文件的函数需测试 `FileNotFoundError` / `OSError` 行为
- **编码错误**：`UnicodeDecodeError` 时降级处理（`errors="replace"`）
- **JSON 解析失败**：`json.JSONDecodeError` 时返回默认值
- **git 不可用**：`subprocess.run(["git", ...])` 失败时不崩溃

---

## 10. 测试执行配置

### 10.1 pytest.ini

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
markers =
    unit: 单元测试
    integration: 集成测试
    e2e: 端到端测试
    slow: 慢测试（可选跳过）
addopts = -v --tb=short
```

### 10.2 执行命令

```bash
# 全部测试
pytest tests/

# 仅单元测试
pytest tests/unit/

# 仅集成测试
pytest tests/integration/

# 仅端到端测试
pytest tests/e2e/

# 按模块测试
pytest tests/unit/test_kb_common.py

# 排除慢测试
pytest tests/ -m "not slow"

# 带覆盖率（可选）
pytest tests/ --cov=template-vault.pipeline --cov-report=term-missing
```

---

## 11. 需求追溯矩阵

| 需求 ID | 测试文件 | 测试函数（示例） | 类型 |
|---------|----------|-------------------|------|
| FR-3.1.1 | test_kb_common.py | test_tokenize_latin / test_tokenize_cjk / test_tokenize_mixed / test_tokenize_empty | unit |
| FR-3.1.2 | test_kb_common.py | test_parse_frontmatter_none / test_parse_frontmatter_with_fm / test_parse_frontmatter_nested / test_parse_frontmatter_comment | unit |
| FR-3.1.15 | test_kb_common.py | test_dedup_check_exact / test_dedup_check_semantic / test_dedup_check_related / test_dedup_check_new | unit |
| FR-3.2.1 | test_rag.py | test_cmd_index_full_rebuild / test_cmd_index_skip_retire / test_cmd_index_skip_no_token | unit |
| FR-3.2.11 | test_rag.py | test_cmd_query_json_output / test_cmd_query_context_budget | unit |
| FR-3.9.1 | test_kb_healthcheck.py | test_check_deadlinks_valid / test_check_deadlinks_broken / test_check_deadlinks_system / test_check_deadlinks_template | unit |
| FR-3.26.1 | test_create_vault.py | test_create_vault_refuse_existing / test_create_vault_new / test_create_vault_with_demo / test_create_vault_with_git | e2e |
| ... | ... | ... | ... |

（完整矩阵覆盖所有 ~100 项 FR，每项至少 1 个测试函数）
