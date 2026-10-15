# A2 组 · 插件层逐文件精读报告

- 项目根：`/home/mushan/workspace/kb-kit-pure`
- 范围：`template-vault/pipeline/plugins/` 下全部 `.py`
- 模式：只读精读（未修改任何项目文件；仅用 `/tmp` 下的临时探针脚本做只读内省）
- 报告：`.survey/A2-plugins.md`

## 0. 口径与方法（先声明两处与任务书的偏差）

1. **文件数**：任务书写「共 33 个文件」，但清单实际列出 **38 个名字**。目录实测
   `ls template-vault/pipeline/plugins/*.py | wc -l` = **38 个 .py**（`__init__.py` +
   `_subprocess_plugin.py` + **36 个插件模块**）。本报告以 38 个文件为准，一个不漏。
2. **插件数**：36 个插件实例（每个插件文件恰好定义 1 个具体 `PluginBase` 子类，已用
   `grep -c "^class "` 核对，`__init__.py` 为 0）。其中 **agent 型 4 个**、**kb_module 型 32 个**。
   该数字由一次只读内省实测得到（`PluginRegistry.discover()` + `list_plugins()`，root 指向
   `/tmp/kb-probe-root`，未触碰项目库）：
   `TOTAL PLUGINS: 36`，全部 `status=loaded`。
3. 关键结论均已实测或定位到 `文件:行号`；无法确证的写「未确认」。
4. 三条证据链：
   - 静态：逐个 `read` 完 38 个插件文件；
   - 框架：`read` 完 `plugin_base.py`(91 行)、`plugin_registry.py`(349 行)、`kb_launcher.py`(477 行)、`README.md`(314 行)；
   - 动态：用 `KBLauncher._parse_params` 与 monkeypatch 掉 `subprocess.run` 的探针，捕获真实 argv
     （**没有真的启动任何子进程、没有写任何库文件**）。

---

## 1. 框架契约（理解插件的前置事实）

| 事实 | 证据 |
|---|---|
| `PluginBase` 三个抽象方法：`metadata()` / `initialize(ctx)` / `execute(action, params)`，可选 `shutdown()` | `plugin_base.py:62-91` |
| `PluginMetadata` 字段：`name/version/type(plugin_type)/actions/dependencies/description/cli_aliases`，**无「是否写操作/是否破坏性」字段** | `plugin_base.py:35-59` |
| `PluginContext` DI 容器声明了 `vault_root/state_store/config/logger` | `plugin_base.py:18-32` |
| 注册中心发现规则：跳过 `_` 开头文件与不定义具体子类的模块；一个模块可注册多个插件；只注册 `__module__` 等于本模块名的类 | `plugin_registry.py:92-141` |
| `execute()` 的错误码约定：未知插件 `2`、禁用 `3`、初始化失败 `1`、动作不在 `actions` 里 `2`、执行抛异常 `1`（并打印 traceback） | `plugin_registry.py:233-259` |
| 插件名冲突只告警后覆盖 | `plugin_registry.py:163-166` |
| 启用/禁用来自 `reference/plugin-config.json`，缺省启用 | `plugin_registry.py:59-75` |

**launcher 参数解析（所有插件的入参都经它）**：`kb_launcher.py:217-245`
- `--key value` → `params["key"]=value`；`--key`（在 `BOOL_FLAGS` 里，或后面没值，或后面以 `--` 开头）→ `True`；
- 单个 `-x` → `params["x"]=True`（注意：丢掉了单横线语义）；
- 非 `-` 开头 → `params["_positional"]`；
- `BOOL_FLAGS` 是**硬编码 13 项白名单**：`kb_launcher.py:190-194`。

---

## 2. 总表（覆盖全部 38 个文件）

**图例**
- 型：`SUB` = 继承 `SubprocessPlugin`（起子进程）；`IN` = 进程内直接 `import` 实现模块调函数。
- 写：`○` 只读；`●` 写盘；`◐` 带 dry-run/`--apply` 闸门的写；`△` 只写报告/状态等非笔记产物。
- 返回码：`SUB` 一律 `= 子进程 returncode`（`_subprocess_plugin.py:70`）；`IN` `= 实现函数返回值`。
- 输出：`SUB` 不捕获输出，子进程 stdout/stderr 直接继承终端（`_subprocess_plugin.py:69`）；
  `IN` 由插件或实现自行 print。

