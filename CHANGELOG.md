---
domain: 管理
status: active
importance: 0.5
created: 2026-09-19
updated: 2026-10-16
tags: ["管理"]
---
# 📋 kb-kit 版本更新日志（CHANGELOG）

> 记录 kb-kit 工程套件的版本演进。日期格式：YYYY-MM-DD。

---

## v2.5.0 — DSH 会话日志摄取 + 本地模型蒸馏 + 记忆晋升通路修复（2026-10-16）

把 DSH 运行时的「每日记忆」从原始对话日志接进知识库，并修好长期失效的
「memory/ → PARA 正典」晋升通路。

### ✨ 新能力
- **DSH 会话日志摄取**（`session` 类型）：解析 `~/.dsh/sessions/**/session*.jsonl.zstd`，
  人类发言落 `memory/dsh-<date>-<hash8>.md`（`status: staged`，仅 `kb query` 检索层）。
- **本地模型蒸馏**：用本地 LLM（`ORNITH_BASE_URL`/`ORNITH_API_KEY`/`ORNITH_CHAT_MODEL`）
  把会话炼成声明式「决定/规则/经验/教训」，落 `memory/dshd-*.md`（`status: active`，可晋升）。
  缺配置时优雅跳过，只落原始轮。
- `agent_registry` 新增 `session` 类型分发 + `_ingest_session`（原始轮 + 蒸馏两条腿），
  `growth_cron.sh` ⑦b 自动覆盖。

### 🔴 修复：晋升通路此前**从未成功过**
- **`best_match` 自匹配**：novelty 比较在含 `memory/` 的整库索引里取 top-k，从不排除查询
  文件自身 → 每个笔记以 sim≈0.99 匹配到自己 → 恒判「与 KB 重复」→ 全部被拦。
  加 `exclude=rel` 后：拟晋升 **0 → 133 条**。
- **`memory_sync` 新增 `status: staged` 硬门**：原始会话轮只做检索层、不晋升。

### 🟠 健壮性
- **`memory_ingest_session` 幂等键修正**：由 `p.name` 改为物理文件路径 `str(p)`——
  同一 `session-<uuid>/` 目录下的 `session`/`v3`/`v4` 多版本文件会互相覆盖，导致每日重扫全量；
  且文件名用「每次运行从 1 重数的当日计数器」时，同日二次运行会**覆盖丢失**前一会话的记忆，
  已改用内容指纹命名并恢复被覆盖条目。
- **`memory_distill_session` 抗中断**：独立 state + 已有笔记指纹并入去重表 + 每 5 篇增量落盘；
  `--limit` 支持分批回填。
- **`agent_registry`**：`load/save_registry` 走 `state_manager.read_json/write_json`
  （原子写 + 规范路径 + legacy 回退）。

---

## v2.4.0 — 全代码梳理与缺陷修复（2026-10-15）

一次**逐文件全文梳理**（8 份分册 + 总册见 `docs/CODE-SURVEY.md`）之后的集中修复。
测试从 69 条涨到 **168 条**（约 1 秒全绿）；改动只落在开发仓库，未触碰生产库。

### 🔴 数据安全（会丢数据 / 违反自家写侧协议）
- **`kb clean apply` 会改写 `raw/` 不可变证据层** → `do_clean` 主循环补 `_is_raw_rel` 守卫
  （同文件其余 5 处本就有）；顺带修：空值 frontmatter 键（`url:`）不再被删。
- **`do_clean(dry=True)` 其实会写盘** → 去重段补 `dry` 守卫（否则把 `clean dry-run`
  接上真实预览时，只读命令会立刻变成破坏性命令）。
- **`kb curate run`（不带 `--apply`）也会改笔记 frontmatter** → noise/raw-only 判定
  写回加 `apply` 门控。
- **`kb sync rollback` 的 `git clean -fdq` 会删未提交笔记** → 清理前列出清单并备份到
  `backups/rollback-<ts>/`，备份不完整则整体放弃清理。
- **`kb_query apply --id X` 清空整个提案文件** → 只重写未写入的条目。
- **sqlite/opencode 适配器「同标题、新内容」被永久丢弃** → 去重键带内容指纹 +
  文件内改内容级幂等（原 tuple 键是死代码）。
- **块列表 `tags:` 被目录名派生标签替换**（`["开发","kb-kit"]` → `["40-资源库 Resources"]`）
  → 根因修在 `kb_common.load_note_full` 支持 YAML 块列表续行。
- `kb_calibrate.synth` 的 `rmtree` 加守卫（误传真实库路径不再整库删除）。

### 🟠 功能不可用 / 静默错误
- **`kb.cmd` 变量闭合符写错**（`%VAULT:~0,-1}`）→ Windows 上 `kb` 完全不可用，已修。
- **`kb tagfix` 整体崩溃**（`taxonomy.known_tags` 签名不匹配）→ 代码回流修复。
- **`kb clean --apply` 报 argparse 错**（README 与 `kb help` 都这么写）→ 启动器新增
  「旗标形态子命令」提升。
- **CLI 解析**：支持 `--key=value`；`BOOL_FLAGS` 从 13 补到 43 个（附源码扫描守卫测试）；
  短选项 `-h` 不再被改写成 `--h`；`--project-context-root`（`nargs="+"`）不再丢值。
