# kb-kit-pure 架构梳理与优化方案

> 调查对象：`/home/mushan/workspace/kb-kit-pure`（模板仓库，HEAD `5c9bd41`，281 tracked files，157 .py）
> 调查时间：2026-10-19 · 调查方法：codegraph 调用链追踪 + 关键模块源码精读 + git 热点统计

---

## 一、项目定位

kb-kit-pure 是一个 **纯标准库 Python 实现的 Obsidian 风格知识库引擎**（无 `requirements.txt` / `pyproject.toml`，零外部运行时依赖）。它把"碎片化记忆 → 结构化正典"的链路工程化：

```
原始证据(raw/) + 代理日志(memory/)  →  清洗去重  →  LLM结晶  →  正典层(canon)  →  混合检索
        证据层                              清洗层          结晶层         知识层           检索层
```

它同时是一个 **可分发的 vault 模板**：`template-vault/` 镜像了生产 vault 的结构，用户克隆后 `python3 create_vault.py` 即可初始化自己的知识库。项目内还嵌套了三个 DSH 生态子项目（`kb-context-dsh`、`kb-context-hook`、`openclaw-skill`），用于把知识库注入 DSH 会话上下文。

---

## 二、架构总览（模块图）

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CLI 入口层                                                              │
│  kb / kb.cmd (bash launcher)                                            │
│    │  exec kb_launcher.py                                               │
│    ▼                                                                     │
│  kb_launcher.py (545行)  ── COMPAT_MAP (~85条 tool+sub→plugin路由)       │
│    │  BOOL_FLAGS / MULTI_FLAGS 参数解析                                  │
│    ▼                                                                     │
│  PluginRegistry (plugin_registry.py, 349行)                             │
│    │  discover() 扫描 plugins/ → 动态 import → 拓扑初始化 → 按名分发      │
│    │  _load_config() 读 reference/plugin-config.json (25条)              │
│    │  _is_enabled() 判断启停 · 错误隔离                                   │
│    ▼                                                                     │
│  plugins/ (39个业务插件 + _subprocess_plugin.py + __init__.py)           │
│    │                                                                      │
│    ├── SubprocessPlugin (默认路径) ──子进程隔离──→ 引擎模块 main()       │
│    └── IN_PROCESS_ACTIONS (快路径) ──同进程直调──→ 引擎模块函数           │
│         (import失败/异常时自动回退到子进程)                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  引擎层 (template-vault/pipeline/，78个.py，~29000行)                     │
│                                                                          │
│  ┌─ 公共基础 ─────────────────────────────────────────────────────────┐ │
│  │ kb_common.py (1236行, god-module)                                  │ │
│  │   tokenize(72) · load_meta(118) · load_note(130) · iter_notes(141) │ │
│  │   load_note_full(992) · parse_frontmatter(86) · content_fingerprint│ │
│  │   count_tokens(159) · root_of(225) · parent_of(202) · kb_layer_of │ │
│  │   atomic_write_text · record_lineage · is_stale · retrievable...   │ │
│  │ state_manager.py ── StateStore: .kb/state/ 统一状态持久化            │ │
│  │   fcntl.flock 文件锁 · os.replace 原子写 · 旧路径只读回退            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌─ 核心业务模块 ─────────────────────────────────────────────────────┐ │
│  │ rag.py (1768行)        混合检索: TF-IDF余弦 + BM25 → RRF融合        │ │
│  │                        + 倒排索引加成 + dense尾部填充 + 5min缓存     │ │
│  │ curate.py (1288行)     LLM结晶: 三关校验→置信闸门→正典写回          │ │
│  │ clean.py (705行)       去重/MinHash/LSH/分块/分类修复               │ │
│  │ consolidate.py         夜间4阶段整理: 签名缓存增量预算               │ │
│  │ memory_ingest.py(656)  多源摄入: JSON/MD/SQLite/OpenCode            │ │
│  │ memory_sync.py(483)    记忆晋升: memory→知识层 + 索引增量重建        │ │
│  │ memory_distill_session 蒸馏: dsh-session→dshd-*精炼记忆(保留)        │ │
│  │ gate.py                三态写闸门: canon/page/raw 分层写入控制       │ │
│  │ recall_schedule + fsrs_core  FSRS-6间隔重复调度                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌─ 研究层 (内部实验, 无CLI入口, test_reachability守护) ──────────────┐ │
│  │ kb_engine.py(771) · kb_l4.py(580) · kb_l5.py · kb_meta.py(524)     │ │
│  │ kb_selfweight.py · kb_claim.py(513) · kb_usage.py · kb_contradiction│ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  契约层 (template-vault/reference/，10个JSON = 单一事实源)                │
│  kb-schema.json        页型→字段→枚举→生命周期 (canon-decision/sop/lesson)│
│  plugin-config.json    25条插件启停配置 (但实际39个插件, 14个无配置)       │
│  taxonomy系列          domain-taxonomy / tag-taxonomy / taxonomy / synonyms│
│  dedup-config.json     近重复检测阈值                                    │
│  triage-config.json    分诊规则                                          │
│  content-type-signals  内容类型信号                                      │
│  users.json            用户档案                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 三、核心数据流

