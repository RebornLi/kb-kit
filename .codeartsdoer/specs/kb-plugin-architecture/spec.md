# KB 插件化架构需求规格说明书

## 1. 项目概述

### 1.1 项目背景
- **项目名称**：kb-kit（知识库一键套件）
- **项目路径**：`/home/mushan/workspace/kb-kit`
- **技术栈**：Python 3.8+，零 pip 依赖，仅使用标准库
- **当前定位**：kb-kit 是一个"知识库一键套件"，包含 Agent 插件系统和 KB 宿主系统两部分

### 1.2 当前架构现状

#### Agent 插件层（已插件化）
Agent 插件采用**静态适配器模式 + 运行时注册表**：
- `agent_registry.py`（351 行）是核心调度器，包含探测层（detect 函数）、注册表读写、命令层、适配器分发
- 三种适配器类型：`md`（subprocess 调用 `memory_ingest.py`）、`json`（直接 import `memory_ingest_json.py`）、`sqlite`（直接 import `memory_ingest_sqlite.py`）
- Agent 数据结构：`kb-agent.json` 包含 `version` + `agents` 字典，每个 agent 有 `type`/`label`/`enabled`/`sources` 字段
- 类型分发是**硬编码 if-elif 链**（第 233-240 行），探测函数固定四个（`detect_openclaw`/`detect_hermes`/`detect_dsh`/`detect_codex`），`AGENT_TYPES` 是常量元组

#### KB 宿主层（未插件化）
KB 是宿主系统，不是插件：
- 物理结构为 PARA 分区（`00-90` 共 10 个目录）
- 逻辑结构为 frontmatter 元数据规范
- 成长引擎 25 个模块直接部署在 `template-vault/pipeline/` 下，覆盖检索(rag)、摄入(intake_triage)、反馈(feedback_loop)、补链(link_engine)、回忆(recall_schedule)、健康(kb_health/dashboard/kb-healthcheck)、清洗(clean/validate)、同步(sync)、记忆摄取(memory_ingest/memory_sync/agent_registry)、分类(classify)、图谱(graph)等
- CLI 启动器 `kb`（bash case 分发）和 `kb.cmd`（Windows）将语义命令**硬编码映射**到 pipeline 脚本
- `state_manager.py` 提供统一状态管理（文件锁 + 原子写入）
- `growth_cron.sh` 定时节拍按序执行 8 步引擎

### 1.3 问题描述
当前架构存在**根本性的不对称**：
1. **Agent 是插件，KB 是宿主**：Agent 有注册表、探测、启用/禁用机制；KB 的 25 个模块是散落的脚本，没有统一的注册、加载、调用机制
2. **类型分发硬编码**：`agent_registry.py` 的 if-elif 链（md/json/sqlite）和 `AGENT_TYPES` 常量元组使得新增 Agent 类型需要修改核心调度器代码
3. **CLI 硬编码映射**：`kb` 启动器的 bash case 语句将每个命令硬编码到特定脚本路径，新增功能需要修改启动器
4. **无统一插件接口**：Agent 适配器和 KB 模块没有共同的基类或协议，无法统一管理生命周期、配置、依赖声明
5. **扩展性受限**：添加新的 Agent 类型或 KB 功能模块都需要修改核心代码，违反开闭原则

### 1.4 需求目标
将 KB 从"宿主系统"改造成"插件式"架构，使其与 openclaw、DSH、hermes 等 Agent 插件采用**统一的插件架构**。具体目标：
1. 引入统一的插件接口/基类，Agent 插件和 KB 功能插件都实现该接口
2. 建立统一的插件注册中心，替代 `agent_registry.py` 的硬编码 if-elif 链和 `kb` 启动器的 bash case 映射
3. KB 的 25 个功能模块封装为标准插件，通过注册中心统一加载和调度
4. 保持向后兼容，现有 CLI 命令、Agent 注册表、成长引擎流程不受影响