- **`kb ingest agent --list` 不列清单而是直接写库** → 短路成只读列举。
- **适配器插件把「写入条数」当退出码**（成功写 5 条 → rc=5 破坏 `set -e`）→ 规范化为 0/1。
- **路由表指向不存在的 PARA 目录**（`30-资源 Resources` / `40-领域 Areas`）→ 改为真实目录名，
  并补 `intake_triage` 漏配的 `运维`/`数据`。
- `kb_consistency` 的忠实度判定**只认英文**，中文回答一律默认判「忠实」→ 中英词表 + 不可解析即降级。
- `sync.ingest-any` 导入不存在的 `convert_to_markdown`、domain 恒「综合」、无 checkpoint → 全修。
- `kb_meta apply --all` 写 0 个却报成功；`kb_rsi` 的 `ALERT` 抛未捕获 `ValueError`；
  curate 蒸馏关恒假条件；`kb_l4` 未接地也照样冻结（自锁）；L3 账本路径错；
  T3 的 A/B 回滚永不触发；`kb_claim` 日期约束导致配不到对；`kb_engine` 配置写死模块目录；
  `dashboard` 死链=0 假告警；`kb-healthcheck` 的 `_INDEX.md` 死链豁免不可达；
  `graph.py` 生成的 Mermaid 语法非法；`gen_kb_arch.py` 新克隆必崩 —— 均修。

### ⚡ 性能（实测）
- **查询期不再物化全库向量**：索引升到 **v5**，预存每篇 `norms`/`dl`/`avgdl`，
  余弦 **155 ms → 2.3 ms**（现役库量级），索引仅 +1.9%。旧索引自动回退，不强制迁移。
- **「增量」索引终于真增量**：mtime 快路径提到任何读盘之前 + 倒排索引零读盘重建
  → 无变更 **30 → 0** 次读盘，改 1 篇 **65 → 1** 次。
- `kb_l4.render` 不再重复全库扫描；`kb_usage`/`kb_adaptretrieve` 的事件窗口从「取最老」
  改为「取最新」（原先最新查询信号被永久忽略）。

### 🧪 测试与守卫（防回退）
- 新增 `tests/test_p0_fixes.py`（85 条）、`test_route_consistency.py`、`test_readme_plugin_list.py`。
- **评测不再污染自身指标**：`rag query --no-record` + `KB_NO_FEEDBACK=1`，
  5 处评测/对照调用点全部接上（以前跑一次黄金集会顶高再问率、反过来压低 fitness）。
- 新增 **`scripts/eval_retrieval_gold.py`**：确定性检索评测（不需要 LLM 裁判）。
- **P1-9 复核结论：不改排序。** 倒排加成看起来是量纲缺陷，实测却是排序主力 ——
  6 种改法对照（30 题×4 目标黄金集）：现状 top1 90.0% / recall@10 98.3%，
  改成乘性+IDF 加权掉到 60.0% / 55.0%。已在 `rag.py` 就地写入证据与「不要这么改」的说明。
- `kb_adaptretrieve` 明确降级为**审计清单**（其写下的「检索修正契约」目前没有运行时消费者，
  不再暗示检索已被修正），并接进 dashboard 告警使其可见。

### 🔀 路由表收敛（按语料证据）
- 4 套 `domain → PARA` 路由表原先在 `产品` / `管理` 上给出 2-3 个不同答案
  （同一篇笔记「从收件箱进来」和「从记忆晋升进来」会落到不同分区）。
- 仓库文档只定义 domain 词表与二级子域，**没有**分区映射，故以现役库知识层实测为准：
  产品 85% 落 `40-资源库 Resources`、管理 48% 落 `10-项目 Projects`（`60-运营` 仅 5.8%）。
- 据此对齐 3 处异类：`intake_triage.产品`、`memory_sync.管理`、`memory_ingest.产品`；
  并删掉 `intake_triage` 里的死配置 `生活`。**现 4 表 8 域全票一致**。
- `tests/test_route_consistency.py` 收紧为「必须全票一致 + 必须等于实测期望值 +
  必须覆盖白名单 + 目标目录必须存在」，`KNOWN_DIVERGENCE` 已删除。

### 🚀 kb-context-dsh / kb-context-hook：常驻查询 worker（单查 ~190ms→~60ms）
- 新增 `pipeline/kb_worker.py`（常驻 retriever：索引加载一次、mtime 失效重载、JSON-lines stdio）。
- 两个上下文钩子改用常驻 worker（懒 spawn + 崩溃回退 execFile + seq 匹配/FIFO 兼容）。
- 片段质量：`context` 优先于 snippet，默认截断 40→140 字；修掉字节封顶没算前缀/围栏的 bug。

### ⚡ 插件只读命令进程内快路径（query 端到端 -25%）
- `SubprocessPlugin` 加 `IN_PROCESS_ACTIONS` 白名单：只读命令（query/validate/healthcheck/
  schema check/gate check/recall deck/audit scan…）在启动器进程内 `import + main()`，省一次
  解释器启动+import（实测 ~40ms，占 query 端到端 21%）；写命令仍走 subprocess 隔离。
- 安全：import 失败/异常回退 subprocess，stdout/stderr 捕获、成功才回放（无双份输出）；
  静态护栏禁止写动作进白名单。query 输出与子进程逐字节一致，287 测试全绿。

