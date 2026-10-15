# A 组全文精读报告：安装 / 建仓 / 入口 / 插件骨架

- 项目根：`/home/mushan/workspace/kb-kit-pure`
- 阅读方式：read 工具逐文件全文读完（大文件分段），共 13 个文件 / 2945 行。
- 除静态阅读外，做了 3 项**只读**验证（不修改任何代码/数据）：
  1. 用 AST 提取 `pipeline/plugins/*.py` 的 `PluginMetadata`（名称 / actions / cli_aliases），与 `kb_launcher.COMPAT_MAP` 求差集；
  2. 用 stub 注册中心（monkeypatch `PluginRegistry`，不执行任何插件）实测 `kb_launcher.KBLauncher.run()` 的路由与参数解析结果；
  3. 用 import 后的 `create_vault.parse_args()` 实测 flag 接受情况（未执行 `main()`，无文件系统副作用）。
- 全文凡标「未确认」者，均为本机（Linux，无 Windows/macOS 环境）**无法实测**的推断；其余结论均可追溯到 `文件:行号`。

---

## 一、本组职责与在系统中的位置

### 1.1 定位

本组是 kb-kit 的「入口层」：把 `template-vault/` 这份模板知识库复制成用户自己的 vault，并在全球用户机器上把「怎么启动 `kb`」这件事做对。三层结构：

| 层 | 文件 | 作用 |
|---|---|---|
| 安装期（仓库侧，不进 vault） | `install.sh` / `install.ps1` / `install.bat` → `scripts/precheck.py` → `create_vault.py` → `scripts/verify.py` | 环境补齐、复制模板、接线、自检 |
| 运行期（进 vault） | `template-vault/kb` / `template-vault/kb.cmd` → `template-vault/pipeline/kb_launcher.py` → `plugin_registry.py` → `pipeline/plugins/*.py` | 把 `kb <命令>` 路由到插件 |
| 契约层（进 vault） | `template-vault/pipeline/plugin_base.py` | `PluginBase` / `PluginMetadata` / `PluginContext` 由所有插件继承 |

关键边界：`scripts/*.py`（precheck/verify/setup_obsidian/_download_utils）**不进 vault**；`template-vault/scripts/` 里是另一套 10 个文件（`growth_cron.sh`、`healthcheck_cron.sh` 等），两套同名目录语义不同（见问题 34）。`create_vault.py:291` 的自检调用的是**仓库侧**的 `scripts/verify.py`。

### 1.2 create_vault 建仓的完整步骤链条（按代码真实执行顺序）

入口 `create_vault.py:505 main()`（`create_vault.py:552-553` 用 `sys.exit(main())`）：

| # | 步骤 | 位置（文件:行） | 说明 |
|---|---|---|---|
| 0 | 三平台安装器先跑环境自检 | `install.sh:38-44`、`install.ps1:35-44`、`install.bat:31-39` | precheck 在 `create_vault` **之前**执行；失败一律退出 3 |
| 0.1 | precheck 检测 Python | `precheck.py:455-463`（`check_python` `:58-87`；失败则 `install_python` `:93-110` → `:113/137/160`） | Python 是唯一硬门槛（`all_passed` `:41-43` 只看 python） |
| 0.2 | precheck 检测/自动安装 Obsidian | `precheck.py:466-471`（`check_obsidian` `:217-228`） | 可选；但会**未经确认**自动下载安装（见问题 3） |
| 0.3 | precheck 检测 git | `precheck.py:474-478`（`check_git` `:234-245`） | 可选 |
| 0.4 | precheck 交互选 agent + 部署外部插件 | `precheck.py:486` → `select_and_install_agents` `:295-402`、`_deploy_agent_plugin` `:405-443` | 此时 **vault 还不存在**（`vault=None`），故只写"一次性偏好文件" `~/.kb-kit-agent-prefs.json`（`precheck.py:281`、`:284-292`） |
| 1 | 参数解析 | `create_vault.py:506` → `parse_args` `:38-50` | 8 个 flag，见 2.1 |
| 2 | 模板目录存在性校验 | `create_vault.py:507-509` | 缺失 → `return 2` |
| 3 | 目标路径解析 | `create_vault.py:511-512` | 相对路径按 **CWD** `resolve()`（install.sh/bat 传相对值，install.ps1 传绝对/脚本目录相对值，见问题 18） |
| 4 | **清理 / 目标准备**（`ensure_target`） | `create_vault.py:514` → `:53-71` | 不存在→mkdir；空/只剩文件→复用；非空且无 `--force`→报错 `return 2`；`--force`→**逐项 rmtree/unlink 清空**（`:63-68`） |
| 5 | **复制模板**（`copy_template`） | `create_vault.py:516` → `:74-80` | `shutil.copytree(..., dirs_exist_ok=True, ignore=STATE_IGNORE)`；`STATE_IGNORE` 在 `:32-35`，只排除 `.git`/`__pycache__`/`*.pyc`/`.pytest_cache`/`*.egg-info`/`vector index` |
| 6 | **接线 agent**（`wire_agents`） | `create_vault.py:520` → `:145-195` | 把 `vault/pipeline` 插入 `sys.path`（`:148`）→ `import agent_registry`（`:150`）→ `detect_agents()`（`:157`）→ 读并**消费删除** precheck 偏好（`:120-132`）→ 写 `vault/kb-agent.json`（`:190`；无 agent 时 `:160` 或失败时 `_write_empty_registry` `:135-142`） |
| 7 | **demo 建索引** | `create_vault.py:522-523` → `:198-215` | `subprocess.run([sys.executable, vault/pipeline/rag.py, "index", "--root", vault])`，timeout 300s（`:206-207`），只回显末 6 行（`:211-213`） |
| 8 | **git init + 首次提交** | `create_vault.py:525-526` → `:218-232` | `git init -q` → `add -A` → 写**仓库级** user.name/email（`:223-224`）→ `commit -q -m "chore: 初始知识库（由 create_vault.py 安装）"`（`:225-229`） |
| 9 | **注册全局 kb** | `create_vault.py:528-529` → `:235-286` | Linux/macOS：`~/.local/bin/kb → <vault>/kb` 符号链接（`:261-272`）+ 往 shell rc 追加 PATH（`:274-284`）；Windows：把 vault 目录写进用户 PATH（`:238-259`） |
| 10 | 兜底首页 | `create_vault.py:531` → `:325-382` | 仅当 `🏠-知识库首页.md` 不存在时生成（模板已带，故正常为 no-op；幂等由 `:332-333` 保证） |
| 11 | 兜底快捷卡 | `create_vault.py:532` → `:385-502` | 同上（`如何使用.md`，`:387-389`） |
| 12 | **写安装基线清单** | `create_vault.py:533` → `:96-117` | 清单 = 模板将被复制的文件（`_template_rel_files` `:83-93`）+ 三个生成文件（`:103`）→ `vault/.kb/install-manifest.json`（`:111`） |
| 13 | **自检**（调 `scripts/verify.py`） | `create_vault.py:535-538` → `:289-306` | `subprocess.run([python, REPO/scripts/verify.py, --vault, vault], timeout=60)`；**失败只打印 warning，不影响返回值**（问题 5） |
| 14 | **打开 Obsidian** | `create_vault.py:540-541` → `:309-322` | `obsidian://open?path=<urlquote(vault)>`，Windows `os.startfile`、macOS `open`、其它 `xdg-open`（`:314-319`） |
| 15 | 结束提示 | `create_vault.py:543-548` | `return 0` |

任务书里给的顺序是「precheck → copy → 清理 → 接线 → demo → git → 全局 kb → 打开 Obsidian → verify」；**代码真实顺序不同**，有两点值得注意：

- **「清理」在 copy 之前**（`ensure_target` `:53-71`），且只服务于 `--force`；复制后**没有任何清理步骤**，模板目录里的运行时残留会原样进用户 vault（问题 15）。
- **verify 在最后**（`:536`），而 `git commit`（`:526`）与 manifest（`:533`）之间有 10/11/12 三个写文件步骤 → **首次提交漏掉这些文件**，装完 `git status` 是脏的（问题 14）。

### 1.3 运行期链条（装完之后）

```
kb / kb.cmd
  ├─ kb:31        set -euo pipefail
  ├─ kb:36-42     跟随符号链接解析自身真实目录（全局 kb 是软链，必须解链）
  ├─ kb:43        VAULT = 脚本所在目录
  ├─ kb:59        export KB_ROOT="${KB_ROOT:-$VAULT}"   # 已有 KB_ROOT 优先
  └─ kb:60        exec python3 "$VAULT/pipeline/kb_launcher.py" "$@"
        └─ kb_launcher.py:470-473  main() → _extract_root(:450-465) → KBLauncher.run(:261-348)
              ├─ :270-274   help / list 特例
              ├─ :276-285   子命令识别（仅当 (tool,sub) 在 COMPAT_MAP 中）
              ├─ :288-313   _resolve 命中 → __list__/__help__ 特例 → 未命中则按插件名直呼
              ├─ :208-214   _ensure_initialized → registry.discover() + initialize_all(ctx)
              ├─ :217-245   _parse_params
              └─ :346-348   params.setdefault("root", vault_root) → registry.execute
                    └─ plugin_registry.py:78-141 discover() → importlib.import_module(模块名)
                          → :100-141 遍历 dir(mod) 实例化所有具体 PluginBase 子类
                          → :144-168 register()（配置禁用 / 命名冲突）
                          → :171-219 initialize_all()（Kahn 拓扑排序 + 依赖检查 + 失败标记）
```

---

## 二、逐文件详解

### 2.1 `create_vault.py` — 553 行

**职责**：唯一安装引擎，三平台安装器都调用它（`create_vault.py:3-19` 头注释自述与代码一致）。

**关键函数（完整签名 + 行号）**

