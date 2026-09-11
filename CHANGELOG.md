# 📋 kb-kit 版本更新日志（CHANGELOG）

> 记录 kb-kit 工程套件的版本演进。日期格式：YYYY-MM-DD。

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
