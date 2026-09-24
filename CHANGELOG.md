---
domain: 管理
status: active
importance: 0.5
created: 2026-09-19
updated: 2026-09-19
tags: ["管理"]
---
# 📋 kb-kit 版本更新日志（CHANGELOG）

> 记录 kb-kit 工程套件的版本演进。日期格式：YYYY-MM-DD。

---

## 未发布 — 多源记忆归一进 vault（2026-09-16）

让所有可接入 Agent / 记忆源最终统一到同一个 vault（Obsidian 一个文件夹即见全部）：

### 🔄 主用系统切换：kb-kit RSI 自我改进引擎（2026-09-20，用户决定）

RSI 引擎与旧"成长引擎 loops"功能重叠，**自即日起 kb-kit 自我改进默认走 RSI 引擎**：
- 主用入口：`kb_engine --run t1,t2`（T1/T2/T3 编排）/ `kb_rsi`（指标·去重）/
  `kb_claim sync·validate`（Layer1 声明验证）/ `kb_usage analyze`（Layer2 真实使用）/
  `kb_embed probe`（Layer0 语义地基）。
- `clean.py` / `sync.py` 标 **superseded**（被 `kb_rsi` dups 指标 + `kb_engine` T1 接管），
  函数保留供 `memory_sync`/`memory_ingest` 摄入兼容，**未删**。
- `feedback_loop.py` 加权核心仍被 RSI `import` 复用（**保留**），仅其独立 CLI（ingest/apply）停用。
- README（架构段/模块表/插件表）已标注沿革；详见 `.agents` 项目记忆「主用系统切换」。

### 🆕 新增
- **DSH·evolve 结晶摄取**：`memory_ingest_json` 现同时摄取原生 `memory.json` 与
  dsh-evolve 结晶 `evolve_memory.json`（同 schema `{unit,global,tables.records}`，内容零重叠）
  - `agent_registry.py`：`detect_dsh()` 探测 `evolve_memory.json`（`KB_DSH_EVOLVE_MEMORY` 覆盖）
    + `_json_sources()` 合并 `json`/`evolve_json`/`evolve_json_path` 去重；`cmd_add` 加 `--evolve-json`
  - 注册表 `kb-agent.json` 的 DSH agent 新增 `sources.evolve_json`
- **project-context `.agents` 归一**：`memory_ingest.py` 新增
  `mirror_project_context(root, roots, agent_name, dry_run)`
  - `rglob("**/.agents/memory/*.md")` → 按 `<发现根名>/<项目内相对路径>` 镜像到
    `reference/project-context/<根>/<rel>`（用根名做命名空间，消除各项目同名 `MEMORY.md` 撞名）
  - content-hash 去重、`dry_run` 严格只读、幂等（状态存 `.kb/state/memory_state.json`）
  - `detect_project_context()` + `detect_agents()` 接入；`cmd_add` 加 `--project-context-root`
    （`nargs="+"` 多根；`KB_DSH_PROJECT_CONTEXT_ROOT` 逗号分隔多根覆盖）
- `kb-agent.json` 新增 `project-context` agent（type=project-context，sources 空=自动探测）
- **反馈阶梯 L3 元调参（`kb_meta.py` · M3）**：用阶梯自身真实结果（`kb_usage` 漏检）调
  L0/L2 的阈值旋钮，实现"阶梯自我调参"——
  - 旋钮（有界·单步）：覆盖门 `L0_MIN_IDF` / `L2_SIGNAL_MIN`（漏检多且该层未动作→下调松覆盖）、
    加权 `L2_L1_MISS_BONUS`（漏检多且 L2 未动作→上调多加权），范围/步长收敛于 `kb_constants`
  - 铁律：外部锚=真实漏检（`kb_usage.analyze`，无信号 best-effort 降级）· 接地阀 P0-1 冻结
    闭库内部调参 · 连续负 delta≥2 → 回滚该旋钮到 `last_good`（写配置 + checkpoint 可回滚）·
    只写运行时配置 `.kb_meta_config.json`，**不改分发仓常量、不改笔记正文**
  - CLI：`report/propose/apply/calibrate/status`（`calibrate` 为主回路：量漏检→回滚→产提议）

