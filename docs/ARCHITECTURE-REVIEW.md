# kb-kit-pure 架构梳理与优化方案

> 调查对象：`/home/mushan/workspace/kb-kit-pure`（模板仓库，297 tracked files，171 .py）
> 初查：2026-10-19 · 方法：codegraph 调用链追踪 + 关键模块源码精读 + git 热点统计
> 状态核对：2026-10-21 — **P0 全完成 / P1 全完成 / P2-1 已落地 ops 域试点**
> ⚠️ §四、§五 保留初查时点视角；每项当前状态见 **§九 状态总账**

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
│  kb_launcher.py (545行)  ── COMPAT_MAP (119条 tool+sub→plugin路由)        │
│    │  BOOL_FLAGS / MULTI_FLAGS 参数解析                                  │
│    ▼                                                                     │
│  PluginRegistry (plugin_registry.py, 349行)                             │
│    │  discover() 扫描 plugins/ → 动态 import → 拓扑初始化 → 按名分发      │
│    │  _load_config() 读 reference/plugin-config.json (40条)              │
│    │  _is_enabled() 判断启停 · 错误隔离                                   │
│    ▼                                                                     │
│  plugins/ (40个业务插件 + _subprocess_plugin.py + __init__.py)           │
│    │                                                                      │
│    ├── SubprocessPlugin (默认路径) ──子进程隔离──→ 引擎模块 main()       │
│    └── IN_PROCESS_ACTIONS (快路径) ──同进程直调──→ 引擎模块函数           │
│         (import失败/异常时自动回退到子进程)                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  引擎层 (template-vault/pipeline/，84个.py，~28970行)                    │
│                                                                          │
│  ┌─ 公共基础 ─────────────────────────────────────────────────────────┐ │
│  │ kb_common.py (691行, shim — P1-1 已拆, 见 §九)                       │ │
│  │   → re-export kb_io / kb_text / kb_metautil 三模块                  │ │
│  │   tokenize(72) · load_meta(118) · load_note(130) · iter_notes(141) │ │
│  │   load_note_full(992) · parse_frontmatter(86) · content_fingerprint│ │
│  │   count_tokens(159) · root_of(225) · parent_of(202) · kb_layer_of │ │
│  │   atomic_write_text · record_lineage · is_stale · retrievable...   │ │
│  │ state_manager.py ── StateStore: .kb/state/ 统一状态持久化            │ │
│  │   fcntl.flock 文件锁 · os.replace 原子写 · 旧路径只读回退            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌─ 核心业务模块 ─────────────────────────────────────────────────────┐ │
│  │ rag.py (106行, shim — P1-3 已拆, 见 §九)                            │ │
│  │   → re-export rag_index / rag_score / rag_query                     │ │
│  │   (原1768行: TF-IDF余弦 + BM25 → RRF融合 + 倒排加成 + dense填充)     │ │
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
│  plugin-config.json    40条插件启停配置（40个插件全覆盖，P0-2 已修）          │
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

> 初查 2026-10-19 → 现状 2026-10-21。**初查前三名已全部拆分**。

| 初查排名 | 文件 | 初查行数 | → 现状 |
|---|---|---|---|
| 1 | `tests/test_p0_fixes.py` | 2260 | ✅ 拆为 32 个 `test_*.py`，本文件降至 400 行 / 7% |
| 2 | `pipeline/rag.py` | 1768 | ✅ 拆为 `rag_index`/`rag_score`/`rag_query`，本文件 106 行 shim |
| 3 | `pipeline/curate.py` | 1288 | ❌ **仍是 1288 行** — 现役最大单文件（P2-3 目标） |
| 4 | `pipeline/kb_common.py` | 1236 | 🔶 拆出 `kb_io`/`kb_text`/`kb_metautil`，本文件 691 行 shim 待删 |
| 5 | `pipeline/kb_engine.py` | 771 | ❌ 未动（研究层；P2-2 固化边界而非拆分） |
| 6 | `pipeline/clean.py` | 705 | ❌ 未动 |
| 7 | `pipeline/memory_ingest.py` | 656 | ❌ 未动 |
| 8 | `pipeline/kb-healthcheck.py` | 639 | ❌ 未动（独立脚本；已被 ops 域插件封装） |

**git 改动频率**（初查近200 commits）：`rag.py` / `kb_launcher.py` / `curate.py` /
`kb_common.py` 各 10-11 次改动——既是热点也是脆弱点。前者已因拆分降级。

---

## 五、待确认问题与已发现问题

> ⚠️ 本节保留 2026-10-19 **初查时点视角**（记录问题如何被发现）。
> 每项**当前状态**见 §九 状态总账。

### 5.1 配置覆盖缺口 ✅ 已解决（P0-2）
`reference/plugin-config.json` 只有 **25 条**配置，但实际有 **39 个**插件——**14 个插件无配置条目**，意味着它们走默认启用、无法通过配置禁用。
> **现状（10-21 实测）**：已补齐，注册 40 ↔ 配置 40，双无缺口。

