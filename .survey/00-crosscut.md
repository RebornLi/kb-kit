# kb-kit 代码梳理 · 第 0 部分：跨模块主线（我本人核验，非子代理产出）

> 所有结论均给 `文件:行号`，已在本机实测的标注「实测」。
> 梳理时的仓库 = `/home/mushan/workspace/kb-kit-pure`（**dev 仓库 / 分发源**）。

---

## 1. 项目定位

一套「一条命令装出可自成长的 Obsidian 知识库 + 本地 RAG + LLM 治理管线」的套件。

- 零第三方依赖：全部 Python 标准库（README「零依赖」；实测 `import` 只出现 stdlib + 同包模块）。
- 跨平台：`install.sh` / `install.ps1` / `install.bat` 三套安装器，都调 `create_vault.py`。
- 数据流主线：`收件箱 → triage 路由 → 清洗/分块/去重 → 建链 → 索引 → 检索 →（LLM）结晶成正典 → 置信/矛盾/遗忘治理 → 巡检/评测/反馈`
- 交付形态：`create_vault.py` 把 `template-vault/` 整目录复制成用户的知识库；**运行时代码就活在用户知识库里**（`<vault>/pipeline/`），不是装成包。

---

## 2. 代码规模（实测 `wc -l`）

| 分区 | 行数 | 文件数 | 说明 |
|---|---|---|---|
| `template-vault/pipeline/*.py` | **24,314** | 69 | 引擎主体（业务模块 + 公共库） |
| `template-vault/pipeline/plugins/*.py` | 989 | 38 | 插件层（薄封装） |
| `scripts/*.py` | 1,194 | 4 | 安装期 precheck/verify/setup_obsidian/_download_utils |
| `template-vault/scripts/*.py` | 412 | 3 | 库内辅助脚本（bench/eval/verify_curate） |
| `tests/*.py` | 1,812 | 19 | 回归测试 |
| 根 `create_vault.py` + `gen_kb_arch.py` | 713 | 2 | 安装引擎 + 架构图 |
| shell/bat/ps1 | 629 | 10 | 启动器 + cron |
| JS/MJS | 1,205 | 10 | DSH / OpenClaw 注入插件 |
| Markdown（tracked） | 2,633+ | 52 | README/quickstart/CHANGELOG/docs/库内文档 |
| **Python 合计** | **29,434** | | |

单文件 TOP：`curate.py` 1241 · `kb_common.py` 1213 · `rag.py` 1145 · `kb_engine.py` 705 · `clean.py` 670 · `memory_ingest.py` 639 · `kb-healthcheck.py` 619。

---

## 3. 入口链路（主线，逐跳已核验）

```
用户敲 kb <工具> [子命令] [参数...]
  └─ template-vault/kb            bash，60 行；解析自身真实目录（跟随软链）→ VAULT
     │                            KB_ROOT := $KB_ROOT 或 VAULT；exec python3 pipeline/kb_launcher.py "$@"
     │                            kb.cmd 是 Windows 等价物（27 行）
  └─ pipeline/kb_launcher.py      main() → _extract_root(argv)（唯一支持 --root= 形式的地方，:459）
       │                          KBLauncher.run()  :261
       │                            ├─ 特殊命令 help/list  :270-274
       │                            ├─ 提取子命令（仅当 (tool,sub) 在 COMPAT_MAP）  :277-285
       │                            ├─ 查 COMPAT_MAP（160+ 条映射，:26-186） 或退化为插件名直调  :289-313
       │                            └─ _parse_params() :217  → params（含 _positional）
       │                               params.setdefault("root", vault_root)  :347
  └─ pipeline/plugin_registry.py  PluginRegistry.__init__(plugins_dir, vault_root, config_path)  :45
       │                          discover() :78  →  importlib.import_module(每个 plugins/*.py)
       │                          register() :144 →  读 metadata()，查 plugin-config.json enabled
       │                          initialize_all(ctx) :171 → Kahn 拓扑排序 + 逐插件 initialize()
       │                          execute(name, action, params) :233 → 动作白名单校验 + 异常隔离
  └─ pipeline/plugins/<X>_plugin.py
       ├─ SubprocessPlugin（多数）: _subprocess_plugin.py:30 起子进程跑原模块
       │     cmd = [sys.executable, <vault>/pipeline/<SCRIPT_NAME>, <原action>, --root <root>, ...flags, ...positionals]
       └─ 直接实现（agent_registry 等少数）
  └─ pipeline/<business>.py       业务模块自带的 argparse + main()
```