### 3.1 调度流（CLI → 引擎）

```
用户: kb curate run --apply
  │
  ├─[1] kb (bash) → exec kb_launcher.py
  │     export KB_ROOT="${KB_ROOT:-$VAULT}"
  │
  ├─[2] kb_launcher.py: COMPAT_MAP["curate"]["run"] = ("curate_plugin", "run")
  │     _parse_params: BOOL_FLAGS/MULTI_FLAGS 解析 --apply
  │
  ├─[3] PluginRegistry: discover() 已扫描 plugins/ → register("curate_plugin")
  │     _is_enabled("curate_plugin") 查 plugin-config.json
  │     initialize_all() 拓扑序初始化
  │
  ├─[4] curate_plugin.py: ACTION_TABLE["run"] = ("subprocess", "curate", "run")
  │     → SubprocessPlugin.execute()
  │
  └─[5] SubprocessPlugin: fork `python3 pipeline/curate.py run --apply`
        (或在 IN_PROCESS_ACTIONS 白名单内则同进程直调)
        → curate.py main() → cmd_run()
```

**关键设计**：插件层是 **薄兼容外壳**，把 pre-existing 的 CLI 脚本包装成统一的 plugin→action 接口。子进程隔离保证单个插件崩溃不影响调度器，`_execute_in_process` 提供同进程快路径并在 import 失败时自动回退。

### 3.2 结晶流（证据 → 正典）★核心业务流

```
memory/*.md (dshd-*蒸馏记忆 / JSON域页)
raw/*.md (网页抓取/保号副本)
  │
  ├─ select_batch() 评分选批
  │   跳过: raw证据层 / 生成报告 / 归档 / 已canon/noise / <80字 / ≥2次尝试
  │   加分: 2000~16000字+3.0 / chunk_of+1.2 / 日志味+1.0
  │
  ├─ curate_one() 单篇结晶
  │   ├─ load() 取frontmatter+正文
  │   ├─ build_messages() 构造分层提示 → llm.ask_json(max_tokens=4096)
  │   ├─ 被截断? → 注入"极紧凑"重试一次
  │   ├─ verdict分流: noise/raw-only → mark_evidence_verdict 写回, 不生成正典
  │   ├─ verify_note() 三关校验:
  │   │   ├─ 信息守恒关: ratio=len(canon)/len(body), 尺寸感知阈值(0.04~0.65)
  │   │   ├─ 接地关: facts逐条精确子串核对, kind=inferred不计入分母
  │   │   └─ 引用关: sources非空(可达性在sid解析侧保证)
  │   ├─ 置信闸门: confidence < 0.6 → _review_append 进人工队列, 不写回
  │   └─ find_dup_canon(≥0.90 Jaccard) → skip / apply_canon() 原位写回
  │
  └─ apply_canon() 写回
      ├─ 原文保号移入 raw/_curated/<原路径> (重名加-N后缀)
      ├─ 原位覆盖: # {title} + render_canon(prop) + 尾注回溯
      ├─ frontmatter: archived_original + kb_layer:canon
      └─ record_lineage() 写血缘
```