| 签名 | 行号 | 说明 |
|---|---|---|
| `parse_args()` | `:38-50` | 8 个 flag，见下表 |
| `ensure_target(vault, force)` | `:53-71` | 目标准备/清空，返回 bool |
| `copy_template(vault)` | `:74-80` | copytree + STATE_IGNORE |
| `_template_rel_files()` | `:83-93` | 按 STATE_IGNORE 语义枚举将被复制的相对路径（供清单） |
| `write_install_manifest(vault)` | `:96-117` | 写 `.kb/install-manifest.json` |
| `_consume_agent_disabled_prefs()` | `:120-132` | 读并**删除** `~/.kb-kit-agent-prefs.json`，返回禁用集合或 None |
| `_write_empty_registry(vault)` | `:135-142` | 写空 `kb-agent.json`（吞 OSError） |
| `wire_agents(vault, interactive)` | `:145-195` | 探测 + 写注册表 |
| `run_demo(vault)` | `:198-215` | 调 `pipeline/rag.py index` |
| `init_git(vault)` | `:218-232` | git init/add/config/commit |
| `register_global_kb(vault)` | `:235-286` | 全局 kb：Windows 走 PATH，其它走软链 |
| `verify_install(vault)` | `:289-306` | 调 `scripts/verify.py`，返回 bool |
| `open_obsidian(vault)` | `:309-322` | `obsidian://open?path=` |
| `generate_homepage(vault)` | `:325-382` | 兜底首页（模板已有则 no-op） |
| `generate_quickstart_card(vault)` | `:385-502` | 兜底快捷卡（no-op） |
| `main()` | `:505-549` | 编排 + 返回码 |

**关键常量**

- `:30 REPO_DIR = Path(__file__).resolve().parent`、`:31 TEMPLATE_DIR = REPO_DIR / "template-vault"`
- `:32-35 STATE_IGNORE`（`shutil.ignore_patterns(".git", ".git/*", "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info", "vector index")`）
- 兜底文案内嵌在 `:334-377`（首页）与 `:390-497`（快捷卡）；`tests/test_create_vault.py:71-85` 有「兜底内容必须与 `template-vault/` 同名文件逐字一致」的守护测试。
- 常量性硬编码：`kb-agent.json`（`:103`/`:138`）、`如何使用.md` 与 `🏠-知识库首页.md`（`:103`）、`.kb/install-manifest.json`（`:111`）、`"kb-installer"`/`"kb@installer.local"`（`:223-224`）、`"向量索引目录名 vector index"`（`:34`、`verify.py:97`）。

**CLI 参数（全部 flag / 默认值 / 互斥关系）**

| flag | 行 | 默认 | 作用 |
|---|---|---|---|
| `--vault` | `:40-41` | `None` → `Path.cwd()/"kb-kit"`（`:511`） | 目标目录 |
| `--no-git` | `:42` | False | 跳过 `:525-526` |
| `--no-demo` | `:43` | False | 跳过 `:522-523` |
| `--force` | `:44` | False | 目标非空也覆盖（`:514`→`:63-68`） |
| `--interactive` | `:45-46` | False | agent 探测时提问（`:173-188`）；**默认非交互=全部启用** |
| `--no-verify` | `:47` | False | 跳过 `:535-538` |
| `--no-open` | `:48` | False | 跳过 `:540-541` |
| `--no-global-kb` | `:49` | False | 跳过 `:528-529` |

**没有互斥约束**（argparse 未用 `add_mutually_exclusive_group`）；`--force` 与「保留用户数据」隐式冲突，靠人工注意。
实测（import 后调 `parse_args`，未执行 main）：`-NoDemo` / `-NoGit` / `--no_git` 一律 `argparse` 报错 **exit 2**（问题 17 提供了这条证据）。

**读/写的外部路径**

- 读：`template-vault/`（`:75`）、`scripts/verify.py`（`:291`）、`vault/pipeline/rag.py`（`:200`）、`~/.kb-kit-agent-prefs.json`（`:122`）、`~/.bashrc`/`~/.zshrc`（`:274-279`）
- 写：目标 vault 全部内容（`:79`）、`vault/kb-agent.json`（`:138`/`:190`）、`vault/.kb/install-manifest.json`（`:111-114`）、`~/.local/bin/kb` 软链（`:265-272`）、shell rc（`:278-279`）、Windows 用户 PATH（`:249-253`）
- 调用外部程序：`git`（`:220-229`）、`python`（`:206`/`:296`）、`powershell`（`:242`/`:250`）、`open`/`xdg-open`（`:317-319`）、`os.startfile`（`:315`）

**依赖关系**：仅标准库（`argparse/json/os/shutil/subprocess/sys/datetime/pathlib`，`:21-28`）；运行时**软依赖** vault 内 `pipeline/agent_registry.py`（`:150`）与 `pipeline/rag.py`（`:200`），缺失时降级不报错。

**隐含假设与不变量**

1. `create_vault.py` 必须与 `template-vault/` 同级（`:31`/`:75-78`/`:507-509`）。
2. 模板目录中 `vector index/` 是运行时态，不得随包分发（`:34`；`tests/test_create_vault.py:26-41` 守护）。
3. `wire_agents` 之后 `vault/kb-agent.json` 必然存在 —— 这是 `verify.py:44-48` 把它列为「必备文件」的前提（`:160`/`:190`/`:138` 三条写路径）。
4. 全局 kb 软链的目标是 `<vault>/kb`（`:266`），且 `kb` 会解链取真实目录（`kb:36-42`），二者互为前提。
5. 兜底首页/快捷卡「不覆盖已有内容」（`:332-333`/`:387-389`），且内容与模板逐字一致（测试守护）。
6. 安装器自身不写任何用户私人路径（`:19` 承诺）—— 但 precheck 的 agent 提示里破例（问题 16）。

---

### 2.2 `install.sh` — 59 行

**职责**：Linux/macOS 一键安装器 = 找 Python → precheck → 询问目录 → exec create_vault.py。

**关键点（逐行）**：`:10 set -euo pipefail`；`:11 SELF_DIR="$(cd "$(dirname "$0")" && pwd)"`；`:14-19` 参数切分（首个非 `-` 开头者=目标目录，其余进 `EXTRA`）；`:23-27` Python 探测顺序 `python3 → python → py`；`:29-35` 未找到 → **exit 3**；`:38-44` precheck 失败 → **exit 3**；`:47-50` 交互询问（留空=`kb-kit`）；`:55-58` `exec "$PY" "$SELF_DIR/create_vault.py" --vault "$VAULT" "${EXTRA[@]}"`。

**无函数/类**（纯脚本）；**关键变量**：`SELF_DIR`(:11)、`VAULT`(:14)、`EXTRA`(:15)、`PY`(:23)。

**读/写**：只读 `$SELF_DIR/scripts/precheck.py`(:38)、`$SELF_DIR/create_vault.py`(:56)；不写文件；`exec`(:56/58) 后自身进程被替换，退出码 = create_vault 的退出码。

**依赖/假设**：bash（用 `${a:0:1}` 子串展开、数组）；`precheck.py` 与 `create_vault.py` 必须在本脚本同级/同级 `scripts/`。注意 `set -e` 下 `:49 [ -z "${VAULT:-}" ] && VAULT="kb-kit"` 属 AND-list，失败不退出（不是脚本最后一行，安全）；macOS 自带 bash 3.2 下空数组展开 `"${EXTRA[@]}"`(:56) 在 `-u` 下会报 unbound —— 当前被 `:55` 的 `${#EXTRA[@]} -gt 0` 守卫（潜在雷，非现网 bug，标「未确认」是否在 bash 3.2 上 `${#arr[@]}` 也报错）。

---

### 2.3 `install.ps1` — 66 行

**职责**：Windows PowerShell 安装器。

**逐行**：`:5 $scriptRoot = $PSScriptRoot`；`:9-11` `Get-Command python|py|python3` 首个命中；`:13-25` 遍历 5 组常见安装路径；`:26-32` 未找到 → `Read-Host` + **exit 3**；`:35-44` precheck（`& $py $precheck`，失败 → `Read-Host` + **exit 3**）；`:47-52` 参数切分；`:53-59` 无位置参数则交互（**留空默认 `$scriptRoot\kb-kit`**，`:57`）；`:64` `& $py (Join-Path $scriptRoot "create_vault.py") --vault $dest $flags`；`:65` 非 0 → `exit $LASTEXITCODE`；`:66 Read-Host "回车退出"`。

**无函数**；无 `param()` 块 —— **这是它的核心缺陷**：`:3` 的用法注释 `.\install.ps1 [-NoDemo] [-Force] [-NoGit]` 暗示 PowerShell 开关参数，实际全部落到 `$args`( `:49`) 被原样转成 `-NoDemo` 传给 argparse → 实测 exit 2（问题 17）。

**读/写**：读 `scripts\precheck.py`(:35)、`create_vault.py`(:64)；无写操作（写操作全在 create_vault）。

---

### 2.4 `install.bat` — 69 行

**职责**：Windows 双击安装（cmd）。

**逐行**：`:6 setlocal enabledelayedexpansion`；`:7-8 SELF_DIR=%~dp0` 并去掉尾部反斜杠（**写法正确**：`set "SELF_DIR=%SELF_DIR:~0,-1%"`）；`:12-21` `where python/py/python3` + 5 组 `dir /b /s` 路径探测，命中即 `goto :foundpy`；`:23-27` 未找到 → `pause` + **`exit /b 3`**；`:31-39` precheck（`if errorlevel 1` → `pause` + **`exit /b 3`**）；`:42-56` `:argloop` 参数切分（`:47` 判断首字符 `-`：正确）；`:57-61` 交互（留空=`kb-kit`）；`:65` 调 create_vault；`:66` 失败只 `echo 安装返回非 0 退出码: %ERRORLEVEL%`；`:67-69` `pause` + `goto :eof`。

**关键缺陷**：`:66` 只打印不 `exit /b %ERRORLEVEL%`，脚本最终退出码为 0（问题 4）。

**读/写**：同 ps1（由 create_vault 完成）；本身不写文件。

---

### 2.5 `template-vault/kb` — 60 行

**职责**：Linux/macOS 运行期启动器；**已经不是业务脚本**，只剩「算出自有目录 + 设 KB_ROOT + exec kb_launcher」三段（`:54-60` 注释明确说明此设计）。

**逐行要点**：`:31 set -euo pipefail`；`:36-42` 解析符号链接直到真实文件（`BASH_SOURCE[0]` → `readlink` 循环 → `cd -P dirname`）：这是全局 `~/.local/bin/kb` 软链能工作的前提；`:43 VAULT="$SELF_DIR"`；`:45-52` Python 探测（`python3→python→py`），无则 **exit 3**；`:59 export KB_ROOT="${KB_ROOT:-$VAULT}"`；`:60 exec "$PY" "$VAULT/pipeline/kb_launcher.py" "$@"`。

**无函数**；用法说明在 `:12-29`（13 条示例命令，与 `kb_launcher._print_help`:351-447 高度重叠但**不完全一致**，例如 `:24 kb clean --apply` 与 help 一致，但脚本头未列 `schema/audit/tagfix/verify/raw/curate/compile/rsi/memory-sync/user/classify/graph/health-metrics`）。