| # | 文件 | plugin.name / v / type | 顶层命令（alias → action） | 型 | 委派实现模块 | 关键参数（透传） | 写 | 返回码/输出约定 |
|---|---|---|---|---|---|---|---|---|
| 1 | `__init__.py` | —（非插件，仅 1 行注释） | 无 | — | — | — | — | 被 `discover()` 跳过（`plugin_registry.py:96`）；整目录以 `plugins` 包路径 + 裸模块名双路径可 import |
| 2 | `_subprocess_plugin.py` | —（非插件，`_` 前缀被跳过） | 无 | — | `subprocess.run` | `SCRIPT_NAME` / `ACTION_MAP` | — | 见第 3 节专项 |
| 3 | `agent_detect_plugin.py` | `agent_registry` / 1.0 / agent | `agent`→None，`ingest-agent`→`ingest`；actions `detect,list,add,enable,setup,ingest` | **IN** | `agent_registry.py`:`cmd_detect/cmd_list/cmd_setup/cmd_ingest/cmd_add/cmd_enable` | `name,type,json/json_path,daily_src,root_src,evolve_json,opencode_db,project_context_root,enable,dry_run` | ● `add/setup/ingest` 写注册表与 KB（`ingest` 有 `--dry-run`）；`detect/list` ○ | rc=cmd_* 的 0/1；`out` 列表由插件 print（`:81-82`） |
| 4 | `agent_json_plugin.py` | `agent_json` / 1.0 / agent | 无 alias → 只能 `kb agent_json ingest` | **IN** | `memory_ingest_json.py:ingest` | `json_path` 或 `json`、`dry_run` | ● 写 KB 笔记 + git commit（`dry_run` 闸门） | **rc = 写入条数**（`memory_ingest_json.py:144`）← 见问题 P1-3 |
| 5 | `agent_md_plugin.py` | `agent_md` / 1.0 / agent | 无 alias → `kb agent_md mirror\|sync\|mirror-core\|extract` | **IN** | `memory_ingest.py:mirror/sync/mirror_core/extract` | `agent_src,agent_root,agent_name,density,min_chars,semantic` | ● mirror/sync/mirror-core/extract 全写 `memory/`+`reference/`+KB | rc=0/1（`memory_ingest.py:235,275,367,435,482,554`）；缺参直接 KeyError |
| 6 | `agent_sqlite_plugin.py` | `agent_sqlite` / 1.0 / agent | 无 alias → `kb agent_sqlite ingest` | **IN** | `memory_ingest_sqlite.py:ingest` | `data_dir`、`dry_run` | ● 写 KB 笔记（`dry_run` 闸门） | **rc = 写入条数**（`memory_ingest_sqlite.py:193`）← 见 P1-3 |
| 7 | `audit_plugin.py` | `audit` / 1.0 / kb_module | `audit`→`scan`；actions `scan,fix,log` | SUB | `kb_audit.py` | `scan`:`--json,--limit`；`fix`:`--confirm,--only`；`log`:`--json` | ◐ `fix` 默认预览、`--confirm` 落盘；`scan/log` ○ | rc 透传（`kb_audit.py:515-522`） |
| 8 | `backup_plugin.py` | `backup` / 1.0 / kb_module | `backup`→`backup`（ACTION_MAP `{"backup": None}`） | SUB | `backup.py` | 位置参数 `daily\|weekly` 或 `--kind` | ● 写 `backups/*.zip`+sha256 | rc 透传（`backup.py:68-75`） |
| 9 | `classify_plugin.py` | `classify` / 1.0 / kb_module | `classify`→`classify`（`None` 不加子命令） | SUB | `classify.py` | 位置参数 `text`、`--signals`、`--quality` | ○ | rc 透传；注意 `classify.py:226` 声明 `--root` **未使用** |
| 10 | `clean_plugin.py` | `clean` / 1.2 / kb_module | `clean`→`dry-run`；8 actions | SUB | `clean.py` | `--threshold,--apply,--include-evidence` | ◐ `taxonomy/repair/repair-headings` 默认 dry-run+`--apply`；`apply/chunk/refine` ●；`dry-run/report` ○ | rc 透传（`clean.py:544-560`） |
| 11 | `compile_plugin.py` | `compile` / 1.0 / kb_module | `compile`→None（action 由子命令决定） | SUB | `kb_query.py` | `query`:`--query,--answer,--json`；`apply`:`--id,--all,--json`；`status`:`--json` | △ `query` 只写提案 jsonl；● `apply` 写 KB；○ `status` | rc 透传（`kb_query.py:320-332`） |
| 12 | `confidence_plugin.py` | `confidence` / 1.0 / kb_module | `confidence`→`audit`；另有顶层 `decay`（`kb_launcher.py:98`） | SUB | `kb_confidence.py` | `show`:`--rel`；`audit`:`--json,--limit`；`apply`:`--dry-run,--limit`；`decay`:`--json,--limit` | ◐ `apply` 写笔记（`--dry-run`）；○ `show/audit/decay`（`decay` 只出建议，`kb_confidence.py:338-352`，实际改写只在 `cmd_apply` `:273`） | rc 透传（`kb_confidence.py:364-374`） |
| 13 | `contextual_plugin.py` | `contextual` / 1.0 / kb_module | `contextual`→`build` | SUB | `contextual.py` | `build`:`--llm,--limit,--dry-run,--json`；`show`:`--rel` | ◐ `build` 只写索引侧上下文（默认零成本 CCH，不改正文），`--dry-run` 预览 | rc 透传（`contextual.py:229-238`） |
| 14 | `contradict_plugin.py` | `contradict` / 1.0 / kb_module | `contradict`→`scan` | SUB | `kb_contradiction2.py` | `scan`:`--threshold,--json`；`apply`:`--threshold,--dry-run,--limit` | ◐ `apply` 写冲突标注+人工队列；○ `scan` | rc 透传（`kb_contradiction2.py:193-202`） |
| 15 | `curate_plugin.py` | `curate` / 1.1 / kb_module | `curate`→`plan`；actions `plan,run,verify,report,review` | SUB | `curate.py` | `plan/run`:`--domain,--limit`；`run`:`--apply,--model,--max-seconds,--prompt`；`verify`:`--batch,--pending`；`review`:`--limit`；全部 `--json` | ◐ `run` 默认只出提案、`--apply` 写正典；其余 ○ | rc 透传（`curate.py:1194-1219`）；**delegate 另有 `recheck` 未暴露**（`curate.py:1197`） |
| 16 | `dashboard_plugin.py` | `dashboard` / 1.0 / kb_module | `dashboard`→`generate` | SUB | `dashboard.py` | `--out` | ● 写仪表盘文件（`dashboard.py:236-237`） | rc 透传（`dashboard.py:101-104`） |
| 17 | `eval_plugin.py` | `eval` / 1.0 / kb_module | `eval`→`run` | SUB | `kb_eval_judge.py` | `run`:`--limit,--json`；`gold`:`--generate,--review`；`compare`:`--baseline` | △ `run/gold` 写评测产物（`kb_eval_judge.py:197-198,240-241,273-274`）；`compare` ○ | rc 透传（`kb_eval_judge.py:334-342`） |
| 18 | `feedback_plugin.py` | `feedback` / 1.0 / kb_module | `feedback`→`ingest` | SUB | `feedback_loop.py` | `ingest`:`--root`；`hit`:`--query,--hit,--useful`；`apply`:**仅 `--root`** | △ `ingest` 写 `feedback_hits.md`；● `apply` 改笔记 | rc 透传（`feedback_loop.py:465-476`）；**`apply` 无 `--dry-run`** ← 见 P1-10 |
| 19 | `graph_plugin.py` | `graph` / 1.0 / kb_module | `graph`→`build` | SUB | `graph.py` | `--domain,--content-type` | ● 写图谱文件（`graph.py:110-116`） | rc 透传（`graph.py:120-124`） |
| 20 | `healthcheck_plugin.py` | `healthcheck` / 1.0 / kb_module | `healthcheck`→`check`，`kb-healthcheck`→`check` | SUB | `kb-healthcheck.py` | 位置参数 `check`（`all\|summary\|deadlinks\|skeletons\|tags\|empty\|frontmatter\|discipline\|overlong\|orphans\|freshness`）、`--log,--checks,--html` | ○ 检测；`--log`/`--html` 写日志与 HTML | rc 透传（`kb-healthcheck.py:548-556`）；vault 不存在时脚本自身 `exit 3`（`:564`） |
| 21 | `health_metrics_plugin.py` | `health_metrics` / 1.0 / kb_module | `health-metrics`→`metrics` | SUB | `kb_health.py` | `--json` | ○（`kb_health.py:363-375` main 只读；`mark_policy_stale` 是**无调用者的死代码** `:342`） | rc 透传 |
| 22 | `ingest_chat_plugin.py` | `ingest_chat` / 1.0 / kb_module | 无 alias → `kb ingest_chat parse <file>` | SUB | `ingest_chat.py` | 位置参数 `file`、`--platform,--channel` | ○（只打印解析结果，`ingest_chat.py:313-329`） | rc 透传 |
| 23 | `ingest_convert_plugin.py` | `ingest_convert` / 1.0 / kb_module | 无 alias → `kb ingest_convert convert <file>` | SUB | `ingest_convert.py` | 位置参数 `file`、`--mirror-dir` | ● 复制到 mirror 目录（`ingest_convert.py:35,44`） | rc 透传 |
| 24 | `intake_plugin.py` | `intake` / 1.0 / kb_module | `ingest`→`review`；`move`/`trash` = `apply`+标志 | SUB | `intake_triage.py` | `review`:`--thr`；`apply`:`--thr,--move,--trash,--dry-run` | ● `apply/move/trash` 改笔记；○ `review` | rc 透传（`intake_triage.py:385-393`）；已实测 `move`→`apply --move`、`trash`→`apply --trash` |
| 25 | `link_plugin.py` | `link` / 1.0 / kb_module | `link`→`suggestions` | SUB | `link_engine.py` | `suggestions`:`--threshold`；`apply`:`--threshold,--limit,--dry-run`；`moc` | ◐ `apply` 写链接（有 `--dry-run`）；● `moc` 写 `_MOC.md`；○ `suggestions` | rc 透传（`link_engine.py:261-271`） |
| 26 | `memory_sync_plugin.py` | `memory_sync` / 1.0 / kb_module | 无 alias；由 `COMPAT_MAP` 提供 `memory-sync`（`kb_launcher.py:161`） | SUB | `memory_sync.py` | `review/promote`:`--density,--min-chars,--semantic,--no-semantic`；`promote` 另有 `--apply` | ◐ `promote --apply` 写笔记（`memory_sync.py:373-374`）；`review` 写状态/ledger（`:144,292-298`） | rc 透传（`memory_sync.py:427-443`） |
| 27 | `rag_plugin.py` | `rag` / 1.0 / kb_module | `query`→`query`，`rag`→None | SUB | `rag.py` | `index`:`--full`；`query`:位置参数 `q`、`--top,--answer,--json,--context,--context-budget,--session,--author,--min-importance,--enforce-level,--tags,--date-from,--date-to,--exclude-stale,--include-raw,--include-stubs` | ● `index` 写索引；○ `query`（`--session` 时记录命中） | rc 透传（`rag.py:55-82`） |
| 28 | `raw_plugin.py` | `raw` / 1.0 / kb_module | `raw`→`list` | SUB | `kb_raw.py` | `list`:`--json`；`show`:位置参数 `id`、`--full,--json,--lines,--path-only`；`find`:位置参数 `q`、`--limit,--context,--json`；`path`:位置参数 `id` | ○ 全只读（`kb_raw.py:319-332`） | rc 透传 |
| 29 | `recall_plugin.py` | `recall` / 1.0 / kb_module | `recall`→`deck` | SUB | `recall_schedule.py` | `deck`:`--n`；`mark`:`--deck,--recalls,--grade`；`status` | △ `mark` 写排期状态；其余 ○ | rc 透传（`recall_schedule.py:243-251`） |
| 30 | `rerank_plugin.py` | `rerank` / 1.0 / kb_module | 无 alias → `kb rerank rerank "q" --hits …` | SUB | `rerank.py` | 位置参数 `query`、`--hits`(JSON 串) | ○ 只读 | rc 透传（`rerank.py:131-136`） |
| 31 | `rsi_plugin.py` | `rsi` / 1.0 / kb_module | `rsi`→`probe` | SUB | `kb_rsi.py` | `--json,--verbose` | ○ 只读探针（仅追加 `.kb_rsi_proposals.jsonl` 运行记录，见 `rsi_plugin.py:5-7` 声明） | rc 透传（`kb_rsi.py:419-423`） |
| 32 | `schema_plugin.py` | `schema` / 1.0 / kb_module | `schema`→`check` | SUB | `kb_schema.py` | `check`:`--json,--limit`；`fields` | ○ 只读契约对照 | rc 透传（`kb_schema.py:176-182`） |
| 33 | `semantic_chunk_plugin.py` | `semantic_chunk` / 1.0 / kb_module | 无 alias → `kb semantic_chunk chunk <file>` | SUB | `semantic_chunk.py` | 位置参数 `file`、`--chunk-size,--overlap` | ○ 只打印分块清单（`semantic_chunk.py:280-296`） | rc 透传 |
| 34 | `sync_plugin.py` | `sync` / 1.0 / kb_module | `sync`→None；`ingest-any`→`ingest-any` | SUB | `sync.py` | `dry-run`:`--allow-existing-issues`；`apply`:位置参数 `note`、`--allow-existing-issues`；`rollback`:`--no-purge`；`history`；`ingest-any`:位置参数 `file`、`--author` | ● `apply/rollback/ingest-any` 写；○ `dry-run/history` | rc 透传（`sync.py:265-278`） |
| 35 | `tagfix_plugin.py` | `tagfix` / 1.0 / kb_module | `tagfix`→`scan` | SUB | `tag_fix.py` | `scan`:`--json,--include-raw`；`fix`:`--apply,--include-raw` | ◐ `fix` 默认 dry-run（`tag_fix.py:154-161`） | rc 透传 |
| 36 | `user_manager_plugin.py` | `user_manager` / 1.0 / kb_module | `user`→None | SUB | `user_manager.py` | `add`:位置参数 `name`、`--role,--display`；`list`；`remove`:位置参数 `name`；`check-perm`:位置参数 `name,perm` | ● `add/remove` 写用户配置；`list/check-perm` ○ | rc 透传（`user_manager.py:141-169`）；**delegate 另有 `inbox` 未暴露**（`:161`） |
| 37 | `validate_plugin.py` | `validate` / 1.0 / kb_module | `validate`→`validate`（`None` 不加子命令） | SUB | `validate.py` | `--strict,--json` | ○ 只读校验 | rc 透传（`validate.py:173-177`，`--strict` 时硬错→非零） |
| 38 | `verify_plugin.py` | `verify` / 1.0 / kb_module | `verify`→`all`（`None`，注释解释避免 `unrecognized arguments: all`，`verify_plugin.py:10-12`） | SUB | `kb_verify.py` | `--json,--with-index,--timeout` | ○（跑 5 项子检查，`kb_verify.py:30-39`） | rc 透传；`0=五项全绿`（`verify_plugin.py:19`） |