### 3.3 检索流（查询 → 答案）

```
kb rag query "问题" --top 3 --answer
  │
  ├─ 检索缓存: StateStore.load("query_cache.json") · TTL 5min
  │   (no_record/评测模式禁用读写, 避免污染)
  │
  ├─ 索引加载: vector index/df_idf.pkl (pickle优先, JSON回退)
  │   _SafeUnpickler 白名单反序列化 (防任意代码执行)
  │   IDX_VERSION=8 (postings扁平表 + dense稀疏投影)
  │
  ├─ 查询扩展 _expand_query → tokenize → _vec(qv)
  │
  ├─ 稀疏检索:
  │   ├─ TF-IDF余弦: _score_cosine_postings (命中文档打分, 免物化全库)
  │   ├─ BM25: _bm25_postings (含df/dl/avgdl预计算)
  │   └─ RRF融合: _rrf_fuse(余弦, BM25, k=RRF_K) ← 排名级融合, 免归一化
  │
  ├─ 倒排加成: tag/domain/content_type/author 命中 × INVERTED_HIT_BOOST
  │
  ├─ Dense尾部填充: dense_rank() 只填尾部槽位, 不参与头部排序
  │
  ├─ L1检索修正: _apply_retrieval_adjustments (把上次漏掉的拉回, KB_RETRIEVAL_ADJUST=0可关)
  │
  └─ 输出: top-k + best_sentence片段 + (可选)LLM作答
      _record_hits() 写反馈信号 (供kb_usage量再问率)
```

### 3.4 记忆流（摄入 → 晋升）

```
DSH session (dshd-*/dsh-*)
  │
  ├─ agent_registry.detect_dsh() → detect_agents()
  ├─ _ingest_session() [distill-only]:
  │   └─ memory_distill_session.distill_session() → dshd-* (精炼记忆, 保留)
  │     (memory_ingest_session.mirror_session → dsh-* 已移除, 模块保留供utils)
  │
JSON agent (domain/project/decision页)
  ├─ memory_ingest_json.ingest() → 写入 memory/*.md
  └─ memory_sync.run():
      ├─ load_index() 增量检测: content_fingerprint(body)对比doc_hashes
      │   不一致 → rag.cmd_index() 重建
      ├─ memory_files() 扫描 memory/ 层
      └─ best_match() + 晋升写入知识层 (ledger_append 记账)
```

> **记忆 vs 日志的区分**（已固化到代码）：`dshd-*`（蒸馏记忆，`tags:[knowledge,dsh-distill]`，保留）与 `dsh-*`（session日志，`tags:[knowledge,dsh-session]`，已停止摄入）。两者原本都经 `_ingest_session` 流转，现已切断 session 镜像，仅保留蒸馏。

### 3.5 夜间整理流（consolidate）

```
consolidate.run(max_seconds预算)
  ├─ 签名缓存增量: 已处理笔记的指纹缓存, 跳过未变更
  ├─ 4阶段整理 (签名缓存驱动, 预算时间到即停)
  └─ StateStore 读写状态: _load_state / _save_state
```

---

## 四、热点清单（最脆弱/最高治理优先级）

