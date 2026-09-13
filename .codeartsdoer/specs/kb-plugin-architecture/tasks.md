# KB 插件化架构编码任务规划

## 任务依赖关系图

```
Task 1 (插件基协议) ──→ Task 2 (注册中心) ──→ Task 3 (CLI 启动器)
                                              ↗
Task 4 (Agent 插件) ──→ Task 5 (KB 插件) ──→ Task 6 (CLI 兼容层)
                                              ↘
Task 7 (集成验证) ←── Task 6
```

---

## Task 1: 创建插件基协议和上下文对象

**描述**：创建 `pipeline/plugin_base.py`，定义所有插件必须遵循的统一接口，包括插件基类、插件元数据值对象、插件上下文依赖注入容器。

**输入**：design.md 第 3.1 节（插件基协议设计）

**输出**：`template-vault/pipeline/plugin_base.py` 文件

**验收标准**：
- `PluginBase` 类继承 `abc.ABC`，定义 `metadata()`、`initialize()`、`execute()` 三个 `@abstractmethod`，`shutdown()` 为普通方法（默认空实现）
- `PluginMetadata` 值对象包含 `name`、`version`、`type`、`actions`、`dependencies`、`description`、`cli_aliases` 七个字段，构造函数有合理默认值
- `PluginContext` 依赖注入容器包含 `vault_root`、`state_store`、`config`、`logger` 四个字段，`config` 默认空 dict，`logger` 默认 `logging.getLogger("kb.plugin")`
- 仅使用 Python 标准库（`abc`、`logging`、`typing`），无 pip 依赖
- Python 3.8+ 兼容（不使用 3.9+ 独有语法）

**子任务**：

### Task 1.1: 实现 PluginContext 依赖注入容器
创建 `PluginContext` 类，包含 `vault_root`（str）、`state_store`（可选）、`config`（dict）、`logger`（logging.Logger）四个字段。构造函数接收这些参数，`config` 和 `logger` 有默认值。此对象是插件访问共享服务的唯一入口。

### Task 1.2: 实现 PluginMetadata 值对象
创建 `PluginMetadata` 类，包含 `name`（str）、`version`（str）、`plugin_type`（str，"agent"或"kb_module"）、`actions`（List[str]）、`dependencies`（List[str]，默认空）、`description`（str，默认空）、`cli_aliases`（Dict[str, str]，默认空）七个字段。

### Task 1.3: 实现 PluginBase 抽象基类
创建 `PluginBase` 类继承 `abc.ABC`。定义三个 `@abstractmethod`：`metadata()` 返回 `PluginMetadata`、`initialize(ctx: PluginContext)` 返回 `None`、`execute(action: str, params: dict)` 返回 `Any`。定义普通方法 `shutdown()` 返回 `None`，默认空实现（子类可选覆盖）。

**代码生成提示**：
```
创建文件 template-vault/pipeline/plugin_base.py
- from abc import ABC, abstractmethod
- from typing import Any, Dict, List, Optional
- import logging
- class PluginContext: 依赖注入容器，4 个字段
- class PluginMetadata: 值对象，7 个字段，构造函数带默认值
- class PluginBase(ABC): 抽象基类，3 个 abstractmethod + 1 个普通方法
```

---

## Task 2: 创建插件注册中心

**描述**：创建 `pipeline/plugin_registry.py`，实现插件发现、注册、生命周期管理、查找分发的完整功能。这是整个插件化架构的核心调度器。

**输入**：Task 1 的 `plugin_base.py`、design.md 第 3.2 节（注册中心设计）

**输出**：`template-vault/pipeline/plugin_registry.py` 文件