### ⏱ 睡眠时整理的灾难性回溯修复（6 分 40 秒 → 6 秒）
- `gate._PLACEHOLDER` 正则 `^(?:...|-+|\s)*$` 里 `-+` 嵌套 `(?:)*` → 遇 `----` 分隔线后接文字
  指数级回溯（每多 2 字符 ×4）。用 faulthandler 堆栈转储定位，改线性字符类 + 回归测试。
- `consolidate` 签名/陈旧/门禁三层缓存：首跑 6 秒、二跑 2.7 秒（0 篇重算）。

### 📅 调度与双时态迁移落地
- 新增 `kb-consolidate-nightly`（04:30）+ `kb-gate-daily`（05:00）两个 openclaw cron 任务
  （isolated + announce + best-effort），gate 已用一次性任务经调度器端到端验证。
- `kb schema bitemporal --apply` 已在生产库执行（842 篇），提交 `50ebcae`，回滚点 `77dd652`。
- 修 checkpoint 的 `git add -A` 越权提交问题（改为只登记本次文件 + 记录 HEAD sha）。

### 🧠 P2 全部落地（FSRS / 睡眠整理 / claim 槽位 / 质量门禁）
- **FSRS-6 替换 SM-2**（`fsrs_core.py`，纯 stdlib）：用定义式当不变量自检 **10/10 成立**
  （`R(0,S)=1`、`R(S,S)=0.9`、`I(S,0.9)=S`）。**评分口径同时切到 1/2/3/4**（SM-2 的 1/3/4/5 会让 D 反向更新）。
  接线时修掉两个真问题：首次复习丢掉 `importance` 先验种子；残留的 `st.pop("stability")` 会删掉 FSRS 主状态。
  顺带纠正 SM-2：同日重复**不该**放大间隔（旧实现 `interval *= ease` 一天点十次会给出荒谬间隔）。
- **睡眠时整理**（`consolidate.py`）：空闲期算近重复/槽位冲突/陈旧/闸门，**只产提议不改笔记**，
  幂等 + 增量 + 有界，默认不用 LLM。
- **claim 补槽位与时间态**：`subject/relation/value/temporal_state/valid_from/valid_to` +
  `classify_pair()` —— 同槽位且时间**不相交**是**时序更新**而非矛盾（混淆会让系统去调和不存在的冲突）。
- **质量门禁三态**（`gate.py` + `source_tier/derived/generated_by`）：reject **只用于不变量**，
  噪声走 quarantine；LLM 派生内容必须挂非 LLM `source_ref`（防回灌污染）。

### 📏 Contextual Retrieval 增益量化（结论：无法证明有增益）
- 加了 `KB_CONTEXT_IN_INDEX` 开关做 A/B + **配对 bootstrap 4000 次**：
  `recall@4 Δ=+1.7pp, CI95=[0,+4.2pp]` → **不显著**，其余指标恰好为 0。
- 保留开启（方向为正、零边际成本），但**不再宣称它有依据** —— Anthropic 的数字属于长文档分块场景。

### ⚖️ 提案裁决改为本地模型自主裁决
- 新增 `pipeline/adjudicate.py` + `kb ladder adjudicate` / `kb ladder verdicts`，接进每日 cron。
- **L1 双闸**：LLM 判「能否回答该查询」（相似度 ≠ 相关性）+ **实测**判「加契约前后排名是否真的改善」，
  两闸都过才落地。实测出现 `LLM 同意但实测无改善 → 否决` 与 `相似度高但 LLM 判答不了 → 否决`。
- L2/L3 无 ground truth → LLM 判断 + 硬边界（上限/只升不降/写前 checkpoint）+ 账本 + 可回滚。
- 每一条判断都进账本（模型身份 + 理由 + 实测前后排名），人工可复查。
- 踩坑修正：本地裁判是**推理模型**（`ornith1.5-35b`），推理 token 吃掉 `max_tokens` 导致
  首版 40/50 次返回空内容 → 额度提到 3000 + 从推理文本兜底提取 + 加倍重试，空响应降到 1/15。

### 🕰 双时态字段（P1#5）
- 契约加 `valid_from` / `valid_to` / `recorded_at` / `superseded_by` / `conflict_with`，
  写清"事件时间 vs 事务时间"语义（2025–2026 时序知识图谱研究的核心结论，**零依赖**实现）。
- 新增 `kb schema bitemporal` 迁移命令：**默认 dry-run**（生产库 842 篇待迁移），
  `--apply` 才写，写前 git checkpoint，可整体回滚。

### 📐 抗 Goodhart 健康 KPI（P1#9）
- 新增 `kb health kpi`：只算**难刷**的 5 个量（两跳可达覆盖率 / 检索一次命中率 /
  预测校准 Brier / 陈旧率 / 复用率）；拿不到数据时**诚实报 N/A**，绝不用 0 冒充"测过了"。
- 易刷量（笔记数/链接数）在输出里被显式标注"仅诊断，勿当 KPI"。
- 生产库实测：**两跳可达覆盖率仅 26.0%**（1100/4228）—— 这是"有条理"的一个真实改进点。

### ⚖️ 提案裁决 + 生成器缺陷修复
- **L1**：修生成器后 29 条提案，逐条实测 → **批准 11 条**（目标进入 top-10，0 条有害）并落地。
- **L2 驳回/缓议**：776 条要改写笔记 frontmatter，无法逐条裁决、信号当天才修好、top-20 仍含污染项。
- **L3 批准 3 条**内部旋钮（低风险、有回滚态）。
- **修掉 3 个生成器缺陷**（这才是首次产出是垃圾的原因）：自动化系统提示词被当用户查询
  （44/50 来自同一段 4788 字提示词）、提案指向不该入索引的路径（28/50）、
  排序不按重问次数（问了 73 次的问题零提案）。修复后垃圾路径 28/50 → **0/29**。