| 排名 | 文件 | 行数 | 职责 | 风险 |
|------|------|------|------|------|
| 1 | `tests/test_p0_fixes.py` | 2260 | 单文件巨型测试 | 改动慢、定位难、合并冲突高发 |
| 2 | `pipeline/rag.py` | 1768 | 检索引擎全栈 | 索引/评分/缓存/反馈耦合在单文件 |
| 3 | `pipeline/curate.py` | 1288 | 结晶主流程 | LLM/校验/写回/批次/子命令全揉在一起 |
| 4 | `pipeline/kb_common.py` | 1236 | 公共工具god-module | 20+模块依赖, 改动爆炸半径最大 |
| 5 | `pipeline/kb_engine.py` | 771 | 研究层引擎 | git备份/快照/成长调度混合 |
| 6 | `pipeline/clean.py` | 705 | 去重清洗 | MinHash/LSH/分块/分类修复混合 |
| 7 | `pipeline/memory_ingest.py` | 656 | 多源摄入 | 4种agent格式条件分支 |
| 8 | `pipeline/kb-healthcheck.py` | 639 | 健康检查 | (非引擎, 独立脚本) |

**git 改动频率**（近200 commits）：`rag.py` / `kb_launcher.py` / `curate.py` / `kb_common.py` 各 10-11 次改动——既是热点也是脆弱点。

---

## 五、待确认问题与已发现问题

### 5.1 配置覆盖缺口
`reference/plugin-config.json` 只有 **25 条**配置，但实际有 **39 个**插件——**14 个插件无配置条目**，意味着它们走默认启用、无法通过配置禁用。无配置的 14 个主要集中在研究层和部分新模块。

### 5.2 插件层是薄兼容外壳
39 个插件中绝大多数的 `ACTION_TABLE` 只是把 `(tool, sub)` 映射到 `(subprocess, module, sub)`——实际逻辑全在引擎 `main()` 里。插件层增加了 ~40 个文件、一层动态 import + 拓扑初始化的复杂度，但并未承担跨插件编排或状态共享职责。这是一层 **可简化的间接层**。

### 5.3 kb_common.py god-module
1236 行、9 个高频工具函数被 20-59 个模块依赖。`tokenize`(59调用者) / `iter_notes`(59) / `load_note_full`(35) / `load_meta`(22) 是事实上的公共基础设施，但它们与 `content_fingerprint` / `record_lineage` / `is_stale` / `kb_layer_of` 等领域函数混在一个文件里。任何改动都有巨大爆炸半径。

### 5.4 memory_ingest_session.mirror_session 死代码
`mirror_session()`（:183）产生 `dsh-*` session日志，上游 `_ingest_session` 已改为仅蒸馏。该函数不再被调用，但模块（321行）仍保留供其他 utils 引用。明确的清理候选。

### 5.5 测试集中度
`test_p0_fixes.py` 单文件 2260 行占全部测试（6085行）的 37%。缺少按模块拆分的测试文件（如 `test_rag.py` / `test_curate.py`），导致定位"哪个模块的测试失败"需要在全文件内搜索。

### 5.6 研究层边界
`kb_l4` / `kb_l5` / `kb_meta` / `kb_selfweight` / `kb_claim` / `kb_usage` / `kb_contradiction` 等 8 个研究层模块共 ~4000 行，无 CLI 入口，仅由 `tests/test_reachability.py` 守护。它们与核心引擎共享 `kb_common` / `state_manager`，但边界模糊——是否有实验代码混入了生产路径需要确认。

### 5.7 无依赖声明
项目无 `requirements.txt` / `pyproject.toml`。虽然设计目标是纯标准库，但 `rag.py` 可选依赖 `contextual` / `embed_rerank`，`curate.py` 依赖外部 LLM（`llm.ask_json`）。这些可选依赖的发现方式是"运行时 try-except"，缺乏声明式的外部依赖契约。

---

## 六、优化方案

按 **收益/成本比** 排序，分为三档优先级。

### 🥇 P0 — 低风险高收益（建议立即做）

#### P0-1：清理 `mirror_session` 死代码
- **动作**：删除 `memory_ingest_session.py:183` 的 `mirror_session()` 函数体；保留模块文件（仍有 utils 被引用）；更新 `_ingest_session` 确认无残留引用。
- **收益**：消除"记忆 vs 日志"混淆的最后一个代码痕迹，减少新人理解成本。
- **风险**：低——已被确认无调用方；`grep -rn "mirror_session"` 确认无外部引用即可。
- **验证**：`python3 -m pytest tests/` 全绿。