**统计**：SUB 32 个（#7–#38）、IN 4 个（#3–#6）、非插件文件 2 个（#1–#2）。

---

## 3. `_subprocess_plugin.py` 机制详解（75 行，全文读完）

### 3.1 子类协议
- `SCRIPT_NAME: str`（`:24`）——原模块文件名，如 `"rag.py"`。
- `ACTION_MAP: Dict[str, Optional[str]]`（`:25`）——action → 原模块子命令；`None` 表示「不加子命令」。
- 子类只实现 `metadata()`；`initialize()` 基类已实现：只把 ctx 存起来（`:27-28`）。

### 3.2 起子进程与传参（`:30-65`）
```python
root = params.get("root", self._ctx.vault_root)      # :31
script_path = Path(root) / "pipeline" / self.SCRIPT_NAME
cmd = [sys.executable, str(script_path)]             # :40  用同一个解释器
orig_sub = self.ACTION_MAP.get(action, action)       # :43  未登记则动作名当子命令
if orig_sub: cmd.append(orig_sub)
cmd.extend(["--root", str(root)])                    # :48  恒定注入 --root
for key, value in params.items():                    # :52
    if key in {"root", "_positional"}: continue
    if value is False or value is None: continue     # :55  无法表达显式 false
    flag = "--" + key.replace("_", "-")              # :57  下划线→连字符
    cmd.append(flag) if value is True else cmd.extend([flag, str(value)])
cmd.extend(params.get("_positional", []))            # :64-65  位置参数排在最后
```
要点：
- **脚本路径由 `params["root"]` 决定**，即「当前库」；不存在则报错返回 1（`:35-37`）。没有任何校验 root 是否真是库。
- 参数顺序恒为 `[子命令] --root <root> <用户参数…> <位置参数…>`；已实测（`kb query`）：
  `rag.py query --root ROOT --top 3 "…"`、`kb-healthcheck.py --root ROOT summary`、`kb_verify.py --root ROOT --with-index`。
