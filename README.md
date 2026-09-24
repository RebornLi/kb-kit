---
domain: 管理
status: active
importance: 0.5
created: 2026-09-19
updated: 2026-09-23
tags: ["管理"]
---
# 🏠 知识库一键套件（kb-kit）

> 把"一套会自成长的 Obsidian 知识库"落成**别人也能一键搭建**的工程。
> 朋友拿到文件夹 → 双击 / 一条命令 → 得到一份**已接线好的空知识库**，自带本地语义检索、收件箱 triage、命中反馈、孤岛补链、间隔回忆、健康巡检、备份演练、多 Agent 记忆摄取。

**一句话**：你只管往收件箱丢想法；引擎帮你**清洗、路由、补链、回忆、巡检、备份**。

- **零依赖**：成长引擎只用 Python 标准库，**不需要 pip install**，任何装了 Python 3.8+ 的机器都能跑
- **跨平台**：`install.sh`（Linux/macOS）、`install.ps1` / `install.bat`（Windows）
- **插件化架构**：KB 功能模块和 Agent 适配器统一为插件，新增功能只需放入 `plugins/` 目录，无需改核心代码
- **多 Agent**：自动探测并摄取 OpenClaw / Hermes / DSH（原生 + evolve 结晶）/ Codex 的记忆进知识库
- **本地优先**：检索、向量索引、回忆调度全部本地完成，数据不出机器

---

## 目录