**不变量**：脚本所在目录 = vault 根；`pipeline/kb_launcher.py` 存在；`set -e` 不影响 `exec`（`:60` 是最后一条命令）。

---

### 2.6 `template-vault/kb.cmd` — 27 行

**逐行**：`:10 setlocal enableextensions`；`:12 set "VAULT=%~dp0"`；`:13 set "VAULT=%VAULT:~0,-1}"` ← **变量引用闭合符写成 `}`（应为 `%`）**；`:16` `where python && set PY=python || (where py … || (where python3 … || goto :nopys))`；`:21 if not defined KB_ROOT set "KB_ROOT=%VAULT%"`；`:22 "%PY%" "%VAULT%\pipeline\kb_launcher.py" %*`；`:23 goto :eof`；`:25-27 :nopys` 报错分支（**注意：该分支没有 `exit /b 3`，与 `kb` 的 exit 3 不对称**）。

**实测证据**：`sed -n '12,13p' | od -c` 显示第 13 行原始字节为 `s e t   " V A U L T = % V A U L T : ~ 0 , - 1 } "`。`grep -c $'\r'` 显示该文件 CRLF 计数为 0（LF-only）。
**后果（高置信，未在 Windows 实测）**：`:13` 因缺少成对的 `%`，cmd 不做变量展开而保留字面量 → `VAULT` 变成字面串 `%VAULT:~0,-1}`（或按另一种已知行为去掉孤立 `%`，同样得到垃圾串）→ `:21` 的 KB_ROOT 与 `:22` 的启动器路径全部非法 → **kb.cmd 在 Windows 上不可用**。同一缺陷已存在于本机另一个安装产物 `/home/mushan/kb-kit-pure/kb.cmd`（已 sed 验证），说明它确实分发到了用户手里。`git blame` 显示该行自首次提交 `fe0c444` 起未改动，且仓库无任何测试覆盖 `kb.cmd`。

---

### 2.7 `scripts/precheck.py` — 493 行

**职责**：环境自检 + 自动补齐（Python/Obsidian/git）+ 安装期 agent 选择与外部插件部署提示。

**关键类/函数**

| 签名 | 行号 |
|---|---|
| `class CheckResult(NamedTuple)`：`name, found, path, version, auto_installed, error` | `:26-32` |
| `class PrecheckReport.__init__(self, python, obsidian, git)` / `.all_passed` / `.failures` | `:35-40` / `:41-43` / `:45-52` |
| `check_python() -> CheckResult` | `:58-87` |
| `install_python() -> CheckResult` | `:93-110` |
| `_install_python_linux() -> CheckResult` | `:113-134` |
| `_install_python_macos() -> CheckResult` | `:137-157` |
| `_install_python_windows() -> CheckResult` | `:160-211` |
| `check_obsidian() -> CheckResult` | `:217-228` |
| `check_git() -> CheckResult` | `:234-245` |
| `_save_agent_disabled_prefs(disabled_names) -> None` | `:284-292` |
| `select_and_install_agents(vault: Optional[Path] = None) -> int`（docstring 说返回名称列表，实际返回 `len(selected)`） | `:295-402` |
| `_deploy_agent_plugin(agent_name: str, info: dict) -> bool` | `:405-443` |
| `run() -> int` | `:449-489` |

**关键常量**

- `:253-275 AGENT_PLUGINS`（`dsh`：`plugin_dir="kb-context-dsh"`、`deploy_doc="DEPLOY.md"`、`detect_hint="~/.dsh/storages/memory.json"`；`openclaw`：`plugin_dir=None`、`detect_hint="~/桌面/桌面文件/openclaw1/workspace"`；`codex`：`~/.codex/*.sqlite`；`hermes`：`~/.hermes/memories/MEMORY.md`）
- `:281 _PREFS_PATH = Path.home()/".kb-kit-agent-prefs.json"`
- 版本回落 `3.12.7`（`:166`）；`:20-21` 同目录导入 `_download_utils` 与 `setup_obsidian`

**CLI 参数**：**无**（无 argparse；`install.{sh,ps1,bat}` 调用时不传参；多余参数被忽略）。

**退出码**：`run()` 返回 `1`（Python 不可用，`:463`）/ `1|2`（`:483`，`2` 分支不可达）/ `0`（`:489`）。三平台安装器把非 0 一律当「检查未通过」→ exit 3。

**读/写**：写 `~/.kb-kit-agent-prefs.json`（`:287-290`）；经 `setup_obsidian` 写磁盘/系统（见 2.9）；`select_and_install_agents` 在 `vault` 非空时会写 `vault/kb-agent.json`（`:355-356`/`:384`，`run()` 传 None 故安装期不触发）；`_deploy_agent_plugin` **只打印提示**，唯一的检测是读 `~/.dsh/config.json`（`:429-437`，失败即返回 False 并提示手动部署）。

**依赖/假设**：Python ≥3.8（`_replace(missing_ok=True)` 等 3.8 API）；`~/.dsh/config.json` 是 DSH 的配置落点（**未确认**，本机 `~/.dsh/` 下可见 `cordis.patch.yml`、`.credentials.yaml` 等，未见 config.json）；`:422-427` 给出的 `dsh.profile.bundles += "<path>"` / `dsh profile reload` 是「指导性伪代码」（**未确认**是否可执行）。

**不变量**：`select_and_install_agents` 绝不写 vault 的注册表（安装期由 create_vault 统一写），跨进程靠 `_PREFS_PATH` 一次性传递（`:278-281` 注释自述）。

---

### 2.8 `scripts/verify.py` — 236 行

**职责**：安装结果自检；被 `create_vault.verify_install`(`:291`) 调用。

| 签名 | 行号 |
|---|---|
| `class VerifyItem(NamedTuple)`：`name, passed, detail, fix_cmd=""` | `:21-25` |
| `check_vault_structure(vault: Path) -> VerifyItem` | `:61-92` |
| `check_vector_index(vault: Path) -> VerifyItem` | `:95-140` |
| `check_kb_command(vault: Path) -> VerifyItem` | `:143-172` |
| `check_install_manifest(vault: Path) -> VerifyItem` | `:175-196` |
| `run(vault: Path) -> int` | `:202-225` |
| `main()` | `:228-232` |

**关键常量**：`:29-41 REQUIRED_DIRS`（9 个 PARA 分区 + `pipeline` + `scripts`）；`:44-48 REQUIRED_FILES`（`kb`、`kb.cmd`、`kb-agent.json`）；`:51-55 CONTENT_FILES`（`🏠-知识库首页.md`、`📖-知识库管理方案.md`、`如何使用.md`，缺失只提示，`:78-83`）。

**CLI**：`--vault`（**required**，`:230`）。

**退出码**：`0` 全过，`1` 有不过项（`:225`）。

**读/写**：读 vault 结构与 `vector index/df_idf.json`（`:97`）、`.kb/install-manifest.json`（`:182`）；执行 `python rag.py query 测试 --top 1 --root <vault>`（`:124-127`，timeout 30s）与 `kb help`（`:148`/`:151`，Windows 用 `kb.cmd help`，其它用 `bash <vault>/kb help`）；**不写任何文件**。

**不变量/假设**：`kb-agent.json` 必然存在（`:47` 注释明确引用 create_vault 的行为）；索引文件顶层有 `n_docs` 字段（`:117`）；索引目录名硬编码为 `vector index`（`:97`）。

---

### 2.9 `scripts/setup_obsidian.py` — 302 行

**职责**：Obsidian 探测 / 下载 / 静默安装 / 协议注册检查。

| 签名 | 行号 |
|---|---|
| `detect() -> Tuple[bool, Optional[Path]]` | `:54-73` |
| `_expand(raw: str) -> Optional[Path]` | `:76-86` |
| `_get_latest_version() -> str` | `:92-106` |
| `download_and_install() -> Tuple[bool, Optional[Path]]` | `:112-131` |
| `_install_linux(arch: str, version: str) -> Tuple[bool, Optional[Path]]` | `:134-161` |
| `_install_macos(arch: str, version: str) -> Tuple[bool, Optional[Path]]` | `:164-213` |
| `_install_windows(arch: str, version: str) -> Tuple[bool, Optional[Path]]` | `:216-247` |
| `ensure_protocol() -> bool` | `:253-276` |
| `run() -> int` | `:282-298` |

**关键常量**：`:24 OBSIDIAN_DOWNLOAD_PAGE="https://obsidian.md/download"`；`:25 OBSIDIAN_GITHUB_LATEST`（obsidian-releases/latest）；`:28-45 OBSIDIAN_PATHS`（三平台候选路径，Windows 用 `%LOCALAPPDATA%` 等，靠 `_expand` `:80-85` 手工展开环境变量）；`:48 FALLBACK_VERSION="1.4.16"`。

**CLI**：无。**退出码**：`run()` → `0` 已装/装成功，`1` 装失败（`:294`）。

**读/写**：下载到 `~/.local/bin/obsidian-<v>.AppImage`→`~/.local/bin/obsidian`(`:134-161`)、`/tmp/obsidian-<v>.dmg`→`/Applications/Obsidian.app`(`:164-213`)、`%TEMP%\obsidian-<v>.exe`(`:216-247`)；调用 `hdiutil`(`:181`/`:199`/`:206`)、`reg query`(`:269-272`)、静默安装器 `/S`(`:231`)。

**假设**：GitHub release 的资产命名（`Obsidian-{v}.AppImage` / `-{suffix}.dmg` / `-{suffix}.exe`，`:137`/`:167`/`:219`）；macOS 装 `/Applications` 需可写（未 sudo，`:169`）；`arch` 用 `aarch64` 判 arm64（`:166`/`:218`，`Platform.arch()` `_download_utils.py:39` 会把 `arm64` 归一为 `aarch64`，一致）。

---

### 2.10 `scripts/_download_utils.py` — 163 行

**职责**：下载 / SHA256 / 平台检测共享工具。

| 签名 | 行号 |
|---|---|
| `class Platform` / `current() -> str` / `arch() -> str` | `:20-40`（静态方法 `:23-30`、`:32-40`） |
| `class DownloadMeta(NamedTuple)`：`url, dest, expected_sha256, official_page` | `:46-50`（**全仓库无引用，死代码**） |
| `check_network() -> bool` | `:70-77`（HEAD `https://www.python.org`，timeout 3） |
| `verify_sha256(filepath: Path, expected: str) -> bool` | `:83-97` |
| `download(url: str, dest: Path, expected_sha256: Optional[str] = None, show_progress: bool = True) -> bool` | `:103-148` |
| `_safe_unlink(p: Path) -> None` | `:151-155` |