- 诚实记录一次**被推翻的判断**：我一度判定"noise 层被排除是重大检索 bug"，
  继续查发现那是 curate + kb_schema **契约强制**的有意设计（`kb_index: false`），
  真正成立的只是 `LAYER_MULTIPLIER["noise"]` 是一处不可达的死配置（已加注）。

### 📊 评测加配对 bootstrap（判断差异是否真实）
- `eval_retrieval_gold.py` 新增 `--bootstrap N`（95% CI）与 `--compare baseline.json`
  （**配对** bootstrap：同批题重采样，看差异是否跨过 0）；n=30 时只信 ≥5pp。
- 自检通过（与自己对比全部不显著），并抓到自身两个 bug（`--save` 顺序、JSON int 键变字符串）。

### 🪜 L0–L5 反馈阶梯接通（26% 的代码此前**连入口都没有**）
- 实测发现：`kb_l4`/`kb_l5`/`kb_retriage`/`kb_selfweight`/`kb_meta`/`kb_adaptretrieve`
  **没有任何插件** → `kb l4` 等全是「未知命令」，用户敲不出来、调度器也无从调用。
  这是比"未调度"更根本的断点。
- 新增 `pipeline/ladder.py`（`kb ladder status/propose/run/apply`）+ 插件 + README 登记
  + `scripts/kb-ladder-daily.sh`（只产提案、不改笔记），排进 openclaw cron **每天 04:00**。
- 首次运行产出（生产库真数据，此前为零）：**L1 检索修正提案 50 条**（来自 2039 次查询中
  5.3× 的反复再问）、**L2 775 条**、L3 旋钮 3 条、L4 案卷 14 条。
- 端到端验证：一次性任务经调度器执行成功并自动删除，提案文件时间戳证明是调度器所写。

### 🔗 建链改多信号（误链代价远大于漏链）
- `link_engine` 从"仅余弦阈值"改为 **≥2 信号且至少一个结构性信号**（共享标签/同源/共引）。
- 真实库实测：建议 3107 → **2164** 条（-30%），滤掉的**全部**是 `lexical+neighbor`
  （同域+用词相近，无任何结构性证据）—— 最弱的一类。
- 依据：KG Refinement（噪声边沿图传播放大）；且 `kb link apply` 会写进笔记。

### 🧠 语义补召回通道（索引 v8）
- **实测依据**：让本地模型写"换措辞、不复用原文关键词"的查询（`tests/probe_semantic_gap.py`），
  纯词法只命中源笔记 **37.5%**，稠密向量 **94%** —— **62% 的情况只有语义能找回**。
- 新增 `pipeline/dense.py`：稀疏随机投影 4096→256（1.6 MB，可复现），向量存进索引（v8），
  增量索引复用未变文档的旧向量。
- 查询侧 **尾部槽位填充**（不是 RRF 融合）：词法前 60% 名次按构造不变，其余槽位给稠密-only。
  实测加权 RRF 会让黄金集 top1 90.0%→83.3%(w=0.3)→76.7%(w=0.5) 单调变差（与语义重排同因）。
- 双指标结果：头部 top1/rec@4/MRR **逐位不变**，改写探针 37.5%→**68.8%**（+31.2pp），
  黄金集 rec@10 98.3%→97.5%（−0.8pp，n=30 噪声内）。可用 `KB_DENSE_FRAC` / `KB_DENSE=0` 调。
- 成本：建向量一次性 113 s · 索引 7.6→9.2 MB · 查询延迟无显著变化。
- 修复：`_SafeUnpickler` 白名单补 `array`（漏登记会让 `load_index` 静默失败 → `index not found`），
  并新增载荷往返守卫测试拦住同类遗漏。

### ♻️ 去重换 bottom-k MinHash
- 每 shingle 哈希数 **64→1**；候选对加 size filter；存活候选用**精确 Jaccard 复核**。
- 真实库 800 篇实测：**5.3s/259 对/88.4% 准确 → 2.6s/263 对/100% 准确**。
- 新增 `containment` 判据区分"重复片段"（只提示不合并）与"重复笔记"。

### 🚀 生产库升级（工具 + 已执行）
- 新增 `tests/deploy.py`（dev-repo 专用）：`plan` 看差异、`rehearse` 在副本上演练、
  `apply` 执行「备份 → 复制代码 → 重建索引 → 验证」。**只部署 `pipeline/`+`scripts/`，
  跳过所有 `.md`**（模板内容 ≠ 用户笔记）；判据是「不引入新失败」而非「绝对全绿」。
- 生产库升级实测：索引 **17.5 MB(version 0) → 7.6 MB(version 7, postings)**；
  黄金集质量**逐项不变**（90.0% / 98.3% / 0.950）；冷查询 p50 **350 → 143 ms**；
  升级窗口内被修改的 `.md` 文件数 **0**（有 `find -newermt` 证据）。

### 🔁 L1 检索修正闭环打通（含实测依据）
- `rag.cmd_query` 现在会消费 `kb adaptretrieve apply` 落下的检索修正契约 —— 原来「信号→缺口→人工确认」
  之后的**最后一跳是断的**（契约写了没人读）。