**验收标准**：
- `PluginRegistry` 类包含 `discover()`、`register()`、`initialize_all()`、`shutdown_all()`、`execute()`、`get_plugin()`、`list_plugins()`、`resolve_alias()` 八个公开方法
- `discover()` 扫描 `plugins/` 目录下所有 `.py` 文件（排除 `__init__.py`、`__pycache__`），用 `importlib.import_module` 动态加载，查找 `PluginBase` 子类并实例化
- 插件加载失败（SyntaxError/ImportError）时记录日志并跳过，不中断其他插件加载
- `register()` 检查插件名冲突（记录警告，后加载覆盖）和依赖缺失（标记 disabled）
- `initialize_all()` 使用拓扑排序（Kahn 算法）按依赖顺序初始化插件，有环依赖标记相关插件为 disabled
- `execute()` 查找插件并调用其 `execute()` 方法，插件不存在返回错误码 2，动作不存在返回错误码 2，插件 disabled 返回错误码 3
- `list_plugins()` 返回所有插件元信息及状态，支持按类型过滤
- 读取 `reference/plugin-config.json` 检查插件启用/禁用状态
- 仅使用 Python 标准库（`importlib`、`json`、`logging`、`pathlib`、`os`、`sys`、`collections`）

**子任务**：

### Task 2.1: 实现插件发现机制
实现 `discover()` 和 `_load_plugin_module()` 方法。扫描 `plugins_dir` 下所有 `.py` 文件（排除 `__init__.py` 和 `__pycache__`），对每个文件用 `importlib.import_module` 动态加载，遍历模块属性查找 `PluginBase` 的非抽象子类（`issubclass` 检查且不是 `PluginBase` 本身），实例化插件类。加载失败时用 `try-except` 捕获 `Exception`，记录日志并继续下一个文件。

### Task 2.2: 实现插件注册和依赖检查
实现 `register()` 方法。接收 `PluginBase` 实例，调用其 `metadata()` 获取元信息。检查插件名是否已存在（记录警告，覆盖）。检查 `plugin-config.json` 中该插件是否 `enabled: false`（是则标记 disabled 跳过）。检查 `dependencies` 列表中每个依赖是否已注册（未注册则标记 disabled 并记录警告）。将插件存入 `_registry` 字典，键为插件名，值为 `_PluginEntry(plugin, metadata, status, error_msg)`。

### Task 2.3: 实现生命周期管理（拓扑排序初始化）
实现 `initialize_all()` 和 `shutdown_all()` 方法。`initialize_all()` 使用 Kahn 算法拓扑排序：构建依赖图（插件 → 其 dependencies），计算入度，从入度为 0 的插件开始初始化，每初始化一个减少其依赖者的入度。有环依赖（最终仍有入度 > 0 的插件）标记为 disabled。初始化时调用 `plugin.initialize(ctx)`，异常则标记 error 状态但不影响其他插件。`shutdown_all()` 按逆序调用 `plugin.shutdown()`。

### Task 2.4: 实现查找分发和查询接口
实现 `execute()`、`get_plugin()`、`list_plugins()`、`resolve_alias()` 方法。`execute(plugin_name, action, params)` 从 `_registry` 查找插件，不存在则打印错误和可用列表返回 2，插件 disabled 返回 3，动作不在 `metadata.actions` 中则打印错误和可用动作返回 2，调用 `plugin.execute(action, params)` 并捕获异常返回 1。`list_plugins(plugin_type)` 返回元信息列表，`plugin_type` 非空时过滤。`resolve_alias()` 遍历所有插件的 `cli_aliases` 查找匹配。

**代码生成提示**：
```
创建文件 template-vault/pipeline/plugin_registry.py
- import importlib, json, logging, os, sys
- from pathlib import Path
- from collections import deque
- from plugin_base import PluginBase, PluginContext, PluginMetadata
- class _PluginEntry: 内部条目 (plugin, metadata, status, error_msg)
- class PluginRegistry: 8 个公开方法
  - discover(): 扫描目录 + importlib 动态加载 + 查找 PluginBase 子类
  - register(): 依赖检查 + 冲突检测 + config 启用/禁用
  - initialize_all(): Kahn 拓扑排序 + 逐个 initialize
  - execute(): 查找 + 动作校验 + 异常捕获
```