---

## 2. 系统范围

### 2.1 系统边界
- **改造范围**：`template-vault/pipeline/` 下的插件基础设施和模块封装
- **改造对象**：插件接口定义、插件注册中心、Agent 适配器插件化、KB 功能模块插件化、CLI 启动器适配
- **不改造**：各模块的内部业务逻辑（rag 的检索算法、memory_sync 的五问门禁、feedback_loop 的命中计数等保持不变）

### 2.2 包含范围
- 统一插件接口/协议定义
- 插件注册中心（替代 agent_registry 的硬编码分发 + kb 启动器的 bash case）
- 插件元数据规范（plugin manifest）
- 插件生命周期管理（发现 → 加载 → 注册 → 调用 → 卸载）
- Agent 适配器插件化（md/json/sqlite 三种适配器封装为插件）
- KB 功能模块插件化（25 个 pipeline 模块封装为插件）
- CLI 启动器适配（从硬编码 case 分发改为通过注册中心动态路由）
- 向后兼容层（现有 `kb-agent.json` 注册表、CLI 命令格式保持兼容）

### 2.3 排除范围
- 各模块内部业务逻辑的重写
- PARA 物理目录结构的调整
- frontmatter 元数据规范的变更
- 向量索引算法的修改
- 外部 LLM API 集成的修改
- Obsidian 配置的修改

---

## 3. 功能需求

### 3.1 统一插件接口

#### FR-3.1.1: 插件基协议定义
- **WHEN** 定义插件基协议，**THEN** 该协议包含以下标准方法：`metadata()`（返回插件元信息）、`initialize(context)`（初始化插件）、`execute(action, params)`（执行插件功能）、`shutdown()`（清理资源）
- **WHEN** 插件元信息包含字段，**THEN** 字段至少包含：`name`（插件名）、`version`（版本）、`type`（插件类型：agent/kb_module）、`actions`（支持的动作列表）、`dependencies`（依赖的其他插件名列表）

#### FR-3.1.2: 插件上下文对象
- **WHEN** 插件初始化时接收 context 对象，**THEN** context 包含：`vault_root`（知识库根路径）、`state_store`（状态管理器实例）、`config`（插件配置 dict）、`logger`（日志记录器）
- **WHEN** 插件通过 context 访问共享服务，**THEN** 不直接 import 其他模块，而是通过 context 提供的接口访问

#### FR-3.1.3: 插件类型区分
- **WHEN** 插件 type 为 `agent`，**THEN** 该插件实现 Agent 记忆摄取功能，actions 包含 `detect`/`ingest` 等动作
- **WHEN** 插件 type 为 `kb_module`，**THEN** 该插件实现 KB 功能模块，actions 对应原 CLI 子命令（如 `index`/`query`/`review`/`promote` 等）

### 3.2 插件注册中心

#### FR-3.2.1: 插件发现
- **WHEN** 注册中心启动，**THEN** 扫描 `pipeline/plugins/` 目录下的所有插件模块
- **WHEN** 发现插件模块，**THEN** 读取插件的 `metadata()` 方法获取元信息并注册到内部注册表
- **WHEN** 插件模块加载失败（ImportError/SyntaxError），**THEN** 记录错误日志并跳过该插件，不中断其他插件的加载

#### FR-3.2.2: 插件注册
- **WHEN** 插件被发现且元信息合法，**THEN** 注册到内部注册表，注册表结构为 `{plugin_name: {metadata, instance, status}}`
- **WHEN** 插件名重复，**THEN** 记录冲突警告，后加载的插件覆盖先加载的（或按配置策略决定）
- **WHEN** 插件有 dependencies 且依赖的插件未注册，**THEN** 记录依赖缺失警告并标记该插件为 `disabled`