- **黄金集实测（30 题）**：契约正确时 top1 90.0%→**96.7%**、recall@4 82.5%→**90.0%**、
  recall@10 98.3%→**100%**、MRR 0.950→**0.983**；而契约指向无关笔记时 top1 掉到 **76.7%**、MRR 0.883。
- 结论：机制有效，**安全性完全取决于契约质量** → 三道护栏：置信地板 `ADJUST_MIN_SIM`、
  条数上限 `ADJUST_MAX`、逃生门 `KB_RETRIEVAL_ADJUST=0`；`--json` 增加 `adjustments_applied` 便于审计；
  `apply` 保持人工确认。

### 🧹 遍历范围与发货洁净
- `kb_schema_migrate.iter_notes` 改为委托 `kb_common.iter_notes`：它原先会遍历 `pipeline/` 与 `logs/`，
  导致 `kb schema-migrate --apply` 给生成物 `pipeline/EVOLVED.md` 写 frontmatter
  （真实库作用范围 4430 → 4228 篇，排除的 202 篇全是非笔记）。
- `create_vault.STATE_IGNORE` 补上 `.kb`/`.agents`/`.trash`/`_curate`/`*.jsonl`：
  原先模板里**跑测试留下的**状态文件会被复制进每个新用户的知识库。
  新增 `TemplateCleanlinessTest` 守卫。

### 🧩 重复实现收尾（可证等价才合并，其余显式登记）
- **实测等价后合并/委托**：`kb_rsi.parse_f`→`parse_importance`、`kb_claim._parse_d`→`parse_date`、
  `rag._cos`/`kb_rsi.cos`(dict 分支)→`kb_common.cos`、`curate_prompt.load_schema`→`curate.load_schema`
  （抽样逐例/逐位验证；热路径耗时无变化）。
- **改名让语义显式**（名字撞车但不是一回事）：`kb_usage._norm_query`/`_cos_vec`、
  `embed_rerank._norm_vec`/`_cos_vec`、`kb_retriage._parse_tags_line`、
  `kb_calibrate._parse_day_epoch`。
- **配置漂移**：`memory_sync.DUP_SIM(0.92)` 与 `kb_constants.DUP_SIM(0.85)` 同名不同值（兜底 vs 阈值）
  → 改名 `DUP_SIM_FALLBACK`；`clean.ARCHIVE_DAYS` 改为 import 单一事实源；
  清掉 `memory_ingest` 两个未使用的导入。
- **不可合并的登记 + 钉住**：新增 `tests/test_intentional_divergence.py`（17 条），
  固定 tokenize 4 口径、frontmatter 4 读取器、iter_notes 2 套排除集、日期/tags/norm-cos 的
  类型与边界差异 —— 谁"顺手统一"了就会红。含配置漂移守卫（常量不得二次定义、
  契约表取值必须与代码常量一致）。
- ⚠️ 顺带发现：`kb_schema_migrate.iter_notes` 会遍历 `pipeline/` 与 `logs/`，
  即 `kb schema-migrate --apply` 会给生成物 `pipeline/EVOLVED.md` 写 frontmatter。
  属于变更写工具作用范围，已在 docstring 与登记表写明，待人工确认。

### 🗂 状态文件路径统一（5 套约定 → `.kb/state/`）
- 状态曾散在 5 处：vault 根 dotfile、`pipeline/` dotfile、`.kb/` 直下、`.kb/state/`、
  vault 根非点文件（`kb-agent.json`）。**迁移 20 个模块**统一到 `<vault>/.kb/state/<name>`。
- `state_manager` 新增统一原语：`state_path()`（写）、`read_state_path()`/`read_json()`/
  `read_jsonl()`（读，含旧位置回退）、`write_json()`/`append_jsonl()`（原子写 + 自动建目录）。
- **旧位置全部保留为只读回退**（回退表 37 项），不搬不删 —— 旧库升级后状态照常读到。
  原先的 `migrate_legacy_state`/`LEGACY_PATHS` 从无调用方（死代码），所谓迁移从未运行。
- 新增守卫 `StatePathConventionTest`：**扫全仓点文件字面量**，禁止再写旧位置。
  它在落地过程中抓出 **14 处漏改**（含跨模块硬编码路径、`except ImportError` 回退分支、
  以及一处「写点绕过了自己的路径助手」）—— 这些没有任何既有测试能发现。
- 顺带补齐原子写：`kb_query` 频率状态、`kb_selfweight`/`kb_retriage` 账本、
  `kb_engine` 配置、`curate` 状态、`kb_contradiction` 快照。

### ⚡ 索引格式 v7（tf → postings）
- 第十四轮后 pickle 口径下 **`tf` 仍占 64.6%**（6.66 MB / 70 万「文档,词」对），`df` 另占 8.5%。
- **改为词主序 `postings`**：`{词: [doc_id, tf, ...]}`（扁平，避免 35 万个 2 元素 list）；
  `df` 由 `len(postings[t])//2` 导出，不再存；打分（余弦 + BM25）**只访问命中文档**。
- **`rag.all_doc_vectors(payload)` 统一入口**：`recall_schedule` / `feedback_loop` / `memory_sync`
  原先各自 `_vec(tf_all[r], ...)`，格式一换就会崩 —— 现已收敛。