**关键设计点**
- `plugins/` 与 `pipeline/` 都被塞进 `sys.path`（`plugin_registry.py:85-90`），插件模块靠**裸模块名** import（脱离包结构，有 stdlib 撞名风险）。
- 插件发现跳过文件名以下划线开头的模块（`:96-97`），所以 `_subprocess_plugin.py` 不会被当插件。
- 只注册 `attr.__module__ == module_name` 的具体子类（`:129`），防重复注册。
- 依赖成环的插件被标 `disabled`（`:195-200`）；依赖缺失同样禁用（`:206-213`）。
- `execute()` 全异常捕获 + 打印 traceback（`:252-259`），错误隔离但不区分「插件 bug」与「外部依赖故障」。
- 插件清单（**实测 `kb list`**）：**4 个 agent 插件 + 32 个 kb_module 插件 = 36**。
  README「插件清单」一节只列了 22 个 KB 模块并写「24」，**已过时**（见 §9）。

---

## 4. 运行环境事实（实测，对优化很关键）

| 事实 | 证据 |
|---|---|
| 本机存在**两份物理代码副本** | dev = `/home/mushan/workspace/kb-kit-pure/template-vault/`；现役库 = `/home/mushan/kb-kit-pure/` |
| `KB_ROOT` 环境变量**已全局设置** | `echo $KB_ROOT` → `/home/mushan/kb-kit-pure` |
| 全局命令 `kb` 是软链 | `/usr/local/bin/kb -> /home/mushan/kb-kit-pure/kb`（root 所有） |
| 现役库规模 | 4,432 个 md；`vector index/df_idf.json` **18.4 MB**（n_docs=1531, vocab=83,591） |
| **dev 仓库落后于现役库 3 个文件** | `pipeline/kb_common.py`（+source_ref 引号感知分块 `_split_bracket_aware`）、`pipeline/kb_embed.py`（改为读 `model_config()` / `.kb/model.json` 而非 env）、`pipeline/taxonomy.py`（+`load_tag_whitelist()` 读 tag-taxonomy.json） |
| 部署方式 | 无脚本，靠手工把 dev 的 `template-vault/` 覆盖到现役库；**上面 3 处漂移证明这条链路会漏** |

> ⚠️ 对优化的直接影响：**改 dev 仓库不会影响正在运行的 `kb`**；且现役库里已有 3 个修复没回流到仓库。任何优化前必须先确定「以哪份为准」并补回这 3 个文件。

---

## 5. 数据模型与状态文件全景（当前最混乱的一块）

### 5.1 frontmatter 字段（README §6 + `reference/kb-schema.json` + 代码）

必填：`tags` `status`(draft→active→stable→legacy→archived) `domain`(中文域) `category`(project/experience/reference/sop/meta) `created` `updated` `importance`(0–1)
路由：`kb_target` `kb_action`(append/update/new/retire) `kb_summary`
生命周期：`review_after` `aliases` `redirect_to`
分层：`kb_layer` ∈ `canon`(×1.25) / `page`(×1.0) / `raw`(×0.72) / `index`(不入索引) / `noise`(不入索引)
检索辅助：`chunk_of` / 父子块链 / `confidence` / `source_ref` / `conflicts_with` / `is_source`

### 5.2 状态文件位置——**存在 5 套互不统一的约定**（实测 grep `_PATH`/`_FILE` 常量）