---

## Task 3: 创建 CLI 统一启动器

**描述**：创建 `pipeline/kb_launcher.py`，作为 CLI 的统一入口，包含兼容映射表和动态路由逻辑，替代原 `kb` bash case 语句和 `kb.cmd` if-elif 链。

**输入**：Task 1 和 Task 2 的产出、design.md 第 3.3 和 5.3 节（CLI 启动器和兼容映射表设计）

**输出**：`template-vault/pipeline/kb_launcher.py` 文件

**验收标准**：
- `KBLauncher` 类包含 `COMPAT_MAP` 兼容映射表和 `run()` 主入口方法
- `COMPAT_MAP` 覆盖所有原 CLI 命令（query/rag/ingest/agent/feedback/link/recall/healthcheck/dashboard/clean/validate/sync/graph/user 等）
- `run()` 方法解析命令行参数，查 `COMPAT_MAP` 得到 `(plugin_name, action)`，通过 `PluginRegistry.execute()` 分发
- 特殊命令 `kb list` 列出所有插件，`kb help` 显示帮助
- 未知命令先尝试作为插件名直接查找，找不到则报错列出可用命令
- `kb ingest agent` 特殊处理：映射到 `(agent_registry, ingest)`
- 包含 `if __name__ == "__main__"` 入口，解析 `sys.argv` 并调用 `run()`
- 自动注入 `--root` 参数（从环境变量 `KB_ROOT` 或脚本路径推断）

**子任务**：

### Task 3.1: 实现兼容映射表
在 `KBLauncher` 类中定义 `COMPAT_MAP` 字典，键为 `(cli_tool, cli_subcommand)` 元组，值为 `(plugin_name, action)` 元组。覆盖 design.md 第 5.3 节列出的所有映射条目。`cli_subcommand` 为 `None` 表示无子命令时的默认映射。

### Task 3.2: 实现命令解析和路由
实现 `run(args)` 方法。从 `args` 中提取 `tool` 和剩余参数。查 `COMPAT_MAP[(tool, subcommand)]` 或 `COMPAT_MAP[(tool, None)]` 得到 `(plugin_name, action)`。如果 `action` 为 `None`，则从 `args` 第一个参数推断。构造 `params` dict 从剩余命令行参数。调用 `PluginRegistry.execute(plugin_name, action, params)`。处理特殊命令 `list`（列出插件）和 `help`（显示帮助）。未知命令尝试作为插件名直接查找。

### Task 3.3: 实现参数解析和 root 注入
实现参数解析逻辑：从 `sys.argv` 提取 `--root` 参数，如未提供则从 `KB_ROOT` 环境变量或 `kb_launcher.py` 文件路径推断（`Path(__file__).resolve().parents[1]`）。将剩余 `--key value` 参数解析为 `params` dict。处理 `--dry-run`、`--json` 等布尔标志参数。

**代码生成提示**：
```
创建文件 template-vault/pipeline/kb_launcher.py
- import sys, os, argparse
- from pathlib import Path
- from plugin_registry import PluginRegistry
- from plugin_base import PluginContext
- class KBLauncher:
  - COMPAT_MAP = { (tool, sub): (plugin, action), ... }  # 完整映射表
  - run(args): 解析 → 查映射 → 构造 params → registry.execute()
- if __name__ == "__main__": 解析 sys.argv + 注入 --root + KBLauncher.run()
```

---

## Task 4: 创建 Agent 适配器插件

**描述**：创建 4 个 Agent 相关的插件文件，将现有的 Agent 探测、MD/JSON/SQLite 三种适配器封装为标准 `PluginBase` 子类。

**输入**：Task 1 的 `plugin_base.py`、现有 `agent_registry.py`/`memory_ingest.py`/`memory_ingest_json.py`/`memory_ingest_sqlite.py` 的函数签名