- **参数名只做 `_`↔`-` 反转**，不做别名映射：用户必须用原脚本的 `--xxx` 拼法（launcher 已把 `--xxx` 归一为 `xxx`）。
- `ACTION_MAP.get(action, action)` 的兜底是**潜在陷阱**：任何 `metadata.actions` 里有、`ACTION_MAP` 里漏了的动作，都会被当作子命令塞给脚本；对无子命令的脚本就是一个非法位置参数。当前 32 个插件都已覆盖，未触发（属健壮性问题，见 P2-15）。
- `value is False` 被跳过（`:55`）：布尔假无法下传（当前 launcher 从不产生 `False`，只在 `intake_plugin.py:31` 注入 `True`，故无实际影响）。

### 3.3 输出捕获
**不捕获**。`subprocess.run(cmd)`（`:69`）未传 `capture_output`/`stdout`/`stderr`，子进程直接继承父进程的 fd：输出实时打到终端，插件无法读取、过滤、加前缀，也无法在返回前汇总。对比 `agent_registry.py:284-291`（`capture_output=True, text=True` 在 `:287`，随后回读拼接）——**两套风格并存**。

### 3.4 返回码透传
```python
result = subprocess.run(cmd); return result.returncode   # :69-70
except KeyboardInterrupt: return 130                     # :71-72
except (OSError, subprocess.CalledProcessError) as e: …  # :73-75 → return 1
```
- 原脚本退出码原样透传（含 `kb-healthcheck.py:569` 的 `exit 3`、argparse 的 `2`）。
- `CalledProcessError` **永不抛出**：`subprocess.run` 未传 `check=True`（默认 `False`）→ 该 except 分支是死代码（P3-25）。

### 3.5 超时
**没有超时**。全文件无 `timeout=` 参数、无 `Popen`+`wait(timeout)`、无信号处理。长任务（`clean refine`、`curate run --max-seconds`、`eval run`、`rag index --full`）只能靠 Ctrl-C。对比 `kb_verify.py:60` 自己实现了 `TimeoutExpired → code 124`——**超时能力在实现层各写各的，插件层缺位**（P3-26）。

### 3.6 哪些用 SUB、哪些 IN
- **SUB（32）**：`audit, backup, classify, clean, compile, confidence, contextual, contradict, curate, dashboard, eval, feedback, graph, healthcheck, health_metrics, ingest_chat, ingest_convert, intake, link, memory_sync, rag, raw, recall, rerank, rsi, schema, semantic_chunk, sync, tagfix, user_manager, validate, verify`。
- **IN（4，全部是 Agent 适配器）**：`agent_registry`（`agent_detect_plugin.py:29-83`）、`agent_json`（`:28-40`）、`agent_md`（`:28-52`）、`agent_sqlite`（`:28-40`）。它们的共同手法是先把 `root/pipeline` 插进 `sys.path`（如 `agent_detect_plugin.py:30-32`），再 `import` 实现模块调 `cmd_*`/`ingest` 函数。选择 IN 的原因：`agent_registry` 需要拿回结构化 `out` 列表（`agent_detect_plugin.py:36,81-82`）；三个 adapter 的实现模块没有稳定的「退出码式」CLI 契约/返回值就是条数。
- 唯一「因文件名不能 import 而必须 SUB」的显式说明：`healthcheck_plugin.py:5-7`（`kb-healthcheck.py` 含连字符）。

