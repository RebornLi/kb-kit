# KB 插件化架构技术设计文档

## 1. 设计概述

### 1.1 设计目标
将 KB 从"宿主系统"改造为"插件式"架构，与 Agent 插件统一为同一套插件接口和注册中心，同时保持 100% 向后兼容。

### 1.2 设计原则
- **开闭原则**：新增插件不改核心代码（注册中心、CLI 启动器）
- **适配器模式**：插件封装层通过 import 调用原模块函数，不重写业务逻辑
- **依赖注入**：插件通过 context 对象访问共享服务，不直接 import 其他模块
- **零依赖**：仅使用 Python 标准库（abc, importlib, json, logging, pathlib, os, sys）
- **渐进式改造**：原模块保留原位，插件层是薄封装，可随时回退

### 1.3 技术选型

| 关注点 | 选型 | 理由 |
|--------|------|------|
| 插件接口 | `abc.ABC` + `@abstractmethod` | Python 3.8+ 标准库，强制子类实现关键方法 |
| 插件发现 | `importlib.import_module` + 目录扫描 | 标准库，无需第三方依赖注入框架 |
| 插件元数据 | 插件类 `metadata()` 静态方法返回 dict | 无需单独的 manifest 文件，自包含 |
| 插件配置 | `reference/plugin-config.json` | 与现有 `reference/` 配置目录一致 |
| 日志 | `logging` 标准库 | 零依赖，支持分级输出 |
| 状态管理 | 复用现有 `state_manager.StateStore` | 不重新发明，通过 context 注入 |
| CLI 路由 | Python 统一分发（`kb_launcher.py`） | 替代 bash case + cmd if-elif，统一逻辑 |

---

## 2. 架构设计

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户层 (CLI)                              │
│                                                                   │
│   kb (bash)          kb.cmd (Windows)                            │
│       │                   │                                      │
│       └───────┬───────────┘                                      │
│               ▼                                                   │
│    ┌──────────────────────┐                                      │
│    │   kb_launcher.py     │  ← 统一入口（替代 bash case 硬编码）  │
│    │  (兼容映射 + 路由)    │                                      │
│    └──────────┬───────────┘                                      │
│               ▼                                                   │
│    ┌──────────────────────┐                                      │
│    │  plugin_registry.py  │  ← 插件注册中心（核心）               │
│    │  (发现/注册/分发/     │                                      │
│    │   生命周期管理)       │                                      │
│    └──────────┬───────────┘                                      │
│               ▼                                                   │
│    ┌──────────────────────────────────────────┐                  │
│    │        pipeline/plugins/                 │  ← 插件目录       │
│    │                                          │                  │
│    │  ┌─── agent plugins ───┐  ┌─ kb_module plugins ─┐         │
│    │  │ agent_md.py          │  │ rag_plugin.py        │         │
│    │  │ agent_json.py        │  │ intake_plugin.py     │         │
│    │  │ agent_sqlite.py      │  │ feedback_plugin.py   │         │
│    │  │ agent_detect.py      │  │ link_plugin.py       │         │
│    │  └─────────────────────┘  │ recall_plugin.py     │         │
│    │                           │ healthcheck_plugin.py│         │
│    │                           │ dashboard_plugin.py  │         │
│    │                           │ clean_plugin.py      │         │
│    │                           │ validate_plugin.py   │         │
│    │                           │ sync_plugin.py       │         │
│    │                           │ memory_sync_plugin.py│         │
│    │                           │ ... (共 25 个)       │         │
│    │                           └──────────────────────┘         │
│    └──────────────────────────────────────────┘                  │
│               ▼                                                   │
│    ┌──────────────────────────────────────────┐                  │
│    │     原 pipeline 模块 (保持原位不变)       │  ← 业务逻辑层   │
│    │  rag.py  intake_triage.py  feedback_loop  │                  │
│    │  link_engine.py  recall_schedule.py  ...   │                  │
│    │  memory_ingest.py  memory_ingest_json.py   │                  │
│    │  memory_ingest_sqlite.py  agent_registry.py│                  │
│    └──────────────────────────────────────────┘                  │
│               ▼                                                   │
│    ┌──────────────────────────────────────────┐                  │
│    │     共享基础设施 (保持原位不变)            │                  │
│    │  kb_common.py  state_manager.py            │                  │
│    └──────────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 分层职责