**输出**：
- `template-vault/pipeline/plugins/__init__.py`（空文件）
- `template-vault/pipeline/plugins/agent_md_plugin.py`
- `template-vault/pipeline/plugins/agent_json_plugin.py`
- `template-vault/pipeline/plugins/agent_sqlite_plugin.py`
- `template-vault/pipeline/plugins/agent_detect_plugin.py`

**验收标准**：
- 每个插件文件定义一个 `PluginBase` 子类
- `AgentMDPlugin`：type="agent"，actions=["mirror", "sync", "mirror-core", "extract"]，`execute()` 延迟 import `memory_ingest` 并调用对应函数
- `AgentJSONPlugin`：type="agent"，actions=["ingest"]，`execute()` 延迟 import `memory_ingest_json` 并调用 `ingest()`
- `AgentSQLitePlugin`：type="agent"，actions=["ingest"]，`execute()` 延迟 import `memory_ingest_sqlite` 并调用 `ingest()`
- `AgentDetectPlugin`：type="agent"，actions=["detect", "list", "add", "enable", "setup", "ingest"]，`execute()` 延迟 import `agent_registry` 并委托调用对应 `cmd_*` 函数
- 每个插件的 `initialize()` 仅存储 `ctx` 引用
- 每个插件的 `metadata()` 返回正确的 `PluginMetadata` 对象

**子任务**：

### Task 4.1: 创建 plugins 包和 MD 适配器插件
创建 `plugins/__init__.py` 空文件。创建 `agent_md_plugin.py`，定义 `AgentMDPlugin(PluginBase)` 类。`metadata()` 返回 `PluginMetadata(name="agent_md", version="1.0", plugin_type="agent", actions=["mirror", "sync", "mirror-core", "extract"], description="MD 记忆源适配器（OpenClaw/Hermes）")`。`initialize(ctx)` 存储 `self._ctx = ctx`。`execute(action, params)` 中 `import memory_ingest as mi`，按 action 分发到 `mi.mirror()`/`mi.sync()`/`mi.mirror_core()`/`mi.extract()`，从 `params` 提取参数（root/agent_src/agent_root/agent_name/density/min_chars/semantic），root 缺省用 `self._ctx.vault_root`。

### Task 4.2: 创建 JSON 和 SQLite 适配器插件
创建 `agent_json_plugin.py`，定义 `AgentJSONPlugin(PluginBase)` 类。`metadata()` 返回 `PluginMetadata(name="agent_json", version="1.0", plugin_type="agent", actions=["ingest"], description="JSON 记忆源适配器（DSH）")`。`execute()` 中 `import memory_ingest_json as mij`，调用 `mij.ingest(params["root"], params["json_path"], params.get("dry_run", False))`。同理创建 `agent_sqlite_plugin.py`，调用 `memory_ingest_sqlite.ingest()`。

### Task 4.3: 创建 Agent 探测与注册表插件
创建 `agent_detect_plugin.py`，定义 `AgentDetectPlugin(PluginBase)` 类。`metadata()` 返回 `PluginMetadata(name="agent_registry", version="1.0", plugin_type="agent", actions=["detect", "list", "add", "enable", "setup", "ingest"], cli_aliases={"agent": None}, description="Agent 探测与注册表管理")`。`execute()` 中 `import agent_registry as ar`，按 action 委托调用 `ar.cmd_detect()`/`ar.cmd_list()`/`ar.cmd_add()`/`ar.cmd_enable()`/`ar.cmd_setup()`/`ar.cmd_ingest()`，传入 `out=[]` 列表收集输出，最后 `print("\n".join(out))`。