#### P0-2：补齐 `plugin-config.json` 的 14 个缺口
- **动作**：为 14 个无配置的插件补齐 `{name, enabled, description}` 条目，使 39 个插件全部可配置。
- **收益**：恢复配置完整性，用户可禁用任意插件（包括研究层模块），减少不必要的运行时初始化。
- **风险**：低——纯数据补充，默认 `enabled: true` 不改变现有行为。
- **验证**：`test_readme_plugin_list.py` 已有 drift guard，可扩展为校验 plugin-config 覆盖率。

#### P0-3：拆分 `test_p0_fixes.py`
- **动作**：按被测模块拆分为 `test_rag.py` / `test_curate.py` / `test_state_manager.py` / `test_clean.py` 等，原文件保留跨模块的 P0 回归子集。
- **收益**：测试定位速度从"全文件搜索"→"按模块直查"；合并冲突面缩小。
- **风险**：低——纯文件移动 + import 调整，行为不变。
- **验证**：拆分前后 `pytest` 用例数与通过数一致。

### 🥈 P1 — 中等风险中高收益（建议下个迭代）

#### P1-1：拆分 `kb_common.py` god-module
- **动作**：按职责拆为 3 个模块：
  - `kb_io.py`：`iter_notes` / `load_meta` / `load_note` / `load_note_full` / `parse_frontmatter` / `atomic_write_text`（文件 I/O）
  - `kb_text.py`：`tokenize` / `count_tokens` / `content_fingerprint` / `_parse_tags`（文本处理）
  - `kb_meta.py`（新，与现有 `kb_meta.py` 研究层区分命名）：`kb_layer_of` / `root_of` / `parent_of` / `is_stale` / `retrievable_status` / `record_lineage`（领域元数据）
- **收益**：爆炸半径从"1 文件 20+ 依赖"→"3 文件各 ~7 依赖"；改动可局部化。
- **风险**：中——59 个调用者的 import 路径需更新；建议用**兼容 shim** 过渡：`kb_common.py` 保留 `from kb_io import *` 等 re-export，分批迁移调用方，最后删除 shim。
- **验证**：codegraph 跟踪 59 个 `iter_notes` 调用者逐批迁移；每批跑 `pytest`。

#### P1-2：补一份 `pyproject.toml`（即使零运行时依赖）
- **动作**：声明项目元数据 + Python 版本要求 + 可选依赖组（`[project.optional-dependencies]`）：`embed = ["contextual", "embed_rerank"]`，`llm = ["..."]`，`dev = ["pytest"]`。
- **收益**：外部依赖契约从"读代码猜"→"声明式可查"；`pip install -e ".[dev]"` 一键搭环境。
- **风险**：低——不引入新依赖，只是声明已有的可选依赖。
- **验证**：`pip install -e .` 成功；现有纯标准库路径不受影响。

#### P1-3：拆分 `rag.py`（1768行）
- **动作**：按职责拆为：
  - `rag_index.py`：`cmd_index` / 索引构建 / `_SafeUnpickler` / `load_index` / `index_file`
  - `rag_score.py`：`_score_cosine_postings` / `_bm25_postings` / `_rrf_fuse` / `_inverted_lookup`
  - `rag_query.py`：`cmd_query` / 缓存 / 检索修正 / 反馈记录
  - `rag.py`：保留 re-export shim + 公共常量
- **收益**：索引构建与查询评分解耦，可独立测试和优化。
- **风险**：中——`rag.py` 被 `memory_sync` / `kb_engine` 等模块跨引用；同样用 shim 过渡。
- **验证**：`test_rag_cache.py` + `test_rag_incremental.py` 全绿。

### 🥉 P2 — 较高成本，长期收益（视资源择机）

#### P2-1：评估插件层简化
- **动作**：评估两种方案：
  - **方案A（渐进）**：把子进程隔离逻辑下沉到引擎 `main()` 统一入口，插件层只保留路由表，删除 `SubprocessPlugin` 的 fork 逻辑。
  - **方案B（激进）**：合并 39 个插件为 ~10 个按领域聚合的插件（检索/结晶/摄入/整理/运维），COMPAT_MAP 直接映射到聚合插件。