| 层 | 职责 | 改造程度 |
|----|------|----------|
| **CLI 层** | 命令解析、兼容映射、路由到注册中心 | `kb`/`kb.cmd` 改为调用 `kb_launcher.py` |
| **注册中心层** | 插件发现、注册、生命周期、分发 | **新建** `plugin_registry.py` |
| **插件接口层** | 定义基协议、上下文对象 | **新建** `plugin_base.py` |
| **插件实现层** | 封装原模块为标准插件 | **新建** `pipeline/plugins/*.py` |
| **业务逻辑层** | 各模块核心算法和流程 | **不变** |
| **共享基础设施** | 公共工具、状态管理 | **不变** |

### 2.3 调用流程

```
用户执行: kb query "怎么备份"
  │
  ├─ kb (bash) → exec python3 pipeline/kb_launcher.py query "怎么备份" --root $VAULT
  │
  ├─ kb_launcher.py
  │    ├─ 解析命令: tool="query", args=["怎么备份"]
  │    ├─ 查兼容映射表: "query" → plugin="rag", action="query"
  │    └─ 调用 PluginRegistry.execute("rag", "query", {"q": "怎么备份", "root": vault})
  │
  ├─ plugin_registry.py
  │    ├─ 查找插件 "rag" → RAGPlugin 实例
  │    └─ 调用 RAGPlugin.execute("query", params)
  │
  ├─ plugins/rag_plugin.py (RAGPlugin)
  │    └─ 调用 rag.cmd_query(root, q, ...)
  │
  └─ rag.py (原模块，不变)
       └─ 执行 TF-IDF 检索，返回结果
```

---

## 3. 核心组件设计

### 3.1 插件基协议 (`plugin_base.py`)

```python
# pipeline/plugin_base.py
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

class PluginContext:
    """插件上下文对象 — 依赖注入容器。"""

    def __init__(self, vault_root: str, state_store=None,
                 config: dict = None, logger: logging.Logger = None):
        self.vault_root: str = vault_root
        self.state_store = state_store       # StateStore 实例
        self.config: dict = config or {}     # 插件配置
        self.logger: logging.Logger = logger or logging.getLogger("kb.plugin")


class PluginMetadata:
    """插件元数据 — 不可变值对象。"""

    def __init__(self, name: str, version: str, plugin_type: str,
                 actions: List[str], dependencies: List[str] = None,
                 description: str = "", cli_aliases: Dict[str, str] = None):
        self.name: str = name
        self.version: str = version
        self.type: str = plugin_type          # "agent" | "kb_module"
        self.actions: List[str] = actions
        self.dependencies: List[str] = dependencies or []
        self.description: str = description
        self.cli_aliases: Dict[str, str] = cli_aliases or {}  # CLI 兼容映射


class PluginBase(ABC):
    """插件基类 — 所有插件必须继承。"""

    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """返回插件元信息。"""
        ...

    @abstractmethod
    def initialize(self, ctx: PluginContext) -> None:
        """初始化插件（注册中心启动时调用）。"""
        ...

    @abstractmethod
    def execute(self, action: str, params: dict) -> Any:
        """执行插件动作。返回值由具体插件定义。"""
        ...

    def shutdown(self) -> None:
        """清理资源（默认空实现，可选覆盖）。"""
        pass
```

**类型定义说明：**
- `PluginContext`：依赖注入容器，插件不直接 import 其他模块，通过 context 访问共享服务
- `PluginMetadata`：不可变值对象，包含 name/version/type/actions/dependencies/cli_aliases
- `PluginBase`：抽象基类，`metadata`/`initialize`/`execute` 为必须实现，`shutdown` 为可选

### 3.2 插件注册中心 (`plugin_registry.py`)