### 5.2 插件层是薄兼容外壳 ✅ 已评估（P2-1：非结构问题）
39 个插件中绝大多数的 `ACTION_TABLE` 只是把 `(tool, sub)` 映射到 `(subprocess, module, sub)`——实际逻辑全在引擎 `main()` 里。插件层增加了 ~40 个文件、一层动态 import + 拓扑初始化的复杂度，但并未承担跨插件编排或状态共享职责。这是一层 **可简化的间接层**。
> **现状（10-21 实测）**：结论相反——`discover()` 全量仅 3.4ms，且该层实际
> 承担 CLI 兼容（COMPAT_MAP）、错误隔离、per-action 快路径选路三项真实职责。
> P2-1 仅把 ops 域 10 个薄壳合并为单一 `OpsPlugin` 作试点。

### 5.3 kb_common.py god-module 🔶 已部分解决（P1-1 主体完成，shim 待删）
1236 行、9 个高频工具函数被 20-59 个模块依赖。`tokenize`(59调用者) / `iter_notes`(59) / `load_note_full`(35) / `load_meta`(22) 是事实上的公共基础设施，但它们与 `content_fingerprint` / `record_lineage` / `is_stale` / `kb_layer_of` 等领域函数混在一个文件里。任何改动都有巨大爆炸半径。
> **现状**：已拆 `kb_io`/`kb_text`/`kb_metautil`，`kb_common.py` 降为 691 行 shim，
> 仍有 74 个调用者走旧路径（删除 shim 是未完成的收尾项）。

### 5.4 memory_ingest_session.mirror_session 死代码 ✅ 已解决（P0-1）
`mirror_session()`（:183）产生 `dsh-*` session日志，上游 `_ingest_session` 已改为仅蒸馏。该函数不再被调用，但模块（321行）仍保留供其他 utils 引用。明确的清理候选。
> **现状**：`def mirror_session` 已删除，仅 docstring/注释解释停用原因。

### 5.5 测试集中度 ✅ 已解决（P0-3）
`test_p0_fixes.py` 单文件 2260 行占全部测试（6085行）的 37%。缺少按模块拆分的测试文件（如 `test_rag.py` / `test_curate.py`），导致定位"哪个模块的测试失败"需要在全文件内搜索。
> **现状**：已拆为 32 个 `test_*.py`，本文件 400 行 / 7%。

### 5.6 研究层边界 ❌ 未动（P2-2）
`kb_l4` / `kb_l5` / `kb_meta` / `kb_selfweight` / `kb_claim` / `kb_usage` / `kb_contradiction` 等 8 个研究层模块共 ~4000 行，无 CLI 入口，仅由 `tests/test_reachability.py` 守护。它们与核心引擎共享 `kb_common` / `state_manager`，但边界模糊——是否有实验代码混入了生产路径需要确认。
> **现状**：仍未动。已实测确认这 8 个模块无任何插件注册（无 CLI 入口）。

### 5.7 无依赖声明 ✅ 已解决（P1-2）
项目无 `requirements.txt` / `pyproject.toml`。虽然设计目标是纯标准库，但 `rag.py` 可选依赖 `contextual` / `embed_rerank`，`curate.py` 依赖外部 LLM（`llm.ask_json`）。这些可选依赖的发现方式是"运行时 try-except"，缺乏声明式的外部依赖契约。
> **现状**：已补 `pyproject.toml`（`[project]` + `optional-dependencies`）。

---

## 六、优化方案

按 **收益/成本比** 排序，分为三档优先级。

### 🥇 P0 — 低风险高收益 ✅ 全部完成（3/3）

#### P0-1：清理 `mirror_session` 死代码 ✅ 已完成
- **动作**：删除 `memory_ingest_session.py:183` 的 `mirror_session()` 函数体；保留模块文件（仍有 utils 被引用）；更新 `_ingest_session` 确认无残留引用。
- **收益**：消除"记忆 vs 日志"混淆的最后一个代码痕迹，减少新人理解成本。
- **风险**：低——已被确认无调用方；`grep -rn "mirror_session"` 确认无外部引用即可。
- **验证**：`python3 -m pytest tests/` 全绿。

#### P0-2：补齐 `plugin-config.json` 的 14 个缺口 ✅ 已完成
- **动作**：为 14 个无配置的插件补齐 `{name, enabled, description}` 条目，使 39 个插件全部可配置。
- **收益**：恢复配置完整性，用户可禁用任意插件（包括研究层模块），减少不必要的运行时初始化。
- **风险**：低——纯数据补充，默认 `enabled: true` 不改变现有行为。
- **验证**：`test_readme_plugin_list.py` 已有 drift guard，可扩展为校验 plugin-config 覆盖率。

#### P0-3：拆分 `test_p0_fixes.py` ✅ 已完成
- **动作**：按被测模块拆分为 `test_rag.py` / `test_curate.py` / `test_state_manager.py` / `test_clean.py` 等，原文件保留跨模块的 P0 回归子集。
- **收益**：测试定位速度从"全文件搜索"→"按模块直查"；合并冲突面缩小。
- **风险**：低——纯文件移动 + import 调整，行为不变。
- **验证**：拆分前后 `pytest` 用例数与通过数一致。

### 🥈 P1 — 中等风险中高收益 ✅ 全部完成（3/3；P1-1/P1-3 有 shim 待删）