---

## 4. 「写操作」声明与 dry-run / 确认机制

**结论：插件层不存在任何写操作声明机制，也不存在 launcher 级 dry-run / 确认闸门。**

1. `PluginMetadata` 没有 `write` / `destructive` / `requires_confirm` 之类的字段（`plugin_base.py:35-59` 全部字段已列举）；36 个插件的 `metadata()` 调用中也从未出现此类关键字（grep `dependencies` 0 命中，同理无 write 字段）。
2. `plugin_registry.execute()`（`:233-259`）只校验「插件存在 / 未禁用 / 动作在 `actions` 里」，**不区分读写**，不做二次确认，不注入 `--dry-run`。
3. `kb_launcher.COMPAT_MAP`（`:26-186`）与 `_parse_params`（`:217-245`）同样无确认/交互逻辑；`--confirm` 只是被当成普通 `--flag` 透传给 `kb_audit.py`（`kb_audit.py:520-521`）。
4. 「写操作」目前**只是 README / help 的文案标注**，例如 `kb_launcher.py:361-362`（ingest move/trash 写操作）、`:374`（link apply 写操作）、`:407`（audit fix --confirm）、`:412`（tagfix fix 默认 dry-run）、`:426`（curate run --apply）、`:433`（sync apply 写操作）、`:441`（compile apply 写操作）；README 侧 `README.md:99-100,286-287`。
5. 真正起作用的安全闸门**全部下沉在各自实现里**，且形态不统一：
   - `--apply` 显式落盘：`clean.py:553,557,559`、`tag_fix.py:161`、`memory_sync.py:443`、`curate.py:1204`；
   - `--confirm`：`kb_audit.py:521`；
   - `--dry-run` 反向开关：`intake_triage.py:393`、`link_engine.py:269`、`kb_confidence.py:372`、`kb_contradiction2.py:201`、`contextual.py:235`、`agent_registry.py:408`（经 `agent_detect_plugin.py:46`）；
   - **完全没有闸门**：`feedback_loop.py:469`（`apply` 只有 `--root`）、`memory_ingest.py:mirror/sync`（默认就写）、`ingest_convert.py`（默认就复制）。
6. 附带：`PluginContext.state_store/config/logger`（`plugin_base.py:25-32`）从未被注入（`kb_launcher.py:212`、`plugin_registry.py:340` 只传 `vault_root`），所以插件也拿不到「可写/只读」之类的运行时配置。

---

## 5. 问题清单

### P1 — 功能损坏 / 静默失败（建议尽快修）

| # | 位置 | 类型 | 影响 | 建议 |
|---|---|---|---|---|
| P1-1 | `kb_launcher.py:191`（`--json` 在 `BOOL_FLAGS`）+ `agent_detect_plugin.py:55-56` + `README.md:261` | bug + 文档不符 + 静默失败 | `kb agent add --name dsh --type json --json <path>`（README 原文）解析为 `{"json": True, "_positional": ["<path>"]}`；插件只读 `params["json"]`，写入注册表 `sources: {"json": true}`，**路径丢失且 `_positional` 被彻底忽略**（实测 `cmd_add kwargs = {'sources': {'json': True}}`）。后续 `kb ingest agent` 走 `agent_registry.py:304`→`_json_sources`（`:323`）→ `memory_ingest_json.py:79-81` 打印「✗ JSON 源不存在: True」并 **`return 0`（静默成功）** | ① `--json` 从 `BOOL_FLAGS` 摘除（或改为「仅当下一 token 以 `-` 开头才算布尔」）；② `agent_detect_plugin` 消费 `_positional`；③ `memory_ingest_json.ingest` 源缺失应返回非零 |
| P1-2 | `agent_json_plugin.py:35-40` | bug（异常吞没→traceback） | 同一冲突下 `json_path` 变成 `True`，`Path(True)` 抛 `TypeError`（实测），由 `plugin_registry.py:254-259` 兜底打印 traceback 并返回 1；README 给的写法不可用，只能改用未文档化的 `--json-path`（实测可用） | 在插件里 `isinstance(json_path, str)` 校验并给出可读报错；统一文档为 `--json-path` |
| P1-3 | `agent_json_plugin.py:40`、`agent_sqlite_plugin.py:40`（实现侧 `memory_ingest_json.py:144`、`memory_ingest_sqlite.py:193`） | bug（返回码语义） | 插件把「写入条数」当退出码返回：成功写 5 条 → 进程退出码 5。破坏 `set -e`、cron、`kb … && …`，并与 `verify` 的「0=全绿」约定冲突 | 插件层 `return 0 if rc >= 0 else 1`，或实现改为返回 0 并把条数打日志 |
| P1-4 | `agent_detect_plugin.py:74-76` | bug（异常吞没） | `kb agent enable --name dsh --enable`（裸布尔，`kb_launcher.py:232-234` 会把它解释成 `True`）→ `True.lower()` 抛 `AttributeError`（实测），traceback + rc=1 | `enabled = str(params.get("enable","true")).lower() not in (...)`；或对 `True/False` 单独分支 |
| P1-5 | `agent_detect_plugin.py:44-47`（实现 `agent_registry.py:407`） | 静默失败 / 功能缺失 | `kb agent ingest --list`、`kb ingest agent --list` 的 `--list` 被完全丢弃（实测 `cmd_ingest` 只收到 `name/dry_run`），用户看不到预期清单，命令却返回 0 | 插件透传 `list` 或显式拒绝未知参数 |
| P1-6 | `agent_detect_plugin.py:65-67` + `kb_launcher.py:232-236` | 参数解析不一致 / 静默截断 | `agent_registry.py:400` 的 `--project-context-root` 是 `nargs="+"`，但 launcher 只吃下一个 token（实测 `{'project_context_root': '/a', '_positional': ['/b']}`），第 2 个起的 root 进 `_positional` 后**被丢弃** | launcher 支持重复取值，或插件从 `_positional` 补齐；文档标注「多次传 `--project-context-root`」 |
| P1-7 | `agent_md_plugin.py:39,41,43,46` | 异常吞没 / 不一致 | 直接 `params["agent_src"]` / `params["agent_root"]` 索引；缺参 → `KeyError` + traceback，而不是像 `agent_json_plugin.py:37-39`/`agent_sqlite_plugin.py:37-39` 那样友好报错 | 统一为「缺参 → 中文提示 + return 1」 |
| P1-8 | `kb_launcher.py:237-239` + `_subprocess_plugin.py:57` | 参数解析不一致 | 单横线短选项被改写成双横线：`-h` → `params["h"]=True` → `--h`（实测 argv 出现 `--h`），`kb query -h` 必失败（`--help` 正常） | 识别短选项并原样透传，或直接拒绝单横线参数并提示用 `--` |
| P1-9 | `kb_launcher.py:190-194`（`BOOL_FLAGS` 白名单） | 参数解析不一致 | 未列入白名单的布尔标志「值贪吃」：`kb clean --apply x.md` → `--apply x.md`（实测），子进程 argparse 报错；只有「位于末尾或后接 `--`」的布尔标志才正确 | 采用 `--flag=true/false` 显式语法，或让各插件声明自己的布尔标志集，由 launcher 组装 |
| P1-10 | `README.md:286` vs `feedback_loop.py:465-476` | 文档不符 | README 称「`kb link apply` / `kb feedback apply` / `kb ingest move` 写前先 `--dry-run`/`review` 看清单」，但 `feedback_loop.py:469` 的 `apply` 子命令**只注册了 `--root`**：`kb feedback apply --dry-run` 直接 argparse 报错 rc=2 | 给 `feedback_loop.py` 的 `apply` 加 `--dry-run`，或修 README 表述 |