#### FR-3.2.3: 插件查找与分发
- **WHEN** 调用方请求执行某插件某动作，**THEN** 注册中心查找插件实例并调用其 `execute(action, params)` 方法
- **WHEN** 请求的插件不存在，**THEN** 返回错误信息提示可用插件列表
- **WHEN** 请求的动作不在插件的 actions 列表中，**THEN** 返回错误信息提示该插件支持的动作列表

#### FR-3.2.4: 插件生命周期管理
- **WHEN** 注册中心启动，**THEN** 按依赖顺序执行各插件的 `initialize(context)` 方法
- **WHEN** 注册中心关闭，**THEN** 按逆依赖顺序执行各插件的 `shutdown()` 方法
- **WHEN** 插件 initialize 抛出异常，**THEN** 标记该插件为 `error` 状态，不影响其他插件

#### FR-3.2.5: 插件列表查询
- **WHEN** 查询已注册插件列表，**THEN** 返回所有插件的元信息及状态（loaded/disabled/error）
- **WHEN** 按类型过滤插件列表，**THEN** 只返回指定类型（agent/kb_module）的插件

### 3.3 Agent 适配器插件化

#### FR-3.3.1: MD 适配器插件
- **WHEN** 将 `memory_ingest.py` 封装为 MD 适配器插件，**THEN** 该插件 type 为 `agent`，actions 包含 `mirror`/`sync`/`mirror-core`/`extract`
- **WHEN** 执行 MD 适配器插件的 `mirror` 动作，**THEN** 调用原 `memory_ingest.py` 的 `mirror()` 函数，行为和输出保持一致
- **WHEN** 执行 MD 适配器插件的 `extract` 动作，**THEN** 调用原 `memory_ingest.py` 的 `extract()` 函数，行为和输出保持一致

#### FR-3.3.2: JSON 适配器插件
- **WHEN** 将 `memory_ingest_json.py` 封装为 JSON 适配器插件，**THEN** 该插件 type 为 `agent`，actions 包含 `ingest`
- **WHEN** 执行 JSON 适配器插件的 `ingest` 动作，**THEN** 调用原 `memory_ingest_json.py` 的 `ingest()` 函数，行为和输出保持一致

#### FR-3.3.3: SQLite 适配器插件
- **WHEN** 将 `memory_ingest_sqlite.py` 封装为 SQLite 适配器插件，**THEN** 该插件 type 为 `agent`，actions 包含 `ingest`
- **WHEN** 执行 SQLite 适配器插件的 `ingest` 动作，**THEN** 调用原 `memory_ingest_sqlite.py` 的 `ingest()` 函数，行为和输出保持一致

#### FR-3.3.4: Agent 探测插件化
- **WHEN** 将 `detect_openclaw`/`detect_hermes`/`detect_dsh`/`detect_codex` 封装为探测插件，**THEN** 每个探测插件实现 `detect()` 方法返回 `{name, type, found, sources, notes}`
- **WHEN** 注册中心执行 Agent 探测，**THEN** 遍历所有 agent 类型插件的 `detect()` 方法，聚合结果返回
- **WHEN** 新增 Agent 类型，**THEN** 只需创建新的探测插件文件放入 plugins 目录，无需修改注册中心代码

#### FR-3.3.5: Agent 注册表兼容
- **WHEN** 现有 `kb-agent.json` 注册表存在，**THEN** 插件化后的系统继续读取该文件作为 Agent 启用/禁用配置
- **WHEN** 通过插件系统添加/启用/禁用 Agent，**THEN** 写入 `kb-agent.json`，格式与现有 `{"version": 1, "agents": {...}}` 结构一致

### 3.4 KB 功能模块插件化

#### FR-3.4.1: RAG 检索插件
- **WHEN** 将 `rag.py` 封装为 KB 功能插件，**THEN** 该插件 type 为 `kb_module`，actions 包含 `index`/`query`
- **WHEN** 执行 RAG 插件的 `index` 动作，**THEN** 调用原 `rag.py` 的索引构建功能，行为和输出保持一致
- **WHEN** 执行 RAG 插件的 `query` 动作，**THEN** 调用原 `rag.py` 的查询功能，行为和输出保持一致