### 🐛 修复
- **`memory_ingest_json.ingest()` dry_run 真只读**：原实现 dry-run 无条件 `write_text` 写 vault
  文件（只跳过 commit，"未落盘"是假的），现已把 写文件/写 state/commit 全部 gate 于 `not dry_run`
  （回归：dry-run 前后 git 改动计数相等）

### 实测（2026-09-16）
| 验证项 | 结果 |
|---|---|
| `ingest --name dsh`（json + evolve） | evolve 7 条 → vault（提交 `0fdc2a5`），幂等重跑 0 |
| `ingest --name project-context` | 3 文件 → `reference/project-context/`（提交 `08db26a`），幂等重跑 0 |
| `ingest` 全量 dry-run | dsh(2 源) / codex / project-context 全部正常，无 crash |

---

## v2.0.0 — KB 插件化架构改造（2026-09-13）

将 KB 从"宿主系统"改造为"插件式"架构，与 openclaw / DSH / hermes 等 Agent 插件
统一为同一套插件接口和注册中心，保持 100% 向后兼容。

### 🆕 新增
- **`pipeline/plugin_base.py`** — 插件基协议
  - `PluginBase`（抽象基类）：`metadata()` / `initialize()` / `execute()` / `shutdown()`
  - `PluginContext`（依赖注入容器）：`vault_root` / `state_store` / `config` / `logger`
  - `PluginMetadata`（元数据值对象）：`name` / `version` / `type` / `actions` / `dependencies` / `cli_aliases`
- **`pipeline/plugin_registry.py`** — 插件注册中心
  - `discover()`：扫描 `plugins/` 目录，`importlib` 动态加载所有 `PluginBase` 子类
  - `register()`：检查插件名冲突、依赖缺失、配置启用/禁用
  - `initialize_all()`：Kahn 拓扑排序按依赖顺序初始化，有环标记 disabled
  - `execute()`：查找插件并调用 `execute(action, params)`，错误隔离
  - `list_plugins()` / `resolve_alias()`：查询与 CLI 兼容映射
- **`pipeline/kb_launcher.py`** — CLI 统一入口
  - `COMPAT_MAP`：旧 CLI 命令 → `(plugin_name, action)` 兼容映射表
  - `KBLauncher.run()`：解析命令 → 查映射 → 构造 params → 注册中心分发
  - 自动注入 `--root`，支持 `kb list` / `kb help`
- **`pipeline/plugins/` 目录** — 24 个插件文件
  - 4 个 Agent 适配器插件：`agent_md` / `agent_json` / `agent_sqlite` / `agent_detect`
  - 19 个 KB 功能模块插件：`rag` / `intake` / `feedback` / `link` / `recall` / `healthcheck` / `dashboard` / `clean` / `validate` / `sync` / `memory_sync` / `classify` / `graph` / `rerank` / `semantic_chunk` / `ingest_chat` / `ingest_convert` / `user_manager` / `health_metrics`
  - 1 个 subprocess 封装基类：`_subprocess_plugin.py`（KB 模块插件统一通过 subprocess 调用原脚本，保持 100% 兼容）
- **`reference/plugin-config.json`** — 23 个插件的启用/禁用配置

### 🔧 改造
- **`kb`（bash 启动器）**：删除 `case "$TOOL" in` 硬编码块，改为 `exec python3 pipeline/kb_launcher.py "$@" --root "$VAULT"`
- **`kb.cmd`（Windows 启动器）**：删除 `if /i "%TOOL%"=="xxx"` 硬编码块，改为统一调用 `kb_launcher.py`
- 原 25 个 pipeline 模块**保持原位不变**，插件封装层通过 import/subprocess 调用

### 🧪 实测（2026-09-13）
| 验证项 | 结果 |
|---|---|
| 插件加载 | ✅ 23 个插件全部 loaded |
| `kb list` | ✅ 列出所有插件及状态 |
| `kb help` | ✅ 显示完整帮助 |
| `kb agent detect` | ✅ 探测到 OpenClaw / Hermes / DSH / Codex |
| `kb validate` | ✅ 29 笔记校验通过 |
| `kb healthcheck` | ✅ 与改造前行为一致 |
| 扩展性 | ✅ 新插件自动发现，无需改核心代码 |
| 错误隔离 | ✅ 语法错误插件被跳过，其他正常 |
| `growth_cron.sh` | ✅ 直接调 .py，不受影响 |