### P2 — 设计 / 一致性 / 文档漂移

| # | 位置 | 类型 | 影响 | 建议 |
|---|---|---|---|---|
| P2-11 | `README.md:198-204` | 文档不符 | 「KB 模块（24）」实际列出 22 个名字，实际插件为 **32 个 kb_module + 4 agent = 36**（实测内省）；清单漏掉 `audit/confidence/contextual/contradict/curate/eval/raw/schema/tagfix/verify` 共 10 个（与 `git log --diff-filter=A` 显示的 2026-09-29 后新增文件吻合） | 用脚本从注册中心生成清单（可直接复用 `plugin_registry.cmd_list()` 输出） |
| P2-12 | `template-vault/reference/plugin-config.json` | 文档不符 / 过期 | 只列 26 个插件，缺上述 10 个；虽因 `plugin_registry.py:72-75` 缺省启用而不致故障，但该文件事实上被当成「插件清单」阅读 | 补齐或用 `_meta` 注明「仅覆盖需开关的插件」 |
| P2-13 | `curate_plugin.py:17-23` vs `curate.py:1197` | 功能缺失 | delegate 有第 6 个子命令 `recheck`（`--apply-pass/--export`），插件 `actions` 未登记 → `kb curate recheck` 被 `plugin_registry.py:248-251` 拒为 rc=2 | 若要暴露，补 `ACTION_MAP`/`actions`；否则在 `curate.py` 注释说明为内部命令 |
| P2-14 | `user_manager_plugin.py:10-15` vs `user_manager.py:161-163` | 功能缺失 | delegate 的 `inbox` 子命令不可达（同样 rc=2） | 同上（补 `"inbox": "inbox"` 与 `actions`） |
| P2-15 | `_subprocess_plugin.py:43` | 健壮性 / 潜在陷阱 | `ACTION_MAP.get(action, action)` 无校验：漏登记的动作会被当子命令塞给脚本，对无子命令脚本即非法位置参数（当前 32 插件均已覆盖，未触发） | 基类 `initialize` 或 `metadata()` 时断言 `set(actions) == set(ACTION_MAP)`（`None` 值也算覆盖） |
| P2-16 | `plugin_base.py:48-59` + `plugin_registry.py` 全文 | 设计缺口 | 无写操作元数据、无 dry-run/确认闸门（第 4 节详述） | 加 `writes: bool` / `requires_confirm: bool` 到 `PluginMetadata`，在 `execute()` 前统一拦一道 |
| P2-17 | `plugin_base.py:25-32` vs `kb_launcher.py:212`、`plugin_registry.py:340` | 死设计 / 文档不符 | `PluginContext` 声明的 DI（`state_store/config/logger`）从未被注入；`plugin_base.py:10-11` 的「插件不直接 import 其他模块，通过 context 访问共享服务」与实现相反（`agent_*` 插件直接 import 实现模块） | 要么接线 `config=plugin-config.json`，要么修正文档 |
| P2-18 | `plugin_base.py:89` + `plugin_registry.py:221-230` | 死代码 | `shutdown()` / `shutdown_all()` 无任何调用者（`kb_launcher.py` 只在 `:213` 调 `initialize_all`），生命周期「关闭」这一半不会执行 | 在 `KBLauncher.run` 结束处 `finally: shutdown_all()`，或删掉 |
| P2-19 | `plugin_base.py:57` + `plugin_registry.py:171-204` | 死代码 / 文档不符 | 36 个插件**无一**声明 `dependencies`（grep 0 命中）→ 拓扑排序退化为「按名字母序」（`plugin_registry.py:184` 的 `sorted`），`:196-200` 的依赖环检测分支不可达 | 删除或标注为扩展点；若保留，补一个声明了依赖的插件作为回归用例 |
| P2-20 | `plugin_registry.py:85-90` | 重复 / 风险 | 同时把 `pipeline/` 与 `pipeline/plugins/` 插入 `sys.path`，同一模块可能有 `x` 与 `plugins.x` 两个身份（`plugin_registry.py:109` 用裸名 `import_module`，而 kb_module 插件用 `from plugins._subprocess_plugin import …`），`isinstance`/类级别状态可能跨身份失配 | 只保留包路径导入，或明确禁止另一种拼法 |
| P2-21 | 全层重复 | 重复 | 32 个 SUB 插件逐字复制 `SCRIPT_NAME`/`ACTION_MAP`/`metadata` 三件套；其中恒等映射（如 `audit_plugin.py:10` 的 `{"scan":"scan","fix":"fix","log":"log"}`）在 `_subprocess_plugin.py:43` 兜底下等价于 `{}`，属纯冗余；`cli_aliases` 与 `kb_launcher.COMPAT_MAP` 是两份真值来源（易漂移，如 `memory_sync_plugin.py:12-18` 无 alias，全靠 `kb_launcher.py:161-163`） | 恒等映射一律写 `None`/省略；alias 只留一处（生成或校验一致性测试） |
| P2-22 | `ingest_chat_plugin.py:10`、`ingest_convert_plugin.py:10`、`rerank_plugin.py:10`、`semantic_chunk_plugin.py:10` | 参数解析不一致 / 可用性 | 既无 `cli_aliases` 也**不在 COMPAT_MAP**：只能敲 `kb <插件名> <同名动作>`，例如 `kb rerank rerank "q" --hits …`、`kb ingest_chat parse f.md`（`kb rerank "q"` 会被判为「不支持动作 查询」，`kb_launcher.py:305-308`）；README 第三部分完全未记录这 4 个命令 | 加 `COMPAT_MAP` 顶层别名或文档化「双写动作」用法 |