#### FR-3.4.2: 摄入 triage 插件
- **WHEN** 将 `intake_triage.py` 封装为插件，**THEN** actions 包含 `review`/`apply`
- **WHEN** 执行该插件的 `review` 动作，**THEN** 行为与原 `intake_triage.py review` 一致

#### FR-3.4.3: 反馈循环插件
- **WHEN** 将 `feedback_loop.py` 封装为插件，**THEN** actions 包含 `ingest`/`hit`/`apply`
- **WHEN** 执行该插件的 `ingest` 动作，**THEN** 行为与原 `feedback_loop.py ingest` 一致

#### FR-3.4.4: 补链引擎插件
- **WHEN** 将 `link_engine.py` 封装为插件，**THEN** actions 包含 `suggestions`/`apply`
- **WHEN** 执行该插件的 `apply` 动作，**THEN** 行为与原 `link_engine.py apply` 一致

#### FR-3.4.5: 回忆排期插件
- **WHEN** 将 `recall_schedule.py` 封装为插件，**THEN** actions 包含 `deck`/`mark`/`status`
- **WHEN** 执行该插件的 `deck` 动作，**THEN** 行为与原 `recall_schedule.py deck` 一致

#### FR-3.4.6: 健康检查插件
- **WHEN** 将 `kb-healthcheck.py` 封装为插件，**THEN** actions 包含 `check`
- **WHEN** 执行该插件的 `check` 动作，**THEN** 行为与原 `kb-healthcheck.py` 一致

#### FR-3.4.7: 仪表盘插件
- **WHEN** 将 `dashboard.py` 封装为插件，**THEN** actions 包含 `generate`
- **WHEN** 执行该插件的 `generate` 动作，**THEN** 行为与原 `dashboard.py` 一致

#### FR-3.4.8: 清洗插件
- **WHEN** 将 `clean.py` 封装为插件，**THEN** actions 包含 `dry-run`/`apply`/`report`/`chunk`
- **WHEN** 执行该插件的 `apply` 动作，**THEN** 行为与原 `clean.py apply` 一致

#### FR-3.4.9: 校验插件
- **WHEN** 将 `validate.py` 封装为插件，**THEN** actions 包含 `validate`
- **WHEN** 执行该插件的 `validate` 动作，**THEN** 行为与原 `validate.py` 一致

#### FR-3.4.10: 同步插件
- **WHEN** 将 `sync.py` 封装为插件，**THEN** actions 包含 `dry-run`/`apply`/`rollback`/`history`/`ingest-any`
- **WHEN** 执行该插件的 `apply` 动作，**THEN** 行为与原 `sync.py apply` 一致

#### FR-3.4.11: 记忆同步插件
- **WHEN** 将 `memory_sync.py` 封装为插件，**THEN** actions 包含 `review`/`promote`
- **WHEN** 执行该插件的 `promote` 动作，**THEN** 行为与原 `memory_sync.py promote` 一致

#### FR-3.4.12: 其余功能模块插件化
- **WHEN** 将 `classify.py`/`graph.py`/`rerank.py`/`semantic_chunk.py`/`ingest_chat.py`/`ingest_convert.py`/`user_manager.py`/`kb_health.py` 封装为插件，**THEN** 每个插件的 actions 对应原模块的子命令
- **WHEN** 执行这些插件的任意动作，**THEN** 行为与原模块对应子命令一致

### 3.5 CLI 启动器适配

#### FR-3.5.1: CLI 动态路由
- **WHEN** 用户执行 `kb <plugin_name> <action> [params]`，**THEN** CLI 启动器通过插件注册中心查找插件并执行对应动作
- **WHEN** 用户执行 `kb <plugin_name>` 不带 action，**THEN** 显示该插件支持的 actions 列表
- **WHEN** 用户执行 `kb list`，**THEN** 列出所有已注册插件及其类型和状态

