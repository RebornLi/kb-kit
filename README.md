---
domain: 管理
status: active
importance: 0.5
created: 2026-09-19
updated: 2026-09-27
tags: ["管理"]
---
# 🏠 知识库一键套件（kb-kit）

> 把「一套会自成长的 Obsidian 知识库」落成**别人也能一键搭建**的工程：
> 一条命令 → 得到一份**已接线好的知识库**，自带本地语义检索、收件箱 triage、
> 命中反馈、孤岛补链、间隔回忆、健康巡检、备份、多 Agent 记忆摄取。

**一句话**：你只管往收件箱丢想法；引擎帮你**清洗、路由、补链、回忆、巡检、备份**。

- **零依赖**：引擎只用 Python 标准库，**不需要 pip install**（Python 3.8+）
- **跨平台**：`install.sh`（Linux/macOS）、`install.ps1` / `install.bat`（Windows）
- **插件化架构**：功能模块与 Agent 适配器统一为插件，放进 `pipeline/plugins/` 即被发现，无需改核心
- **多 Agent**：自动探测并摄取 OpenClaw / Hermes / DSH / Codex / OpenCode 的记忆进知识库
- **本地优先**：检索、索引、回忆调度全部本地完成，数据不出机器

---

## 目录

- [一、快速开始](#一快速开始)
- [二、装好之后怎么用](#二装好之后怎么用)
- [三、命令参考](#三命令参考)
- [四、架构](#四架构)
- [五、目录结构](#五目录结构)
- [六、frontmatter 规范](#六frontmatter-规范)
- [七、多 Agent 记忆摄取](#七多-agent-记忆摄取)
- [八、让知识库「自己跑」](#八让知识库自己跑)
- [九、开发与测试](#九开发与测试)
- [十、常见问题](#十常见问题)
- [License](#license)

---

## 一、快速开始

### 前提
- **Python 3.8+**（Windows 安装时勾选 “Add to PATH”）
- **Obsidian**（可选，免费的笔记 GUI；纯命令行也能用）

### 安装（三平台等价）
```bash
# Linux / macOS
bash install.sh
# Windows：双击 install.bat，或 PowerShell 里 .\install.ps1
```
安装器会：环境自检（`scripts/precheck.py`）→ 复制模板（`create_vault.py`）→
探测本机 Agent → 建首份向量索引（demo）→ `git init` → 注册全局 `kb` → 打开 Obsidian → 自检（`scripts/verify.py`）。

也可直接调引擎（等价、全平台）：
```bash
python3 create_vault.py --vault "我的知识库"          # 指定目标目录
python3 create_vault.py --vault kb-kit --no-demo      # 不跑 demo（不建索引）
python3 create_vault.py --vault kb-kit --no-global-kb # 不注册全局 kb
```

### 试检索
```bash
kb query "怎么备份知识库"
```

---

## 二、装好之后怎么用

| 你想做 | 敲什么 |
|--------|--------|
| 提一个问题 | `kb query "你的问题"` |
| 写了新笔记后让它能被搜到 | `kb rag index` |
| 看收件箱要怎么整理 | `kb ingest` |
| 检查知识库健康度 | `kb healthcheck` |
| 今天该复习哪些 | `kb recall` |
| 生成治理仪表盘 | `kb dashboard` |
| 备份 | `kb backup daily` |
| 看所有插件与状态 | `kb list` |

> `kb`（Windows 用同目录 `kb.cmd`）默认以**自身所在目录**为知识库；`kb` 会被安装器
> 注册成全局命令（`~/.local/bin/kb` 或加入 PATH）。需要操作其它库时用 `--root <路径>`
> 或设环境变量 `KB_ROOT`（优先级：`--root` > `KB_ROOT` > 脚本所在目录）。

---

## 三、命令参考

统一由 `pipeline/kb_launcher.py` 路由到插件注册中心（`COMPAT_MAP` 兼容旧命令名）。

**检索**
- `kb query "问题" [--top N] [--json] [--answer] [--domain X] [--content-type T] [--tags a,b] [--date-from D] [--date-to D] [--exclude-stale] …`
- `kb rag index`　重建/增量更新本地向量索引

**摄入**
- `kb ingest`　收件箱四维 triage（只读，写 `intake_triage.md`）
- `kb ingest move`　按 `kb_target` 路由收件箱（写操作；合并时写墓碑/别名/lineage）
- `kb ingest trash`　归档收件箱（写操作）
- `kb ingest agent`　按注册表摄取 Agent 记忆进 KB

**Agent 管理**
- `kb agent detect | list | add --name N --type T | enable --name N --enable true | setup | ingest`

**成长引擎**
- `kb feedback [ingest | apply | hit --query Q --hit PATH --useful true|false]`
- `kb link [suggestions | apply | moc]`　（`moc` 生成知识地图 `_MOC.md`）
- `kb recall [deck | mark --grade again|hard|good|easy | status]`　（SM-2 式间隔重复）
- `kb memory-sync [review | promote]`
- `kb classify "<文本>"`　（需要输入文本；用于自动分类建议）
- `kb graph`　知识图谱构建

**治理**
- `kb healthcheck [all | summary | deadlinks | skeletons | tags | empty | frontmatter | discipline | overlong | orphans | freshness]`
- `kb validate [--strict]`
- `kb dashboard`
- `kb clean [dry-run | apply | chunk | report | refine | taxonomy | repair]`
  - `refine` = 结构化+去重→分块→重建索引→校验　·　`taxonomy [--apply]` = 标签归一（受控词表/层级）
  - `repair [--apply]` = 修历史分块残留（未插值占位符 → 实际标题；分块索引页打 `kb_layer: index` 标）
- `kb user [add | list | remove | check-perm]`

**知识结晶（空闲本地 Agent 把日志炼成可调用正典）**
- `kb curate plan`　只读：选出最该结晶的笔记 + 本地模型可用性
- `kb curate run [--apply] [--limit N] [--max-seconds S] [--model M]`　跑结晶（默认只出提案）
- `kb curate verify [--batch ID]`　离线重校验提案（接地/守恒/引用三关，不调模型）
- `kb curate report [--json]`　结晶进度与质量指标（正典覆盖率、压缩比、接地率）
- `kb curate review [--limit N]`　列出待人工裁决的低置信提案

**原记忆（证据层，按需调用）**
- `kb raw list [--json]`　证据层清单（`raw/` 网页抓取 + `memory/` Agent 日志，同内容副本合并）
- `kb raw show <ID> [--full] [--json]`　取**原文真实内容**（不是索引摘要）
- `kb raw find "关键词" [--limit N]`　在证据层**原文全文**里搜索（不是索引检索）
- `kb raw path <ID>`　只解析原文件路径（供脚本/Agent 管道消费）

**同步**
- `kb sync [dry-run | apply | rollback | history | ingest-any]`

**其它**
- `kb backup [daily | weekly]`　全量快照（zip + sha256，跨平台）
- `kb list`　列出所有插件及状态
- `kb help`　帮助

**研究层（可选，RSI）**
- `kb rsi [--json] [--verbose]`　RSI 只读探针（改进建议）
- `kb compile query --query Q | status | apply --all`　查询→知识 正回路（写提案，人工在环）

---

## 四、架构

```
kb / kb.cmd ──► pipeline/kb_launcher.py ──► PluginRegistry ──► plugins/*.py
 (bash/cmd)         (COMPAT_MAP 路由)        (发现/注册/分发)      (业务插件)
                                              │
                                              ├─ agent_registry ─ memory_ingest*.py（多 Agent 记忆摄取）
                                              └─ SubprocessPlugin ─ rag.py / intake_triage.py / …（业务模块）
```

- **入口**：`kb`（bash）/ `kb.cmd`（Windows）→ `pipeline/kb_launcher.py`。只解析参数、查
  `COMPAT_MAP`、向注册中心分发；`--root` 优先级见上。
- **注册中心**：`pipeline/plugin_registry.py` 扫描 `plugins/`，动态 `import` 所有具体
  `PluginBase` 子类，按依赖拓扑排序初始化，按名分发动作，错误隔离。
- **插件协议**：`pipeline/plugin_base.py`（`PluginBase` / `PluginContext` DI / `PluginMetadata`）。
  KB 模块插件普遍用 `plugins/_subprocess_plugin.py` 封装原脚本（subprocess，保持原 CLI 100% 兼容）。
- **检索**：`pipeline/rag.py`（**混合检索**：TF-IDF 余弦 + BM25，中文 unigram+bigram 兜底，免 jieba）。
  支持增量索引、查询缓存、同义词扩展、倒排索引加成、rerank、8 种过滤器、`--json`、
  `--exclude-stale`；**来源权威**：仅 `status∈{active,stable}` 入索引（draft/archived/legacy 不检索）。
- **知识分层与降级**（`kb_layer`，`kb_common`/`rag.py`）：笔记按可信度分层并影响排序 ——
  `canon`（Agent 结晶后的正典）**提升 1.25×**；`raw`（原记忆/网页抓取证据层）**降级 0.72×**；
  `index`（分块索引页/目录页）**不入索引**。同一根文档的碎片按 `chunk_of` 链归并，
  每个根家族最多出 2 条命中（防「整篇碎片墙」顶掉其它主题）。`--include-raw` / `--include-stubs`
  可临时取消降级做排查。
- **知识结晶**：`pipeline/curate.py`（**空闲本地 Agent** 把日志/碎片炼成结论优先的正典）。
  Agent 输出结构化 JSON（`verdict/title/sections/facts/sources/confidence`），代码守三关：
  **接地关**（`facts` 逐条回原文核对，未接地一律丢弃）、**信息守恒关**（正典/原文长度比落在
  `[4%,65%]`，12% 以下只警告）、**引用关**（`sources` 必须可达）。写回 = 原位替换为正典，
  原文按 ID **保号移入 `raw/_curated/<原路径>`**，并写 `source_ref` 与 lineage；
  低置信（<`CONF_MIN`）不自动写回，进人工裁决队列。`kb raw show "<source_ref>"` 一键回原文。
- **原记忆可达性**：`pipeline/kb_raw.py`（证据层 `raw/` + `memory/` 的 `list/show/find/path`）。
  这是「必要时调用原记忆文件」的落地：知识层只放结晶内容，但每条内容都能一键回到一手原文。
- **清洗与去重**：`pipeline/clean.py`（结构化归一 / **正文指纹去重** / **MinHash+LSH 近重复候选** / 递归分块+父索引+contextual header / `refine` 编排 / `taxonomy` 标签归一 / `repair` 修历史残留）。
  分块护栏：分块索引页、空壳指针页、`raw/` 证据层**绝不二次分块**（杜绝 `-p2-p2` 级联）。
- **生命周期**：`kb_common` 提供 `freshness`/`review_after`/`is_stale`；合并/去重写**墓碑（redirect_to）+ 别名重定向 + lineage**（`.kb/state/lineage.jsonl`）。
- **回忆**：`pipeline/recall_schedule.py`（SM-2 式 `interval/ease/reps` + 单日上限负载均衡）。
- **Agent 注入插件**（独立包，不在 pipeline 内）：
  - `kb-context-dsh/`（DSH）：`agent/pre-step` 钩子自动注入检索命中 + 显式 `kb_query`（可 `full=true`
    取整篇正典）与 `kb_raw`（按 `source_ref` 取一手原文）两个模型工具
  - `kb-context-hook/`（OpenClaw）：`before_prompt_build` 钩子自动注入
  两者都：只对用户回合跑、失败静默跳过、fenced 边界包裹、字节封顶、同会话命中集去重。
- **安装引擎**：`create_vault.py`（复制模板 → 接线 Agent → demo 建索引 → git → 全局 `kb`
  → 首页/快捷卡兜底 → 自检）。

> **研究层**：`pipeline/` 下另有一批 RSI/反馈阶梯模块（`kb_engine`、`kb_l4/l5`、`kb_meta`、
> `kb_selfweight`、`kb_claim`、`kb_usage` 等）。它们只有 `kb_rsi` / `kb_query` 暴露了 CLI
> 入口，其余作为内部研究层，由 `tests/test_reachability.py` 显式登记与管理。

### 插件清单（`kb list`）

- **Agent 适配器（4）**：`agent_registry`、`agent_md`、`agent_json`、`agent_sqlite`
- **KB 模块（24）**：`rag`、`intake`、`feedback`、`link`、`recall`、`healthcheck`、
  `health_metrics`、`dashboard`、`clean`、`validate`、`sync`、`memory_sync`、`classify`、
  `graph`、`rerank`、`semantic_chunk`、`ingest_chat`、`ingest_convert`、`user_manager`、
  `compile`、`rsi`、`backup`

---

## 五、目录结构

```
kb-kit/
├── install.sh / install.ps1 / install.bat   # 三平台安装器（都调 create_vault.py）
├── create_vault.py                          # 安装引擎
├── gen_kb_arch.py                           # 架构图（draw.io XML）生成
├── scripts/                                  # precheck.py / verify.py / setup_obsidian.py / backup_now.sh
├── tests/                                    # Python 回归测试（stdlib unittest）
├── kb-context-dsh/                           # DSH 注入插件（src/{index,config,kb,inject}.js）
├── kb-context-hook/                          # OpenClaw 注入插件（index.mjs/config.mjs/inject.mjs）
├── openclaw-skill/kb-query/SKILL.md          # OpenClaw skill：先 kb query 再回答
└── template-vault/                           # 复制到目标库的模板
    ├── kb / kb.cmd                           # 启动器
    ├── pipeline/                             # 引擎（插件注册中心 + 业务模块 + plugins/）
    ├── scripts/                              # growth_cron.sh / backup_now.sh / drill_now.sh / healthcheck_cron.sh
    ├── reference/                            # 参考与插件配置（plugin-config.json 等）
    └── 00-… 90-…                             # PARA 分区 + 治理层
```

---

## 六、frontmatter 规范

| 字段 | 必填 | 取值 |
|------|------|------|
| `tags` | ✅ | `[daily]/[moc]/[template]/[decision]/[领域]` |
| `status` | ✅ | `draft→active→stable→legacy→archived` |
| `domain` | ✅ | 运维/开发/安全/产品/数据/管理/综合 |
| `category` | ✅ | `project/experience/reference/sop/meta`（英文 token） |
| `created` / `updated` | ✅ | `YYYY-MM-DD` |
| `importance` | ✅ | `0.0–1.0` |
| `kb_target` | 路由必填 | 目标路径 |
| `kb_action` | 路由必填 | `append/update/new/retire`（`retire` = 归档，不参与检索） |
| `kb_summary` | 路由必填 | 一句话摘要 |
| `review_after` | 可选 | `YYYY-MM-DD`，到期视为陈旧（freshness 过滤） |
| `aliases` | 可选 | 别名列表；合并/去重时旧 `[[名]]` 经此**重定向** |
| `redirect_to` | 可选 | 墓碑指向的新笔记（合并后归档层保留） |

`domain`（领域，中文，“这是哪一行”）与 `category`（笔记类型，英文 token，“这是什么类型”）
是两个正交维度。`kb validate [--strict]` 做只读校验；`kb clean chunk` 分块（递归+父索引）；
`kb clean taxonomy` 按 `reference/taxonomy.json` 归一标签（别名/层级）。

---

## 七、多 Agent 记忆摄取

`kb-agent.json`（运行时状态，gitignore）记录要摄取哪些 Agent。安装时自动探测本机存在的
OpenClaw / Hermes / DSH / Codex / OpenCode 并注册；可手动增删：

```bash
kb agent detect                 # 探测本机 Agent
kb agent list                   # 列出已注册
kb agent add --name dsh --type json --json ~/.dsh/storages/memory.json
kb agent add --name opencode --type opencode --opencode-db ~/.local/share/opencode/opencode.db
kb agent enable --name dsh --enable false
kb ingest agent --dry-run       # 预览摄取（只读）
kb ingest agent                 # 实际摄取
```

来源标记 `kb_source` 记为实际 Agent 名。探测用「标准位置 + CLI + `KB_<AGENT>_<FIELD>`
环境变量」覆盖，绝不写死私人路径。

---

## 八、让知识库「自己跑」

把定时任务指向模板自带的节拍脚本（Linux/macOS）：

```bash
40 4 * * *  /path/to/vault/scripts/growth_cron.sh /path/to/vault
```

`growth_cron.sh` 每轮独立隔离（单步失败不中断整条流水线），依次：
`rag index` → `intake review` → `feedback ingest` → `recall deck` → `link suggestions` →
`dashboard` → 记忆写入侧（`memory_ingest`）→ 注册表摄取（`agent_registry ingest`）→
`memory_sync promote`。

> `growth_cron.sh` 只做只读治理与索引刷新；改笔记的 `kb link apply` / `kb feedback apply` /
> `kb ingest move` 保留人工执行（写前先 `--dry-run`/`review` 看清单）。

> Windows 无 bash 时，`growth_cron.sh` 不适用；可分别用 `kb` 子命令自行编排任务计划。

---

## 九、开发与测试

- **Python 回归**：`python3 -m unittest discover -s tests`（66 条；覆盖 rag 增量/缓存/混合检索/来源权威、
  状态原子写、verify、create_vault、插件发现、可达性、help 对齐、`--root`、清洗去重/分块、taxonomy、SM-2 回忆、墓碑/lineage）。
- **DSH 插件**：`cd kb-context-dsh && npm test`（`test.mjs` + `load-probe.mjs`，无需 DSH host）。
- **OpenClaw 插件**：`cd kb-context-hook && node scripts/test.mjs`。
- **架构图**：`python3 gen_kb_arch.py`。

---

## 十、常见问题

- **`kb` 命令找不到？** 重启终端；或在库目录内用 `./kb`（Windows：`kb.cmd`）。
- **搜不到结果？** 先 `kb rag index` 建索引；写了新笔记后也要重跑（增量）。
- **想摄取别的 Agent 记忆？** `kb ingest agent`（先 `--dry-run` 预览）。
- **看全部功能？** `kb help` / `kb list`。

---

## License

[MIT](LICENSE)