**关键常量**：`:55-64 SHA256_HASHES` —— 8 个键**全部为 `None`**，即校验当前是空操作（`:140` 只在非空时校验）。

**CLI**：`__main__`(`:161-163`) 只打印平台 + 网络，无参数。**读/写**：写 `dest`（`:117`），失败/校验不过时删除（`:133`/`:143`）。

---

### 2.11 `template-vault/pipeline/kb_launcher.py` — 477 行

**职责**：运行期唯一 CLI 入口：参数解析 → `COMPAT_MAP` 兼容映射 → 注册中心分发。

| 签名 | 行号 |
|---|---|
| `class KBLauncher.__init__(self, vault_root: str)` | `:200-206` |
| `_ensure_initialized(self) -> None` | `:208-214` |
| `@staticmethod _parse_params(args: List[str]) -> dict` | `:217-245` |
| `_resolve(self, tool: str, sub: Optional[str]) -> Optional[Tuple[str, Optional[str]]]` | `:248-259` |
| `run(self, args: List[str]) -> int` | `:261-348` |
| `_print_help(self) -> int` | `:351-447` |
| `_extract_root(argv: List[str]) -> Tuple[str, List[str]]` | `:450-465` |
| `main() -> int` | `:468-473` |

**关键常量**

- `:26-186 COMPAT_MAP`：**105 个键 / 36 个 tool 名**（实测统计），映射 `(cli_tool, cli_subcommand) → (plugin_name, action)`；`action=None` 表示「由参数推断」。
- `:190-194 BOOL_FLAGS`：`{"--dry-run","--dry","--json","--answer","--semantic","--incremental","--full","--move","--trash","--list","--verbose","--debug","--help","-h"}`（`-h` 是死条目，见问题 32）。
- `:202-205` 硬编码派生路径：`pipeline_dir = Path(__file__).parent`、`plugins_dir = pipeline/plugins`、`config_path = <vault_root>/reference/plugin-config.json`。

**CLI 参数解析规则（`_parse_params` `:217-245`）**

- `--key value` → `params["key"]="value"`（`:232-236`，`-`→`_`）
- `--bool-flag`（在 BOOL_FLAGS 中，或是最后一个参数，或下一参数以 `--` 开头）→ `True`（`:232-233`）
- **`--key=value` 不支持**（见问题 8）
- `-x` 短选项**一律** `True`（`:237-239`）
- 位置参数（含子命令值、查询串、`daily`/`weekly`）→ `params["_positional"]`（`:240-244`）
- `_extract_root`(`:450-465`)：**先**从 `KB_ROOT` 取默认（`:452`），**再**被 `--root X` 或 `--root=X` 覆盖（`:456-461`）→ 优先级与 `kb:57-59`、`kb.cmd:20-21`、README:83-85 的声明一致。

**读/写**：读 `<vault>/reference/plugin-config.json`（经注册中心，`:204-205`）；自身不写文件（插件可能写）。

**依赖/假设**：`plugin_base`/`plugin_registry` 必须与它同目录（`sys.path[0]` 由「以脚本方式运行」保证，`:19-20`）；`vault_root` 必须能推出 `reference/plugin-config.json`（缺失则走默认全启用）。

---

### 2.12 `template-vault/pipeline/plugin_base.py` — 91 行

**职责**：插件契约（ABC + 上下文 + 元数据值对象）。零依赖、Python 3.8+ 兼容（`:8-11` 自述）。