- [一、这是什么](#一这是什么)
- [二、快速开始](#二快速开始)
- [三、装好之后怎么用](#三装好之后怎么用)
- [四、成长引擎](#四成长引擎)
- [五、目录结构](#五目录结构)
- [六、frontmatter 规范](#六frontmatter-规范)
- [七、多 Agent 记忆摄取](#七多-agent-记忆摄取)
- [八、插件化架构](#八插件化架构)
- [九、让知识库"自己跑"](#九让知识库自己跑)
- [十、常见问题](#十常见问题)
- [十一、版本演进](#十一版本演进)
- [License](#license)

---

## 一、这是什么

它不是模板套壳，而是把一套会自成长的知识库方案（v4.0）落成可移植的代码：

- **PARA 分区** + frontmatter 元数据规范 + 笔记模板
- **成长引擎**（纯 Python 标准库，零依赖、跨平台）
  - ① `intake_triage` 摄入 triage · ② `feedback_loop` 命中信号
  - ③ `link_engine` 孤岛补链 · ④ `recall_schedule` 间隔回忆
  - ⑤ `kb_health` / `dashboard` 健康与治理面
  - `clean` 清洗分块 · `validate` 校验 · `rag` 本地语义检索
  - RSI **自我治理**（主用）：`kb_engine` T1/T2 编排 · `kb_rsi` 只读探针 · `kb_claim`/`kb_usage` 适应度闸 · `kb_embed` 语义地基 · `kb_query` 查询→知识正回路
- **本地语义检索** `rag`（TF-IDF / BM25 风格，中文 unigram+bigram 兜底，免 jieba）
- **运维**：备份 / 恢复演练 / 定时节拍 / 健康巡检
- **多 Agent 记忆摄取**：OpenClaw / Hermes / DSH（原生 + evolve 结晶）/ Codex 的记忆自动归一到 KB（project-context 等可手动 `kb agent add` 摄取）

> 为什么零依赖？引擎只用了 Python 标准库，所以**不需要 pip install**，任何装了 Python 3 的机器都能跑。这就是"一键"的关键。

---

## 二、快速开始

### 通用前提
- **Python 3.8+**（Windows 装时勾选 "Add to PATH"）。装之前终端敲 `python --version` 确认在。
- **Obsidian**（可选，免费笔记 GUI，用来打开 / 阅读 / 编辑；纯命令行也能跑）。

### 自己机器上试用（克隆本套件）
```bash
cd kb-kit
bash install.sh          # Linux/macOS（无需 chmod；或先 chmod +x 再 ./install.sh）
# Windows: 双击 install.bat 或 运行 .\install.ps1
```
会提示输入目标目录（留空则建当前目录下的 `KB`）。安装时顺带跑一轮"成长引擎 demo"——生成首份向量索引、收件箱 triage 清单、补链建议、治理仪表盘，让你立刻看到效果。

### 分享给别人
把整个 `kb-kit/` 打包 zip 发给对方。对方解压后：
- **Windows** → 双击 `install.bat`
- **macOS/Linux** → 直接 `bash install.sh`（无需 chmod）；或先 `chmod +x install.sh` 再 `./install.sh`

> 三个安装器内部都调同一个引擎 `create_vault.py`。若某平台脚本执行有问题，可直接调引擎（等价、全平台）：
> ```bash
> python3 create_vault.py --vault "我的知识库"
> ```

### 安装时会发生什么
1. 校验/创建目标目录
2. 拷贝模板仓物料（PARA 分区 + 模板 + 引擎 + 启动器 + .obsidian 配置）
3. 探测本机已安装的 agent（OpenClaw / Hermes / DSH 原生+evolve 结晶 / Codex），交互选择后写入 `kb-agent.json`
4. 初始化运行时状态文件（`.memory_*_state.json` 等，已 gitignore）
5. 建首份向量索引（`vector index/`）
6. 跑一轮成长引擎 demo（intake / feedback / link / recall / health / dashboard）
7. 可选 `git init` + 首次提交（用 `KB_GIT_EMAIL` / `KB_GIT_NAME` 或全局 git 配置署名）

---

## 三、装好之后怎么用

进入 vault，用 `kb`（Windows 用 `kb.cmd`）这条捷径——它自动注入 `--root`（= 它所在目录 = vault 根），你不用记路径。

### 命令清单

| 命令 | 作用 | 写操作 |
|------|------|--------|
| `kb rag index` | 重建本地向量索引（写新笔记后必跑） | 否 |
| `kb query "问题"` | 自然语言检索 + 最佳片段 | 否 |
| `kb query "问题" --answer` | 检索 + 本地模型生成答案（需配 ORNITH） | 否 |
| `kb ingest` | 收件箱摄入 triage 清单 | 否 |
| `kb ingest move` | 按路由搬运收件箱到 PARA 区 | **是**（先 `kb ingest` 看清单） |
| `kb ingest trash` | 把收件箱低价值项归档 | **是**（先 `kb ingest` 看清单） |
| `kb ingest review` | 只读预览 triage 结论（不移动文件） | 否 |
| `kb feedback` | 命中信号计数 | 否 |
| `kb feedback apply` | 把命中提升写进 importance | **是**（只升不降） |
| `kb link` | 孤岛补链建议清单 | 否 |
| `kb link apply` | 应用补链（写入建议区块） | **是**（先 `kb link` 看清单） |
| `kb recall` | 今日回忆 deck | 否 |
| `kb recall mark` | 标记已回忆 → 推进稳定性 | **是** |
| `kb recall status` | 回忆排期状态 | 否 |
| `kb clean` | 清洗分块 dry-run 预览 | 否 |
| `kb clean --apply` | 清洗：补全 frontmatter + 去重 | **是**（先 `kb clean` 看清单） |
| `kb clean --chunk` | 对超长笔记按 `##` 标题分块 | **是** |
| `kb clean --report` | 扫描超长/待清洗/过期清单 | 否 |
| `kb validate` | frontmatter 校验（硬错误/软告警） | 否 |
| `kb healthcheck` | 健康巡检（死链/缺字段/空目录/超长/孤儿/标签越界） | 否 |
| `kb dashboard` | 生成治理仪表盘 | **是**（写 `_INDEX.md`） |
| `kb sync dry-run` | 知识同步管道预览 | 否 |
| `kb sync apply <note.md>` | 同步笔记到 PARA 区 | **是**（先 dry-run） |
| `kb sync rollback` | 回滚上次 sync apply | **是** |
| `kb sync history` | 查看同步历史 | 否 |
| `kb backup [daily\|weekly]` | 全量快照（zip + sha256） | **是**（写 `backups/`） |
| `kb ingest agent` | 摄取本机 agent 记忆进 KB | **是**（先 `--dry-run`） |
| `kb agent` | agent 注册表管理（detect/list/add/enable） | 否 |
| `kb list` | 列出所有已注册插件及状态 | 否 |
| `kb help` | 显示命令帮助 | 否 |

### 三条纪律（写在代码里）
1. **写前 checkpoint**：任何 `apply` / `promote` 先 `review` / `--dry-run` 看清单。
2. **importance 只升不降**：防刷票（累计提升上限 0.30，单次最小 0.05）。
3. **不 sweep Obsidian**：脚本只 `git add` 自己改的文件，绝不 `git add -A`。

### 让 `kb query` 调模型生成答案（可选）
`kb query "问题" --answer` 会走本地 ORNITH（vLLM）模型。要生效需设环境变量：
```bash
export ORNITH_API_KEY=你的密钥                 # Windows: set ORNITH_API_KEY=你的密钥
export ORNITH_BASE_URL=http://<vllm地址>/v1     # 例: http://192.168.1.3:8000/v1
```
不全时不会崩溃，而是给出精准提示（缺 key / 缺 URL / 端点连不上 分别提示）；都没配则静默返回检索片段。

---

## 四、成长引擎

知识库像**有机体**：有新陈代谢（清洗/归档）、有免疫系统（巡检）、会自我连接（补链）、会按间隔回忆（recurrence）。

### 引擎① 摄入 triage（`intake_triage.py`）
对收件箱每篇按**四维打分**（结构 25 + 密度 25 + 唯一性 25 + 价值信号 25），路由为：
- `keep` 保留 · `merge` 合并进更高价值现注 · `trash` 归档垃圾
- `refine` 补写（进待审队列）· `route` 按 `kb_target` 挪到对应 PARA 区

> **⚠️ 主用系统已切换（2026-09-20 用户决定）**：本节旧"成长引擎 loops"中，`clean.py` /
> `sync.py` 已被 **RSI 自我改进引擎**（`kb_engine` T1/T2/T3 + `kb_rsi` + `kb_claim` +
> `kb_usage` + `kb_embed`）取代——今后知识库自我改进/收敛动作**默认走 RSI CLI**（如
> `kb_engine --run t1,t2`）。`feedback_loop.py` 的加权核心仍被 RSI `import` 复用（**保留**），
> 仅其独立 CLI（`ingest/apply`）停用；`link_engine` / `recall_schedule` / `kb_health` 与 RSI
> 部分重叠、按需保留。本节仅作架构沿革参考。

### 引擎② 命中信号（`feedback_loop.py`）
把 RAG 命中当作"需求信号"：每篇笔记被多少查询命中 = 价值信号。累积成 importance 上浮（只升不降，上限 0.30）。

### 引擎③ 孤岛补链（`link_engine.py`）
发现"相似却未互链"的笔记对，突出**跨域弱连接**（= 洞察来源），供人工采纳。`apply` 时写入独立建议区块（幂等，已有区块跳过）。

### 引擎④ 间隔回忆（`recall_schedule.py`）
按 importance 衰减窗口（L4 180d / L3 30d / L2 14d / <0.3 7d）+ 递增稳定性（spaced repetition），算出今天"该回忆"的笔记，按 `urgency = importance × (1 + overdue/稳定性)` 排序。每条喂 `rag query` 做"检索浮层"核对。

### 引擎⑤ 健康与治理面（`kb_health.py` + `dashboard.py`）
**六大维度度量**：
- 🔗 连接度（平均出链 / 孤儿笔记% / 跨域连接比）
- ⏰ 时效（过期未 review 笔记%）
- 🌱 新鲜（近 30 天新增 vs 合并 vs 清理）
- 📥 沉淀质量（收件箱积压 vs 已处理）
- 💡 涌现（MOC / 综合类笔记数）
- 🧩 去重候选（摘要相似度 > 0.9 的笔记对）

`dashboard.py` 汇总校验 + 备份新鲜度 + 管道同步 + 成长性 + 巡检告警，生成 `70-知识治理 Governance/_INDEX.md`。

### 健康巡检（`kb-healthcheck.py`）
八项门禁巡检（仅检测 + 报告，不自动修改）：
- `deadlinks` 死链检测（贴近 Obsidian 链接语义，处理 `[[path|alias]]` / `[[path#anchor]]` / 裸名跨命名空间）
- `skeletons` 骨架笔记（<500B 且 >7 天）
- `tags` 标签越界（不在白名单的自由 tag）
- `empty` 空目录检测
- `frontmatter` frontmatter 缺失
- `discipline` 顶层目录纪律（非 `NN-` 编号格式）
- `overlong` 超长未分块（>2000 字）
- `orphans` 孤儿笔记（无出链）

### 清洗与校验
- `clean.py`：结构化（补全 frontmatter）+ 标准化（归一 domain/status/tags）+ **精确去重（superseded by `kb_rsi` dups 指标 + `kb_engine` T1）** + 分块（按 `##` 标题拆分）。保留函数供 memory_sync/memory_ingest 摄入兼容。
- `validate.py`：只读校验。✗ 硬错误（必填缺失 / 取值域非法）+ ⚠ 软告警（正文过长 / retire 但 active）

---

## 五、目录结构

```
kb-kit/                        ← 分发给别人就这个文件夹
├── install.sh / .ps1 / .bat   各平台安装器
├── create_vault.py            安装引擎（把模板仓复制 + 接线 + 探测 agent + 跑 demo）
├── README.md                  本文件
├── quickstart.md              5 分钟上手
├── CHANGELOG.md               版本更新日志
├── LICENSE                    MIT License
└── template-vault/            模板仓（安装时照此建仓）
    ├── kb / kb.cmd            每日使用启动器（Linux/macOS / Windows）
    ├── 🏠-知识库首页.md        首页（总览 + 规则速览）
    ├── 📖-知识库管理方案.md    方案 v4.0（设计依据）
    ├── 01-如何开始.md         5 步上手
    ├── 00-收件箱 Inbox/       原始输入，自然积压
    ├── 10-项目 Projects/      进行中的项目
    ├── 20-技术 Technology/    可复用技术沉淀
    ├── 30-决策日志 Decisions/ 决策 + 理由
    ├── 40-资源库 Resources/   通用方法论/资料
    ├── 50-模板 Templates/     笔记模板（项目/决策/日报/日记/周回顾/技术经验）
    ├── 60-运营 Operations/    清洗/流转 SOP
    ├── 70-知识治理 Governance/ 元层：SOP / 指标 / 巡检 / 仪表盘
    ├── 90-归档 Archive/       收尾/过期
    ├── pipeline/              成长引擎 + RSI 自我治理（44 个 Python 模块 + 插件化架构）
    │   ├── plugin_base.py     插件基协议（PluginBase + PluginContext + PluginMetadata）
    │   ├── plugin_registry.py 插件注册中心（发现/注册/拓扑排序/分发）
    │   ├── kb_launcher.py     CLI 统一入口（兼容映射 + 动态路由）
    │   ├── plugins/           插件目录（4 个 Agent + 19 个 KB 模块）
    │   └── *.py               原 25 个业务模块（保持原位不变）
    ├── scripts/               备份 / 演练 / 定时 / 巡检脚本
    ├── reference/             协议与规则 + plugin-config.json（插件启用/禁用）
    ├── logs/                  运行日志（gitignore）
    ├── vector index/          向量索引（gitignore）
    └── .obsidian/             Obsidian 配置
```

### pipeline/ 模块清单

| 模块 | 职责 | 引擎编号 |
|------|------|---------|
| `plugin_base.py` | 插件基协议（PluginBase + PluginContext + PluginMetadata） | - |
| `plugin_registry.py` | 插件注册中心（发现/注册/拓扑排序/分发） | - |
| `kb_launcher.py` | CLI 统一入口（兼容映射 + 动态路由） | - |
| `kb_common.py` | 公共工具：frontmatter 解析 + ROOT_DEFAULT + EXCLUDE + 分词 | - |
| `rag.py` | 本地语义检索（TF-IDF 余弦，中文 unigram+bigram） | - |
| `intake_triage.py` | 摄入 triage（四维打分 + 路由） | ① |
| `feedback_loop.py` | 命中信号（importance 上浮，只升不降） | ② |
| `link_engine.py` | 孤岛补链（跨域弱连接 = 洞察来源） | ③ |
| `recall_schedule.py` | 间隔回忆（spaced repetition + 检索浮层核对） | ④ |
| `kb_health.py` | 成长性度量（六维度） | ⑤ |
| `dashboard.py` | 治理仪表盘生成 | ⑤ |
| `kb-healthcheck.py` | 健康巡检（八项门禁） | - |
| `clean.py` | 清洗/分块（结构化 + 标准化 + 去重）**⚠️ superseded by kb_rsi/kb_engine(T1)，保留给摄入兼容** | - |
| `validate.py` | frontmatter 只读校验 | - |
| `sync.py` | 知识同步管道（幂等 / dry-run / 回滚）**⚠️ superseded by kb_engine，保留给既有调用方兼容** | - |
| `memory_ingest.py` | 记忆写入侧（agent 记忆流 → KB ETL；含 `.agents`/project-context 归一） | ⑦ |
| `memory_sync.py` | 记忆同步（五问 + 去重 + 路由） | ⑥ |
| `memory_ingest_json.py` | JSON 记忆源 adapter（DSH 原生 memory.json + evolve 结晶） | ⑦ |
| `memory_ingest_sqlite.py` | SQLite 记忆源 adapter（Codex） | ⑦ |
| `agent_registry.py` | 多 agent 记忆摄取注册表 | ⑦ |
| `classify.py` | 内容分类（内容类型信号 + 域归类） | - |
| `graph.py` | 知识图谱构建（节点/边/聚类） | - |
| `ingest_chat.py` | 对话记录摄入（chat → 笔记 ETL） | - |
| `ingest_convert.py` | 格式转换（多源 → Markdown 标准化） | - |
| `rerank.py` | 重排序（检索结果二次精排） | - |
| `semantic_chunk.py` | 语义分块（长文本 → 语义连贯片段） | - |
| `state_manager.py` | 状态管理（引擎运行态持久化） | - |
| `user_manager.py` | 用户管理（多用户配置 / 权限） | - |

### RSI 自我治理引擎（主用系统）

自 2026-09-20 起，知识库自我改进**默认走 RSI 引擎**（详见第四章节尾注释）。`kb_rsi` 负责只读采集与建议，`kb_engine` 负责有回滚的可执行编排，其余为适应度/地基/回路支撑：

| 模块 | 职责 | 角色 |
|------|------|------|
| `kb_rsi.py` | 只读探针：孤儿 / 去重 / 外部接地% / 跨域比 / 陈旧 / 新鲜度等指标 + 改进建议（四道闸：外部接地 / 适应度 / 有界可回滚 / 多样性） | 采集 |
| `kb_engine.py` | RSI 编排器：T1 回填链接 / T2 去重收敛 / T3 升级（带开关 + 质量门 + 可回滚） | 执行 |
| `kb_calibrate.py` | 阈值标定：在仿真大语料上 A/B 标定 `DUP_SIM` / `MERGE_MIN_SIM` / `RETIRE_AGE_DAYS` 等硬编码阈值 | 标定 |
| `kb_fitness.py` | 外部锚定适应度：把 fitness 对齐"对现实的预测误差"（修 Goodhart 闭环） | 适应度 |
| `kb_usage.py` | 真实使用捕获（Layer 2）：查询成功率 / 再问率 | 适应度 |
| `kb_claim.py` | 声明验证循环（Layer 1）：把适应度闸落地为对现实的预测 | 适应度 |
| `kb_embed.py` | 语义向量地基（Layer 0）：给 `rag` / `kb_rsi` 提供可选语义向量路径 | 地基 |
| `kb_query.py` | 闭合"查询→知识"正回路：合成耐久性答案写提案（人工在环 + 有界，绝不自动入库） | 回路 |
| `kb_adaptretrieve.py` | L1 查询即反馈（反馈阶梯·实时回路）：把"被重问却漏检"喂回检索修正（只提议·人工 apply） | 回路 |
| `kb_selfweight.py` | L2 自我加权（反馈阶梯·代谢回路）：外部锚定信号·importance 只升不降·封顶+多样性地板；接地不足冻结内部人气 | 回路 |
| `kb_retriage.py` | L0 检索表层修正（反馈阶梯·检索纠错 · M2b）：接 L1 漏检缺口，只增补 `tags` / 充实 `kb_summary`（rag.py 打分只用 token）；接地阀冻结 + raw/ 隔离 + 单次 checkpoint 可回滚 | 回路 |
| `kb_meta.py` | L3 元调参（反馈阶梯·元调参层 · M3）：用阶梯真实结果（`kb_usage` 漏检）调 L0/L2 阈值旋钮（`L0_MIN_IDF`/`L2_SIGNAL_MIN`/`L2_L1_MISS_BONUS`）；有界单步·接地阀冻结·连续负 delta（≥2）回滚到 last_good·不改仓内常量/笔记正文 | 回路 |
| `kb_ingest.py` | 真实外部摄入：把外部素材永久存入不可变 `raw/` 层（`external_inflow` 真值来源，阈值 ≥5%） | 摄入 |
| `kb_eval.py` | RAG 检索质量评估网：把"检索变好了吗"变成可复跑的量化基线 | 度量 |
| `kb_scale.py` | 规模路径预留：元数据桥接，让 RSI 感知 `vector index` 层 | 规模 |
| `kb_contradiction.py` | 矛盾检测 lint：发现谈同一主题却给出相反断言的笔记对 | 质量 |
| `kb_schema_migrate.py` | 分类体系细化迁移：由顶级分区客观推导 `domain × category` 二维分类 | 结构 |
| `kb_constants.py` | 阈值统一源：收敛散布在各模块的硬编码阈值与魔法数字 | 基建 |
| `kb_types.py` | 类型定义：`Frontmatter` / `NoteInfo` / `SimResult` 等 TypedDict | 基建 |
| `evolution_log.py` | RSI 统一演进日志：四类事件追加到 `.kb_evolution.jsonl`（机读、可 diff）+ `EVOLVED.md`（人读、按日期） | 日志 |

### plugins/ 插件清单

所有插件统一放在 `pipeline/plugins/` 目录，通过 `PluginRegistry` 自动发现和加载。

| 插件 | 类型 | 封装的原模块 | actions |
|------|------|-------------|---------|
| `agent_md_plugin.py` | agent | `memory_ingest.py` | mirror / sync / mirror-core / extract |
| `agent_json_plugin.py` | agent | `memory_ingest_json.py` | ingest |
| `agent_sqlite_plugin.py` | agent | `memory_ingest_sqlite.py` | ingest |
| `agent_detect_plugin.py` | agent | `agent_registry.py` | detect / list / add / enable / setup / ingest |
| `rag_plugin.py` | kb_module | `rag.py` | index / query |
| `intake_plugin.py` | kb_module | `intake_triage.py` | review / apply |
| `feedback_plugin.py` | kb_module | `feedback_loop.py` | ingest / hit / apply |
| `link_plugin.py` | kb_module | `link_engine.py` | suggestions / apply |
| `recall_plugin.py` | kb_module | `recall_schedule.py` | deck / mark / status |
| `healthcheck_plugin.py` | kb_module | `kb-healthcheck.py` | check |
| `health_metrics_plugin.py` | kb_module | `kb_health.py` | metrics |
| `dashboard_plugin.py` | kb_module | `dashboard.py` | generate |
| `clean_plugin.py` | kb_module | `clean.py`（superseded by RSI）| dry-run / apply / report / chunk |
| `validate_plugin.py` | kb_module | `validate.py` | validate |
| `sync_plugin.py` | kb_module | `sync.py`（superseded by RSI）| dry-run / apply / rollback / history / ingest-any |
| `memory_sync_plugin.py` | kb_module | `memory_sync.py` | review / promote |
| `classify_plugin.py` | kb_module | `classify.py` | classify |
| `graph_plugin.py` | kb_module | `graph.py` | build |
| `rerank_plugin.py` | kb_module | `rerank.py` | rerank |
| `semantic_chunk_plugin.py` | kb_module | `semantic_chunk.py` | chunk |
| `ingest_chat_plugin.py` | kb_module | `ingest_chat.py` | parse |
| `ingest_convert_plugin.py` | kb_module | `ingest_convert.py` | convert |
| `user_manager_plugin.py` | kb_module | `user_manager.py` | add / list / remove / check-perm |

### scripts/ 脚本清单

| 脚本 | 作用 |
|------|------|
| `backup_now.sh` | 手动全量快照（zip + sha256，排除 .git 与 backups 自身） |
| `drill_now.sh` | 恢复演练（10 分钟 SLA：抽备份 → sha256 校验 → 临时还原 → 抽查 → 回滚核验） |
| `growth_cron.sh` | 成长引擎定时节拍（rag index → intake → feedback → recall → link → dashboard → 记忆写入侧） |
| `healthcheck_cron.sh` | 健康巡检定时（kb-healthcheck all → logs/health-DATE.log） |

---

## 六、frontmatter 规范

每篇笔记的 frontmatter 必填字段：

| 字段 | 必填 | 取值 | 说明 |
|------|------|------|------|
| `tags` | ✅ | `[类型, 领域]` | 类型：`moc`/`daily`/`template`/`decision`/`sop`/`lesson`/`meta`；领域对应 domain |
| `status` | ✅ | `draft` → `active` → `stable` → `legacy` → `archived` | 生命周期 |
| `domain` | ✅ | `运维`/`开发`/`安全`/`产品`/`数据`/`管理`/`综合` | 知识领域 |
| `created` | ✅ | `YYYY-MM-DD` | 创建日期 |
| `updated` | ✅ | `YYYY-MM-DD` | 更新日期 |
| `importance` | ✅ | `0.0`–`1.0` | 重要度（AI 建议 + 人工复核，重要标 1.0） |
| `kb_target` | 路由必填 | 目标路径 | PARA 区目标 |
| `kb_action` | 路由必填 | `append`/`update`/`new`/`retire` | 路由动作 |
| `kb_summary` | 路由必填 | 一句话摘要 | 用于检索 / 回忆 / 仪表盘 |

可选字段：`kb_source`（记忆来源 agent）、`aliases`（Obsidian 别名）、`phase` / `priority`（项目笔记）。

---

## 七、多 Agent 记忆摄取

kb-kit 支持摄取本机多种 agent / 记忆源进知识库（最终产物统一落到同一个 vault，Obsidian 一键展示）：

| 记忆源 | 形态 | 探测位置 | adapter |
|-------|-----|---------|---------|
| **OpenClaw** | `.md` 文件 | `~/桌面/桌面文件/openclaw1/workspace` / `~/Documents/openclaw` / `~/.openclaw` | `memory_ingest.py` |
| **Hermes** | `.md` 文件 | `~/.hermes/memories/MEMORY.md` | `memory_ingest.py` |
| **DSH（原生）** | 纯 JSON | `~/.dsh/storages/memory.json` | `memory_ingest_json.py` |
| **DSH·evolve 结晶** | 纯 JSON | `~/.dsh/storages/evolve_memory.json`（同上 schema） | `memory_ingest_json.py` |
| **project-context** | `.md` 事件记忆 | 各项目 `<项目>/.agents/memory/{MEMORY,CONTEXT,HANDOFF}.md` | `memory_ingest.py` |
| **Codex** | SQLite | `~/.codex/*.sqlite`（`thread_items` 表） | `memory_ingest_sqlite.py` |

> DSH 的原生 `memory.json` 与 dsh-evolve 的结晶 `evolve_memory.json` 是**两套不同存储**
> （实测内容零重叠），现在被**一起归一摄取**进同一个 vault——前者是会话记忆，后者是
> 自进化沉淀的技能/事实，都在 Obsidian 里统一呈现。

### 探测铁律
- **通用，绝不写死任何用户的私人路径**。探测走"标准位置 + CLI + 环境变量覆盖"
- 环境变量覆盖：`KB_OPENCLAW_HOME` / `KB_HERMES_MEMORIES` / `KB_DSH_MEMORY` / `KB_DSH_EVOLVE_MEMORY` / `KB_DSH_PROJECT_CONTEXT_ROOT`（逗号分隔多根）/ `KB_CODEX_DATA`
- **所有源最终归一到同一个 vault**，Obsidian 打开一个文件夹即见全部
- 注册表 `kb-agent.json` 是运行时状态（gitignore），不是知识库内容

### project-context 的 .agents 归一
各项目的 `.agents/memory/*.md` 散落在不同项目目录（都有同名 `MEMORY.md`），归一时用**项目根名做命名空间**：
`reference/project-context/<项目名>/<项目内相对路径>/<file>.md`，消除同名撞名；content-hash 去重；dry-run 严格只读。
`KB_DSH_PROJECT_CONTEXT_ROOT` 可指定探测根（逗号分隔多根）；缺省自动探测 `~/workspace / ~/.dsh / ~/桌面 / ~/deepseek-harness / ~/知识库`。

### 用法
```bash
# 安装时自动探测 + 交互选择
bash install.sh

# 之后随时手动重扫
kb ingest agent --setup

# 摄取（先 dry-run 预览）
kb ingest agent --dry-run
kb ingest agent

# 注册表管理
kb agent detect          # 探测本机 agent（含 DSH·evolve 结晶）
kb agent list            # 列出已注册 agent
kb agent add --name DSH --type json --json ~/.dsh/storages/memory.json --evolve-json ~/.dsh/storages/evolve_memory.json
kb agent add --name project-context --type project-context --project-context-root ~/deepseek-harness ~/桌面
kb agent enable --name DSH --enable true

# 也可用环境变量覆盖（安装后不改注册表即可）
export KB_DSH_EVOLVE_MEMORY=~/.dsh/storages/evolve_memory.json
export KB_DSH_PROJECT_CONTEXT_ROOT="~/deepseek-harness,~/桌面"
```

---

## 八、插件化架构

v2.0.0 将 KB 从"宿主系统"改造为"插件式"架构，与 Agent 适配器统一为同一套插件接口。

### 架构概览

```
用户层 (CLI)
  kb (bash) / kb.cmd (Windows)
      ↓
  kb_launcher.py        ← CLI 统一入口（兼容映射 + 动态路由）
      ↓
  plugin_registry.py    ← 插件注册中心（发现/注册/拓扑排序/分发）
      ↓
  pipeline/plugins/     ← 插件目录（4 个 Agent + 19 个 KB 模块）
      ↓
  原 pipeline 模块      ← 业务逻辑层（保持原位不变）
```

### 核心组件

| 组件 | 职责 |
|------|------|
| `plugin_base.py` | 插件基协议：`PluginBase`（抽象基类）+ `PluginContext`（依赖注入）+ `PluginMetadata`（元数据） |
| `plugin_registry.py` | 插件注册中心：发现 `plugins/` 目录 → 动态加载 → 依赖拓扑排序 → 统一分发 |
| `kb_launcher.py` | CLI 统一入口：兼容映射表（旧命令 → 插件 action）+ 动态路由 |
| `plugins/*.py` | 插件实现：薄封装层，延迟 import 原模块并调用，不重写业务逻辑 |

### 插件接口

每个插件继承 `PluginBase`，实现四个方法：

```python
class MyPlugin(PluginBase):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="my", version="1.0",
                             plugin_type="kb_module",  # 或 "agent"
                             actions=["hello"])

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx  # 接收依赖注入

    def execute(self, action: str, params: dict) -> int:
        if action == "hello":
            print("Hello!")
            return 0

    def shutdown(self) -> None:
        pass  # 可选：清理资源
```

### 扩展：新增插件

只需在 `pipeline/plugins/` 目录下创建 `.py` 文件，定义 `PluginBase` 子类，**无需修改注册中心或 CLI 启动器**：

```bash
# 创建新插件
cat > pipeline/plugins/my_plugin.py << 'EOF'
from plugin_base import PluginBase, PluginContext, PluginMetadata

class MyPlugin(PluginBase):
    def metadata(self):
        return PluginMetadata(name="my", version="1.0",
                             plugin_type="kb_module", actions=["hello"])
    def initialize(self, ctx): self._ctx = ctx
    def execute(self, action, params):
        print("Hello!"); return 0
EOF

# 立即可用
kb my hello
```

### 插件配置

`reference/plugin-config.json` 控制插件启用/禁用：

```json
{
  "rag": { "enabled": true },
  "my_plugin": { "enabled": false }
}
```

### 设计原则

- **开闭原则**：新增插件不改核心代码（注册中心、CLI 启动器）
- **适配器模式**：插件封装层通过 import 调用原模块函数，不重写业务逻辑
- **依赖注入**：插件通过 `PluginContext` 访问共享服务，不直接 import 其他模块
- **零依赖**：仅使用 Python 标准库（abc/importlib/json/logging）
- **错误隔离**：单个插件加载/执行失败不影响其他插件
- **100% 向后兼容**：原 CLI 命令、`kb-agent.json`、`growth_cron.sh` 全部不变

---

## 九、让知识库"自己跑"

把下面加进定时任务（Linux cron / Windows 任务计划）：

```bash
# 每天凌晨 4:40 跑成长引擎（命中信号 / 补链 / 回忆 / 仪表盘 / 记忆写入）
40 4 * * *  /path/to/vault/scripts/growth_cron.sh /path/to/vault

# 每周一早上 9:00 跑健康巡检
0  9 * * 1  /path/to/vault/scripts/healthcheck_cron.sh /path/to/vault
```

Windows 用任务计划调 `scripts/growth_cron.sh` 即可（需有 bash；或自建 PowerShell 版）。

`growth_cron.sh` 每天自动：
1. 重建向量索引（`rag index`）
2. 收件箱 triage 报告（`intake review`，只读）
3. 命中信号计数（`feedback ingest`）
4. 今日回忆 deck（`recall deck`）
5. 自动补链（`link apply`，每轮取分最高前 50 对，幂等）
6. 刷新治理仪表盘（`dashboard`）
7. 记忆写入侧（agent 记忆落 KB，需设 `AGENT_ROOT` / `AGENT_MEMORY` 环境变量）
8. 记忆同步（`memory/` → KB，过"五问"的沉淀灌进知识库）

**隔离设计**：每步独立隔离，某引擎带坏只记日志并继续，绝不因单步崩而中断整条流水线。

### 环境变量（可选覆盖）

| 变量 | 作用 | 默认 |
|------|------|------|
| `KB_ROOT` | vault 根目录 | 脚本所在目录 |
| `PYTHON` | Python 解释器 | `python3` |
| `AGENT_ROOT` | agent 工作区根（含 SOUL/USER/MEMORY 等核心文件） | - |
| `AGENT_MEMORY` | agent 每日记忆目录（含 `*.md` 日报） | - |
| `AGENT_SELECT` | 非交互安装时指定 agent 子集（逗号分隔） | 全选 |
| `ORNITH_API_KEY` | 本地模型 API 密钥 | - |
| `ORNITH_BASE_URL` | 本地 vLLM 地址（`http://<vllm>/v1`） | - |
| `KB_GIT_EMAIL` / `KB_GIT_NAME` | git 署名 | 全局 git 配置或占位符 |

---

## 十、常见问题

- **一定要用 Obsidian 吗？** 建议用（看图、链接、仪表盘体验最好）。但引擎本身只看 `.md`，纯命令行也能跑（`pipeline/*.py`）。
- **迁移旧知识库？** 把你的 `.md` 按 PARA 分类放进新建 vault，跑 `kb rag index` 即可。
- **Windows 报 python 找不到？** 重装 Python 并勾选 "Add python to PATH"，或把脚本里的 `python` 换成 `py`。
- **安装时跳过了 git（`--no-git`）？** 定时任务会跳过提交但照常刷新文件；想要版本管理再 `git init` 即可。
- **`kb query` 查不到结果？** 先跑 `kb rag index` 建索引。写新笔记后也要重跑。
- **巡检报标签越界？** 标签要在白名单里（见 `70-知识治理 Governance/命名约定与标签词库.md`）。或把新标签加进 `kb-healthcheck.py` 的 `TAG_WHITELIST_TOPIC`。
- **摄取 agent 记忆后巡检报死链？** v1.3.0 已修复（adapter 注入的源自由标签归一为 `[domain]`，锚点页落地裸双链）。
- **性能慢？** 笔记量超过 400 时，`link_engine` / `kb_health` 的两两比对为 O(n²)。可考虑分域运行。

---

## 十一、版本演进

详见 [CHANGELOG.md](CHANGELOG.md)。

### 未发布 — 多源记忆归一 & RSI 默认治理（2026-09-16 → 2026-09-23）

- **自我改进默认走 RSI 引擎**：`kb_engine`（T1/T2/T3 编排）/ `kb_rsi`（指标·去重）/ `kb_claim`（Layer1 声明验证）/ `kb_usage`（Layer2 真实使用）/ `kb_embed`（Layer0 语义地基）。旧 `clean.py` / `sync.py` 标 **superseded**（函数保留供摄入兼容），`feedback_loop.py` 加权核心仍被 RSI `import` 复用。**仅其独立 CLI（ingest/apply）停用**，健康巡检/补链/回忆仍按需保留。
- **多源归一**：DSH·evolve 结晶（`evolve_memory.json`）与原生 `memory.json` 一套 schema 一起归一摄取；`project-context` 归一能力**保留**（不再自动列出，可手动 `kb agent add --name project-context` 摄取）。
- **回到 4 种自动探测 agent**：OpenClaw / Hermes / DSH / Codex（`project-context` 从 `detect_agents()` 移除，摄取能力仍在）。
- **安装加固**：`install.bat` / `install.ps1` 重复参数 bug 修复（首个非 flag 参数=目标目录，其余透传，与 `install.sh` 一致）；**Obsidian 降级为可选**（缺失仅告警，不再阻断安装）；自动安装失败优雅降级（告警不阻断，仍要求 Python）。

### v2.0.0 — KB 插件化架构改造（2026-09-13）
- 将 KB 从"宿主系统"改造为"插件式"架构，与 Agent 插件统一接口
- 新增 `plugin_base.py`（插件基协议）+ `plugin_registry.py`（注册中心）+ `kb_launcher.py`（CLI 统一入口）
- 新增 `pipeline/plugins/` 目录：4 个 Agent 适配器插件 + 19 个 KB 功能模块插件
- `kb` / `kb.cmd` 启动器改为统一调用 `kb_launcher.py`，删除硬编码 case/if-elif
- 新增 `kb list` 命令列出所有插件，`reference/plugin-config.json` 控制启用/禁用
- 100% 向后兼容：原 CLI 命令、`kb-agent.json`、`growth_cron.sh` 全部不变

### v1.0.0 — 首次发布（2026-08）
kb-kit 初始交付：PARA 分区 + 成长引擎 + 本地语义检索 + 运维 + 跨平台安装器。

### v1.1.0 — Agent 对接与稳定性加固（2026-09-07）
- 新增 `kb` CLI 启动器（vault 根软链 + `~/.local/bin/kb` 全局可用）
- 修复 importance 占位符崩溃（共享 `_parse_importance()` 安全解析器）
- 修复 kb-healthcheck 误报（empty/discipline/deadlinks 噪声过滤）
- 确立"写前 checkpoint / importance 只升不降 / 不 sweep Obsidian"三条纪律

### v1.2.0 — 多 Agent 记忆摄取（2026-09-07）
- 新增 `agent_registry.py` + `memory_ingest_json.py` + `memory_ingest_sqlite.py`
- 支持 OpenClaw / Hermes / DSH / Codex 四种 agent
- 安装时自动探测 + 交互选择 + 写入 `kb-agent.json`
- `memory_ingest.py` 移除 10 处 `openclaw` 硬编码，改收 `agent_name` 参数

### v1.3.0 — 摄取越界标签修复（2026-09-07）
- 修复 DSH / Codex adapter 把源自由标签照抄进笔记 `tags:` 的 bug
- adapter 注入的标签归一为 `[domain]`（来源信息由 `kb_source` 承载）
- 补回 Codex 产物漏写的 `importance` 字段
- 模板仓与真实仓的 `reference/` 各加 `dsh.md` / `codex.md` 锚点页，死链归零

---

## License

采用 [MIT License](LICENSE)，代码"原样"提供，供学习和内部使用，可自由复制与二次分发。

---

> 📖 完整设计依据见 `template-vault/📖-知识库管理方案.md`（方案 v4.0）。
> 🚀 5 分钟上手见 `quickstart.md`。