**代码生成提示**：
```
创建 5 个文件:
- plugins/__init__.py: 空文件
- plugins/agent_md_plugin.py: AgentMDPlugin(PluginBase), 4 个 action, import memory_ingest
- plugins/agent_json_plugin.py: AgentJSONPlugin(PluginBase), 1 个 action, import memory_ingest_json
- plugins/agent_sqlite_plugin.py: AgentSQLitePlugin(PluginBase), 1 个 action, import memory_ingest_sqlite
- plugins/agent_detect_plugin.py: AgentDetectPlugin(PluginBase), 6 个 action, import agent_registry
每个插件: metadata() + initialize(ctx) + execute(action, params) + 延迟 import 原模块
```

---

## Task 5: 创建 KB 功能模块插件

**描述**：创建 19 个 KB 功能模块的插件文件，将 `rag.py`、`intake_triage.py`、`feedback_loop.py` 等 19 个 pipeline 模块封装为标准 `PluginBase` 子类。

**输入**：Task 1 的 `plugin_base.py`、design.md 第 4.2.2 节（插件清单）、各原模块的函数签名

**输出**：`template-vault/pipeline/plugins/` 下 19 个插件文件

**验收标准**：
- 每个插件文件定义一个 `PluginBase` 子类，遵循统一模式：`metadata()` 返回元信息、`initialize()` 存储 ctx、`execute()` 延迟 import 原模块并调用对应函数
- 每个插件的 actions 与 design.md 第 4.2.2 节插件清单一致
- 每个插件的 `execute()` 从 `params` 提取参数并传给原模块函数，root 缺省用 `self._ctx.vault_root`
- 插件名和 CLI 别名与 design.md 第 4.2.2 节一致

**子任务**：

### Task 5.1: 创建核心检索和摄入插件（6 个）
创建以下插件文件，每个封装对应原模块：
- `rag_plugin.py`：`RAGPlugin`，actions=["index", "query"]，import `rag`，调用 `rag.cmd_index()`/`rag.cmd_query()`
- `intake_plugin.py`：`IntakePlugin`，actions=["review", "apply"]，import `intake_triage`，调用 `intake_triage.triage()`/`intake_triage.apply_moves()`
- `feedback_plugin.py`：`FeedbackPlugin`，actions=["ingest", "hit", "apply"]，import `feedback_loop`，调用 `feedback_loop.ingest()`/`feedback_loop.record_hit()`/`feedback_loop.apply_bumps()`
- `link_plugin.py`：`LinkPlugin`，actions=["suggestions", "apply"]，import `link_engine`，调用 `link_engine.suggestions()`/`link_engine.apply_links()`
- `recall_plugin.py`：`RecallPlugin`，actions=["deck", "mark", "status"]，import `recall_schedule`，调用对应函数
- `memory_sync_plugin.py`：`MemorySyncPlugin`，actions=["review", "promote"]，import `memory_sync`，调用 `memory_sync.do_review()`/`memory_sync.do_promote()`

### Task 5.2: 创建健康和治理插件（4 个）
创建以下插件文件：
- `healthcheck_plugin.py`：`HealthcheckPlugin`，actions=["check"]，import `kb-healthcheck`（注意模块名含连字符，用 `importlib.import_module` 加载）
- `health_metrics_plugin.py`：`HealthMetricsPlugin`，actions=["metrics"]，import `kb_health`
- `dashboard_plugin.py`：`DashboardPlugin`，actions=["generate"]，import `dashboard`
- `validate_plugin.py`：`ValidatePlugin`，actions=["validate"]，import `validate`

### Task 5.3: 创建清洗和同步插件（2 个）
创建以下插件文件：
- `clean_plugin.py`：`CleanPlugin`，actions=["dry-run", "apply", "report", "chunk"]，import `clean`，调用 `clean.main()` 或对应子命令函数
- `sync_plugin.py`：`SyncPlugin`，actions=["dry-run", "apply", "rollback", "history", "ingest-any"]，import `sync`，调用对应子命令函数