| 约定 | 落点 | 使用方 |
|---|---|---|
| A. `root/.kb/state/<x>.json`（经 StateStore，**带 fcntl 锁**） | 库内 `.kb/state/` | `state_manager.py`（被 rag/recall/memory_*/feedback/kb_usage/kb_claim/kb_adaptretrieve/kb_selfweight 9 个模块用） |
| B. `root/.kb/<x>.json(l)`（**无锁、非原子**） | 库内 `.kb/` | `curate.py:38-41`、`contextual.py:29`、`embed_rerank.py:27`、`llm_rerank.py:31`（+`_EXPAND_CACHE_PATH:156`） |
| C. `root/.kb_<x>.json(l)`（**散落库根**） | **库根 dotfile** | `kb_l4` `.kb_l4_state.json/.kb_l4_config.json/.kb_l4_verdicts.jsonl`、`kb_l5` `.kb_l5_state.json/.kb_l5_policy.json`、`kb_meta` `.kb_meta_{proposals,state,config}`、`kb_retriage` `.kb_retriage_*`、`kb_selfweight` `.kb_selfweight_*`、`kb_adaptretrieve` `.kb_adaptretrieve_proposals.jsonl`、`kb_contradiction` `.kb_contradictions.json`、`kb_query` `.kb_query_{proposals,state}`、`evolution_log` `.kb_evolution.jsonl`、`memory_ingest*` `.memory_ingest_state.json`、`recall_schedule` `.recall_state.json`、`feedback_loop` `.feedback_state.json`、`agent_registry` `kb-agent.json`、`kb_rsi`/`kb_engine` `.kb_engine_proposals.jsonl`/`.kb_rsi_proposals.jsonl` |
| D. `root/pipeline/<x>`（**把运行时态写进代码目录**） | `<vault>/pipeline/` | `kb_selfweight.py:77,81`（`.kb_selfweight_proposals.jsonl` / `.kb_selfweight_applied.json`）、`kb_engine.py:179,387,406,414,438,493,692`（`.kb_engine_proposals.jsonl`） |
| E. `Path(__file__).parent/<x>`（**配置写进源码目录**） | `<vault>/pipeline/` | `kb_engine.py:32` `CONFIG_PATH = .kb_rsi_config.json`、`kb_eval.py:43` `TEST_DIR/eval_config.json` |

**证据（实测现役库）**：`.kb/embed_cache.json` 25.6 MB、`.kb/state/feedback_state.json` 2.1 MB、`.kb/curate_ledger.jsonl` 1.6 MB、`.kb/state/query_cache.json` 927 KB、`.kb/curate_state.json` 298 KB、库根 `.memory_ingest_state.json` 62 KB。

**衍生问题**
1. 库根被 dotfile 污染（C 类 20+ 个），而 `template-vault/.gitignore` 只逐条 ignore，**新增一个状态文件极易忘记加 ignore 而被提交**。
2. D/E 类**在只读安装或升级时会失败/丢失**（代码目录被替换）。
3. 只有状态约定 A 有文件锁，且 `state_manager.py:15` 明写「Windows 降级为无锁」。
4. `kb_consistency.py:32` 与 `kb_drift.py:15` **共用同一个 LEDGER 名 `promotions.jsonl`**，两者互不知情。

### 5.3 索引与其它产物

| 产物 | 位置 | 格式 | 写入方式 |
|---|---|---|---|
| 向量索引 | `<vault>/vector index/df_idf.json` | 单 JSON：`version=4, n_docs, vocab, idf, tf, meta, doc_hashes, doc_mtimes, inverted_index` | `rag.py:231,314` 直接 `write_text`（**非原子**；写坏 = 索引全废） |
| 同步状态 | `.kb/sync_state.json` | — | `sync.py` |
| 血缘 | `.kb/state/lineage.jsonl` | from→to 追加行 | `kb_common.py:468-470` |
| 评测 | `.kb/eval/{gold_candidates.jsonl,last_run.json,baseline-p4.json}` | — | `kb_eval.py` |
| 备份 | `<vault>/backups/`（实测 28 MB） | zip + sha256 | `backup.py` |
| 日志 | `<vault>/logs/`（实测 340 KB） | — | cron 脚本 |