```python
# pipeline/plugin_registry.py
class PluginRegistry:
    """插件注册中心 — 发现、注册、分发、生命周期管理。"""

    def __init__(self, plugins_dir: str, vault_root: str):
        self._plugins_dir: Path = Path(plugins_dir)
        self._vault_root: str = vault_root
        self._registry: Dict[str, _PluginEntry] = {}  # name → entry
        self._logger = logging.getLogger("kb.registry")

    # ── 发现与加载 ──
    def discover(self) -> None:
        """扫描 plugins_dir，import 所有 *_plugin.py / agent_*.py 模块。"""
        ...

    def _load_plugin_module(self, module_path: Path) -> Optional[PluginBase]:
        """import 单个插件模块，实例化 PluginBase 子类。"""
        ...

    # ── 注册 ──
    def register(self, plugin: PluginBase) -> bool:
        """注册插件到内部注册表，检查依赖。"""
        ...

    # ── 生命周期 ──
    def initialize_all(self, ctx: PluginContext) -> None:
        """按依赖顺序初始化所有已注册插件。"""
        ...

    def shutdown_all(self) -> None:
        """按逆依赖顺序关闭所有插件。"""
        ...

    # ── 查找与分发 ──
    def execute(self, plugin_name: str, action: str, params: dict) -> Any:
        """查找插件并执行动作。"""
        ...

    def get_plugin(self, name: str) -> Optional[PluginBase]:
        """按名获取插件实例。"""
        ...

    # ── 查询 ──
    def list_plugins(self, plugin_type: str = None) -> List[PluginMetadata]:
        """列出所有插件元信息，可按类型过滤。"""
        ...

    def resolve_alias(self, cli_command: str) -> Optional[tuple]:
        """CLI 兼容：将旧命令名解析为 (plugin_name, action)。"""
        ...
```

**内部数据结构：**

```python
class _PluginEntry:
    """注册表内部条目。"""
    plugin: PluginBase          # 插件实例
    metadata: PluginMetadata    # 元信息
    status: str                 # "loaded" | "disabled" | "error"
    error_msg: str              # status=error 时的错误信息
```

**插件发现算法：**
1. 扫描 `plugins_dir` 下所有 `*.py` 文件（排除 `__init__.py`、`__pycache__`）
2. 对每个文件用 `importlib.import_module` 动态加载
3. 在模块中查找 `PluginBase` 子类（遍历 `dir(module)`，检查 `issubclass`）
4. 实例化插件类，调用 `metadata()` 获取元信息
5. 检查 `plugin-config.json` 中是否 `enabled: false`，是则跳过
6. 调用 `register()` 注册到 `_registry`

**依赖排序算法：**
- 使用拓扑排序（Kahn 算法）确定初始化顺序
- 有环依赖 → 记录错误，相关插件标记为 `disabled`
- 无依赖的插件按发现顺序初始化

### 3.3 CLI 启动器 (`kb_launcher.py`)

```python
# pipeline/kb_launcher.py
class KBLauncher:
    """CLI 统一入口 — 兼容映射 + 动态路由。"""

    # 旧 CLI 命令 → (plugin_name, action) 兼容映射表
    COMPAT_MAP: Dict[str, tuple] = {
        "query":    ("rag", "query"),
        "rag":      ("rag", None),       # action 由子参数决定
        "ingest":   ("intake", None),    # "ingest" → intake review; "ingest agent" → agent ingest
        "agent":    ("agent_registry", None),
        "feedback": ("feedback", None),
        "link":     ("link", None),
        "recall":   ("recall", None),
        "healthcheck": ("healthcheck", "check"),
        "dashboard":   ("dashboard", "generate"),
        "clean":    ("clean", None),
        "validate": ("validate", "validate"),
        "sync":     ("sync", None),
        "graph":    ("graph", None),
        "user":     ("user_manager", None),
        # ... 完整映射
    }

    def run(self, args: List[str]) -> int:
        """主入口：解析命令 → 兼容映射 → 注册中心分发。"""
        ...
```