#### P1-1：拆分 `kb_common.py` god-module 🔶 主体完成，shim 待删
- **动作**：按职责拆为 3 个模块：
  - `kb_io.py`：`iter_notes` / `load_meta` / `load_note` / `load_note_full` / `parse_frontmatter` / `atomic_write_text`（文件 I/O）
  - `kb_text.py`：`tokenize` / `count_tokens` / `content_fingerprint` / `_parse_tags`（文本处理）
  - `kb_meta.py`（新，与现有 `kb_meta.py` 研究层区分命名）：`kb_layer_of` / `root_of` / `parent_of` / `is_stale` / `retrievable_status` / `record_lineage`（领域元数据）
- **收益**：爆炸半径从"1 文件 20+ 依赖"→"3 文件各 ~7 依赖"；改动可局部化。
- **风险**：中——59 个调用者的 import 路径需更新；建议用**兼容 shim** 过渡：`kb_common.py` 保留 `from kb_io import *` 等 re-export，分批迁移调用方，最后删除 shim。
- **验证**：codegraph 跟踪 59 个 `iter_notes` 调用者逐批迁移；每批跑 `pytest`。

#### P1-2：补一份 `pyproject.toml`（即使零运行时依赖）✅ 已完成
- **动作**：声明项目元数据 + Python 版本要求 + 可选依赖组（`[project.optional-dependencies]`）：`embed = ["contextual", "embed_rerank"]`，`llm = ["..."]`，`dev = ["pytest"]`。
- **收益**：外部依赖契约从"读代码猜"→"声明式可查"；`pip install -e ".[dev]"` 一键搭环境。
- **风险**：低——不引入新依赖，只是声明已有的可选依赖。
- **验证**：`pip install -e .` 成功；现有纯标准库路径不受影响。

#### P1-3：拆分 `rag.py`（1768行）✅ 已完成（shim 待删）
- **动作**：按职责拆为：
  - `rag_index.py`：`cmd_index` / 索引构建 / `_SafeUnpickler` / `load_index` / `index_file`
  - `rag_score.py`：`_score_cosine_postings` / `_bm25_postings` / `_rrf_fuse` / `_inverted_lookup`
  - `rag_query.py`：`cmd_query` / 缓存 / 检索修正 / 反馈记录
  - `rag.py`：保留 re-export shim + 公共常量
- **收益**：索引构建与查询评分解耦，可独立测试和优化。
- **风险**：中——`rag.py` 被 `memory_sync` / `kb_engine` 等模块跨引用；同样用 shim 过渡。
- **验证**：`test_rag_cache.py` + `test_rag_incremental.py` 全绿。

### 🥉 P2 — 较高成本，长期收益（视资源择机）

#### P2-1：评估插件层简化 ✅ 已评估 + 已落地 ops 域试点（其余 4 域暂缓）

> **历史教训（记录在案）**：2026-10-21 首轮评估写的是「方案B：合并 39→5 个领域插件、
> 旧 39 文件做 re-export shim、COMPAT_MAP 直接映射到聚合插件、plugin-config 39→5」。
> 落地前用注册表源码 + 实测**逐条推翻**，结论已作废重写。若不先做这轮验证就动手，
> 会得到一个**编译得过但 CLI 出现未知命令**的破坏性版本。推翻过程见文末「E」节。

**核心结论**：插件层「文件多」是**表象**，不是结构问题。39 个文件 = 35 个 ~20 行的
`SubprocessPlugin` 薄壳（这是 `SubprocessPlugin` 基类的**正常使用形态**，非冗余）
+ 4 个真正有逻辑的自定义 `execute()`。合并的真实收益只剩「文件数」，实测开销基准
`discover()` 全量 = **3.4ms**，故**性能收益不存在**。

**现状基线**（2026-10-21 复查）：39 插件 / 1039 行 / 35 薄壳 + 4 自定义 /
COMPAT_MAP 119 条（首轮误记 113）/ 39 条 plugin-config / 全量 290 passed。

---

##### 一、可落地的方案B：只合并 5 个域中的 1 个作为试点

真正的合并（35 个薄壳 → 5 个域类）**成立的前提**是 action 改名消歧 + cli_aliases
消歧 + plugin-config 同步 + README 同步，每一步都可做但**风险与收益严重不成比例**
（E 节量化）。因此把 P2-1 收敛为：

**第一步（本任务执行）**：只合并 `ops` 域 —— 该域 **action 撞名 0 个、cli_aliases
撞名 0 个**（实测），是 5 个域中唯一零冲突的域。合并 10 个插件 → 1 个 `OpsPlugin`，
走完整流程（含 README / plugin-config / 测试同步）跑通一次，作为「领域合并」的
**可复制模板与决策依据**。

其余 4 个域（retrieval/crystallization/ingestion/organization）**暂缓**，理由是
撞名 action 实例共 16 个（E 节量化），必须改名 —— 改名即改 CLI 行为，属 API 变更，
需要单独评审。用第一步的实际账（改动量/回归风险/收益）来决定是否值得继续。

**诚实预期**：这一步的收益是「验证域合并流程可行 + 为后续真合并铺路」，
**不是本次就降低复杂度**（详见第三节末尾的冗余说明）。如果认为这个收益不值得，
P2-1 应直接收缩为 E 节记录的那项 no-op sys.path 清理。

##### 二、ops 域合并的完整设计（零 action/cli 冲突）

合并的 10 个插件及其 action / cli_aliases / SCRIPT_NAME / IN_PROCESS_ACTIONS：