### 📐 设计原则
- **开闭原则**：新增插件不改核心代码（注册中心、CLI 启动器）
- **适配器模式**：插件封装层通过 import 调用原模块函数，不重写业务逻辑
- **依赖注入**：插件通过 `PluginContext` 访问共享服务，不直接 import 其他模块
- **零依赖**：仅使用 Python 标准库（abc / importlib / json / logging）
- **错误隔离**：单个插件加载/执行失败不影响其他插件
- **100% 向后兼容**：原 CLI 命令、`kb-agent.json`、`growth_cron.sh` 全部不变

---

## v1.0.0 — 首次发布（2026-08，套件初始版本）

kb-kit 初始交付：把一套会自成长的 Obsidian 知识库落成可一键部署的工程。

**功能**
- PARA 分区 + frontmatter 元数据规范 + 笔记模板
- 成长引擎（纯 Python 标准库，零依赖）：`intake_triage` 摄入 triage、`feedback_loop` 命中反馈、`link_engine` 孤岛补链、`recall_schedule` 间隔回忆、`kb_health`/`dashboard` 健康治理、`clean` 清洗分块、`validate` 校验
- 本地语义检索 `rag`（TF-IDF/BM25 风格，中文 unigram+bigram 兜底，免 jieba）
- 运维：备份 / 恢复演练 / 定时节拍 / 健康巡检
- 跨平台：`install.sh`（Linux/macOS）、`install.ps1` / `install.bat`（Windows）

---

## v1.1.0 — Agent 对接与稳定性加固（2026-09-07）

面向 Hermes Agent（`kb` CLI 启动器）完成对接、修复引擎崩溃、确立喂养纪律。

### 🆕 新增
- **`kb` CLI 启动器**：vault 根目录软链 + `~/.local/bin/kb` 全局可用，任意目录 `kb <cmd>` 可调用
- **`kb ingest review`（只读预览）**：kb 自带的只读预览命令，路由前先看 triage 结论不移动任何文件；本次确立其作为「C 自动化唯一可用入口」的定位（详见下方 C 边界）
- **Direction A 喂养纪律**（写入 `kb-kit` skill）：回答涉及 vault 的问题前，先跑一轮 `kb query`，答案扎根共享知识库
- **`CHANGELOG.md`**（本文件）：首次建立版本演进记录

### 🐛 修复（Pipeline 稳定性）
- **importance 占位符崩溃**：`intake_triage.py`、`recall_schedule.py`、`feedback_loop.py` 多处裸 `float(fm.get("importance"))` 在遇到 `0.0-1.0`、`importance:: 0.8`、空值时 ValueError 崩。
  - 改为共享 `_parse_importance()` 安全解析器（re 提取首数字，失败回退 0.0），三处复用。
  - 旧补丁 `.split()[0]` 系**假补丁**（`"0.0-1.0".split()[0]` 仍是 `"0.0-1.0"`，照样崩），本次一并清除。
- **kb-healthcheck.py 误报**：
  - empty 检测：把 `.git`/`.deepeval`/`.codeartsdoer` 等系统/工具目录当空目录 → 加排除列表
  - discipline 检测：把 `_` 开头目录（如 `_SELF_OPT`）当违规 → 跳过
  - deadlinks：严格路径匹配误判标题/别名真实存在的条目 → 加 `collect_titles()` 兜底匹配
  - 噪声过滤：`[[link]]`、`[[双向链接]]`、`[[reply_to:...]]` 等文档示例不计入死链
  - 模板仓 `/kb-kit/` 不计入死链统计（它是蓝本示例）

### 🔧 内容层调整
- 参考文件加 frontmatter aliases：AGENTS / AGENTS、TOOLS、MEMORY、SOUL、HEARTBEAT
- 修真实死链：收件箱 `[[📥 收件箱]]`、治理总览 `[[📖-治理总方案-v4|治理总览]]`、推理引擎 `_INDEX` 绝对路径
- 标签越界 723 → 0：`TOPIC_WHITE` 加 62 个自由 tag + 删 14 个笔记的 `#ai` 标签
- 巡检合计：801 → 0