**兼容映射策略：**
- 简单命令（`kb query "问题"`）：直接映射到 `(plugin="rag", action="query")`
- 带子命令的命令（`kb ingest agent`）：先查 COMPAT_MAP 得到 plugin，再根据子参数决定 action
- `kb ingest agent` 特殊处理：映射到 `(plugin="agent_registry", action="ingest")`
- 未知命令：先尝试作为插件名直接查找，找不到则报错列出可用命令

### 3.4 插件配置 (`reference/plugin-config.json`)

```json
{
  "_meta": { "version": 1, "description": "插件启用/禁用配置" },
  "rag": { "enabled": true },
  "intake": { "enabled": true },
  "feedback": { "enabled": true },
  "agent_md": { "enabled": true },
  "agent_json": { "enabled": true },
  "agent_sqlite": { "enabled": true }
}
```

---

## 4. 插件实现设计

### 4.1 Agent 适配器插件

#### 4.1.1 MD 适配器插件 (`plugins/agent_md.py`)

```python
class AgentMDPlugin(PluginBase):
    """MD 记忆源适配器插件 — 封装 memory_ingest.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_md",
            version="1.0",
            plugin_type="agent",
            actions=["mirror", "sync", "mirror-core", "extract"],
            description="MD 记忆源适配器（OpenClaw/Hermes）",
        )

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        import memory_ingest as mi  # 延迟 import 原模块
        root = params.get("root", self._ctx.vault_root)
        if action == "mirror":
            return mi.mirror(root, params["agent_src"], params.get("agent_name", "openclaw"))
        elif action == "sync":
            return mi.sync(root, params["agent_src"], params.get("agent_name", "openclaw"))
        elif action == "mirror-core":
            return mi.mirror_core(root, params["agent_root"], params.get("agent_name", "openclaw"))
        elif action == "extract":
            return mi.extract(root, params["agent_root"], params.get("density", 0.15),
                              params.get("min_chars", 80), params.get("semantic", False),
                              params.get("agent_name", "openclaw"))
```

#### 4.1.2 JSON 适配器插件 (`plugins/agent_json.py`)

```python
class AgentJSONPlugin(PluginBase):
    """JSON 记忆源适配器插件 — 封装 memory_ingest_json.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_json", version="1.0", plugin_type="agent",
            actions=["ingest"],
            description="JSON 记忆源适配器（DSH）",
        )

    def execute(self, action: str, params: dict) -> int:
        import memory_ingest_json as mij
        return mij.ingest(params["root"], params["json_path"], params.get("dry_run", False))
```

#### 4.1.3 SQLite 适配器插件 (`plugins/agent_sqlite.py`)

```python
class AgentSQLitePlugin(PluginBase):
    """SQLite 记忆源适配器插件 — 封装 memory_ingest_sqlite.py。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_sqlite", version="1.0", plugin_type="agent",
            actions=["ingest"],
            description="SQLite 记忆源适配器（Codex）",
        )

    def execute(self, action: str, params: dict) -> int:
        import memory_ingest_sqlite as mis
        return mis.ingest(params["root"], params["data_dir"], params.get("dry_run", False))
```

#### 4.1.4 Agent 探测与注册表插件 (`plugins/agent_detect.py`)

```python
class AgentDetectPlugin(PluginBase):
    """Agent 探测 + 注册表管理插件 — 封装 agent_registry.py 的探测和注册表操作。"""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="agent_registry", version="1.0", plugin_type="agent",
            actions=["detect", "list", "add", "enable", "setup", "ingest"],
            cli_aliases={"agent": None, "ingest-agent": "ingest"},
            description="Agent 探测与注册表管理",
        )

    def execute(self, action: str, params: dict) -> int:
        import agent_registry as ar  # 委托给原模块
        out = []
        if action == "detect":
            return ar.cmd_detect(params["root"], out)
        elif action == "list":
            return ar.cmd_list(params["root"], out)
        elif action == "ingest":
            return ar.cmd_ingest(params["root"], params.get("name"),
                                 params.get("dry_run", False), out)
        # ...
```

### 4.2 KB 功能模块插件

所有 KB 功能模块插件遵循统一模式：薄封装层，延迟 import 原模块，在 `execute()` 中调用原函数。