| 原插件 | SCRIPT_NAME | actions | cli_aliases | IN_PROCESS |
|--------|-------------|---------|-------------|-----------|
| `backup` | `backup.py` | `backup` | `{"backup": "backup"}` | — |
| `sync` | `sync.py` | `dry-run`,`apply`,`rollback`,`history`,`ingest-any` | `{"sync": None, "ingest-any": "ingest-any"}` | — |
| `memory_sync` | `memory_sync.py` | `review`,`promote` | — | — |
| `healthcheck` | `kb-healthcheck.py` | `check` | `{"healthcheck": "check", "kb-healthcheck": "check"}` | `check` |
| `health_metrics` | `kb_health.py` | `metrics`,`kpi` | `{"health-metrics": "metrics"}` | — |
| `dashboard` | `dashboard.py` | `generate` | `{"dashboard": "generate"}` | — |
| `audit` | `kb_audit.py` | `scan`,`fix`,`log` | `{"audit": "scan"}` | `scan`,`log` |
| `eval` | `kb_eval_judge.py` | `run`,`gold`,`compare` | `{"eval": "run"}` | — |
| `verify` | `kb_verify.py` | `all` | `{"verify": "all"}` | — |
| `user_manager` | `user_manager.py` | `add`,`list`,`remove`,`check-perm` | `{"user": None}` | — |

**消歧后的新 action 命名规则**：`{prefix}_{orig}`，prefix 取原插件名的 snake 形式：

| 原 (plugin, action) | 新 action |
|---|---|
| backup.backup | `backup`（域内无冲突，保留原名） |
| sync.dry-run/apply/rollback/history/ingest-any | `sync_dry-run`…`sync_ingest-any` |
| memory_sync.review/promote | `memory_sync_review`/`memory_sync_promote` |
| healthcheck.check | `healthcheck` |
| health_metrics.metrics/kpi | `health_metrics`/`health_metrics_kpi` |
| dashboard.generate | `dashboard_generate` |
| audit.scan/fix/log | `audit_scan`/`audit_fix`/`audit_log` |
| eval.run/gold/compare | `eval_run`/`eval_gold`/`eval_compare` |
| verify.all | `verify_all` |
| user_manager.add/list/remove/check-perm | `user_add`/`user_list`/`user_remove`/`user_check-perm` |

**cli_aliases 零冲突验证**（10 个插件的别名表取并集，键无重复）：
`backup` `sync` `ingest-any` `healthcheck` `kb-healthcheck` `health-metrics` `dashboard`
`audit` `eval` `verify` `user` —— 11 个键互不相同 ✅（`user_manager.user` 与
`audit.audit` 不撞，`ingest-any` 是 `sync` 的独有别名）。

**实现方式**——新文件 `template-vault/pipeline/plugins/ops_plugin.py`：

```python
class OpsPlugin(SubprocessPlugin):
    """ops 域聚合插件：backup/sync/memory_sync/healthcheck/health_metrics/
    dashboard/audit/eval/verify/user_manager 十个运维能力，单一分发入口。"""

    # action → 脚本路径选择：本类的 execute() 据此把 action 路由到具体脚本
    _ROUTES = {
        # 新 action 名:        (SCRIPT_NAME, 原子子命令, in_process)
        "backup":               ("backup.py",        None,           False),
        "sync_dry-run":         ("sync.py",          "dry-run",      False),
        "sync_apply":           ("sync.py",          "apply",        False),
        "sync_rollback":        ("sync.py",          "rollback",     False),
        "sync_history":         ("sync.py",          "history",      False),
        "sync_ingest-any":      ("sync.py",          "ingest-any",   False),
        "memory_sync_review":   ("memory_sync.py",   "review",       False),
        "memory_sync_promote":  ("memory_sync.py",   "promote",      False),
        "healthcheck":          ("kb-healthcheck.py","check",        True),
        "health_metrics":       ("kb_health.py",     None,           False),
        "health_metrics_kpi":   ("kb_health.py",     "kpi",          False),
        "dashboard_generate":   ("dashboard.py",     None,           False),
        "audit_scan":           ("kb_audit.py",      "scan",         True),
        "audit_fix":            ("kb_audit.py",      "fix",          False),
        "audit_log":            ("kb_audit.py",      "log",          True),
        "eval_run":             ("kb_eval_judge.py", "run",          False),
        "eval_gold":            ("kb_eval_judge.py", "gold",         False),
        "eval_compare":         ("kb_eval_judge.py", "compare",      False),
        "verify_all":           ("kb_verify.py",     None,           False),
        "user_add":             ("user_manager.py",  "add",          False),
        "user_list":            ("user_manager.py",  "list",         False),
        "user_remove":          ("user_manager.py",  "remove",       False),
        "user_check-perm":      ("user_manager.py",  "check-perm",   False),
    }

    def metadata(self) -> PluginMetadata:
        actions = sorted(self._ROUTES.keys())
        return PluginMetadata(
            name="ops", version="1.0", plugin_type="kb_module",
            actions=actions,
            cli_aliases={"backup": "backup", "sync": None,
                         "ingest-any": "sync_ingest-any",
                         "healthcheck": "healthcheck",
                         "kb-healthcheck": "healthcheck",
                         "health-metrics": "health_metrics",
                         "dashboard": "dashboard_generate",
                         "audit": "audit_scan", "eval": "eval_run",
                         "verify": "verify_all", "user": None},
            description="运维域：备份/同步/巡检/度量/仪表盘/审计/评测/验收/用户管理",
        )

    def execute(self, action: str, params: dict) -> int:
        route = self._ROUTES.get(action)
        if route is None:
            print(f"❓ ops 不支持动作: {action}", flush=True)
            return 2
        script, orig_sub, in_proc = route
        # 实例属性遮蔽类属性：每 action 只在本次调用生效，不污染其他 action
        self.SCRIPT_NAME = script
        self.ACTION_MAP = {action: orig_sub}
        self.IN_PROCESS_ACTIONS = {action} if in_proc else set()
        return SubprocessPlugin.execute(self, action, params)
```