- **确定性构建**：范数改为排序后求和、postings/倒排按排序后文档序构建 →
  「增量索引」与「全量重建」**逐字节一致**（可复现基线的前提，有守卫测试）。
- 顺带修掉第十四轮的一处漏改：`memory_sync` 里残留的裸 `json.loads(idx_path.read_text())`
  在 pickle 索引上会 `UnicodeDecodeError`。
- 顺带修一处真实的浮点结合律差异（`f*(idf*w)` vs `(f*idf)*w`，1 ULP 在并列项上翻转名次）。
- 结果（clone 1530 篇，对比最初 v5 基线）：索引 **17.95 → 7.64 MB（−57%）**、
  `load_index` **189 → 75 ms**、进程内查询 **302 → 89 ms（3.4×）**、冷缓存端到端 **350 → 182 ms**；
  **15 题分数 15/15 一致**（唯一顺序差异是第 10 名的精确并列换位）、黄金集**完全不变**。

### ⚡ 索引格式 v6（先测量再优化）
- **先做耗时分解**：一次真实查询进程内 302 ms = `load_index` 189 ms + `_bm25_scores` 88 ms + 其余。
- **`_bm25_scores` 71.3 ms → 1.2 ms（59×）**：它每次查询都遍历全库「文档×词次」（≈70 万次）
  重算 `df`，而 `df` 只与 `tf` 有关。索引 v6 预存 `df`（构建期本已算过），
  并把 `idf`/文档归一因子两个循环不变量提出内层；分数逐位一致。
- **实测推翻一个想当然**：倒排 postings 从路径字符串换成整数 doc id，体积 −19%
  但解析**慢 9 ms** —— JSON 解析成本取决于 token 数量而非字节数。编码保留（省 3 MB），
  但不再指望它提速。
- **真正的杠杆是格式**：同一 payload `pickle.load` 70 ms vs `json.loads` 161 ms（2.3×），
  体积还小 20%。索引 v6 主格式改为 **pickle**，仍能读旧 `df_idf.json`（不强制迁移）。
- **安全**：受限 `_SafeUnpickler` 只放行内置容器/标量与白名单（`collections.Counter`
  是 dict 子类，`isinstance` 检查会漏过 —— 实测踩到）；写入「先原子落 pkl，成功再删旧 json」，
  中途失败不会出现无索引空窗。已反向验证：放开白名单 → 恶意 pickle 真的执行 → 测试变红。
- **收敛 9 处写死的索引路径**（含 `feedback_loop` 绕过 `rag.load_index` 直接读 JSON、
  shell 巡检脚本、6 个测试）到 `rag.index_file()` / `rag.load_index()`。
- 结果：索引 **17.95 MB → 10.32 MB（−42.5%）**；`cmd_query` **302 → 123 ms**；
  端到端冷缓存 **350 → 217 ms**；**15/15 查询命中与分数完全一致**、
  黄金集 **top1 90.0% / recall@10 98.3% / MRR 0.950 逐项不变**。

### 🧩 重复实现收敛（只收敛可证明等价的）
- **CJK 字正则 9 份 → 1 份**：9 个逐字相同的 `re.compile(r"[\u4e00-\u9fff]")`（散在 7 个文件，
  `kb_common` 内部还重复一份）归一到 `kb_common.CJK`。
- 删除死函数 `kb_common.dedup_check`（无调用方，且内联重复实现了 `norm`），共 61 行。
- **状态写入原子化**：新增 `kb_common.atomic_write_text`；`kb_meta`（状态+配置）、
  `user_manager.save_users`、`agent_registry.save_registry` 由直接 `write_text` 改为原子写
  （写一半崩溃不再留下半截文件），`state_manager._atomic_write` 改为委托同一实现。
- **明确不合并**：`tokenize`（5 种切分口径）、`cos`、日期解析（7 份）、tags 解析、
  配置加载（3 套策略）、`iter_notes`、状态 load/save 包装 —— 语义确有差异，合并会改行为。
- 顺带修一个潜伏缺陷：`kb_claim` 换用共享 `CJK` 后缺 import，测试未覆盖该路径，
  直接调用 `_signature()` 才暴露 `NameError`，已修。

### 🧪 开发测试环境（`tests/testenv.py`）
- 新增可复现的验证场地：`build` / `check` / `status` / `clean`，产物在 `.testenv/`（gitignore）。
  - `fresh/` 安装验收库（`create_vault` 装出来，`kb verify` 必须全过）
  - `synth/` 合成库（300 篇有效笔记 + 黄金集 + 索引）→ 全链路冒烟
  - `clone/`（`--from-vault`）真实库副本 + dev 代码 + 重建索引 → 检索**质量**与真实规模**性能**基线
- **隔离铁律**：所有子进程经 `run()` 发起并强制 `KB_ROOT` 指向沙箱；构建目标落在保护路径
  （`$KB_ROOT` / 现役库）内一律 abort。由 `tests/test_testenv.py` 守卫。
- `check --perf` 一次给出：单测 + 安装验收 + 合成库链路 + 真实库质量 + 两档性能基线。

### ✅ 验收器（`kb verify`）
- **修 P0 级缺陷：验收错了对象。** 三项检查以裸 `kb` 作命令（PATH 符号链接指向另一个库）
  且不传 `--root` —— `kb verify --root <A>` 实际测量的是另一个知识库 B 的数据。
  改为用**目标库自己的** `pipeline/kb_launcher.py` + 显式 `--root`；目标不是 kb-kit 库时明确报错。