#### 4.2.1 RAG 插件 (`plugins/rag_plugin.py`)

```python
class RAGPlugin(PluginBase):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="rag", version="1.0", plugin_type="kb_module",
            actions=["index", "query"],
            cli_aliases={"query": "query", "rag": None},
            description="本地 TF-IDF 语义检索",
        )

    def execute(self, action: str, params: dict) -> int:
        import rag
        root = params.get("root", self._ctx.vault_root)
        if action == "index":
            return rag.cmd_index(root, params.get("incremental", True))
        elif action == "query":
            return rag.cmd_query(root, params["q"], params.get("top", 10),
                                 params.get("answer", False),
                                 params.get("as_json", False),
                                 params.get("context", 240),
                                 params.get("context_budget", None))
```

#### 4.2.2 插件清单（全部 25 个 KB 模块插件）

| 插件文件 | 插件名 | 原模块 | actions | CLI 别名 |
|----------|--------|--------|---------|----------|
| `rag_plugin.py` | `rag` | `rag.py` | `index`, `query` | `query`, `rag` |
| `intake_plugin.py` | `intake` | `intake_triage.py` | `review`, `apply` | `ingest` |
| `feedback_plugin.py` | `feedback` | `feedback_loop.py` | `ingest`, `hit`, `apply` | `feedback` |
| `link_plugin.py` | `link` | `link_engine.py` | `suggestions`, `apply` | `link` |
| `recall_plugin.py` | `recall` | `recall_schedule.py` | `deck`, `mark`, `status` | `recall` |
| `healthcheck_plugin.py` | `healthcheck` | `kb-healthcheck.py` | `check` | `healthcheck` |
| `health_metrics_plugin.py` | `health_metrics` | `kb_health.py` | `metrics` | `health-metrics` |
| `dashboard_plugin.py` | `dashboard` | `dashboard.py` | `generate` | `dashboard` |
| `clean_plugin.py` | `clean` | `clean.py` | `dry-run`, `apply`, `report`, `chunk` | `clean` |
| `validate_plugin.py` | `validate` | `validate.py` | `validate` | `validate` |
| `sync_plugin.py` | `sync` | `sync.py` | `dry-run`, `apply`, `rollback`, `history`, `ingest-any` | `sync`, `ingest-any` |
| `memory_sync_plugin.py` | `memory_sync` | `memory_sync.py` | `review`, `promote` | — |
| `classify_plugin.py` | `classify` | `classify.py` | `classify` | — |
| `graph_plugin.py` | `graph` | `graph.py` | `build` | `graph` |
| `rerank_plugin.py` | `rerank` | `rerank.py` | `rerank` | — |
| `semantic_chunk_plugin.py` | `semantic_chunk` | `semantic_chunk.py` | `chunk` | — |
| `ingest_chat_plugin.py` | `ingest_chat` | `ingest_chat.py` | `parse` | — |
| `ingest_convert_plugin.py` | `ingest_convert` | `ingest_convert.py` | `convert` | — |
| `user_manager_plugin.py` | `user_manager` | `user_manager.py` | `add`, `list`, `remove`, `check-perm` | `user` |
| `agent_md_plugin.py` | `agent_md` | `memory_ingest.py` | `mirror`, `sync`, `mirror-core`, `extract` | — |
| `agent_json_plugin.py` | `agent_json` | `memory_ingest_json.py` | `ingest` | — |
| `agent_sqlite_plugin.py` | `agent_sqlite` | `memory_ingest_sqlite.py` | `ingest` | — |
| `agent_detect_plugin.py` | `agent_registry` | `agent_registry.py` | `detect`, `list`, `add`, `enable`, `setup`, `ingest` | `agent` |

---

## 5. CLI 兼容层设计

### 5.1 `kb` (bash) 改造

原 `kb` 的 bash case 语句改为统一调用 `kb_launcher.py`：