`execute()` 复用 `SubprocessPlugin._execute_in_process` 与 CLI 构造逻辑，通过
**实例级** `SCRIPT_NAME` / `IN_PROCESS_ACTIONS` / `ACTION_MAP` 在每次调用前按
`_ROUTES[action]` 设置，再委托原逻辑（行为与现 10 个插件逐字节一致）。

**该原型已在 `/tmp/opsproto` 实测通过**（用 `echo_args.py` / `probe.py` 替身脚本验证）：

| 验证项 | 结果 |
|---|---|
| 注册与 discover | `ops` 注册成功，24 个 action |
| `cli_aliases` 解析 | 11 个 CLI 名全部 → `("ops", <正确action>)`，无撞名 |
| CLI 构造一致性 | `audit_scan --log` → `ARGV=scan\|--root\|…\|--log`；`backup daily` → `ARGV=--root\|…\|daily`，与原 per-plugin 类**逐字节同构** |
| 进程内快路径 | `in_proc=True` 与 `False` 两路都跑通，per-action 开关生效 |
| 静态护栏 | 类级 `IN_PROCESS_ACTIONS = set()` → `test_write_actions_are_not_in_process` 通过；实例级遮蔽不被静态扫描看到，护栏设计仍然有效 |

**原型没抓到的两个坑（实施时实测踩到，已修 + 已加回归守卫）**：

| # | 坑 | 后果 | 修法 |
|---|---|---|---|
| 1 | shim 保留原 `meta.actions`（那是 CLI 契约，不能改），而 `_ROUTES` 的键是消歧后的聚合名 | 注册全绿、`kb list` 正常，但**所有 shim 执行都报"ops 不支持动作"**。首次冒烟 `kb verify` 才发现（巡检项挂掉） | 加 `_COMPAT_ACTIONS`（原 action 名 → 聚合路由键），`execute()` 开头归一化 |
| 2 | `_ROUTES` 里 `healthcheck` 的原子子命令误写成 `"check"` | `kb-healthcheck.py: error: argument check: invalid choice` —— 该脚本的位置参数 choices 是 `all/summary/deadlinks/...`，原插件 `ACTION_MAP={"check": None}` 明确**不传** | 改为 `None`，并加回归测试锁死 |

> 教训：**"注册成功 + pytest 绿" 不等于 "CLI 通"**。这两条都是静态注册层面完全正常、
> 只有真跑 CLI 才暴露的静默回归。因此新增的守卫测试直接断言**构造出来的 cmd**，
> 并对 3 个变异（改子命令 / 删兼容映射 / 兼容名指错）做过反向验证，确认会变红。

**cli_aliases 撞名的实际影响（易踩坑，已实测澄清）**：`ops` 的 11 个别名里有 4 个
（`sync`/`ingest-any`/`verify`/`user`）与原插件同名，而 `resolve_alias()` 按注册表
遍历顺序返回**第一个**命中者 —— 实测 `kb verify` 解析到的是 `("ops","verify_all")`
而非 `("verify","all")`。**但这不改变 CLI 行为**，因为：

1. 主路径是 `_resolve()` 查 `COMPAT_MAP`，11 个 CLI **全部**有 COMPAT_MAP 条目，
   `plugin_name` 由 COMPAT_MAP 决定；
2. `resolve_alias()`（`kb_launcher.py:392`）只在 COMPAT_MAP 命中后兜底取 **action**
   用，`plugin_name` 此时已是权威值，不受别名撞名影响。

实测证据（打桩 `PluginRegistry.execute` 记录真实路由）：

```
kb verify → ('verify','all')        kb user list → ('user_manager','list')
kb sync --help → (无执行)            kb ingest-any → ('sync','ingest-any')
kb backup → ('backup','backup')     kb audit → ('audit','scan')
```

⇒ **`ops` 与原名单手并存时 CLI 契约 100% 不变**。但若将来删除原插件、
把 COMPAT_MAP 改指 `ops`，就必须同步删掉 `ops` 里那些撞名的别名，否则
`resolve_alias` 的顺序依赖会变成不确定行为。

**关键实现约束（已实测验证）**：`SubprocessPlugin.execute()` 读 `self.SCRIPT_NAME` /
`self.ACTION_MAP` / `self.IN_PROCESS_ACTIONS`，`self.X` 在无实例属性时**回落到类属性**。
聚合插件在 `execute()` 开头写实例属性即可遮蔽，实测生效且不污染类属性：