#### FR-3.5.2: CLI 向后兼容
- **WHEN** 用户执行现有 CLI 命令格式（如 `kb query "问题"`、`kb ingest agent`、`kb feedback` 等），**THEN** 通过兼容映射层路由到对应插件，行为和输出与改造前完全一致
- **WHEN** 用户执行 `kb help`，**THEN** 显示所有可用命令及其对应插件的说明

#### FR-3.5.3: Agent 命令兼容
- **WHEN** 用户执行 `kb agent detect`/`kb agent list`/`kb agent add`/`kb agent enable`/`kb agent ingest`，**THEN** 通过插件注册中心路由到对应 Agent 插件，行为与改造前一致
- **WHEN** 用户执行 `kb ingest agent`，**THEN** 等价于 `kb agent ingest`，通过兼容映射层路由

### 3.6 插件配置管理

#### FR-3.6.1: 插件配置加载
- **WHEN** 插件初始化，**THEN** 从 `reference/plugin-config.json` 加载该插件的配置（如存在）
- **WHEN** 插件配置文件不存在，**THEN** 使用插件 `metadata()` 中声明的默认配置

#### FR-3.6.2: 插件启用/禁用
- **WHEN** 在插件配置中设置某插件 `enabled: false`，**THEN** 注册中心跳过该插件的加载
- **WHEN** 插件被运行时禁用，**THEN** 该插件不再响应执行请求，但已加载的实例保留

---

## 4. 非功能需求

### 4.1 向后兼容性
- **WHEN** 插件化改造完成，**THEN** 现有 `kb-agent.json` 注册表格式保持不变
- **WHEN** 插件化改造完成，**THEN** 现有所有 CLI 命令的用法和输出格式保持不变
- **WHEN** 插件化改造完成，**THEN** `growth_cron.sh` 定时节拍流程保持不变，各步骤行为一致
- **WHEN** 插件化改造完成，**THEN** 现有 `state_manager.py` 的 StateStore 接口保持不变
- **WHEN** 插件化改造完成，**THEN** 现有 `kb_common.py` 的公共函数接口保持不变

### 4.2 零依赖约束
- **WHEN** 实现插件架构，**THEN** 不引入任何 pip 依赖，仅使用 Python 标准库
- **WHEN** 插件接口定义，**THEN** 使用 Python 标准库的 `abc`（抽象基类）或 `typing.Protocol`（协议）

### 4.3 性能
- **WHEN** 插件注册中心启动，**THEN** 插件发现和加载总时间 < 2 秒（25 个插件 + 4 个 Agent 适配器）
- **WHEN** 通过插件注册中心执行命令，**THEN** 相比直接调用原脚本的额外开销 < 50ms

### 4.4 可维护性
- **WHEN** 新增 Agent 类型，**THEN** 只需在 `pipeline/plugins/` 目录下创建新插件文件，无需修改注册中心或 CLI 启动器
- **WHEN** 新增 KB 功能模块，**THEN** 只需在 `pipeline/plugins/` 目录下创建新插件文件，无需修改注册中心或 CLI 启动器
- **WHEN** 插件接口变更，**THEN** 所有已注册插件需要适配新接口

### 4.5 可测试性
- **WHEN** 测试插件注册中心，**THEN** 可使用临时目录创建模拟插件进行隔离测试
- **WHEN** 测试单个插件，**THEN** 可独立实例化插件并传入 mock context 进行测试

### 4.6 错误隔离
- **WHEN** 某个插件加载失败，**THEN** 不影响其他插件的正常加载和使用
- **WHEN** 某个插件执行出错，**THEN** 返回错误信息但不崩溃注册中心，不影响后续命令执行
- **WHEN** 插件依赖的其他插件不可用，**THEN** 该插件标记为 disabled 并在查询时提示依赖缺失