### 🧪 已实测（2026-09-07）
| 命令 | 结果 |
|---|---|
| `kb ingest` | ✅ 通过（含 ingest move 物理路由 + trash 归档） |
| `kb feedback` | ✅ 通过（含 feedback apply 写回 importance） |
| `kb recall deck` | ✅ 通过 |
| `kb healthcheck` | ✅ 0 问题 |
| `kb rag index` | ✅ 423 文档 |

### ⚠️ 关键结论（已记录于决策日志）
- **B（放大飞轮）证伪**：`kb query` 只读，agent 查询不计 hits；hits 仅由 `kb feedback` 内部循环累积。"查询越多越准"不成立，不投入。
- **C（自动化写）有破坏性边界**：`kb ingest move`/`--trash` 会物理删除收件箱文件，无人监管的每日 cron 存在误删风险。只读侧可自动化，移动/归档保留人工审一步。

---

---

## v1.2.0 — 多 Agent 记忆摄取（OpenClaw / Hermes / DSH / Codex）（2026-09-07）

kb-kit 原本只服务于 OpenClaw。本次改造为**跨 Agent 通用**：安装后可探测本机
存在的 agent（OpenClaw、Hermes、DSH、Codex），用户选择后按各自记忆形态
（.md / JSON / SQLite）把记忆灌进知识库，`kb_source` 标记来源。

### 🆕 新增
- **`pipeline/agent_registry.py`** — 多 agent 记忆摄取注册表。
  - `detect` 探测本机 agent（按标准位置 + CLI + 环境变量覆盖，**不写死私人路径**）
  - `add / enable / list / setup / ingest` 管理并调度摄取
- **`pipeline/memory_ingest_json.py`** — JSON 记忆源 adapter（DSH agent-memory，
  `$DSH_HOME/storages/memory.json`，解析 records 喂 sync/mirror）。
- **`pipeline/memory_ingest_sqlite.py`** — SQLite 记忆源 adapter（Codex，
  解析 `~/.codex/*.sqlite` 的 `thread_items` 交互记录）。
- **`kb ingest agent` 命令**（kb 启动器）：调度 agent_registry 摄取。

### 🔧 改造
- **`pipeline/memory_ingest.py`**：移除 10 处 `openclaw` 硬编码，
  `mirror` / `sync` / `mirror-core` / `extract` 全部改收 `agent_name` 参数，
  `kb_source` 记为实际 agent 名（openclaw / hermes / ...），
  镜像文件改名为 `{agent_name}-*`。
- **`kb` 启动器**：新增 `agent` 与 `ingest agent` 两条命令。

### 🧪 实测（空目录 + 真实 vault）
| Agent | 形态 | 空目录摄取 | 真实 vault 行为 |
|---|---|---|---|
| OpenClaw | .md | 镜像 62 条 + 核心 5 条 | 增量 0（已摄入，正确） |
| Hermes | .md | 核心 2 条 | 增量 0（已摄入，正确） |
| DSH | JSON | 18 条 | 增量 0（已摄入，正确） |
| Codex | SQLite | 11 条 | 增量 0（已摄入，正确） |

> 真实 vault 摄取返回 0 是**正确的增量去重**：记录已存在于 KB，按 `kb_target::标题`
> 去重跳过，非 bug。

### 🐛 修复（写出路径补 `.md` 扩展名）
- **DSH / Codex adapter 写出文件缺 `.md` 扩展名**：`_kb_target()` 返回的是
  目录形态路径（如 `20-技术 Technology/综合/综合`），但写入逻辑当文件用，
  产出无扩展名文件 → Obsidian 可能不渲染、巡检抓不到。
  - 改 `_kb_target()` 三处末尾统一补 `.md`（json + sqlite 两 adapter 同步），
    `touched` 数组改用完整 `kb_path`（原 `kb_target + "/" + name` 导致
    `...部署运维.md/部署运维.md` 拼接错误）。
  - 迁移真实 vault：把 4 个无扩展名旧文件（DSH/Codex 之前产出）重命名为
    `.md` 保留全部 27 条目（备份留 `/tmp/old_ext_files/`）。