---

## 6. 模块依赖图（实测：ast 无关的正则扫描 + 人工核对）

**fan-in TOP（被依赖次数）**
```
42  kb_common        ← 几乎所有模块（唯一事实源式公共库）
14  kb_rsi           ← 研究层 8 个模块（compute 信号复用）
11  kb_constants     ← 阈值常量中心
 9  state_manager
 8  evolution_log
 6  rag
 4  feedback_loop / kb_usage / kb_embed
 2  classify / plugin_base / kb_claim / memory_ingest_sqlite / taxonomy
 1  （12 个模块只被 1 个模块引用）
```

**零 fan-in 的 32 个模块**：多数是 `SubprocessPlugin` 通过**子进程**调用（静态 import 扫不到），并非死代码。真正的可达性由 `tests/test_reachability.py` 守卫：从「插件 SCRIPT_NAME + scripts/*.sh 引用 + kb / kb.cmd 引用」做 BFS，任何既不可达又不在 `INTERNAL_MODULES`（19 条，含理由）里的模块会让测试红。**这是一条很好的架构守卫，优化时要保留。**

可疑点：`kb_types.py`（Frontmatter/NoteInfo/NoteRecord/SimResult 的 TypedDict 集中定义）**全项目零 import**，只被写进 `INTERNAL_MODULES` 声明为「类型定义库」——事实上没人用。

**重复实现清单（同名顶层函数 ≥3 份）**
```
_vec ×5   _tokenize ×4   _write_proposals ×4   _state_path ×4   _prop_path ×4
load_state ×4   load_index ×4   _load_state ×4   save_state ×4
_cos ×3   _split_body ×3   sha ×3   _safe_kb_path ×3   _fm_block ×3
_read_state ×3   _write_state ×3   _config_path ×3   load_config ×3   load_schema ×3
```
另有 56 个模块各自定义 `main()`（正常，但与 989 行的 plugins 薄封装层叠加后，CLI 面被定义了两遍）。

---

## 7. 测试基线（实测）

```
$ env -u KB_ROOT python3 -m unittest discover -s tests
Ran 69 tests in 0.853s
OK
$ git status --short        # 空 → 测试不污染仓库
```
- README §9 写「66 条」，**实际 69 条**（文档漂移）。
- 19 个测试文件，全部 stdlib unittest，用 tmp 目录造库，0.85s 全绿 → 秒级反馈，适合做优化的安全网。
- **26 / 69 个 pipeline 模块无任何测试触达**（含最关键的几个）：
  `curate.py` `curate_prompt.py` `kb_query.py` `kb_raw.py` `kb_verify.py` `sync.py` `memory_sync.py` `memory_ingest_sqlite.py` `memory_ingest_json.py` `dashboard.py` `kb_rsi.py` `kb_audit.py` `kb-confidence` 相关 `kb_confidence.py` `kb_health.py` `kb_eval_judge.py` `kb_embed.py` `rerank.py` `embed_rerank.py` `llm_rerank.py` `tag_fix.py` `user_manager.py` `evolution_log.py` `kb_constants.py` `kb_contradiction2.py` `ingest_chat.py` `ingest_convert.py`
  → **`kb query`（主检索链路）与 `kb curate`（唯一会写正典的链路）都没有端到端测试**，是优化时最大的风险面。

---

## 8. 已实测确认的缺陷（第一批，按严重度）

### 8.1 【高】CLI 参数解析不支持 `--key=value`（实测）
`kb_launcher._parse_params`（`kb_launcher.py:217-245`）只认 `--key value`：
```
['--top=3','hello']       -> {'top=3': 'hello'}      # 键名带 = 号，值被吞
['--top','3','hello']     -> {'top': '3', '_positional': ['hello']}   # 正常
```
只有 `--root=` 被 `_extract_root`（`:459`）特判。用户按 common 习惯写 `--top=5` 会静默错传。

### 8.2 【高】`BOOL_FLAGS` 白名单不全 → 28 个 store_true 旗标会吞掉下一个参数
`kb_launcher.py:190-194` 只登记 13 个无值旗标。全项目 39 个 `store_true` 旗标里，**29 个未登记**（精确 AST 式括号配对提取，已核对）：
`--apply --all --apply-pass --ab-report --allow-existing-issues --auto --confirm --decay --exclude-stale --export --force --include-raw --include-stubs --include-evidence --llm --log --no-purge --out --path-only --pending --quality --report --reset-config --review --status --stdin --strict --with-index --generate`
实测：`kb query --exclude-stale "怎么备份"` → `{'exclude_stale': '怎么备份'}`（query 文本被当成旗标值；因 `rag.py` 的 query 子解析器恰好只有 1 个位置参数才「侥幸跑通」，见实测输出正常返回 3 条命中）。**换任一位置参数 ≥2 个的子命令即失败**。

### 8.3 【高】`kb clean --apply` 直接报错（README 与 `kb help` 都推荐这个写法）
- README §3：「`kb clean --apply`　清洗/分块（写操作）」
- `kb help` 第 382 行同样写 `kb clean --apply`
- 实测：
```
$ python3 pipeline/kb_launcher.py clean --apply
clean.py: error: unrecognized arguments: --apply      # 退出码 2
```
根因：`--apply` 以 `-` 开头 → `sub` 提取不到（`:279`）→ `COMPAT_MAP[("clean",None)] = ("clean","dry-run")` → 变成 `clean.py dry-run --apply`，而 `dry-run` 子解析器没有 `--apply`。正确写法是 `kb clean apply`。

### 8.4 【中】插件清单文档过时
README 写「KB 模块（24）」并实列 22 个；**实测 32 个**（多出 `audit/compile/confidence/contextual/contradict/curate/eval/raw/schema/tagfix/verify` 等 11 个）。

### 8.5 【中】仓库里跟踪了本机私有配置
`kb-context-dsh/.env` **已入库**（`git ls-files` 命中），内含作者当前生效配置：`KB_ROOT=/home/mushan/kb-kit-pure`、`KB_MIN_SCORE=0.18`、systemd 服务名与 `systemctl --user set-environment` 具体命令。与 `.env.example` 内容不同（后者是模板）。无 API key 泄漏，但有私人路径与本机部署细节。

### 8.6 【中】模板里的 cron 脚本硬编码作者的家目录
```
template-vault/scripts/kb-curate.sh:28            VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"
template-vault/scripts/kb-daily-supervisor.sh:26  VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"
template-vault/scripts/kb-verify-cron.sh:3        VAULT="${1:-/home/mushan/kb-kit-pure}"
```
`create_vault.py` **不会**重写这三行（grep 无命中）。用户照抄这三条 cron 会指向作者的家目录。
（对照组：`growth_cron.sh:19` / `healthcheck_cron.sh:11` 用 `${KB_ROOT:-}` 并在为空时报错退出——是正确做法。）

### 8.7 【中】状态写入原子性/并发
- `state_manager.StateStore` 有 `fcntl.flock` + 临时文件 + `os.replace`（`:39-76`），但**只有 11 个模块用**；另有 **47 个模块直接 `open(...,'w')`/`write_text`** 写文件。
- `rag.py:231,314` 用 `write_text` 覆写 18 MB 索引，**非原子**；进程中断即索引损坏。
- Windows 无锁（`state_manager.py:15`）。

### 8.8 【低】`--strict` 等旗标被登记但位置语义不同
`kb validate --strict` 可用（子解析器有该旗标且 `sub` 为 None 时 action 已固定为 `validate`），但 `kb clean --apply` 就崩——**同一种写法两种结果**，用户无法从 help 判断。

---

## 9. 性能基线（实测）

| 指标 | 实测值 | 说明 |
|---|---|---|
| `kb list`（含 36 插件发现 + 初始化） | **0.03 s** | 小到不是瓶颈 |
| `kb help`（不初始化） | 0.03 s | — |
| `json.load(18.4 MB df_idf.json)` | **201 ms** | n_docs=1531 / vocab=83,591；**每次查询都要付** |
| `.kb/embed_cache.json` | **25.6 MB** | embed_rerank 全量 json.load |
| `.kb/state/feedback_state.json` | **2.1 MB** | 反馈状态单体 JSON |
| `.kb/curate_ledger.jsonl` | 1.6 MB | 追加式，尚可 |
| `.kb/state/query_cache.json` | 927 KB | — |

**判断**：检索侧的主要成本不是算法而是 **「单个巨型 JSON 全量读写」**。查询路径 = 读 18 MB 索引（~200 ms）+ 可能的 25 MB embed 缓存 + 写回 query_cache；索引重建时还要整份序列化。这是最值得优先优化的方向（分片/二进制/增量/或换 SQLite）。

---

## 10. 写侧协议合规（对照 `docs/write-side-protocol.md` 实测统计）

协议三条硬约定：① 预览优先 ② 预览可数可指认 ③ 写入可回滚（写前 checkpoint + 只精确 `git add --` + 破坏性动作写 lineage）。

| 模块 | dry-run 提及 | git commit | 精确 `add --` | 备注 |
|---|---|---|---|---|
| `clean.py` | 17 | 8 | 经 `_stage_paths():152` | 合规样板 |
| `curate.py` | 1 | 1（pre-curate checkpoint `:136`） | — | 默认只出提案 |
| `intake_triage.py` | 11 | 1 | — | — |
| `link_engine.py` | 7 | 1 | 1 | — |
| `memory_sync.py` | 9 | 1 | 1 | — |
| `feedback_loop.py` | **0** | 3 | 3 | **无 dry-run**（`feedback` 命令无预览） |
| `kb_audit.py` | 3 | **0** | 1 | 有 `--confirm` 闸，但无写前 checkpoint |
| `kb_contradiction.py` | **0** | 2 | 1 | 无 dry-run |
| `kb_contradiction2.py` | 4 | **0** | 1 | 与上一个功能重叠 |
| `tag_fix.py` | 3 | **0** | **0** | 默认 dry-run，但写回无 commit/精确 add |
| `kb_retriage.py` | **0** | 1 | 1 | — |
| `kb_selfweight.py` | **0** | 1 | 1 | 且状态写进 `pipeline/`（约定 D） |
| `kb_meta.py` / `kb_l4.py` / `kb_l5.py` | 0 | 各自 1（`_checkpoint`） | — | 研究层，人工在环 |
| `sync.py` | 6 | 2 | 1 | — |

lineage 只有 `kb_common.py:468` 一个写入口（`.kb/state/lineage.jsonl`）——协议执行是收敛的，好。

---

## 11. 仓库卫生

- `git status` 干净；`__pycache__/`、`.kb/`、`vector index/`、`backups/`、`logs/`、`.codegraph/`、`.agents/` 均已被忽略。
- `template-vault/.kb/state/{feedback_state.json,query_cache.json}` 与 `template-vault/vector index/df_idf.json` **未被跟踪**（属运行时态，正确）。
- `template-vault/raw/` 内含 3 个**真实抓取物**（`AI理论/2310-06770-swe-bench-….md`、`model-collapse-finding.md`、`外部/different-note.md`）——是示例还是作者私有内容需确认（会进用户索引）。
- 根 `CHANGELOG.md` 25 KB、`README.md` 16 KB，`.survey/`（本次梳理产物）与 `docs/` 的 CODE-SURVEY 属新增未跟踪文件。