---

## 5. 约束条件

### 5.1 技术约束
- Python 3.8+ 兼容（不使用 3.9+ 独有语法特性）
- 零 pip 依赖（仅使用 Python 标准库）
- 插件接口使用 `abc.ABC` + `abstractmethod` 或鸭子类型（不强制使用 `typing.Protocol` 以兼容 3.8）

### 5.2 架构约束
- 插件文件统一放置在 `template-vault/pipeline/plugins/` 目录下
- 插件注册中心位于 `template-vault/pipeline/plugin_registry.py`
- 原 `agent_registry.py` 保留为兼容层，内部委托给插件注册中心
- 原 25 个 pipeline 模块保留原位，插件封装层通过 import 调用原模块函数

### 5.3 兼容约束
- `kb-agent.json` 文件格式不变
- CLI 命令格式不变（`kb <command> [subcommand] [params]`）
- `growth_cron.sh` 调用的脚本路径和参数不变
- `state_manager.py` 的 `StateStore` 接口不变
- `kb_common.py` 的公共函数接口不变

---

## 6. 验收标准

### 6.1 插件接口完整性
- **WHEN** 验收测试，**THEN** 插件基协议定义了 `metadata`/`initialize`/`execute`/`shutdown` 四个标准方法
- **WHEN** 验收测试，**THEN** 插件上下文对象包含 `vault_root`/`state_store`/`config`/`logger` 四个标准字段

### 6.2 插件注册中心功能
- **WHEN** 启动注册中心，**THEN** 成功发现并加载所有插件（4 个 Agent 适配器 + 25 个 KB 功能模块）
- **WHEN** 查询插件列表，**THEN** 返回所有插件的名称、类型、状态信息
- **WHEN** 请求执行不存在的插件，**THEN** 返回错误信息及可用插件列表

### 6.3 Agent 适配器兼容
- **WHEN** 通过插件系统执行 `kb agent detect`，**THEN** 输出与改造前 `python3 agent_registry.py detect` 一致
- **WHEN** 通过插件系统执行 `kb agent ingest`，**THEN** 行为与改造前 `python3 agent_registry.py ingest` 一致
- **WHEN** 通过插件系统执行 `kb agent list`/`add`/`enable`，**THEN** 行为与改造前一致

### 6.4 KB 功能模块兼容
- **WHEN** 通过插件系统执行 `kb query "问题"`，**THEN** 输出与改造前 `kb query "问题"` 一致
- **WHEN** 通过插件系统执行 `kb ingest`/`kb feedback`/`kb link`/`kb recall`/`kb healthcheck`/`kb dashboard`/`kb clean`/`kb validate`/`kb sync`，**THEN** 行为和输出与改造前一致

### 6.5 CLI 向后兼容
- **WHEN** 执行现有任何 CLI 命令，**THEN** 用法、参数、输出格式与改造前完全一致
- **WHEN** 执行 `kb help`，**THEN** 显示所有可用命令

### 6.6 成长引擎兼容
- **WHEN** 执行 `growth_cron.sh`，**THEN** 8 步引擎流程和各步行为与改造前一致

### 6.7 扩展性验证
- **WHEN** 在 `pipeline/plugins/` 目录下创建新的 Agent 探测插件，**THEN** 无需修改注册中心或 CLI 启动器即可被系统发现和使用
- **WHEN** 在 `pipeline/plugins/` 目录下创建新的 KB 功能插件，**THEN** 无需修改注册中心或 CLI 启动器即可被系统发现和使用

### 6.8 错误隔离验证
- **WHEN** 某个插件文件有语法错误，**THEN** 注册中心跳过该插件并记录错误，其他插件正常加载
- **WHEN** 某个插件执行时抛出异常，**THEN** 返回错误信息但不影响注册中心和其他插件