### P3 — 轻微 / 提示性

| # | 位置 | 类型 | 影响 | 建议 |
|---|---|---|---|---|
| P3-23 | `_subprocess_plugin.py:73` | 死代码 | `except (OSError, subprocess.CalledProcessError)` 中 `CalledProcessError` 永不抛出（`:69` 未传 `check=True`） | 删掉该异常类，或改 `check=False` 注释说明 |
| P3-24 | `_subprocess_plugin.py:69` | 设计缺口 | 无 `timeout=`，长任务可无限挂起（`kb_verify.py:60` 已自建超时→124，风格不统一） | 基类加 `TIMEOUT` 类属性，默认透传给 `subprocess.run(timeout=…)`，`TimeoutExpired → 124` |
| P3-25 | `_subprocess_plugin.py:69` | 能力缺口 | 不捕获输出，插件无法汇总/加前缀/结构化；与 `agent_registry.py:285-292` 的 `capture_output` 风格并存 | 提供 `capture` 开关，默认继承终端，需要时回读 |
| P3-26 | `kb_launcher.py:338-344` + `sync_plugin.py:10` | 语义不一致 | `kb sync`（无子命令）走到「打印动作列表并 return 0」，而 `sync.py:265-267` 的 argparse 是 `required=True`（真跑会报错）；rc=0 易被误判成功 | 无 action 时返回非零或明确提示用法 |
| P3-27 | `classify.py:226`（委派侧） | 无操作参数 | `classify.py` 显式声明 `--root`「兼容插件注入，未使用」，说明基类无条件注入 `--root`（`_subprocess_plugin.py:48`）对无 root 语义的脚本是噪音 | 允许插件声明 `INJECT_ROOT=False` |
| P3-28 | `agent_md_plugin.py:52` | 静默失败 | 未识别 action 时 `return 1` 且**无任何提示**（当前被 `plugin_registry.py:248` 前置拦截，实际不可达） | 加 stderr 提示（与 `agent_detect_plugin.py:78` 一致） |
| P3-29 | `agent_registry.py:389`、`kb_health.py:342`（委派侧，非 A2 文件） | 死代码 | `sub = ap.add_parser("sub") if False else ap` 是永久死亡分支；`mark_policy_stale()` 全仓无调用者（grep 仅定义处命中） | 顺手清理 |
| P3-30 | `tests/test_help_alignment.py:54-64` | 测试盲区 | 仅校验 `kb help` 里的**命令名**能被 `_resolve()` 或插件名命中，不校验 action/子命令/参数 → P1 全部逃逸（例：help/README 宣传的 `kb agent add --json` 写法实际是坏的） | 增加「每个 COMPAT_MAP 的 (plugin, action) 能在 `SCRIPT_NAME --help` 中得到校验」的对齐测试 |
| P3-31 | `kb.cmd` 全文（非 A2 文件） | 未确认 | 未确认退出码是否透传（`.cmd` 末尾无 `exit /b %ERRORLEVEL%`）；另 `set "VAULT=%VAULT:~0,-1}"` 疑似花括号笔误（`}` 应为 `)`）→ 该行可能整体不生效 | 建议由 Windows 侧负责人复核 |

---

## 6. README 第三部分「命令参考」交叉核对（`README.md:89-147`）

### 6.1 逐条核对结论
README 第三部分出现的命令，**全部都能在插件里找到对应实现**（无「宣传了但没实现」的命令）。逐条对应：