```bash
#!/usr/bin/env bash
# kb — 知识库启动器（插件化版本）
set -euo pipefail
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="$SELF_DIR"

PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
fi
if [ -z "$PY" ]; then echo "❌ 未找到 Python" >&2; exit 3; fi

# backup 仍走 bash 脚本（不插件化）
if [ "${1:-}" = "backup" ]; then
  exec bash "$SELF_DIR/scripts/backup_now.sh" "$VAULT" "${2:-daily}"
fi

# 所有其他命令统一走 kb_launcher.py
exec "$PY" "$VAULT/pipeline/kb_launcher.py" "$@" --root "$VAULT"
```

### 5.2 `kb.cmd` (Windows) 改造

```cmd
@echo off
setlocal enableextensions
set "VAULT=%~dp0"
set "VAULT=%VAULT:~0,-1%"
where python >nul 2>&1 && set "PY=python" || (where py >nul 2>&1 && set "PY=py" || goto :nopys)
"%PY%" "%VAULT%\pipeline\kb_launcher.py" %* --root "%VAULT%"
goto :eof
```

### 5.3 兼容映射表 (`kb_launcher.py` 内)

```python
COMPAT_MAP = {
    # (cli_tool, cli_subcommand) → (plugin_name, action)
    ("query", None):      ("rag", "query"),
    ("rag", None):        ("rag", None),          # action 由 args[0] 决定
    ("rag", "index"):     ("rag", "index"),
    ("ingest", None):     ("intake", "review"),
    ("ingest", "move"):   ("intake", "apply"),
    ("ingest", "trash"):  ("intake", "apply"),    # params 标记 trash
    ("ingest", "agent"):  ("agent_registry", "ingest"),
    ("agent", None):      ("agent_registry", None),
    ("feedback", None):   ("feedback", "ingest"),
    ("feedback", "hit"):  ("feedback", "hit"),
    ("feedback", "apply"):("feedback", "apply"),
    ("link", None):       ("link", "suggestions"),
    ("link", "apply"):    ("link", "apply"),
    ("recall", None):     ("recall", "deck"),
    ("recall", "mark"):   ("recall", "mark"),
    ("recall", "status"): ("recall", "status"),
    ("recall", "deck"):   ("recall", "deck"),
    ("healthcheck", None):("healthcheck", "check"),
    ("health-metrics",None):("health_metrics", "metrics"),
    ("dashboard", None):  ("dashboard", "generate"),
    ("clean", None):      ("clean", "dry-run"),
    ("validate", None):   ("validate", "validate"),
    ("sync", None):       ("sync", None),
    ("ingest-any", None): ("sync", "ingest-any"),
    ("user", None):       ("user_manager", None),
    ("graph", None):      ("graph", None),
    ("list", None):       ("__list__", None),     # 特殊：列出所有插件
    ("help", None):       ("__help__", None),     # 特殊：显示帮助
}
```

---

## 6. 数据模型

### 6.1 插件注册表内部结构

```python
# PluginRegistry._registry 的结构
{
    "rag": _PluginEntry(
        plugin=<RAGPlugin instance>,
        metadata=PluginMetadata(name="rag", version="1.0", ...),
        status="loaded",
        error_msg=""
    ),
    "agent_md": _PluginEntry(
        plugin=<AgentMDPlugin instance>,
        metadata=PluginMetadata(name="agent_md", version="1.0", ...),
        status="loaded",
        error_msg=""
    ),
    # ...
}
```

### 6.2 插件配置文件结构

```json
{
  "_meta": { "version": 1 },
  "rag": { "enabled": true },
  "intake": { "enabled": true },
  "feedback": { "enabled": true },
  "link": { "enabled": true },
  "recall": { "enabled": true },
  "healthcheck": { "enabled": true },
  "dashboard": { "enabled": true },
  "clean": { "enabled": true },
  "validate": { "enabled": true },
  "sync": { "enabled": true },
  "memory_sync": { "enabled": true },
  "agent_md": { "enabled": true },
  "agent_json": { "enabled": true },
  "agent_sqlite": { "enabled": true },
  "agent_registry": { "enabled": true }
}
```

### 6.3 `kb-agent.json` 结构（不变）