### Task 5.4: 创建辅助功能插件（7 个）
创建以下插件文件，每个封装对应原模块的子命令：
- `classify_plugin.py`：`ClassifyPlugin`，actions=["classify"]，import `classify`
- `graph_plugin.py`：`GraphPlugin`，actions=["build"]，import `graph`
- `rerank_plugin.py`：`RerankPlugin`，actions=["rerank"]，import `rerank`
- `semantic_chunk_plugin.py`：`SemanticChunkPlugin`，actions=["chunk"]，import `semantic_chunk`
- `ingest_chat_plugin.py`：`IngestChatPlugin`，actions=["parse"]，import `ingest_chat`
- `ingest_convert_plugin.py`：`IngestConvertPlugin`，actions=["convert"]，import `ingest_convert`
- `user_manager_plugin.py`：`UserManagerPlugin`，actions=["add", "list", "remove", "check-perm"]，import `user_manager`

**代码生成提示**：
```
创建 19 个插件文件，统一模式:
- from plugin_base import PluginBase, PluginMetadata, PluginContext
- class XxxPlugin(PluginBase):
  - def metadata(self): return PluginMetadata(name="xxx", version="1.0", plugin_type="kb_module", actions=[...], ...)
  - def initialize(self, ctx): self._ctx = ctx
  - def execute(self, action, params): import xxx_original; 按 action 分发调用原函数
注意: kb-healthcheck.py 模块名含连字符，需用 importlib.import_module("kb-healthcheck") 加载
```

---

## Task 6: 创建 CLI 兼容层和插件配置

**描述**：修改 `kb`（bash）和 `kb.cmd`（Windows）启动器改为统一调用 `kb_launcher.py`，创建插件配置文件 `reference/plugin-config.json`。

**输入**：Task 3 的 `kb_launcher.py`、现有 `kb` 和 `kb.cmd` 文件

**输出**：
- 修改 `template-vault/kb`
- 修改 `template-vault/kb.cmd`
- 新建 `template-vault/reference/plugin-config.json`

**验收标准**：
- `kb`（bash）保留 Python 探测逻辑和 `backup` 特殊处理，其余命令统一 `exec python3 pipeline/kb_launcher.py "$@" --root "$VAULT"`
- `kb.cmd`（Windows）保留 Python 探测逻辑，统一调用 `kb_launcher.py`
- `plugin-config.json` 包含所有 23 个插件的启用/禁用配置，默认全部 `enabled: true`
- `growth_cron.sh` 不修改（直接调用 .py，不经过 kb）

**子任务**：

### Task 6.1: 修改 kb bash 启动器
修改 `template-vault/kb` 文件。保留 shebang 和 `set -euo pipefail`。保留 `SELF_DIR`/`VAULT`/`PY` 探测逻辑。保留 `backup` 特殊处理（`exec bash "$SELF_DIR/scripts/backup_now.sh"`）。删除所有 `case "$TOOL" in ... esac` 硬编码块。替换为 `exec "$PY" "$VAULT/pipeline/kb_launcher.py" "$@" --root "$VAULT"`。

### Task 6.2: 修改 kb.cmd Windows 启动器
修改 `template-vault/kb.cmd` 文件。保留 `@echo off`/`setlocal`/`VAULT`/`PY` 探测逻辑。删除所有 `if /i "%TOOL%"=="xxx"` 硬编码块。替换为 `"%PY%" "%VAULT%\pipeline\kb_launcher.py" %* --root "%VAULT%"`。保留 `:nopys` 和 `:help` 标签。

### Task 6.3: 创建插件配置文件
创建 `template-vault/reference/plugin-config.json` 文件。包含 `_meta` 元信息字段（`{"version": 1, "description": "插件启用/禁用配置"}`）。为所有 23 个插件添加配置条目，格式为 `"plugin_name": {"enabled": true}`。插件名列表：rag, intake, feedback, link, recall, healthcheck, health_metrics, dashboard, clean, validate, sync, memory_sync, classify, graph, rerank, semantic_chunk, ingest_chat, ingest_convert, user_manager, agent_md, agent_json, agent_sqlite, agent_registry。