```python
实例遮蔽生效? True                        类属性未被污染? True
a.SCRIPT_NAME = "rag.py"                  Agg.SCRIPT_NAME == ""
```

副作用：`test_p0_fixes.py:240 test_write_actions_are_not_in_process` 扫 `vars(mod)`
找 `SubprocessPlugin` 子类时会把 `OpsPlugin` 也纳入。**这是好事**——它正好用
类级 `IN_PROCESS_ACTIONS`（聚合类应为空 `set()`）拦住「写动作进程内化」的错误写法。
本步**无需改动该测试**。

##### 三、旧 10 个插件文件的处置：**shim 保留一个版本周期**

首轮评估写的「`from plugins.ops_plugin import OpsPlugin` re-export」**不可行**——
已实测：`_load_plugin_module` L129 `__module__ != module_name` 会**跳过** re-export 的类，
故 shim 文件不会重复注册（✅ 安全），但**也不会提供原类**，因此
`test_p0_plugins.py` 式的直接 import 会失效。

故旧文件改为「薄壳子类」形态，`__module__` = 本模块 → **会被注册**：

```python
# backup_plugin.py（形态：保留原 meta.name，继承聚合类的实现）
from plugins.ops_plugin import OpsPlugin

class BackupPlugin(OpsPlugin):
    """@deprecated shim — 请直接用 ops 域聚合插件（name="ops"）。
    本文件保留一个版本周期，用于兼容外部 import 与插件名查找。"""
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="backup", ...)   # 原名原样，CLI 契约不变
```

实测确认（真实 `sorted(glob)` 顺序 + 真实 `SubprocessPlugin` 子类）：`backup_plugin.py`
与 `ops_plugin.py` 各自用自己的 `meta.name` 注册，**互不覆盖**、无 WARNING 噪声
（`shim_reexport` 那条是我临时测试残留，与项目无关）：

```
注册表: ['backup', 'ops']
   backup  class=BackupPlugin  actions=['backup']
   ops     class=OpsPlugin     actions=['backup', 'audit_scan', ...]
```

即：**`ops` 聚合入口完全可用**，`backup` 等 10 个原名也完全可用，二者并存。
这带来一个真实的冗余（两套入口做同一件事），但**CLI 契约零变更**——
COMPAT_MAP 继续指向原名，`ops` 作为新入口只被显式 `kb ops …` 使用。

**因此本任务的实际产出**是：10 个文件收敛为 1 个实现文件 + 10 个 ~8 行 shim；
CLI 契约零变更，但**新增一套与原名并存的入口**（两套入口做同一件事的冗余）。
**这一步的收益是「为后续真合并铺路 + 验证域合并流程可行」，不是本次就降低复杂度。**
若你认为这个收益不值得，应把 P2-1 收缩为 E 节末记录的那项 no-op sys.path 清理。

##### 四、必须同步的护栏文件（漏一个测试就红）

| 文件 | 现状 | 本步动作 |
|---|---|---|
| `tests/test_p0_fixes.py:242-255` | 扫 `vars(mod)` 找 `SubprocessPlugin` 子类 + 断言 IN_PROCESS 无写动作 + `assertIn("query", rp.RAGPlugin.IN_PROCESS_ACTIONS)` | **不动**（本次不合并 retrieval 域，`rag_plugin.py` 保留） |
| `tests/test_readme_plugin_list.py` | 绑定 README 插件清单 + `plugin-config.json` 到实测集合 | README 的「实测 39 个」→ 改为 39（39 类仍在：10 shim + 1 ops + 28 未动）✅ 数字不变，**README 无需改** |
| `template-vault/reference/plugin-config.json` | 39 条全覆盖 | 新增 `"ops"` 条目（新增类必须有配置，否则 `test_every_real_plugin_has_config_entry` 红） |
| `docs/CODE-SURVEY.md:51,84` | 记 `989 行 / 36 插件` | 数字漂移，**不强制同步**（非测试护栏），但应在文档记录 |

**注意**：`plugin-config.json` 加 `ops` 后，`test_no_phantom_plugin_in_config` 要求
配置里每个名字都真实注册 → ✅ `ops` 会注册成功。同时 10 个 shim 的原名仍在配置里，
也仍然注册 → 双向覆盖都满足。

##### 五、执行顺序与本次落地记录

1. ~~**建基线记录**~~ ✅ `pytest -q` 确认 290 passed（改动前基线）。
2. ~~**写 `ops_plugin.py`**~~ ✅ 含 `_ROUTES` + `_COMPAT_ACTIONS` + `execute()` + `metadata()`。
3. ~~**plugin-config.json 加 `ops` 条目**~~ ✅（README 插件清单同步 +1，否则
   `test_declared_counts_match_reality` 等 3 条红）。
4. ~~**10 个原插件文件替换为 shim 子类**~~ ✅ 逐个替换。
   - 踩坑：`test_reachability.py` 只扫 `SCRIPT_NAME = "..."`，聚合后 `kb_audit.py` /
     `sync.py` 失联 → 已把 `plugin_scripts()` 扩展为同时解析 `_ROUTES` 元组首元素。
5. ~~**冒烟**~~ ✅ `kb verify`（5 项全绿 rc=0）、`kb list`、`kb audit scan`、
   `kb audit log`、`registry.execute("ops", "audit_scan"/"audit_log")` 均 rc=0。