| README 命令 | 落地 | 证据 |
|---|---|---|
| `kb query` | `rag_plugin.py` | `README.md:94` → `kb_launcher.py:28` → `rag.py:61-82` |
| `kb rag index` | `rag_plugin.py` | `README.md:95` → `kb_launcher.py:30` → `rag.py:58-60` |
| `kb ingest` / `move` / `trash` / `agent` | `intake_plugin` / `agent_registry` | `README.md:98-101` → `kb_launcher.py:34-38` |
| `kb agent detect\|list\|add\|enable\|setup\|ingest` | `agent_detect_plugin` | `README.md:104` → `kb_launcher.py:42-48`（**写法有 bug，见 P1-1**） |
| `kb feedback [ingest\|apply\|hit]` | `feedback_plugin` | `README.md:107` → `kb_launcher.py:51-54`（**apply 无 `--dry-run`，见 P1-10**） |
| `kb link [suggestions\|apply\|moc]` | `link_plugin` | `README.md:108` → `kb_launcher.py:57-60` |
| `kb recall [deck\|mark\|status]` | `recall_plugin` | `README.md:109` → `kb_launcher.py:63-66` |
| `kb memory-sync [review\|promote]` | `memory_sync_plugin` | `README.md:110` → `kb_launcher.py:161-163` |
| `kb classify "<文本>"` | `classify_plugin` | `README.md:111` → `kb_launcher.py:177` |
| `kb graph` | `graph_plugin` | `README.md:112` → `kb_launcher.py:173` |
| `kb healthcheck [...]` | `healthcheck_plugin` | `README.md:115` → `kb_launcher.py:69`（位置参数透传 `kb-healthcheck.py:548-550`） |
| `kb validate [--strict]` | `validate_plugin` | `README.md:116` → `kb_launcher.py:145` |
| `kb dashboard` | `dashboard_plugin` | `README.md:117` → `kb_launcher.py:74` |
| `kb clean [dry-run\|apply\|chunk\|report\|refine\|taxonomy\|repair]` | `clean_plugin` | `README.md:118-120` → `kb_launcher.py:77-85`（**缺 `repair-headings`，见 6.3**） |
| `kb user [add\|list\|remove\|check-perm]` | `user_manager_plugin` | `README.md:121` → `kb_launcher.py:166-170` |
| `kb curate plan\|run\|verify\|report\|review` | `curate_plugin` | `README.md:124-128` → `kb_launcher.py:137-142` |
| `kb raw list\|show\|find\|path` | `raw_plugin` | `README.md:131-134` → `kb_launcher.py:130-134` |
| `kb sync [...]` | `sync_plugin` | `README.md:137` → `kb_launcher.py:152-158` |
| `kb backup [daily\|weekly]` | `backup_plugin` | `README.md:140` → `kb_launcher.py:149` + `backup.py:75` 位置参数 |
| `kb list` / `kb help` | 注册中心/launcher 直处理 | `README.md:141-142` → `kb_launcher.py:270-274,318-322` |
| `kb rsi [--json] [--verbose]` | `rsi_plugin` | `README.md:145` → `kb_launcher.py:181` |
| `kb compile query\|status\|apply` | `compile_plugin` | `README.md:146` → `kb_launcher.py:180` |

### 6.2 README 第三部分**缺失**的命令（插件已实现、但命令参考里查不到）
| 命令 | 实现 | 只在别处出现 |
|---|---|---|
| `kb audit [scan\|fix\|log]` | `audit_plugin.py` | 仅 `kb_launcher.py:405-408`（help） |
| `kb tagfix [scan\|fix]` | `tagfix_plugin.py` | 仅 `kb_launcher.py:411-412` |
| `kb confidence [show\|audit\|apply\|decay]`、`kb decay` | `confidence_plugin.py` | 仅 `kb_launcher.py:392-394` |
| `kb contradict [scan\|apply]` | `contradict_plugin.py` | 仅 `kb_launcher.py:395-396` |
| `kb eval [run\|gold\|compare]` | `eval_plugin.py` | 仅 `kb_launcher.py:399-402` |
| `kb schema [check\|fields]` | `schema_plugin.py` | 仅 `kb_launcher.py:388-389` |
| `kb contextual [build\|show]` | `contextual_plugin.py` | 仅 `kb_launcher.py:125-127`（且 help 未宣传该命令） |
| `kb verify [--with-index]` | `verify_plugin.py` | 仅 `kb_launcher.py:416` |
| `kb health-metrics` / `kb kb-healthcheck` | `health_metrics_plugin` / `healthcheck_plugin` | 无任何文档（仅 COMPAT_MAP `kb_launcher.py:70-71`） |
| `kb rerank` / `kb semantic_chunk` / `kb ingest_chat` / `kb ingest_convert` | 4 个 SUB 插件 | 仅在 README 插件清单出现，无命令写法（且需双写动作，见 P2-22） |
| `kb clean repair-headings [--apply] [--include-evidence]` | `clean_plugin.py:18` | README:118 未列；仅 `kb_launcher.py:386` |
| `kb ingest-any` | `sync_plugin.py`（`kb_launcher.py:158`） | README 未提 |
| `kb memory-sync promote` 的 `--apply`、`kb curate run --prompt`、`kb healthcheck --log/--html/--checks` | 各 delegate | 均未在 README 命令参考出现 |

### 6.3 **多余**的命令（README 写了但实现/参数对不上）
| README 处 | 问题 |
|---|---|
| `README.md:118` 的 `kb clean [... \| taxonomy \| repair]` | 反向缺失：插件有 `repair-headings`，README 未列（属 P2-11 一类文档漂移） |
| `README.md:107` 的 `kb feedback … apply` | 配合 `README.md:286` 的「写前先 `--dry-run`」暗示，但实现无 `--dry-run`（P1-10） |
| `README.md:261` 的 `kb agent add … --json <path>` | 命令可解析、但参数被吞（P1-1），属「写法错误」而非多余命令 |
| `README.md:198-204` 的插件清单 | 「24」与实际 32 不符，且列举 22 个（P2-11） |

### 6.4 结论
- **无「README 宣传但无实现」的命令**（这一层守得住）。
- 问题集中在另一侧：**10+ 个已实现命令未进命令参考**、4 个命令无可文档化写法、2 处 README 写法实际不可用（`--json`、`feedback apply --dry-run`），以及插件清单规模过期。

---

## 7. 未确认事项（不编造）

1. `kb.cmd` 的退出码透传与 `set "VAULT=%VAULT:~0,-1}"` 花括号笔误——属 Windows 侧，未在 Windows 环境验证（P3-31）。
2. `graph.py` 写出的图谱文件具体路径/是否受 `--domain` 影响——只确认「有写盘」（`graph.py:110-116`），未逐行追路径。
3. `memory_sync.py:373-374` 的 `promote --apply` 是否覆盖既有笔记（改名避让/墓碑逻辑）——未逐行确认，仅确认是写盘。
4. `kb_audit.py fix --only` 的取值语义（`kb_audit.py:521` 只看到 `default=None`）——未展开读取实现。
5. `plugin-config.json` 缺项的意图（是「只列需开关的」还是忘了更新）——无注释说明，未确认。