**代码生成提示**：
```
修改 template-vault/kb: 删除 case 块，替换为 exec kb_launcher.py
修改 template-vault/kb.cmd: 删除 if-elif 块，替换为调用 kb_launcher.py
创建 template-vault/reference/plugin-config.json: 23 个插件 enabled: true
```

---

## Task 7: 集成验证和向后兼容测试

**描述**：验证插件化改造后的系统功能完整性和向后兼容性，确保所有现有 CLI 命令行为不变，插件注册中心正确加载所有插件。

**输入**：Task 1-6 的全部产出

**输出**：验证结果（无新文件产出，或产出验证脚本）

**验收标准**：
- 执行 `python3 pipeline/plugin_registry.py` 能发现并加载所有 23 个插件，无加载错误
- 执行 `kb list` 列出所有插件及其类型和状态
- 执行 `kb help` 显示所有可用命令
- 执行 `kb query "测试"` 行为与改造前一致
- 执行 `kb agent detect` 行为与改造前一致
- 执行 `kb ingest` 行为与改造前一致
- 执行 `kb feedback`/`kb link`/`kb recall`/`kb healthcheck`/`kb dashboard`/`kb validate` 行为与改造前一致
- `growth_cron.sh` 各步骤正常执行
- 在 `plugins/` 目录下创建一个测试用新插件，能被系统自动发现和加载

**子任务**：

### Task 7.1: 验证插件注册中心加载
执行 `python3 pipeline/plugin_registry.py`（或编写验证脚本），确认：注册中心成功发现 `plugins/` 目录下所有 23 个 `.py` 文件，每个文件中找到 `PluginBase` 子类并实例化，所有插件 status 为 "loaded"，`list_plugins()` 返回 23 条记录。检查加载时间 < 2 秒。

### Task 7.2: 验证 CLI 向后兼容
逐一执行现有 CLI 命令，确认行为和输出与改造前一致：`kb query "测试"`、`kb rag index`、`kb ingest`、`kb ingest agent`、`kb agent detect`、`kb agent list`、`kb feedback`、`kb link`、`kb recall`、`kb healthcheck`、`kb dashboard`、`kb validate`、`kb help`。对比改造前后的输出，确认无差异。

### Task 7.3: 验证 growth_cron.sh 兼容
执行 `bash scripts/growth_cron.sh $VAULT`，确认 8 步引擎流程正常执行，各步骤行为与改造前一致。由于 cron 直接调用 `.py` 不经过 `kb`，原模块保持原位不变，此验证应无问题。

### Task 7.4: 验证扩展性
在 `plugins/` 目录下创建一个测试用新插件文件（如 `test_plugin.py`，定义 `TestPlugin(PluginBase)`，actions=["hello"]），执行 `kb test hello`，确认新插件被自动发现和执行，无需修改注册中心或 CLI 启动器。验证完成后删除测试插件。

### Task 7.5: 验证错误隔离
创建一个有语法错误的插件文件（如 `broken_plugin.py`，内容为 `def`），执行 `kb list`，确认注册中心跳过该插件并记录错误，其他 23 个插件正常加载。验证完成后删除错误插件。

**代码生成提示**：
```
验证步骤:
1. python3 -c "from plugin_registry import PluginRegistry; r = PluginRegistry(...); r.discover(); print(r.list_plugins())"
2. kb list / kb help / kb query "测试" / kb agent detect / kb ingest / kb feedback / ...
3. bash scripts/growth_cron.sh $VAULT
4. 创建 plugins/test_plugin.py → kb test hello → 删除
5. 创建 plugins/broken_plugin.py (语法错误) → kb list → 确认其他正常 → 删除
```