6. ~~**提交**~~ ✅ 无 CLI 行为变更。

**回归守卫（新增 4 条，3 次变异验证有效）**：
`tests/test_p0_fixes.py::OpsAggregatePluginTest`
- `test_shim_actions_all_route`：`_COMPAT_ACTIONS` 每个值都必须是真实路由键
- `test_healthcheck_passes_no_positional`：healthcheck 不得传位置子命令
- `test_every_ops_action_builds_expected_cli`：23 个 action 的 CLI 逐字节对照
- `test_ops_aggregate_actions_are_prefixed`：聚合入口必须带前缀、裸名只走兼容层

**测试账**：290 passed → **294 passed**（+4 条守卫，0 条改动既有断言）。

---

##### E、首轮评估的推翻记录（留档，防再次凭想象评估）

| 首轮声称 | 实测 | 判定 |
|---|---|---|
| 动态 import 39→5 省启动开销 | `discover()` 39 模块 = **3.4ms**；注册+初始化合计 8.2ms | ❌ 收益不存在 |
| COMPAT_MAP 113→~35 条 | 键是 `(CLI工具名, 子命令)`，由 **39 个 CLI 入口**决定，与插件数无关；**实测 119 条**（首轮数成 113 也错） | ❌ 条数不会减少 |
| plugin-config 39→5 是收益 | 39 条是 P0-2 刚加的防漂移卫士，`test_readme_plugin_list` 三处硬绑定 | ❌ 是回归不是收益 |
| re-export shim 可行 | L129 `__module__` 检查**跳过** re-export 类 → 原名不可解析 | ❌ 需子类 shim |
| 删 `SubprocessPlugin` fork 逻辑 | 139 行**单一基类**，35 个插件是它的正常使用 | ❌ 无冗余可删 |
| 「插件多」是结构问题 | 35 个 ~20 行薄壳 + 4 个有逻辑插件 = 该架构的正常形态 | ❌ 是表象 |

**发现（真问题，未修）**：`agent_detect_plugin.py` / `agent_json_plugin.py` /
`agent_md_plugin.py` / `agent_sqlite_plugin.py` 里各有 4-6 行
`if pipeline_dir not in sys.path: sys.path.insert(0, pipeline_dir)`，
实测在 `discover()` 后恒为 no-op（`discover()` 已插入 `plugins_dir.parent` 与
`plugins_dir`，且 `.testenv/{fresh,clone,synth}` 均满足
`vault_root/pipeline == plugins_dir.parent`）。约 24 行装饰性代码，
**可安全删除但风险收益同样不成比例**，留作后续清理项。

##### F、方案A 否决记录

方案A 提议"把子进程隔离下沉到引擎 `main()` 统一入口，插件层只保留路由表"。
前提不成立：

1. 引擎模块**没有统一的 `main()` 入口**——39 个插件各自封装 39 个独立脚本
   （`rag.py` / `curate.py` / `clean.py` / …），每个脚本有独立的 `argparse` + `main()`，
   子命令签名各异。下沉隔离逻辑意味着要么为 39 个脚本统一入口（大改引擎层，远超插件层范围），
   要么在引擎层重新实现一套分发（与现有 `PluginRegistry` 重复）。
2. `SubprocessPlugin` 的 fork 逻辑（139 行）已是**单一基类**，集中在一处，
   并无分散在各插件中的冗余。下沉不会减少代码，只会搬移位置。
3. 不减少插件文件数量、不减少动态 import 数，收益不明确但风险触及引擎层核心。

#### P2-2：研究层边界固化 ❌ 未动
- **动作**：为 8 个研究层模块建立明确的 internal 包边界（如移入 `pipeline/_research/` 子包 + `__init__.py` 标注 internal），并在 `kb_common` 中加 `import guard` 防止生产路径意外依赖。
- **收益**：防止实验代码污染生产路径；明确哪些可以安全删除。
- **风险**：中——需确认无生产模块 import 研究层符号。
- **验证**：`test_reachability.py` + codegraph 反向依赖检查。

#### P2-3：`curate.py` 子命令提取 ❌ 未动
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
   - 改检索评分 → `rag_score.py`（P1-3 已拆分），外加 `memory_sync`（调 `rag.cmd_index`）与 `kb_engine`（调 `rag.cmd_query`）
   - 改结晶逻辑 → `curate.py` 内部 + `curate_plugin.py`（薄包装）+ `ladder.py`（调用方）
   - 改公共工具 → `kb_common` 的 74 个调用者（shim 收敛后分别指向 `kb_io` / `kb_text` / `kb_metautil`）

2. **数据怎么流、存哪里？**
   - 证据/记忆 → `raw/` + `memory/`（markdown 文件）
   - 状态 → `.kb/state/*.json`（`StateStore` 统一管理，fcntl 锁 + 原子写）
   - 索引 → `vector index/df_idf.pkl`（pickle，gitignored，可重建）
   - 正典 → 原位覆盖知识层笔记 + 原文保号移入 `raw/_curated/`
   - 反馈 → `.kb/state/query_cache.json` + agent_hits