- 活库必然失败的两项改为非阻断：缺 `tests/` → **SKIP**（新增 `--strict` 供 CI）；
  结晶产出类断言在**从未启用结晶**时 → **N/A**（真跑过却无产出仍 FAIL）。
- 效果：`create_vault` 出的新库从「3 项失败」变为**全部通过**；模板库自带内容问题也已修
  （孤儿 `如何使用.md` 接进首页；管理方案文档压缩到自身颗粒度上限内，未改阈值）。

### 🩺 巡检
- **死链检测不再把代码示例当链接**：写文档讲双链语法时的 `` `[[名]]` `` 曾被判死链。
  根因修法（先剥掉代码块与行内 code span），而不是继续往噪声词表里补特例。

### 📖 文档
- README：插件清单 24 → **32**（实测 4 agent + 32 模块）、命令参考补 13 个已实现工具、
  测试条数 66 → 168、补 `--no-record` / `eval_retrieval_gold.py` 等说明；
  上述两处一致性由 `test_readme_plugin_list.py` 守卫。

### ⚠️ 已知未决（需人工拍板）
- `kb_adaptretrieve` 的 `boost` 是否要真正影响检索（要先有评测支持）。
- 状态文件 5 套约定未统一；`json.loads` 186 ms 与 `_bm25_scores` 76 ms 的索引编码优化未做。

---

## v2.3.0 — OpenCode 记忆源 + 治理巡检作用域收敛（2026-09-28）

### 🆕 OpenCode 记忆源（第 6 种 agent）
- 新增 `pipeline/memory_ingest_opencode.py`：读取 `~/.local/share/opencode/opencode.db`
  的 `session`/`message`/`part` 表，按会话聚合 user/assistant 交互，产出带
  `kb_source: opencode` 的交互记录。
- `agent_registry`：新增 `opencode` 类型（`detect_opencode` 探测、`--opencode-db` 注册、
  `_ingest_opencode` 摄取分发）；`reference/opencode.md` 锚点页承接 `[[opencode]]` 双链。
- 修复 SQLite/OpenCode adapter 的 `--dry-run` 会落盘的问题（现只计数、不写文件/状态）。

### 🧹 治理巡检作用域收敛（消除噪声告警）
- `raw/`（不可变来源）、`90-归档`（已退役）、`/kb-kit/`（嵌套仓）、生成产物
  不再计入 死链 / 超长 / 新鲜度 巡检；`legacy` 状态视为可检索（保留知识）。
- `kb_common.EXCLUDE` 增 `.codeartsdoer`；`check_overlong` 跳过已分块产物与已有
  `-index.md` 的聚合页。
- 效果：死链 397→≈0、超长未分块 124→7、非可检索 61→2、`kb validate` 硬错误 0。

---

## v2.2.0 — 全生命周期优化（分诊→清洗→检索→连接→复习→治理→归档）（2026-09-27）

按「收件→归档」全流程优化，零依赖（纯标准库）、无损、幂等、人工在环。

### 🆕 P0 来源权威 + 新鲜度 + 混合检索
- `kb_common`：`retrievable_status`（仅 active/stable 可检索）、`freshness`/`is_stale`
  （`review_after` 到期或 updated 超 180 天）→ `index_excluded` 纳入状态权威。
- `rag`：**混合检索**（TF-IDF 余弦 + BM25，`HYBRID_ALPHA` 加权）；`kb query --exclude-stale`。
- `kb healthcheck freshness` 子命令；`dashboard` freshness 告警。

### 🆕 P1 SM-2 式回忆 + 受控词表
- `recall_schedule`：`interval/ease/reps`（SM-2），`kb recall mark --grade again|hard|good|easy`，
  单日上限 `DAILY_CAP` 负载均衡；旧状态自动迁移。
- `reference/taxonomy.json` + `taxonomy.py`：受控 `domain/category/status` + tag 层级 + 别名；
  `kb clean taxonomy [--apply]` 归一标签（别名→规范、扁平→`group/leaf`，默认 dry-run）。

### 🆕 P2 链接类型/MOC + 墓碑/lineage
- `link_engine`：建议带 `kind`（cross/concept）；`apply` 分组写入；`kb link moc` 生成 `_MOC.md`。
- 合并/去重：目标笔记声明别名（旧 `[[名]]` **重定向**）+ 写**墓碑**（`redirect_to`/`merged_from`）
  + lineage 记入 `.kb/state/lineage.jsonl`；`clean report` 增 TTL 归档建议。

### 🐛 修复
- `validate` 跳过清单改为统一 `is_generated_report`（修 `_MOC.md` 等产物导致的误报硬错误）。
- `intake` 插件补 `move/trash` 动作；搬运不覆盖同名（改名避让）；`clean.do_chunk` 幂等且不写 `raw/`；
  报告产物不入检索/补链/分块；`feedback` 命中计数衰减、跳过生成产物。

### 🧪 测试
- `tests/` 扩到 66 条（rag 混合/来源、清洗去重/分块、taxonomy、SM-2、P2 墓碑/lineage 等）。

---

## v2.1.0 — 架构加固 · 跨平台修复 · 文档校正（2026-09-27）

一次系统性排障与收口：修复 P0–P3 共 20 类问题，补齐 Python 回归测试，并按当前实现重写文档。