- **收益**：减少 ~30 个薄包装文件；初始化时动态 import 数从 39→10。
- **风险**：高——涉及调度核心，需完整回归。方案A 更安全。
- **验证**：`test_plugin_registry.py` + `test_launcher_flags.py` + 全量 `pytest`。

#### P2-2：研究层边界固化
- **动作**：为 8 个研究层模块建立明确的 internal 包边界（如移入 `pipeline/_research/` 子包 + `__init__.py` 标注 internal），并在 `kb_common` 中加 `import guard` 防止生产路径意外依赖。
- **收益**：防止实验代码污染生产路径；明确哪些可以安全删除。
- **风险**：中——需确认无生产模块 import 研究层符号。
- **验证**：`test_reachability.py` + codegraph 反向依赖检查。

#### P2-3：`curate.py` 子命令提取
- **动作**：把 `cmd_run` / `cmd_verify` / `cmd_report` / `cmd_recheck` / `cmd_review` 拆为独立函数文件或 class，`curate.py` 保留 `curate_one` / `verify_note` / `apply_canon` 核心。
- **收益**：1288 行→核心 ~500 行 + 子命令各 ~150 行，可读性显著提升。
- **风险**：中——子命令间共享状态（state/ledger/proposals），拆分需传递上下文。
- **验证**：`scripts/verify_curate.py` + 手动 `kb curate run/recheck/report` 冒烟。

---

## 七、落地建议

| 阶段 | 内容 | 预估工作量 | 前置条件 |
|------|------|-----------|---------|
| 第1周 | P0-1 + P0-2 + P0-3 | 1-2人天 | 无 |
| 第2-3周 | P1-1（kb_common拆分, 含shim过渡） | 3-5人天 | P0-3完成（测试已拆, 有安全网） |
| 第3周 | P1-2 + P1-3（pyproject + rag拆分） | 3-4人天 | P1-1的shim模式可复用 |
| 视资源 | P2-1/P2-2/P2-3 | 各5+人天 | P1全部完成, 有充分测试覆盖 |

**核心原则**：所有拆分都走 **shim 过渡**（旧路径 re-export 新模块）——保证调用方零改动即可运行，分批迁移调用方的 import，最后删除 shim。这样每一步都是可回退的小步重构，而非大爆炸式改写。

---

## 八、质量门自检

> 能直接回答三问，才算建立模型：

1. **改 X 功能波及哪些模块？**
   - 改检索评分 → `rag.py` 内部（已隔离在单文件），外加 `memory_sync.load_index`（调 `rag.cmd_index`）和 `kb_engine`（调 `rag.cmd_query`）
   - 改结晶逻辑 → `curate.py` 内部 + `curate_plugin.py`（薄包装）+ `ladder.py`（调用方）
   - 改公共工具 → `kb_common.py` 的 20-59 个调用者（爆炸半径最大，优先拆分）

2. **数据怎么流、存哪里？**
   - 证据/记忆 → `raw/` + `memory/`（markdown 文件）
   - 状态 → `.kb/state/*.json`（`StateStore` 统一管理，fcntl 锁 + 原子写）
   - 索引 → `vector index/df_idf.pkl`（pickle，gitignored，可重建）
   - 正典 → 原位覆盖知识层笔记 + 原文保号移入 `raw/_curated/`
   - 反馈 → `.kb/state/query_cache.json` + agent_hits

3. **哪个文件最脆弱、最要先动？**
   - `kb_common.py`（1236行 god-module，59个调用者，git高频改动）—— 拆分收益最大、风险可控（shim过渡）
   - `test_p0_fixes.py`（2260行，占测试37%）—— 拆分是其他重构的安全网前提

---

*本报告基于 HEAD `5c9bd41` 的代码状态。如需更新，重跑 codegraph sync 后复核。*