### 🆕 安装时 agent 探测集成
- **`create_vault.py` 自动探测 + 注册 agent**：建仓后调用 `agent_registry.detect_agents()`
  探测本机 agent（OpenClaw / Hermes / DSH / Codex），把已探测到的写入新 vault 根的
  `kb-agent.json`。探测走标准位置 + 环境变量覆盖（`KB_OPENCLAW_HOME` /
  `KB_DSH_MEMORY` / `KB_CODEX_DATA` 等），**绝不写死私人路径**；非交互安装默认全部注册，
  可用 `AGENT_SELECT` 环境变量指定子集。

### 🧪 实测
- `create_vault.py --vault /tmp/newvault`：空目录建仓成功，
  `kb-agent.json` 写入 4 agent（openclaw / hermes / dsh / codex），
  首份向量索引 26 文档，git 首次提交通过。

### ⚠️ 已知约束
- 安装时需**本机存在对应 agent**才能探测；`kb-agent.json` 为运行时状态（gitignore）。
- 非标准部署路径可通过 `KB_<AGENT>_<FIELD>` 环境变量覆盖，勿硬编码进模板仓。

---

## v1.3.0 — 摄取越界标签修复（2026-09-07）

v1.2.0 的多 agent 摄取有个隐藏 bug：**DSH / Codex adapter 在写入新 vault 时，
会把记忆源里的自由标签 + 固定 `kb-agent` 标签照抄进笔记 `tags:`**，而这些标签
不在巡检白名单里。于是「全新建仓 → 摄取 → 巡检」会出现几十条越界、骨架、死链
问题（`[[dsh]]` / `[[codex]]` 裸双链还因为没有锚点页变成死链）。

### 🐛 修复（两个 adapter 各 2 份副本同步）
- **`memory_ingest_json.py`（DSH）**：`tags` 由「源自由标签 + `kb-agent` + 域」
  改为**只保留 `[domain]`**（DSH 源标签如 `kb-kit`、`knowledge-base`、`部署`
  等本就不该进知识库标签层；来源信息已由 `kb_source` 承载）。
- **`memory_ingest_sqlite.py`（Codex）**：
  - `tags` 去掉造出来的 `会话交互`，改为 `[domain]`；
  - 补回被漏写的 `importance`（原来 Codex 产物缺该字段 → 巡检报骨架）。
- **`kb-healthcheck.py`**（真实仓 + 治理脚本 + 模板仓三副本）：`TOPIC_WHITE`
  加入 `开发`、`产品`、`数据` 三个受控 domain 值（adapter 按内容判定 domain，
  属可控枚举，非自由输入）。
- **锚点页**：模板仓与真实仓的 `reference/` 各加 `dsh.md` / `codex.md`，
  用 `aliases` 把裸双链 `[[dsh]]` / `[[codex]]` 落地为目标页，死链归零。

### 🧪 实测（真实仓 262 死链基线不变）
- 真实 vault 巡检：标签越界 19 → 0，缺失 frontmatter 1 → 0，死链 30 → 0，
  合计 **50 → 0**。
- 用模板仓 `template-vault/` 全新建仓（不接真实记忆），接 `kb-healthcheck` 三
  副本同步改过白名单，`kb healthcheck all` **0 问题**——证明修在蓝本层，以后
  每次 `create_vault` 产出的新 vault 从一开始就是干净的。

### 📐 教训（已记入 kb-kit skill）
- **修在标签"源头"，不是往白名单里堆词**：adapter 注入的源自由标签应在写入时
  归一为 `[domain]`；把 `dreame`/`src`/`kb-agent`/`会话交互` 这类词加进
  `TOPIC_WHITE` 是本末倒置。
- **新 vault 必须从头巡检**：模板仓巡检 0 ≠ 新 vault 0。adpater 摄取发生在安装时，
  脏的是「建仓后摄取」这一步；只在旧 vault 上重跑巡检发现不了新 bug。

---

## 未来方向（待决策）
- [ ] C 只读侧可挂每日 `kb ingest review`，结果入决策日志，move 留人工
- [ ] 考虑将选 A、证伪 B 固化为长期纪律