```json
{
  "version": 1,
  "agents": {
    "openclaw": { "type": "md", "label": "OpenClaw", "enabled": true, "sources": {...} },
    "dsh": { "type": "json", "label": "DeepSeek Harness", "enabled": true, "sources": {...} }
  }
}
```

---

## 7. 接口设计

### 7.1 PluginRegistry 公开接口

| 方法 | 签名 | 说明 |
|------|------|------|
| `discover()` | `() → None` | 扫描插件目录，加载所有插件 |
| `register(plugin)` | `(PluginBase) → bool` | 注册单个插件 |
| `initialize_all(ctx)` | `(PluginContext) → None` | 按依赖顺序初始化所有插件 |
| `shutdown_all()` | `() → None` | 按逆依赖顺序关闭所有插件 |
| `execute(name, action, params)` | `(str, str, dict) → Any` | 查找插件并执行动作 |
| `get_plugin(name)` | `(str) → Optional[PluginBase]` | 按名获取插件实例 |
| `list_plugins(type)` | `(Optional[str]) → List[PluginMetadata]` | 列出插件元信息 |
| `resolve_alias(cmd)` | `(str) → Optional[tuple]` | CLI 兼容映射解析 |

### 7.2 PluginBase 公开接口

| 方法 | 签名 | 说明 |
|------|------|------|
| `metadata()` | `() → PluginMetadata` | 返回插件元信息（抽象） |
| `initialize(ctx)` | `(PluginContext) → None` | 初始化插件（抽象） |
| `execute(action, params)` | `(str, dict) → Any` | 执行动作（抽象） |
| `shutdown()` | `() → None` | 清理资源（可选覆盖） |

### 7.3 KBLauncher 公开接口

| 方法 | 签名 | 说明 |
|------|------|------|
| `run(args)` | `(List[str]) → int` | 主入口：解析→映射→分发 |

---

## 8. 错误处理设计

### 8.1 插件加载错误

| 错误场景 | 处理策略 | 用户可见行为 |
|----------|----------|-------------|
| 插件文件 SyntaxError | 记录日志，跳过该插件 | `⚠️ 插件 xxx 加载失败: SyntaxError ...` |
| 插件类未找到 | 记录日志，跳过 | `⚠️ xxx.py 中未找到 PluginBase 子类` |
| metadata() 抛异常 | 记录日志，跳过 | `⚠️ 插件 xxx 元数据获取失败: ...` |
| 插件名冲突 | 记录警告，后加载覆盖 | `⚠️ 插件名冲突: xxx 被覆盖` |
| 依赖缺失 | 标记 disabled | `⚠️ 插件 xxx 依赖 yyy 未注册，已禁用` |

### 8.2 插件执行错误

| 错误场景 | 处理策略 | 用户可见行为 |
|----------|----------|-------------|
| 插件不存在 | 返回错误码 2 | `❓ 未知命令: xxx → 用 kb help` |
| 动作不存在 | 返回错误码 2 | `❓ 插件 xxx 不支持动作 yyy，可用: a, b, c` |
| 插件 disabled | 返回错误码 3 | `⚠️ 插件 xxx 已禁用` |
| execute() 抛异常 | 捕获，打印 traceback，返回错误码 1 | 异常信息 + `❌ 插件 xxx 执行失败` |

---

## 9. 文件结构设计