### 🐛 修复（P0 功能性）
- **全局 `kb` 软链自指**：`kb` 启动器现跟随符号链接解析自身真实目录（此前经 `~/.local/bin/kb`
  调用会把 vault 解析成 `~/.local/bin`）。
- **Windows 备份**：新增跨平台 `pipeline/backup.py`（zip + sha256），`kb backup`/`kb.cmd` 不再调用
  `.sh`；`scripts/backup_now.sh` 改为兼容包装。
- **OpenClaw 注入插件配置接线**：改为经 `api.pluginConfig` 读取宿主配置，schema 与实现对齐；
  移除硬编码私人路径。
- **DSH `config.js` 候选路径**：修正越界一级的 `../../../template-vault`。

### 🐛 修复（P1 数据一致性）
- **增量索引**：正确清理 `kb_action=retire` 的归档笔记（此前会残留）。
- **查询缓存**：缓存键纳入 `context`/`answer`；命中缓存仍记录反馈（不再短路 `agent_hits`）。
- **`StateStore`**：改 `os.replace` 原子覆盖（修复 Windows 第二次写入即失败）。
- **双 hook 语义统一**：OpenClaw 版补齐 per-session 去重、UTF-8 字节封顶、topN 守卫、闭合围栏。

### 🐛 修复（P2 安装/模板）
- `verify.py` 仅硬性要求机制文件，示例内容降级为提示；保证 `kb-agent.json` 总被写入。
- 清理指向已删文档的悬空引用（`DEPLOY.md`/README 等）。
- 不再把运行时 `vector index/` 复制进新库；自检显示真实文档数。
- 快捷卡与知识库首页兜底生成且引用自洽。

### 🐛 修复（P3 架构）
- 接线 `kb_query`（`kb compile`）与 `kb_rsi`（`kb rsi`）；研究层模块显式登记 + 可达性守卫。
- 插件发现：注册模块内**所有**具体 `PluginBase` 子类；跳过下划线辅助模块，消除噪声告警。
- `kb`/`kb.cmd` 不再追加 `--root`：目标库优先级 `--root` > `KB_ROOT` > 脚本目录。
- `kb help` 与路由表对齐守卫。

### 🧪 测试
- 新增 `tests/`（stdlib unittest，25 条）：rag 增量/缓存、状态原子写、verify、create_vault、
  插件发现、可达性、help 对齐、`--root` 优先级。
- 修正 DSH/OpenClaw 插件测试的路径与脆弱断言。

### 📖 文档
- 按当前实现重写 `README.md`、`quickstart.md`、`📖-知识库管理方案.md`、SOP 等，
  统一到「插件注册中心 + 已接线成长引擎 + 研究层 internal」的真实架构。

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
- **反馈阶梯 L4 宪法层·人工裁判（`kb_l4.py` · M4）**：让"反馈阶梯本身"可被真人所裁判——
  L0–L3 各自优化一个*代理信号*（会 Goodhart 失真），L4 由真人逐条裁决低层 auto 调整是否真正确：
  - 裁决值：`correct` / `wrong` / `ignore`（`ignore` 仅记档，不进信任度分母）
  - 信任度：每 rung = `correct/(correct+wrong)` ∈[0,1]；`docket` 汇编四 rung 已应用·待裁清单
  - 宪法 veto：连续负裁决（`wrong` 尾部连续 ≥`L4_FREEZE_CONSEC=3` 且样本 ≥`L4_MIN_SAMPLE=5`）
    → 冻结该 rung（写 `.kb_l4_config.json` + git checkpoint 可回滚；冻结=进入人工复核，
    **须人工 `unfreeze`** 解除）
  - 铁律：外部锚=真人显式裁决（`judge` CLI 逐条传入）——**最强锚**，非 rung 自述"我有用"；
    接地阀 P0-1 未接地→不*自动*冻 rung（闭锁系统自动冻只会更锁死）但真人裁决照常记录 ·
    raw/ 不改 · best-effort 降级（无账本→信任 unknown·不冻结）
  - CLI：`docket/report/judge/freeze/unfreeze/status`
- **反馈阶梯 L5 元策略层（`kb_l5.py` · M5）**：站在 L4 宪法之上——L4 裁"对不对"（真人裁决），
  L5 学"值不值"（阶梯到底帮到库了吗）：
  - 信号=**真实结果趋势**（外部锚 = `external_inflow` + `cross_ratio`，非阶梯自打分防 Goodhart）；
    每 rung 的"活跃期"跟着来的是库变好还是变差 → running score ±单步（`L5_STEP=0.25`，score∈[-1,1]）
  - 归化成每 rung **激活权重** ∈`[L5_WEIGHT_FLOOR=0.05, 1]`（绝不归零·保留探索·防阶梯塌缩）；
    `propose` 算权重（只读），`apply` 才发布 `.kb_l5_policy.json` + checkpoint
  - 有界：≥`L5_MIN_SAMPLE=3` cycle 才有参考（样本少→权重中性，绝不拍脑袋）；接地阀 P0-1 闭库→
    冻结内部自学习（记 cycle 日志但不加分，不给回声室加杠杆）
  - 铁律：外部锚最硬·有界·人工在环（`train` 只学不强制·`apply` 才发布·调度器读 policy）·raw/ 不改·best-effort 降级
  - CLI：`report/train/propose/apply/status`（`train` 为主回路：记 cycle→score±步长）

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