3. **哪个文件最脆弱、最要先动？**
   - `curate.py`（**1288 行**，现役最大单文件，且**无独立 `test_curate.py`**）—— P2-3 前先补测试
   - `clean.py`（705 行，MinHash/LSH/分块/分类修复混合）
   - `kb_common.py` / `rag.py` 两个 re-export shim（74 / 25 个调用者仍在走旧路径）

---

## 九、状态总账（2026-10-21 逐项核对代码得出）

### 9.1 已完成

| 项 | 状态 | 证据 |
|---|---|---|
| P0-1 `mirror_session` 清理 | ✅ | `memory_ingest_session.py` 已无 `def mirror_session` |
| P0-2 plugin-config 补齐 | ✅ | 实测注册 40 ↔ 配置 40，双向无缺口 |
| P0-3 测试拆分 | ✅ | 2260 行/37% → 400 行/7%，共 32 个 `test_*.py` |
| P1-1 `kb_common` 拆分 | ✅ 主体 | 1236 → 691 行 shim；拆出 `kb_io`/`kb_text`/`kb_metautil` |
| P1-2 `pyproject.toml` | ✅ | `[project]` + `optional-dependencies`（embed/llm/dev） |
| P1-3 `rag.py` 拆分 | ✅ | 1768 → 106 行 shim；拆出 `rag_index`/`rag_score`/`rag_query` |
| P2-1 插件层评估+试点 | ✅ | ops 域 10→1 `OpsPlugin`，CLI 契约零变更，294 passed |

### 9.2 未完成清单（按建议顺序）

#### ① P1-1 / P1-3 收尾：删除两个 re-export shim 🔶 中收益、低风险 —— 建议先做
`kb_common.py`（691 行）与 `rag.py`（106 行）现为 re-export shim，
仍有 **74 个** / **25 个**调用者走旧 import 路径。

- **动作**：分批把 `from kb_common import X` 按归属改为 `from kb_io|kb_text|kb_metautil import X`，
  `import rag` 改为直引三个子模块；每批跑 `pytest`，全绿后删 shim。
- **收益**：依赖关系从"74 个调用者指向一个假模块"变为指向真实模块，
  codegraph / IDE 跳转恢复准确，消除"改 shim 不知改了哪个真实模块"的误导。
- **风险**：低（纯 import 路径改写，行为不变）。
- **验证**：`pytest` 仍 294 passed + `grep -rn "from kb_common import"` 归零。
- ⚠️ **注意**：`kb_metautil.py` 是 §P1-1 原文规划之外新增的**第 4 个模块**，
  `record_lineage` / `is_stale` / `retrievable_status` / `kb_layer_of` / `root_of` /
  `parent_of` 归它管；迁移时按此归属拆分。

#### ② P2-2 研究层边界固化 ❌ 未动 —— 中等收益、中等风险
8 个研究层模块（~4000 行）无 CLI 入口（已实测确认无任何插件注册），
仅靠 `test_reachability.py` 的 `INTERNAL_MODULES` **手写清单**守护——会腐化。

- **动作**：移入 `pipeline/_research/` 子包 + `__init__.py` 标注 internal；
  把 `INTERNAL_MODULES` 改为从目录结构推导（`_research/` 下即 internal），
  `test_internal_list_not_stale` 已有的"清单不腐化"断言天然承接。
- **前置确认**：查生产模块是否 import 研究层符号（§P2-2 原文要求）。
- **验证**：`test_reachability.py` 全绿 + codegraph 反向依赖。

#### ③ P2-3 `curate.py` 子命令提取 ❌ 未动 —— 中等收益、中等风险
`curate.py` 仍 1288 行，现役最大单文件。

- **动作**：拆出 `cmd_run` / `cmd_verify` / `cmd_report` / `cmd_recheck` / `cmd_review`，
  保留 `curate_one` / `verify_note` / `apply_canon` 核心。
- ⚠️ **这是唯一没有测试安全网的 P2 项**：P0-3 拆的是 `test_p0_fixes.py`，
  并无独立 `test_curate.py`。**先补 `test_curate.py` 再拆分**。
- **验证**：`scripts/verify_curate.py` + 手动 `kb curate run/recheck/report` 冒烟。

#### ④ P2-1 剩余 4 域真合并 🔶 已试点未扩展 —— 建议**不主动做**
ops 域已跑通（可复制模板），其余 4 域各有 action 撞名（retrieval 3 /
crystallization 8 / ingestion 4 / organization 2 实例），必须改名 = API 变更。
收益只剩"文件数"，而撞名改名成本与 CLI 兼容风险显著更高（详见 §六 P2-1 推翻记录）。
若将来要做：先逐一评审撞名 action 改名表，并复用 `OpsAggregatePluginTest` 守卫模式。

#### ⑤ 遗留小项
- `agent_detect_plugin.py` / `agent_json_plugin.py` / `agent_md_plugin.py` /
  `agent_sqlite_plugin.py` 各 4-6 行 `sys.path.insert(0, pipeline_dir)`
  实测恒为 no-op（见 §六 P2-1 E 节），约 24 行装饰代码。
- 热点表已重排（见 §四）；`kb_engine.py`(771) / `clean.py`(705) /
  `memory_ingest.py`(656) 尚未治理，优先级低于 ①②③。

---

*本报告初查于 HEAD `5c9bd41`（2026-10-19）；§九 状态总账核对于 2026-10-21。改码后重跑 codegraph sync 复核。*