| 类型 | 行号 | 字段/方法 |
|---|---|---|
| `class PluginContext` | `:18-32` | `__init__(self, vault_root: str, state_store: Any = None, config: Optional[dict] = None, logger: Optional[logging.Logger] = None)`；属性 `vault_root: str`(`:29`)、`state_store`(`:30`)、`config: dict = config or {}`(`:31`)、`logger: logging.Logger = logger or logging.getLogger("kb.plugin")`(`:32`) |
| `class PluginMetadata` | `:35-59` | `__init__(self, name: str, version: str, plugin_type: str, actions: List[str], dependencies: Optional[List[str]] = None, description: str = "", cli_aliases: Optional[Dict[str, Optional[str]]] = None)`（`:48-52`）；实例属性 `name`/`version`/**`type`**（注意构造参数叫 `plugin_type`，属性名是 `type`，`:55`）/`actions`(`:56`)/`dependencies`(`:57`)/`description`(`:58`)/`cli_aliases`(`:59`) |
| `class PluginBase(ABC)` | `:62-91` | `@abstractmethod metadata(self) -> PluginMetadata`(`:74-77`)、`@abstractmethod initialize(self, ctx: PluginContext) -> None`(`:79-82`)、`@abstractmethod execute(self, action: str, params: dict) -> Any`(`:84-87`)、`shutdown(self) -> None` 默认空实现(`:89-91`) |

**隐含假设**：插件不直接互相 import，只用 `ctx`（`:9-11`/`:19-23`）；`plugin_type` 取值约定为 `"agent" | "kb_module"`（`:41`/`:55`，`plugin_registry.cmd_list` `:308-309` 依赖该约定分栏）。

---

### 2.13 `template-vault/pipeline/plugin_registry.py` — 349 行

**职责**：插件发现 / 注册 / 生命周期 / 分发 / 查询。

| 签名 | 行号 |
|---|---|
| `class _PluginEntry.__init__(self, plugin: PluginBase, metadata: PluginMetadata, status: str = "loaded", error_msg: str = "")`（`__slots__`） | `:28-38` |
| `class PluginRegistry.__init__(self, plugins_dir: str, vault_root: str, config_path: Optional[str] = None)` | `:45-56` |
| `_load_config(self) -> None` | `:59-70` |
| `_is_enabled(self, plugin_name: str) -> bool` | `:72-75` |
| `discover(self) -> None` | `:78-98` |
| `_load_plugin_module(self, module_path: Path) -> List[PluginBase]` | `:100-141` |
| `register(self, plugin: PluginBase) -> bool` | `:144-168` |
| `initialize_all(self, ctx: PluginContext) -> None` | `:171-219` |
| `shutdown_all(self) -> None` | `:221-230` |
| `execute(self, plugin_name: str, action: str, params: dict) -> Any` | `:233-259` |
| `get_plugin(self, name: str) -> Optional[PluginBase]` | `:261-264` |
| `list_plugins(self, plugin_type: Optional[str] = None) -> List[Tuple[PluginMetadata, str, str]]` | `:267-280` |
| `resolve_alias(self, cli_command: str) -> Optional[Tuple[str, Optional[str]]]` | `:282-288` |
| `_print_available(self) -> None` | `:290-299` |
| `cmd_list(self) -> int` | `:302-320` |
| `_main()`（`python3 plugin_registry.py [list] --root R [--plugins-dir D]`，见 3.3） | `:323-345` |

**配置读取**：`:53-55` 默认 `<vault_root>/reference/plugin-config.json`；`:64-67` 读入并剔除 `_` 开头键（如 `_meta`）；`:74-75` 缺失即默认启用。
现场 `template-vault/reference/plugin-config.json` 只列了 26 个插件，**audit/confidence/contradict/eval/tagfix/verify/contextual/raw/curate/schema 等不在其中 → 走默认启用**；`agent_detect_plugin.py` 定义的名字 `agent_registry` 在列表中。

---

## 三、`kb` 启动器与 `kb.cmd` 的完整命令面

### 3.1 命令发现与透传机制

`kb`(`:60`) 与 `kb.cmd`(`:22`) 都是**薄壳**：解析自身目录 → 设 `KB_ROOT` → `exec/调用 kb_launcher.py "$@"`。因此命令面完全由 `kb_launcher.COMPAT_MAP`(`:26-186`) + 插件名直呼回退(`:290-313`) + `_print_help`(`:351-447`) 决定。

**路由优先级（`run()` `:261-348`）**

1. `args[0] ∈ {help,--help,-h}` → 帮助（`:270-271`）；`args[0] == list` → 插件清单（`:272-274`）。
2. 若 `args[1]` 非 `-` 开头**且** `(tool, args[1])` 在 COMPAT_MAP 中 → 视为子命令（`:279-285`）。
3. `_resolve`：先查 `(tool, sub)`，再回退 `(tool, None)`（`:248-259`）。
4. 未命中 → 把 `tool` 当**插件名**直呼（`:291-311`），action 取下一个位置参数；action 不在 `metadata.actions` 中 → `rc=2`（`:305-308`）。
5. 命中且 `action is None` → 用第一个位置参数当 action（`:327-331`），否则查插件 `cli_aliases`（`:333-337`），再不行打印该插件可用动作并 `rc=0`（`:338-344`）。

### 3.2 全部子命令（105 条映射 / 36 个 tool）

| tool | 子命令 → (插件, action) |
|---|---|
| `query` | `None → (rag, query)` |
| `rag` | `None → (rag, None)`（推断）、`index`、`query` |
| `ingest` | `None → (intake, review)`、`review`、`move`、`trash`、`agent → (agent_registry, ingest)` |
| `ingest-agent` | `None → (agent_registry, ingest)` |
| `agent` | `None → (agent_registry, None)`、`detect`、`list`、`add`、`enable`、`setup`、`ingest` |
| `feedback` | `None → (feedback, ingest)`、`hit`、`apply`、`ingest` |
| `link` | `None → (link, suggestions)`、`apply`、`suggestions`、`moc` |
| `recall` | `None → (recall, deck)`、`deck`、`mark`、`status` |
| `healthcheck` | `None → (healthcheck, check)` |
| `kb-healthcheck` | `None → (healthcheck, check)` |
| `health-metrics` | `None → (health_metrics, metrics)` |
| `dashboard` | `None → (dashboard, generate)` |
| `clean` | `None → (clean, dry-run)`、`dry-run`、`apply`、`report`、`chunk`、`refine`、`taxonomy`、`repair`、`repair-headings` |
| `schema` | `None → (schema, check)`、`check`、`fields` |
| `confidence` | `None → (confidence, audit)`、`audit`、`show`、`apply`、`decay` |
| `decay` | `None → (confidence, decay)` |
| `contradict` | `None → (contradict, scan)`、`scan`、`apply` |
| `eval` | `None → (eval, run)`、`run`、`gold`、`compare` |
| `audit` | `None → (audit, scan)`、`scan`、`fix`、`log` |
| `tagfix` | `None → (tagfix, scan)`、`scan`、`fix` |
| `verify` | `None → (verify, all)`、`all` |
| `contextual` | `None → (contextual, build)`、`build`、`show` |
| `raw` | `None → (raw, list)`、`list`、`show`、`find`、`path` |
| `curate` | `None → (curate, plan)`、`plan`、`run`、`verify`、`report`、`review` |
| `validate` | `None → (validate, validate)` |
| `backup` | `None → (backup, backup)`（`daily`/`weekly` 作为位置参数透传，见 `:147-149` 注释） |
| `sync` | `None → (sync, None)`、`dry-run`、`apply`、`rollback`、`history`、`ingest-any` |
| `ingest-any` | `None → (sync, ingest-any)` |
| `memory-sync` | `None → (memory_sync, None)`、`review`、`promote` |
| `user` | `None → (user_manager, None)`、`add`、`list`、`remove`、`check-perm` |
| `graph` | `None → (graph, build)`、`build` |
| `classify` | `None → (classify, classify)` |
| `compile` | `None → (compile, None)`（action 由子命令决定） |
| `rsi` | `None → (rsi, probe)` |
| `list` / `help` | `__list__` / `__help__`（`:184-185`） |

**校验结果（本次实测）**：105 条映射的插件名与 action **全部真实存在**（AST 比对，无非法映射）。
**COMPAT_MAP 未列出、但可经「插件名直呼」使用的插件**：`agent_json`、`agent_md`、`agent_sqlite`、`ingest_chat`、`ingest_convert`、`rerank`、`semantic_chunk`（例如 `kb rerank rerank`、`kb semantic_chunk chunk`）。
**曾被 help 宣传但 COMPAT_MAP 无键的 tool**：0 个（`tools/test_help_alignment.py:54-64` 就是这个守卫）。
**COMPAT_MAP 有但 help 未宣传的 tool**：`classify`、`contextual`、`graph`、`health-metrics`、`ingest-agent`、`ingest-any`、`kb-healthcheck`、`memory-sync`、`user`（9 个，属文档欠账）。

### 3.3 `--root` / `KB_ROOT` 优先级与透传细节

| 环节 | 行为 | 位置 |
|---|---|---|
| `kb`（bash） | 解链取真实目录 → `VAULT`；`export KB_ROOT="${KB_ROOT:-$VAULT}"`（**不追加 `--root`**，以便用户 `--root` 生效） | `kb:36-43`、`:57-60` |
| `kb.cmd` | `VAULT=%~dp0`（**当前实现已损坏，见 2.6**）；`if not defined KB_ROOT set "KB_ROOT=%VAULT%"`；`%*` 原样透传 | `kb.cmd:12-22` |
| 启动器 | `_extract_root`：初始取 `$KB_ROOT`，**行内** `--root X` / `--root=X` 覆盖并**从 argv 中剥离** | `kb_launcher.py:450-465` |
| 兜底 | 两者都没有 → `Path(__file__).resolve().parents[1]`（= vault 根） | `kb_launcher.py:452` |
| 注入 | `params.setdefault("root", self.vault_root)`（`:310`、`:347`）→ 插件总能拿到 `root` | `kb_launcher.py:310/347` |
| 回归测试 | 4 个用例锁死「env 生效 / `--root` 覆盖 env / `--root=` 形式 / 回落 pipeline 上一级」 | `tests/test_launcher_root.py:29-52` |

**`set -euo pipefail` 的影响（`kb:31`）**：脚本里只有变量赋值、`command -v`、`[ -L ]`、`readlink` 和最后的 `exec`；`-e` 使 `cd -P`/`readlink` 失败即中止（比继续用错目录更好）；`-u` 下所有外部输入都写了默认值（`${KB_ROOT:-$VAULT}` `:59`、`${VAULT:-}` 只在 `install.sh:49` 出现）；最后一条是 `exec`，所以**插件的退出码会原样成为 `kb` 的退出码**（`:60`）。

### 3.4 Windows 差异（`kb.cmd` vs `kb`）

| 维度 | `kb`（bash） | `kb.cmd` |
|---|---|---|
| 真实目录解析 | 显式解符号链接（`:36-42`） | 无（Windows 靠 PATH 注册，不是软链） |
| 默认库传递 | 环境变量 `KB_ROOT`（`:59`） | 环境变量 `KB_ROOT`（`:21`） |
| 参数透传 | `"$@"` 保留引号语义 | `%*` 原样拼接；含空格的值需要用户在命令行自行加引号 |
| Python 选择 | `python3→python→py`（`:45-48`） | `python→py→python3`；`where python` 可能命中 Microsoft Store 占位符 |
| 无 Python | `echo` + `exit 3`（`:50-52`） | `:nopys`(`:25-27`) 只 `echo`，**没有 `exit /b`**（退出码不对称） |
| 退出码 | `exec` → 插件退出码 | `goto :eof`；python 的 ERRORLEVEL 是否被保留 **未确认**（同一命令块内无其它命令，通常保留） |
| 行尾 | LF（正常） | **LF（异常，见问题 29）** |

---

## 四、插件契约（PluginBase / PluginMetadata / PluginContext / PluginRegistry）

### 4.1 字段与方法签名

见 2.12（`plugin_base.py`）。补充三个「契约陷阱」：

1. `PluginMetadata` 的构造参数是 `plugin_type`，**实例属性是 `type`**（`plugin_base.py:48`/`:55`）→ 读 `meta.type`、写 `plugin_type=`。
2. `PluginMetadata` 不是 dataclass、**无 `__eq__`/`__repr__`/不可变保证**（`:35-59` 自述"不可变值对象"，实际属性可改）。
3. `PluginBase.initialize`/`execute` 的返回类型是 `Any`；`execute` 的返回值被 `PluginRegistry.execute`(`:253`) 直接透传成 CLI 退出码。

### 4.2 发现机制（`discover` `:78-98` + `_load_plugin_module` `:100-141`）

- 扫描路径：`<vault>/pipeline/plugins`（由 `kb_launcher.py:202-203` 传入）；不存在 → 仅 `logger.warning` 后返回（`:80-82`）。
- **sys.path 污染**：把 `plugins.parent`（=pipeline）与 `plugins` 目录都 `insert(0)` 进 `sys.path`（`:85-90`）。
- **import 方式**：`importlib.import_module(module_path.stem)`（`:106`/`:109`）——**按裸模块名导入**，不是 `spec_from_file_location`。
- 跳过规则：文件名以 `_` 开头的一律跳过（`:96-97`，例如被插件 import 的基类 `_subprocess_plugin.py`）。
- 一个模块可注册**多个**具体子类（`:114-134`）：遍历 `dir(mod)` → 必须是 `PluginBase` 子类（`:117-124`）→ 必须无 `__abstractmethods__`（`:126-127`）→ 必须 `__module__ == module_name`（`:129-130`，防 import 进来的类被重复注册）→ `attr()` 实例化 + `register()`。
- **失败处理**：import 失败 `:110-112` 只 warning 并返回 `[]`；实例化失败 `:131-136` 只 warning；`register` 里 `metadata()` 抛异常 `:146-150` 只 warning 返回 False。**没有 fatal 路径**：一个插件加载失败，`kb list` 里就是不出现（用户不一定看得见）。
- 日志：`logging.getLogger("kb.registry")`（`:50`），`kb_launcher` **没有** `basicConfig`，所以只有 WARNING+ 会经 `lastResort` 打到 stderr；`tests/test_plugin_registry.py:104-105` 明确要求发现阶段**零 WARNING 噪声**。

### 4.3 alias 与 COMPAT_MAP 的关系（两套机制并存）

- **COMPAT_MAP**（`kb_launcher.py:26-186`）是启动器侧的**主**机制：105 条 `(tool, sub) → (plugin, action)`。
- **`cli_aliases`**（`PluginMetadata` 字段，`plugin_base.py:45`/`:59`）是插件侧**备用**机制：`resolve_alias(cli_command)`(`plugin_registry.py:282-288`) 遍历所有插件，返回第一个 `cli_command in cli_aliases` 的 `(name, action)`。**只在 `action is None` 且没有位置参数时被调用**（`kb_launcher.py:333-337`）。
- 现场插件的 `cli_aliases`（AST 实测，26 个声明）：`agent_registry {'agent': None, 'ingest-agent': 'ingest'}`、`rag {'query':'query','rag':None}`、`intake {'ingest':'review'}`、`user_manager {'user':None}`、`sync {'sync':None,'ingest-any':'ingest-any'}`、`healthcheck {'healthcheck':'check','kb-healthcheck':'check'}`、`health_metrics {'health-metrics':'metrics'}`、`compile {'compile':None}`、`clean {'clean':'dry-run'}`、`link {'link':'suggestions'}` … 与 COMPAT_MAP **大面积重复**（两处维护同一份映射，是明显的重复/漂移风险）。
- **覆盖规则**：`COMPAT_MAP` 先命中（`:288`），`cli_aliases` 只在 action 推断不出来时兜底 → 实际上 `cli_aliases` 对绝大多数命令**永不生效**（因为 COMPAT_MAP 已给出了 action）。

### 4.4 注册优先级与互相覆盖

1. **配置禁用优先**（`:154-160`）：`plugin-config.json` 里 `{"<name>": {"enabled": false}}` → 记入注册表但 `status="disabled"`，且 `register()` 返回 False；`execute` 遇到 disabled 返回 **3**（`:240-243`）。
2. **命名冲突**（`:162-166`）：同名后注册者**覆盖**先前条目，只 warning「插件名冲突: X 被覆盖」。加载顺序 = `sorted(glob("*.py"))`（`:94`）→ **按文件名字典序**，让 `agent_detect_plugin.py`（定义 `agent_registry`）这类"名不副实"的模块成为冲突源。
3. **依赖拓扑**（`initialize_all` `:171-219`）：Kahn 排序（`:182-193`），环上的插件 `status="disabled"` + `error_msg="依赖环"`（`:195-200`）；初始化前再查依赖是否 `loaded`，缺则 disabled（`:206-213`）；`initialize` 抛异常 → `status="error"`（`:214-219`）。
4. **执行期状态映射**（`execute` `:233-259`）：未知插件 → 2（`:236-239`）；disabled → 3（`:240-243`）；error → 1（`:244-247`）；action 不支持 → 2（`:248-251`）；执行抛异常 → 打印 traceback + 1（`:252-259`）。
5. **关闭**（`shutdown_all` `:221-230`）：注释写"按逆依赖顺序"，实现是「按注册字典逆序」（`:223-224`）——**注释与实现不符**（但 `kb_launcher` 从不调用 `shutdown_all`，属未接线路径）。

---

## 五、安装器的跨平台行为差异

| 步骤 | `install.sh`（Linux/macOS） | `install.ps1`（Windows） | `install.bat`（Windows） |
|---|---|---|---|
| Python 探测 | `python3→python→py`（`:23-27`） | `Get-Command python→py→python3`（`:9-11`）+ 5 组路径（`:13-25`） | `where python→py→python3`（`:12-14`）+ `dir /b /s` 5 组（`:17-21`） |
| 无 Python | 打印 4 行手动指引，**exit 3**（`:29-35`） | 打印 + `Read-Host`，**exit 3**（`:26-32`） | 打印 + `pause`，**exit /b 3**（`:23-27`） |
| precheck | `"$PY" scripts/precheck.py`，非 0 → **exit 3**（`:38-44`） | `& $py $precheck`，`$LASTEXITCODE -ne 0` → **exit 3**（`:35-44`） | `"%PY%" "...precheck.py"`，`if errorlevel 1` → **exit /b 3**（`:31-39`） |
| 参数切分 | 首字符 `-` 判断（`:16-19`，**正确**） | `$a -like '-'`（`:50`，**错误**，只匹配字面 `-`） | `"!ARG:~0,1!"=="-"`（`:47`，正确） |
| 未给目录时默认 | `kb-kit`（相对 CWD，`:49`） | `$scriptRoot\kb-kit`（脚本目录，`:57`） | `kb-kit`（相对 CWD，`:59`） |
| 相对路径基准 | CWD（`:56` → `create_vault.py:511-512`） | 位置参数保持相对→CWD；交互输入被拼成绝对（`:58`） | CWD（`:65`） |
| 调引擎 | `exec … --vault "$VAULT" "${EXTRA[@]}"`（`:55-58`） | `& $py … --vault $dest $flags`（`:64`） | `"%PY%" … --vault "%VAULT%"!FLAGS!`（`:65`） |
| 失败退出码 | 继承 create_vault（`:58` 被 exec 替换） | `exit $LASTEXITCODE`（`:65`） | **只 echo，最终退 0**（`:66-69`，问题 4） |
| 交互收尾 | 无 | `Read-Host "回车退出"`（`:66`，非交互环境会阻塞） | `pause`（`:68`，同样阻塞） |
| 平台特有逻辑 | 无（全在 create_vault） | 无 | 无 |
| 行尾 | LF（正常） | LF（对话式脚本无碍） | **LF（.bat/.cmd 风险，问题 29）** |

**写在 create_vault 里的平台分支（三平台共用，但本质是平台特有行为）**：
- 全局 kb：`sys.platform=="win32"` → 改用户 PATH（`create_vault.py:238-259`）；其它 → `~/.local/bin/kb` 软链 + shell rc（`:261-284`）。
- 打开 Obsidian：`win32` → `os.startfile`（`:314-315`）；`darwin` → `open`（`:316-317`）；其它 → `xdg-open`（`:318-319`）。
- 结束提示：`./kb.cmd` vs `./kb`（`:543`）。
- **precheck/setup_obsidian 的平台分支**：Python 装法（`precheck.py:104-109` → apt/yum/dnf `:113-134`、brew `:137-157`、exe 静默装 `:160-211`）；Obsidian 装法（`setup_obsidian.py:124-129` → AppImage `:134-161`、dmg+hdiutil `:164-213`、exe `/S` `:216-247`）；协议检查（desktop 文件 `:257-263`、`/Applications` `:264-266`、`reg query` `:267-273`）。

---

## 六、发现的问题清单

> 严重度：🔴 高（会致数据丢失/功能不可用/安全）｜🟠 中（静默错行为/跨平台不一致）｜🟡 低（可维护性/死代码）
> 类型：bug / 静默失败 / 死代码 / 重复 / 平台假设 / 安全 / 可维护性

### 🔴 高

**1. `kb.cmd` 变量引用闭合符写错，Windows 启动器整体不可用** — `template-vault/kb.cmd:13`
- 类型：bug / 平台假设
- 事实：`set "VAULT=%VAULT:~0,-1}"`（`%` 误写为 `}`），同一行在 `install.bat:8` 是正确写法 `%SELF_DIR:~0,-1%`。`od -c` 已确认字节；无测试覆盖；`git blame` 显示自首次提交 `fe0c444` 起未变；本机另一个安装产物 `/home/mushan/kb-kit-pure/kb.cmd` 含同一缺陷。
- 影响：`VAULT`/`KB_ROOT` 成为字面垃圾串，`kb.cmd:22` 指向不存在的启动器路径 → **Windows 用户敲 `kb` 全部失败**（`kb help`、README:83 的整套 Windows 路径都失效）。
- 建议：改为 `%VAULT:~0,-1%`；补一个「Windows 冒烟」环节（哪怕只在 CI 用 `wine cmd`，或纯文本断言该行以 `%` 闭合）。另注意 `verify.py:143-172` 在 Windows 分支用 `cmd = [str(kb), "help"]`（`:147-148`）**没有 `shell=True`**：Windows 的 CreateProcess 不能直接执行 `.cmd`，会抛 OSError 并被 `:169-172` 兜成「执行异常」❌ —— 也就是说自检**本应**报出问题 1，只因自检失败不影响退出码（问题 5）才被静默吞掉，两者叠加放大了本问题。
- 置信：高（逻辑确定）；「cmd 对孤立 `%` 的具体处置」两种可能都得到垃圾串，故结论不依赖该细节。

**2. `verify.py` 给出的「修复命令」会把用户知识库清空** — `scripts/verify.py:92`、`:156`、`:195`
- 类型：安全 / 数据丢失
- 事实：三处 `fix_cmd = f"python3 create_vault.py --vault {vault} --force"`；而 `create_vault.py:63-68` 在 `--force` 下对目标目录的**每个**子项 `shutil.rmtree` / `unlink`（包括用户全部笔记、`.git` 历史）。此外 vault 内根本没有 `create_vault.py`（它在仓库根，`:31`/`:75`），该命令在用户目录里必然报「找不到文件」。
- 影响：用户按提示复制粘贴 → 知识库与 git 历史不可恢复地丢失。
- 建议：改成分级建议（先 `kb rag index`、`kb healthcheck`，再给「备份后重装」并显式警示 `--force` 会删除目录内容），或直接改为「重新运行安装器并选择空目录」。

**3. 未经用户确认即自动下载并静默安装 Obsidian，且无任何完整性校验** — `precheck.py:217-228` + `setup_obsidian.py:112-247` + `_download_utils.py:55-64`
- 类型：安全 / 供应链
- 事实：`check_obsidian()` 在未装时**直接** `download_and_install()`（`precheck.py:223-224`），无确认、无 `--no-obsidian` 开关；Windows 走下载 exe + `/S` 静默安装（`setup_obsidian.py:231`），macOS 写入 `/Applications`（`:169`），Linux 下载 AppImage 到 `~/.local/bin`（`:138-155`）。同时 `SHA256_HASHES` 8 个键**全为 None**（`_download_utils.py:55-64`），`download()` 只在 `expected` 非空时校验（`:140-148`）→ 校验形同虚设。
- 影响：安装器在用户不知情下把第三方二进制落盘并静默执行；传输/来源被篡改无法发现。
- 建议：加 `--no-obsidian` / 交互确认；发布前填真 SHA256（`_download_utils.py:53` 注释也是这个意思）；至少输出下载 URL 与校验结果。

**4. `install.bat` 吞掉失败退出码** — `install.bat:66-69`
- 类型：静默失败
- 事实：`if %ERRORLEVEL% NEQ 0 ( echo ... )` 之后只有 `pause` 与 `goto :eof`，没有 `exit /b %ERRORLEVEL%` → 脚本最终退出码为 0。
- 影响：批处理/无人值守调用永远看到"成功"；问题 1 的 `kb.cmd` 缺陷也更难在自动化里被发现。
- 建议：`if %ERRORLEVEL% NEQ 0 ( echo ... & exit /b %ERRORLEVEL% )`（或记下 err 后统一 `exit /b %err%`）。

**5. 安装自检失败不影响退出码，"装坏了也算成功"** — `create_vault.py:535-538`（配套 `:301-306`）
- 类型：静默失败
- 事实：`verify_install` 返回 False 时只 `print("⚠️ 自检有失败项…")`，`main()` 依然 `return 0`（`:549`）；且 `verify_install` 对 `TimeoutExpired`/异常都 `return True`（`:301-306`），timeout 只有 60s 而 `verify.py` 内部还会跑 `kb help`(15s)+`rag query`(30s)（`verify.py:124`/`:159`）→ 超时即被当成通过。
- 影响：`install.ps1:65`、`install.sh:58` 的退出码失去意义；Windows 上问题 1 因此被静默放过。
- 建议：自检失败返回非 0（或至少加 `--strict-verify`），并把 timeout 提到与内部超时之和相称（≥60s 余量）。

**6. shell rc 追加不幂等：每次安装都多写一行 PATH** — `create_vault.py:274-284`（判定在 `:277`）
- 类型：bug / 幂等
- 事实：`if str(bin_dir) not in content`（`:277`）比较的是**展开后的绝对路径** `/home/mushan/.local/bin`，而写入的内容是 `export PATH="$HOME/.local/bin:$PATH"`（`:279`，含 `$HOME` 字面量）→ 永远判定"未包含"。
- 现场证据：`~/.bashrc` 第 154、157 行是两段完全相同的 `# kb-kit` / `export PATH="$HOME/.local/bin:$PATH"`。
- 影响：重复安装/多库安装不断追加 PATH 行（PATH 膨胀，且语义上重复）；`.zshrc` 同样。
- 建议：判定同时匹配 `$HOME/.local/bin` 与展开路径（或写绝对路径），或改用 idempotent 的标记块（`# >>> kb-kit >>>` … `# <<< kb-kit <<<`）。

**7. 无升级/卸载路径，`--force` 是唯一的"重装"手段且不可逆** — `create_vault.py:53-71`、`:106-110`
- 类型：可维护性 / 数据丢失
- 事实：`install-manifest.json` 写了 `"version": 1`（`:107`）但**没有任何代码读它**（`verify.py:175-196` 只比对文件是否存在）；`--force` 会连 `.git` 一起删除（`:63-68`）→ 历史丢失且无法回滚；全仓库无 uninstall（三平台安装器都没有卸载分支，`register_global_kb` 也没有对应的"注销全局 kb"）。
- 影响：用户想升级模板只能全量覆盖（丢自建内容/历史）；想卸载要手工删软链、PATH、目录。
- 建议：加 `--upgrade`（保留用户笔记与 `.git`，只覆盖模板基线文件，用 manifest 判差异）、加 `print-uninstall`/`uninstall.sh`，并让 manifest 带模板版本号参与升级判断。

### 🟠 中

**8. `--key=value` 语法不被支持，参数被静默丢弃** — `kb_launcher.py:230-236`
- 类型：bug / 静默失败
- 实测：`kb rag query x --top=3` → `params={'top=3': True, '_positional': ['x']}`（真正的 `--top` 未生效，插件只能拿到垃圾键）。
- 影响：与 `--root=` 支持（`:459-461`）不一致；用户按 README:94 的 `[--top N]` 写 `--top=5` 会静默失效。
- 建议：在 `:230` 分支先判断 `"=" in arg`，拆成 key/value；或直接复用 `argparse`。

**9. 末尾裸 `--root`（缺值）会以 `True` 形式传给插件** — `kb_launcher.py:456-465` + `:310`/`:347`
- 类型：bug
- 实测：`kb query x --root` → `params={'root': True, '_positional': ['x']}`（`_extract_root` 未消费它，`setdefault` 又不覆盖已存在的键）。
- 影响：插件拿到布尔 `root`，路径拼接/文件系统操作出现难懂的错误。
- 建议：`--root` 缺值时 `print` 用法错误并 `return 2`；或统一在 `_parse_params` 里把 `root` 强制回填为 `vault_root`。

**10. `kb ingest apply` 静默跑成 `intake review`** — `kb_launcher.py:33-39`（缺 `("ingest","apply")`）+ 插件 `intake` 的 actions（`review/apply/move/trash`）
- 类型：静默失败 / 行为不一致
- 实测：`kb ingest apply` → `plugin='intake' action='review' params={'_positional': ['apply']}`（`apply` 变成无关位置参数，动作照跑 review）；同类缺口还有 `agent_md`（`mirror/mirror-core/sync/extract`）、`rerank`、`semantic_chunk`、`ingest_chat`、`ingest_convert`、`agent_json`、`agent_sqlite`（后几个尚可通过 `kb <插件名> <动作>` 直呼，唯 `intake apply` 因为 `("ingest",None)` 已绑定 `review` 而必然错路由）。
- 影响：写操作被静默降级/提级；用户以为在 apply，实际只 review（或反之）。
- 建议：给 `intake` 补 `("ingest","apply")`；更根本的是在 `:279-285` 之外加一条兜底——当 `(tool, args[1])` 不是合法子命令时给出明确报错（而不是把 sub 当位置参数吞掉）。

**11. `--force` 清空 + 全局 kb 劫持的组合风险（多库场景）** — `create_vault.py:63-68`、`:265-272`
- 类型：安全 / 数据丢失
- 事实：`register_global_kb` 发现 `~/.local/bin/kb` 已存在且指向别的 vault 时，直接 `link.unlink()` 再重建（`:271-272`）——若该路径是**普通文件**（非软链），同样被删除；Windows 侧则是"PATH 先到先得"，多库时哪个生效取决于 PATH 顺序。
- 影响：一次误操作/多库安装会让全局 `kb` 指向非预期库（`kb` 默认操作 `KB_ROOT`/自身目录），用户可能"对错的库"执行清洗、备份等写操作。
- 建议：覆盖前打印旧目标并要求确认（或 `--force-global`）；Windows 侧提供显式 `kb -vault <path>` 的推荐用法并文档化多库行为。

**12. Windows PATH 注册的注入/截断/静默失败** — `create_vault.py:238-259`
- 类型：安全 / 平台假设 / 静默失败
- 事实：`:251` 把整串 PATH 用单引号拼进 PowerShell：`f"...SetEnvironmentVariable('Path', '{new_path}', 'User')"` → 路径含 `'` 会破坏脚本（注入风险）；`:249-253` 的 `subprocess.run` **未检查返回码**（只有读 PATH 的 `:243-246` 检查了）；整体重写用户 PATH 有经典的长度截断风险；未广播 `WM_SETTINGCHANGE`（提示"重启终端生效"）；`:247` 用子串判定 `str(vault) not in current`，前缀相似路径会误判（如 `C:\KB` 与 `C:\KB2`）。
- 建议：用 `powershell -EncodedCommand` 或转义单引号（`''`）；检查返回码；用 `;` 精确比较条目；考虑 `.cmd` 快捷方式/`doskey` 替代改 PATH。

**13. 安装器文档化的 PowerShell 开关参数根本不存在** — `install.ps1:3`（配合 `:49-52`）
- 类型：bug / 平台假设
- 事实：脚本没有 `param()` 块；`.\install.ps1 -NoDemo` 会被原样传给 argparse，**实测 `-NoDemo`/`-NoGit` 均 exit 2**（`create_vault.py:38-50` 只认 `--no-demo`/`--no-git`）。
- 影响：Windows 用户照抄用法文档即安装失败；错误信息来自 argparse usage，用户难对应。
- 建议：加 `param([switch]$NoDemo,[switch]$Force,[switch]$NoGit)` 并映射为 `--no-demo` 等；或把注释改为 `--no-demo` 形式。

**14. `install.ps1` 的 flag 判定写成 `-like '-'`** — `install.ps1:50`
- 类型：bug / 平台假设
- 事实：PowerShell 的 `-like` 需要通配符；`'-'` 只匹配字面 `-`。于是 `.\install.ps1 --no-git myvault` 会把 `--no-git` 当成 `$dest`（`:51`）→ `create_vault.py --vault --no-git …`；`install.sh:17` / `install.bat:47` 的同位置判断是"首字符为 `-`"（正确）。
- 影响：flag 写在位置参数之前的调用方式在 Windows 上失败（报 argparse 错）。
- 建议：改为 `if ($a -like '-*')`。

**15. 三平台默认安装落点不一致** — `install.sh:49`、`install.ps1:57`、`install.bat:59`（配合 `create_vault.py:511-512`）
- 类型：平台假设 / 一致性
- 事实：留空时 `install.sh`/`install.bat` 传相对 `kb-kit`（按 **CWD** 解析），`install.ps1` 传 `Join-Path $scriptRoot "kb-kit"`（按**脚本目录**），而 create_vault 自身默认是 `Path.cwd()/"kb-kit"`（`:511`）——三处"默认"三种含义；位置参数保持相对时又以 CWD 为基准（`resolve()` `:512`），与 ps1 交互输入被拼成绝对（`:58`）也不一致。
- 影响：双击 `.bat`（CWD 由资源管理器决定，通常为脚本目录，**未确认**）、从别处 `bash /path/install.sh`、PowerShell 三种入口装到不同地方，用户找不到库。
- 建议：统一为「脚本所在目录」或「强制要求显式目录」，并在打印里回显 `resolve()` 后的绝对路径（`:518` 已回显，可满足）。

**16. `precheck.py` 的 openclaw 提示里写死作者私人路径** — `precheck.py:263`
- 类型：可维护性 / 隐私
- 事实：`"detect_hint": "~/桌面/桌面文件/openclaw1/workspace"`；与其他条目（`~/.dsh/...`、`~/.codex/*.sqlite`）风格不一致，且与 `create_vault.py:19`「绝不写死任何用户的私人路径」的承诺相悖。
- 影响：提示误导用户；对外分发时暴露作者目录结构。
- 建议：改成 `~/.openclaw/workspace`（与 `kb-agent.json.example` 的 `sources.root` 一致）或环境变量 `KB_OPENCLAW_HOME`。

**17. 模板目录携带未跟踪的运行时态，会被复制并提交进用户首个 commit** — `template-vault/.kb/state/query_cache.json`（12.8KB）、`feedback_state.json`（6.2KB）+ `create_vault.py:32-35`、`:221`
- 类型：隐私 / 打包卫生
- 事实：两个文件只被**仓库根** `.gitignore:35`（`/**/.kb/`）忽略，故在模板目录里以未跟踪文件形式存在；`template-vault/.gitignore`（会被复制进 vault）**不含** `.kb/state/query_cache.json`；`STATE_IGNORE`（`create_vault.py:32-35`）也不排除 `.kb` → `copytree` 会带走，随后 `git add -A`（`:221`）把它们写进用户首个提交。内容已验证含作者真实查询历史（`"query": "知识库备份 记忆摄取 间隔回忆"`）与命中笔记路径。
- 影响：每个新用户的 vault 出厂即带第三方查询历史（隐私），且缓存指向的路径未必存在（脏状态）。
- 建议：`STATE_IGNORE` 增加 `.kb/state`（或 `.kb` 下除白名单外全部排除），并在打包前清理模板目录；给 `template-vault/.gitignore` 补 `.kb/state/query_cache.json`。

**18. `wire_agents` 无异常兜底 + 空注册表静默失败** — `create_vault.py:150-157`、`:135-142`
- 类型：bug / 静默失败
- 事实：`:150-155` 捕获 import 失败，但 `:157 agents = ar.detect_agents()` **未捕获**（探测器读文件系统，可能抛 OSError）→ 崩在复制之后，留下无 manifest、无自检、无 git 的半成品；`_write_empty_registry` 吞 `OSError`（`:141-142`），一旦写失败，`verify.py:44-48` 的必备文件检查会 ❌（而问题 5 让它不影响退出码）。
- 建议：`detect_agents()` 包 try；`_write_empty_registry` 失败时打印显式错误；关键写操作失败应让 `main()` 返回非 0。

**19. 一次性偏好文件的跨会话污染** — `precheck.py:281-292`、`:397-400`；`create_vault.py:120-132`
- 类型：静默失败
- 事实：`~/.kb-kit-agent-prefs.json` 在 precheck 末尾写入（`:398-400`），只有在 create_vault 成功执行到 `wire_agents` 时才被消费删除（`create_vault.py:128-132` 的 `finally`）。若用户中途 Ctrl-C、或 precheck 单独运行（`:6-7` 说明支持独立运行）后再手动建库，残留文件会把**上一次会话**的禁用集合套用到新库；另外 `_consume_agent_disabled_prefs` 读取失败也返回 None（`:126-127`）→ 静默转成"全部启用"。
- 建议：文件名带时间戳/进程号，或写入后立即在 precheck 侧给出显式提示，或把选择作为命令行参数传给 create_vault（去掉隐式全局状态）。

**20. `verify.py` 的 patch 建议与检测方式偏弱** — `verify.py:143-172`、`:117`、`:190`
- 类型：可维护性 / 平台假设
- 事实：非 Windows 用 `bash <vault>/kb help`（`:151`）而不是直接执行 `<vault>/kb`，所以 `kb` 的**可执行位从未被验证**（虽有 `bash` 兜底）；`n_docs` 直接假设索引顶层字段（`:117`，`isinstance` 检查冗余）；`:190` 只判文件是否存在，不校验内容/哈希，也不读 manifest 的 `version`（`:107`），因此"清单"只挡"缺文件"，挡不住"被改坏"。
- 建议：非 Windows 先试 `[x] kb` 再回落 `bash kb`；manifest 增加 size/sha256 与模板版本，用于升级比对。

### 🟡 低（死代码 / 重复 / 可维护性）

**21. Windows 批处理行尾为 LF 且仓库无 `.gitattributes`** — `install.bat`（69 行全 LF）、`template-vault/kb.cmd`（27 行全 LF）、`install.ps1`
- 类型：平台假设
- 事实：`grep -c $'\r'` = 0（三个文件）；仓库根无 `.gitattributes`。cmd.exe 官方预期 CRLF，LF-only 批处理在 `goto`/标签/多行括号块上有已知的不可靠行为（`install.bat` 用了 8 个 `goto :foundpy`（`:12,13,14,17,18,19,20,21`）、`:argloop`(`:44`)、`goto :eof`(`:69`)；`kb.cmd` 用了 `:nopys`(`:25`) 与 `goto :eof`(`:23`)）。
- 影响：即使修好问题 1，Windows 上仍可能间歇性出现「找不到批处理标签」。**未在 Windows 实测**。
- 建议：加 `.gitattributes`：`*.bat text eol=crlf`、`*.cmd text eol=crlf`，并重新落盘这两个文件。

**22. `precheck.PrecheckReport.failures` 死代码 + `all_passed` 语义自相矛盾 + `return 2` 不可达** — `precheck.py:41-52`、`:482-483`
- 类型：死代码
- 事实：`all_passed` 只返回 `self.python.found`（`:43`），`failures`（`:45-52`，含 Obsidian）**全仓库无引用**（已 rg 确认）；`:483 return 1 if not py.found else 2` —— 因为 `all_passed == py.found`，走到 `:482` 就必然 `not py.found` 为真，`2` 永不可达。
- 建议：删除 `failures` 或改用它；`install_python` 失败已经 `return 1`（`:463`），`:482-483` 可整体简化。

**23. `check_python` 的版本判断对 Python 4.x 假阴性** — `precheck.py:79`
- 类型：bug（低）
- 事实：`if major >= 3 and minor >= 8` —— `major=4, minor=0` 会判为"版本过低"；正确写法是 `(major, minor) >= (3, 8)`。
- 建议：改成元组比较。

**24. Windows 自动装 Python 的版本来源与 python.org 目录不匹配的风险** — `precheck.py:166-180`
- 类型：bug / 依赖假设（**未实测网络**）
- 事实：默认 `3.12.7`（`:166`），随后用 **python/cpython 的 GitHub release tag**（`:168-176`）拼 **python.org** 的安装包 URL（`:180`）；cpython 仓库的 release tag 可能是 rc/alpha（python.org 无对应 amd64 安装包）→ 404 下载失败；`:192` 的 `Include_pip=0` 会让装完后没有 pip（与「纯标准库」一致，但用户预期可能不同）。
- 建议：固定并校验已知可用版本（或读 python.org 的发布列表），把 `Include_pip=0` 的取舍写进注释/输出。

**25. SHA256 常量与 `DownloadMeta` 均为死配置** — `_download_utils.py:46-50`、`:55-64`；`setup_obsidian.py:19`；`_download_utils.py:9`
- 类型：死代码 / 安全（与问题 3 同源）
- 事实：`DownloadMeta` 全仓库无引用（rg 确认）；`SHA256_HASHES` 8 键全 `None`；`setup_obsidian.py:19` 导入 `verify_sha256` 但从未使用；`_download_utils.py:9` 导入 `shutil` 但从未使用（AST 检查）。
- 建议：删除 `DownloadMeta`/未用导入；填哈希或用 `--insecure-skip-verify` 显式表达跳过。

**26. `install.sh` 的参数切分无法表达 `--vault X`** — `install.sh:16-19`
- 类型：可维护性
- 事实：首个非 `-` 开头参数一律当目标目录；用户若写 `./install.sh --vault foo`，`--vault` 进 `EXTRA`、`foo` 成为 `VAULT` → 最终命令为 `create_vault.py --vault foo --vault`（argparse 报错 exit 2）。三个平台同病（`install.bat:44-55` 同理）。
- 建议：只接受位置参数目录，或显式解析 `--vault` 并禁止重复；错误信息里提示正确用法。

**27. `logging`/初始化路径未接线：`PluginContext.config` 永远是空 dict** — `kb_launcher.py:212`、`plugin_base.py:25-32`
- 类型：死代码 / 未接线
- 事实：`_ensure_initialized` 只传 `vault_root`（`:212`），`state_store`/`config`/`logger` 均默认；`reference/plugin-config.json` 只被注册中心用于 enable/disable（`plugin_registry.py:59-75`），**插件的自定义配置无法从上下文获得**；`kb_launcher` 也没有 `logging.basicConfig`，插件日志只有 WARNING+ 可见（`plugin_registry.py:50`）。
- 建议：要么真正注入（读 `plugin-config.json` 的插件段传给 `ctx.config`），要么删掉该字段以免误导插件作者。

**28. `PluginRegistry.initialize_all` 的死条件与 `shutdown_all` 的注释不符** — `plugin_registry.py:206-208`、`:221-230`
- 类型：死代码 / 重复
- 事实：`:179` 已经把 `dependencies` 与 `loaded_names` 求交，所以 `:207` 的 `d not in self._registry` 分支不可达；`:223` 注释写"按逆依赖顺序"，实现是"按注册字典逆序"（`:224`），且该方法在 `kb_launcher` 中**从未被调用**（`shutdown` 生命周期实际上不存在）。
- 建议：统一注释与实现；在 `KBLauncher.run` 里用 `try/finally` 调 `shutdown_all`（或删除该死路径）。

**29. 插件导入方式带来的命名遮蔽/缓存风险** — `plugin_registry.py:85-90`、`:106-109`
- 类型：可维护性 / 风险
- 事实：`plugins` 目录被插到 `sys.path[0]`，模块按裸名 `import_module`；插件文件名一旦与标准库/三方模块同名（如 `types.py`、`tokenize.py`、`json.py`）会**遮蔽**它们；若同名模块已在 `sys.modules` 中（例如安装器已 `import agent_registry`，`create_vault.py:148-150`），`import_module` 会直接复用缓存对象而非加载插件目录里的那份。同名插件后加载者静默覆盖（`:162-166` 只 warning）。
- 建议：改用显式 `importlib.util.spec_from_file_location` 加载，模块名加前缀（如 `kbplug_<stem>`），或真包化（`pipeline.plugins` + `__init__.py` 已存在，`:94` 的 glob 可换成 `pkgutil`）。

**30. `BOOL_FLAGS` 含死条目与旧版残留** — `kb_launcher.py:190-194`、`:237-239`
- 类型：死代码 / 重复
- 事实：短选项分支（`:237-239`）只赋 `True`，从不查 `BOOL_FLAGS`，故 `"-h"` 永远不参与判断；`--move`/`--trash`/`--dry` 在当前 COMPAT_MAP 下都不是已用值（`move`/`trash` 已是 `ingest` 的子命令）。
- 建议：删掉 `-h`；其余确认后清理，并把"是否布尔"的判断收敛到一处。

**31. COMPAT_MAP 与插件 `cli_aliases` 双份维护** — `kb_launcher.py:26-186` vs 26 个插件的 `cli_aliases`
- 类型：重复
- 事实：两处描述同一映射（如 `intake {'ingest':'review'}` 对应 `("ingest",None)→("intake","review")`），且 `resolve_alias` 只在极窄条件下生效（`kb_launcher.py:333-337`），实际几乎总是被 COMPAT_MAP 抢先。
- 建议：单一事实源——由插件 `cli_aliases` 生成路由，或让 COMPAT_MAP 只保留别名（不再重复动作名）；`tests/test_help_alignment.py` 可扩展成"两表一致性"守卫。

**32. 其它零散可维护性问题**
- `create_vault.py:237 import os` 与模块级 `:23` 重复导入（函数内多余）。
- `precheck.py:99-100` f-string 无占位符；`:162-163` 函数内 `import json` 与模块级 `:10` 重复。
- `precheck.py:292` 注释「偏好文件不可写不影响安装」与 `create_vault.py:116-117` 同类处理一致，属有意吞错，但建议至少 `-v` 可见。
- `create_vault.py:59` 用 `list(vault.iterdir())` 仅判空，大目录下多余（可 `next(vault.iterdir(), None)`）。
- `install.ps1:30`、`:41`、`:66` 与 `install.bat:26`、`:36`、`:68` 的 `Read-Host`/`pause` 在非交互环境会挂住（CI 场景）。
- `scripts/` 与 `template-vault/scripts/` 同名不同义（前者 4 个安装脚本：`precheck.py`/`verify.py`/`setup_obsidian.py`/`_download_utils.py`；后者 10 个运行期 cron/bench 脚本：`growth_cron.sh` 等，且**不含 verify.py**），`README.md:215` 只描述了前者，易混淆；同时意味着 vault 装好后**没有** `scripts/verify.py`，用户无法在库内自检（`create_vault.py:291` 依赖仓库侧文件）。
- `kb:12-29` 头部用法清单与 `kb_launcher._print_help` 不一致（头部缺 13 个 tool），属文档重复。

---

## 七、结论摘要

- 本组是"装得上 + 跑得起来"的关键路径：`create_vault.py` 的 15 步链条清晰、降级设计完备（几乎每步失败都只是 warning），但**"降级不报错"被用在了退出码上**（问题 4/5），使安装器的成败信号不可信。
- 运行期路由层（`kb_launcher` + `plugin_registry`）设计完整、插件发现对抽象类/重导出/下划线辅助模块的处理正确（`tests/test_plugin_registry.py` 覆盖），但**参数解析不支持 `--key=value`**、**`--root` 缺值**、**`ingest apply` 错路由**三处会静默给出错误行为。
- 跨平台上，`install.sh`/`install.bat` 的逻辑基本正确，`install.ps1` 的参数解析与文档用法有实测可复现的错误（问题 13/14）；**`kb.cmd` 存在致命的变量引用拼写错误（问题 1）**，Windows 运行期入口不可用。
- 数据安全方面最危险的一条是 `verify.py` 的"修复建议"会引导用户用 `--force` 清空知识库（问题 2）；其次是 `--force` 无升级路径（问题 7）与自动安装 Obsidian 无校验（问题 3）。