```
template-vault/pipeline/
├── plugin_base.py              # 【新建】插件基协议 + 上下文对象
├── plugin_registry.py          # 【新建】插件注册中心
├── kb_launcher.py              # 【新建】CLI 统一入口
├── plugins/                    # 【新建】插件目录
│   ├── __init__.py             # 空文件，标记为 Python 包
│   ├── agent_md_plugin.py      # 【新建】MD 适配器插件
│   ├── agent_json_plugin.py    # 【新建】JSON 适配器插件
│   ├── agent_sqlite_plugin.py  # 【新建】SQLite 适配器插件
│   ├── agent_detect_plugin.py  # 【新建】Agent 探测+注册表插件
│   ├── rag_plugin.py           # 【新建】RAG 检索插件
│   ├── intake_plugin.py        # 【新建】摄入 triage 插件
│   ├── feedback_plugin.py      # 【新建】反馈循环插件
│   ├── link_plugin.py          # 【新建】补链引擎插件
│   ├── recall_plugin.py        # 【新建】回忆排期插件
│   ├── healthcheck_plugin.py   # 【新建】健康检查插件
│   ├── health_metrics_plugin.py# 【新建】健康度量插件
│   ├── dashboard_plugin.py     # 【新建】仪表盘插件
│   ├── clean_plugin.py         # 【新建】清洗插件
│   ├── validate_plugin.py      # 【新建】校验插件
│   ├── sync_plugin.py          # 【新建】同步插件
│   ├── memory_sync_plugin.py   # 【新建】记忆同步插件
│   ├── classify_plugin.py      # 【新建】分类插件
│   ├── graph_plugin.py         # 【新建】图谱插件
│   ├── rerank_plugin.py        # 【新建】重排序插件
│   ├── semantic_chunk_plugin.py# 【新建】语义分块插件
│   ├── ingest_chat_plugin.py   # 【新建】对话摄入插件
│   ├── ingest_convert_plugin.py# 【新建】格式转换插件
│   └── user_manager_plugin.py  # 【新建】用户管理插件
├── agent_registry.py           # 【不变】原模块（被 agent_detect_plugin 委托调用）
├── memory_ingest.py            # 【不变】原模块
├── memory_ingest_json.py       # 【不变】原模块
├── memory_ingest_sqlite.py     # 【不变】原模块
├── memory_sync.py              # 【不变】原模块
├── rag.py                      # 【不变】原模块
├── intake_triage.py            # 【不变】原模块
├── ... (其余 20 个原模块不变)
├── kb_common.py                # 【不变】公共工具
└── state_manager.py            # 【不变】状态管理

template-vault/
├── kb                          # 【修改】改为调用 kb_launcher.py
├── kb.cmd                      # 【修改】改为调用 kb_launcher.py
└── reference/
    └── plugin-config.json      # 【新建】插件启用/禁用配置
```

---

## 10. 向后兼容设计

### 10.1 兼容策略矩阵

| 原有接口 | 兼容方式 | 风险 |
|----------|----------|------|
| `kb <command> [sub] [params]` | kb_launcher.py COMPAT_MAP 映射 | 无：映射表覆盖所有原命令 |
| `kb-agent.json` 格式 | agent_detect_plugin 直接读写原格式 | 无：不修改文件结构 |
| `growth_cron.sh` 调用路径 | 脚本不变，调用的 .py 不变 | 无：cron 直接调 .py 不经过 kb |
| `state_manager.StateStore` | 通过 PluginContext 注入 | 无：接口不变 |
| `kb_common.*` 函数 | 原模块仍直接 import | 无：不动 kb_common |
| `agent_registry.py` CLI | agent_detect_plugin 委托调用 | 无：原函数签名不变 |

### 10.2 `growth_cron.sh` 兼容

`growth_cron.sh` 直接调用 `pipeline/xxx.py`，不经过 `kb` 启动器。由于原模块保持原位不变，cron 脚本无需修改。

---

## 11. 性能设计

### 11.1 插件加载性能

- 插件发现：扫描目录 + import 25+4=29 个模块，预计 < 1 秒
- 插件初始化：大部分插件 `initialize()` 仅存储 context 引用，预计 < 0.5 秒
- 总启动时间：< 2 秒（满足 NFR-4.3）

### 11.2 命令执行性能

- 注册中心查找：dict O(1) 查找 + 一次方法调用，< 1ms
- 兼容映射：dict O(1) 查找，< 1ms
- 延迟 import：首次执行时 import 原模块，后续走 Python 模块缓存
- 额外开销：< 50ms（满足 NFR-4.3）

### 11.3 优化策略

- 插件模块在 `discover()` 阶段已 import，`execute()` 阶段仅调用函数
- 原模块的 import 在插件 `execute()` 内部延迟执行，避免未使用的模块影响启动速度
- `plugin-config.json` 加载结果缓存在注册中心实例
