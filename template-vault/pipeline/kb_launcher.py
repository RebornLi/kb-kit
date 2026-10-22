#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_launcher.py — CLI 统一入口。

职责：
  1. 解析命令行参数
  2. 通过 COMPAT_MAP 将旧 CLI 命令映射到 (plugin_name, action)
  3. 通过 PluginRegistry 分发执行
  4. 自动注入 --root（vault 根路径）

替代原 kb bash case 语句和 kb.cmd if-elif 链。
零依赖，仅使用 Python 标准库。
"""
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from plugin_base import PluginContext
from plugin_registry import PluginRegistry


# ── 兼容映射表 ──────────────────────────────────────────────
# (cli_tool, cli_subcommand) → (plugin_name, action)
# cli_subcommand 为 None 表示无子命令时的默认映射
COMPAT_MAP: Dict[Tuple[str, Optional[str]], Tuple[str, Optional[str]]] = {
    # 检索
    ("query", None):       ("rag", "query"),
    ("rag", None):         ("rag", None),           # action 由 args[0] 决定
    ("rag", "index"):      ("rag", "index"),
    ("rag", "query"):      ("rag", "query"),

    # 摄入
    ("ingest", None):      ("intake", "review"),
    ("ingest", "review"):  ("intake", "review"),
    ("ingest", "apply"):   ("intake", "apply"),   # intake 有 apply 动作；缺这条会静默落到 review
    ("ingest", "move"):    ("intake", "move"),
    ("ingest", "trash"):   ("intake", "trash"),
    ("ingest", "agent"):   ("agent_registry", "ingest"),

    # Agent 管理
    ("agent", None):       ("agent_registry", None),
    ("agent", "detect"):   ("agent_registry", "detect"),
    ("agent", "list"):     ("agent_registry", "list"),
    ("agent", "add"):      ("agent_registry", "add"),
    ("agent", "enable"):   ("agent_registry", "enable"),
    ("agent", "setup"):    ("agent_registry", "setup"),
    ("agent", "ingest"):   ("agent_registry", "ingest"),
    ("ingest-agent", None):("agent_registry", "ingest"),

    # 反馈
    ("feedback", None):    ("feedback", "ingest"),
    ("feedback", "hit"):   ("feedback", "hit"),
    ("feedback", "apply"): ("feedback", "apply"),
    ("feedback", "ingest"):("feedback", "ingest"),

    # 补链
    ("link", None):        ("link", "suggestions"),
    ("link", "apply"):     ("link", "apply"),
    ("link", "suggestions"):("link", "suggestions"),
    ("link", "moc"):       ("link", "moc"),

    # 回忆
    ("recall", None):      ("recall", "deck"),
    ("recall", "deck"):    ("recall", "deck"),
    ("recall", "mark"):    ("recall", "mark"),
    ("recall", "status"):  ("recall", "status"),

    # 健康
    ("healthcheck", None): ("healthcheck", "check"),
    ("health-metrics", None): ("health_metrics", "metrics"),
    ("health-metrics", "kpi"): ("health_metrics", "kpi"),
    ("kb-healthcheck", None): ("healthcheck", "check"),

    # 仪表盘
    ("dashboard", None):   ("dashboard", "generate"),

    # 清洗
    ("clean", None):       ("clean", "dry-run"),
    ("clean", "dry-run"):  ("clean", "dry-run"),
    ("clean", "apply"):    ("clean", "apply"),
    ("clean", "report"):   ("clean", "report"),
    ("clean", "chunk"):    ("clean", "chunk"),
    ("clean", "refine"):   ("clean", "refine"),
    ("clean", "taxonomy"): ("clean", "taxonomy"),
    ("clean", "repair"):   ("clean", "repair"),
    ("clean", "repair-headings"): ("clean", "repair-headings"),

    # 知识契约（schema）
    ("schema", None):      ("schema", "check"),
    ("schema", "check"):   ("schema", "check"),
    ("schema", "fields"):  ("schema", "fields"),
    ("schema", "bitemporal"): ("schema", "bitemporal"),

    # P3：置信分与矛盾在环
    ("gate", None):           ("gate", "check"),
    ("gate", "check"):        ("gate", "check"),
    ("consolidate", None):    ("consolidate", "run"),
    ("consolidate", "run"):   ("consolidate", "run"),
    ("consolidate", "status"):("consolidate", "status"),
    ("ladder", None):         ("ladder", "status"),
    ("ladder", "status"):     ("ladder", "status"),
    ("ladder", "propose"):    ("ladder", "propose"),
    ("ladder", "run"):        ("ladder", "run"),
    ("ladder", "apply"):      ("ladder", "apply"),
    ("ladder", "adjudicate"): ("ladder", "adjudicate"),
    ("ladder", "verdicts"):   ("ladder", "verdicts"),
    ("confidence", None):     ("confidence", "audit"),
    ("confidence", "audit"):  ("confidence", "audit"),
    ("confidence", "show"):   ("confidence", "show"),
    ("confidence", "apply"):  ("confidence", "apply"),
    ("confidence", "decay"):  ("confidence", "decay"),
    ("decay", None):          ("confidence", "decay"),
    ("contradict", None):     ("contradict", "scan"),
    ("contradict", "scan"):   ("contradict", "scan"),
    ("contradict", "apply"):  ("contradict", "apply"),

    # P4：评测
    ("eval", None):        ("eval", "run"),
    ("eval", "run"):       ("eval", "run"),
    ("eval", "gold"):      ("eval", "gold"),
    ("eval", "compare"):   ("eval", "compare"),

    # P5：审计与确认后修复
    ("audit", None):       ("audit", "scan"),
    ("audit", "scan"):     ("audit", "scan"),
    ("audit", "fix"):      ("audit", "fix"),
    ("audit", "log"):      ("audit", "log"),

    # P6：标签越界治理
    ("tagfix", None):      ("tagfix", "scan"),
    ("tagfix", "scan"):    ("tagfix", "scan"),
    ("tagfix", "fix"):     ("tagfix", "fix"),

    # 一行验收（五项全绿）
    ("verify", None):      ("verify", "all"),
    ("verify", "all"):     ("verify", "all"),

    # P2/P5：上下文强化（LLM 上下文为可选增强）
    ("contextual", None):    ("contextual", "build"),
    ("contextual", "build"): ("contextual", "build"),
    ("contextual", "show"):  ("contextual", "show"),

    # 原记忆可达性（证据层按需调用）
    ("raw", None):         ("raw", "list"),
    ("raw", "list"):       ("raw", "list"),
    ("raw", "show"):       ("raw", "show"),
    ("raw", "find"):       ("raw", "find"),
    ("raw", "path"):       ("raw", "path"),

    # 知识结晶（本地 Agent 把日志炼成正典）
    ("curate", None):      ("curate", "plan"),
    ("curate", "plan"):    ("curate", "plan"),
    ("curate", "run"):     ("curate", "run"),
    ("curate", "verify"):  ("curate", "verify"),
    ("curate", "report"):  ("curate", "report"),
    ("curate", "review"):  ("curate", "review"),

    # 校验
    ("validate", None):    ("validate", "validate"),

    # 备份（跨平台：pipeline/backup.py；kb / kb.cmd 均经此路由）
    # 只映射到插件；daily/weekly 作为位置参数透传，故不单列 (backup, daily/weekly)
    ("backup", None):      ("backup", "backup"),

    # 同步
    ("sync", None):        ("sync", None),
    ("sync", "dry-run"):   ("sync", "dry-run"),
    ("sync", "apply"):     ("sync", "apply"),
    ("sync", "rollback"):  ("sync", "rollback"),
    ("sync", "history"):   ("sync", "history"),
    ("sync", "ingest-any"):("sync", "ingest-any"),
    ("ingest-any", None):  ("sync", "ingest-any"),

    # 记忆同步
    ("memory-sync", None): ("memory_sync", None),
    ("memory-sync", "review"):  ("memory_sync", "review"),
    ("memory-sync", "promote"): ("memory_sync", "promote"),

    # 用户管理
    ("user", None):        ("user_manager", None),
    ("user", "add"):       ("user_manager", "add"),
    ("user", "list"):      ("user_manager", "list"),
    ("user", "remove"):    ("user_manager", "remove"),
    ("user", "check-perm"):("user_manager", "check-perm"),

    # 图谱
    ("graph", None):       ("graph", "build"),
    ("graph", "build"):    ("graph", "build"),

    # 分类
    ("classify", None):    ("classify", "classify"),

    # 研究层：查询→知识 正回路 / RSI 只读探针
    ("compile", None):     ("compile", None),        # action 由子命令决定
    ("rsi", None):         ("rsi", "probe"),

    # 研究层只读入口（P2-3）：使用信号 / 适应度 / 漂移 / 规模
    # 此前这 4 个模块的 main() 完整可跑，却**没有任何 CLI 入口** ——
    # 用户不知道它们存在，也就从没用过（README 曾自述"26% 的代码、0% 的动作"）。
    # 只暴露零写操作的模块；kb_usage 的 mark（会提升 importance）刻意不暴露。
    # 无子命令时 action 必须留 None：launcher 会从 params_args 里取第一个非旗标
    # token 当 action（`kb research report` 的 `report`）。这里若写死 "report"，
    # 那个 `report` 就会作为**位置参数**透传给 kb_usage.py，argparse 报
    # `unrecognized arguments: report`。`kb research`（无子命令）由
    # `ResearchPlugin._ALIASES["research"]` → ("kb_usage","report") 兜底。
    ("research", None):        ("research", None),
    ("research", "usage"):     ("research", "kb_usage.report"),
    ("research", "analyze"):   ("research", "kb_usage.analyze"),
    ("research", "stats"):     ("research", "kb_usage.stats"),
    ("research", "fitness"):   ("research", "kb_fitness.fitness"),
    ("research", "drift"):     ("research", "kb_drift.drift"),
    ("research", "scale"):     ("research", "kb_scale.scale"),
    ("research", "bracket"):   ("research", "kb_scale.bracket"),
    ("research", "fabric"):    ("research", "kb_scale.fabric"),

    # 特殊命令
    ("list", None):        ("__list__", None),
    ("help", None):        ("__help__", None),
}


# ── 布尔标志参数（无值，存在即 True）────────────────────────
# 这份清单必须覆盖 pipeline/ 与 plugins/ 里**全部** action="store_true" 的旗标：
# 解析器只有两种形态 —— 在白名单里 = 无值布尔；不在 = 会吞掉下一个 token 当值。
# 曾经只登记了 13 个，而全项目有 39 个 store_true，于是 `kb clean --apply x.md` 之类
# 会把位置参数吃成旗标值。tests/test_launcher_flags.py 会扫描源码守卫这份清单。
BOOL_FLAGS = {
    "--ab-report", "--all", "--allow-existing-issues", "--answer", "--apply",
    "--apply-pass", "--auto", "--confirm", "--debug", "--decay",
    "--dry", "--dry-run", "--exclude-stale", "--export", "--force",
    "--full", "--generate", "--help", "--include-evidence", "--include-raw",
    "--include-stubs", "--incremental", "--json", "--list", "--llm",
    "--log", "--move", "--no-purge", "--no-record", "--out", "--path-only",
    "--mark-policy-stale", "--pending", "--policy-stale", "--quality",
    "--report", "--reset-config", "--review",
    "--semantic", "--status", "--stdin", "--strict", "--strict-fresh",
    "--trash",
    "--verbose", "--with-index",
    "-h",
    "--kpi",
}

# 可重复取值的旗标（nargs="+"）：lancher 只吃一个 token 会让第 2 个起落到 _positional
# 然后被静默丢弃（如 `kb agent add --project-context-root /a /b`）。
MULTI_FLAGS = {"--project-context-root"}


class KBLauncher:
    """CLI 统一入口 — 兼容映射 + 动态路由。"""

    def __init__(self, vault_root: str):
        self.vault_root = vault_root
        pipeline_dir = str(Path(__file__).resolve().parent)
        plugins_dir = str(Path(pipeline_dir) / "plugins")
        config_path = str(Path(vault_root) / "reference" / "plugin-config.json")
        self.registry = PluginRegistry(plugins_dir, vault_root, config_path)
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """延迟初始化注册中心（首次执行时加载插件）。"""
        if not self._initialized:
            self.registry.discover()
            ctx = PluginContext(vault_root=self.vault_root)
            self.registry.initialize_all(ctx)
            self._initialized = True

    # ── 参数解析 ───────────────────────────────────────────
    @staticmethod
    def _parse_params(args: List[str]) -> dict:
        """将命令行参数解析为 params dict。

        --key value  → params["key"] = value
        --key=value  → params["key"] = value（与 --root= 保持一致的写法）
        --bool-flag  → params["bool_flag"] = True
        --multi a b  → params["multi"] = ["a", "b"]（MULTI_FLAGS 里的 nargs="+" 旗标）
        -h           → params["-h"] = True（短选项保留原形，不改写成 --h）
        位置参数      → params["_positional"] = [...]
        """
        params: dict = {}
        positional: List[str] = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                # `--key=value`：以前不识别，整串被当成键名（`top=3`）导致值静默丢失
                if "=" in arg:
                    key, _, val = arg[2:].partition("=")
                    params[key.replace("-", "_")] = val
                    i += 1
                    continue
                key = arg[2:].replace("-", "_")
                if arg in MULTI_FLAGS:
                    vals: List[str] = []
                    j = i + 1
                    while j < len(args) and not args[j].startswith("-"):
                        vals.append(args[j])
                        j += 1
                    params[key] = vals
                    i = j
                    continue
                if arg in BOOL_FLAGS or i + 1 >= len(args) or args[i + 1].startswith("--"):
                    params[key] = True
                else:
                    params[key] = args[i + 1]
                    i += 1
            elif arg.startswith("-") and len(arg) > 1:
                # 短选项原样保留：以前改写成 `--h`，`kb query -h` 必然报错
                params[arg] = True
            else:
                positional.append(arg)
            i += 1
        if positional:
            params["_positional"] = positional
        return params

    # ── 命令解析与路由 ─────────────────────────────────────
    def _resolve(self, tool: str, sub: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
        """查 COMPAT_MAP 得到 (plugin_name, action)。"""
        # 优先查 (tool, sub)
        if sub is not None:
            key = (tool, sub)
            if key in COMPAT_MAP:
                return COMPAT_MAP[key]
        # 回退到 (tool, None)
        key = (tool, None)
        if key in COMPAT_MAP:
            return COMPAT_MAP[key]
        return None

    def run(self, args: List[str]) -> int:
        """主入口：解析命令 → 兼容映射 → 注册中心分发。"""
        if not args:
            return self._print_help()

        tool = args[0]
        rest = args[1:]

        # 特殊命令
        if tool in ("help", "--help", "-h"):
            return self._print_help()
        if tool in ("list",):
            self._ensure_initialized()
            return self.registry.cmd_list()

        # 提取子命令（如果第一个剩余参数不是 -- 开头）
        sub = None
        params_args = rest
        if rest and not rest[0].startswith("-"):
            # 第一个非 -- 参数可能是子命令
            candidate_sub = rest[0]
            # 检查 (tool, candidate_sub) 是否在映射表中
            if (tool, candidate_sub) in COMPAT_MAP:
                sub = candidate_sub
                params_args = rest[1:]
        # 写开关被写成旗标形态时，把它提升成子命令：
        # `kb clean --apply` → `kb clean apply`。README 与 `kb help` 都用这种写法，
        # 但以前会解析成 `clean.py dry-run --apply` → dry-run 子解析器没有 --apply → 报错退出。
        # 只在「(tool, 旗标名) 确实是 COMPAT_MAP 里的合法子命令」时提升，不误伤真旗标
        # （如 `--log`/`--json`/`--full` 都不是任何工具的子命令名）。
        if sub is None and rest and rest[0].startswith("--"):
            candidate = rest[0][2:]
            if candidate and "--" not in candidate and (tool, candidate) in COMPAT_MAP:
                sub = candidate
                params_args = rest[1:]

        # 查兼容映射表
        resolved = self._resolve(tool, sub)
        if resolved is None:
            # 尝试作为插件名直接查找
            self._ensure_initialized()
            plugin = self.registry.get_plugin(tool)
            if plugin is not None:
                # 直接作为插件名调用
                action = sub if sub else (params_args[0] if params_args and not params_args[0].startswith("-") else None)
                if action and action.startswith("-"):
                    action = None
                if action:
                    params_args = [a for a in params_args if a != action]
                meta = plugin.metadata()
                if action is None:
                    # 无 action，显示可用 actions（P3-26：返回 2，见下方同处说明）
                    print(f"插件 {meta.name} 需要指定动作，支持: "
                          f"{', '.join(meta.actions)}", file=sys.stderr)
                    print(f"  例: kb {tool} {meta.actions[0]}", file=sys.stderr)
                    return 2
                if action not in meta.actions:
                    print(f"❓ 插件 {meta.name} 不支持动作 {action}，"
                          f"可用: {', '.join(meta.actions)}", file=sys.stderr)
                    return 2
                params = self._parse_params(params_args)
                params.setdefault("root", self.vault_root)
                return self.registry.execute(meta.name, action, params)
            print(f"❓ 未知命令: {tool}  →  用 kb help", file=sys.stderr)
            return 2

        plugin_name, action = resolved

        # 特殊插件标记
        if plugin_name == "__list__":
            self._ensure_initialized()
            return self.registry.cmd_list()
        if plugin_name == "__help__":
            return self._print_help()

        self._ensure_initialized()

        # 如果 action 为 None，从参数推断
        if action is None:
            # 从第一个非 -- 参数推断
            if params_args and not params_args[0].startswith("-"):
                action = params_args[0]
                params_args = params_args[1:]
            else:
                # 查插件的 cli_aliases 获取默认 action
                alias_resolved = self.registry.resolve_alias(tool)
                if alias_resolved:
                    _, alias_action = alias_resolved
                    action = alias_action
            if action is None:
                # 显示插件可用动作
                plugin = self.registry.get_plugin(plugin_name)
                if plugin:
                    meta = plugin.metadata()
                    # P3-26：这里曾 `return 0`。问题是**语义撒谎** ——
                    # 用户/cron 敲 `kb sync` 想触发同步，得到动作清单 + 退出码 0，
                    # 于是"看起来成功、实际什么都没做"；而底层 sync.py 的 argparse
                    # 是 required=True，真跑会返回 2。列清单本身是有用的，但必须
                    # 伴随非零退出码，让调用方能分辨"这是用法提示，不是执行成功"。
                    print(f"插件 {meta.name} 需要指定动作，支持: "
                          f"{', '.join(meta.actions)}", file=sys.stderr)
                    print(f"  例: kb {tool} {meta.actions[0]}", file=sys.stderr)
                    return 2

        params = self._parse_params(params_args)
        params.setdefault("root", self.vault_root)
        return self.registry.execute(plugin_name, action, params)

    # ── 帮助 ───────────────────────────────────────────────
    def _print_help(self) -> int:
        """显示帮助信息。"""
        print("用法: kb <命令> [子命令] [参数...]")
        print("")
        print("检索:")
        print("  kb query \"问题\"           自然语言检索")
        print("  kb rag index              重建向量索引")
        print("")
        print("摄入:")
        print("  kb ingest                 收件箱 triage（review）")
        print("  kb ingest move            按路由搬运收件箱（写操作）")
        print("  kb ingest trash           归档收件箱（写操作）")
        print("  kb ingest agent           摄取 Agent 记忆进 KB")
        print("")
        print("Agent 管理:")
        print("  kb agent detect           探测本机 Agent")
        print("  kb agent list             列出已注册 Agent")
        print("  kb agent add --name N --type T   添加 Agent")
        print("  kb agent enable --name N --enable true   启用/禁用")
        print("")
        print("成长引擎:")
        print("  kb feedback               命中信号计数")
        print("  kb link                   孤岛补链建议")
        print("  kb link apply             应用补链（写操作）")
        print("  kb link moc               生成知识地图 MOC")
        print("  kb recall                 今日回忆 deck")
        print("  kb recall status          回忆排期状态")
        print("")
        print("治理:")
        print("  kb healthcheck            健康巡检")
        print("  kb dashboard              生成治理仪表盘")
        print("  kb clean --apply          清洗/分块（写操作）")
        print("  kb clean refine           一键：结构化+去重→分块→重建索引→校验")
        print("  kb clean taxonomy [--apply] 标签归一（受控词表：别名/层级）")
        print("  kb clean repair [--apply] 修历史分块残留（占位符/索引页打标）")
        print("  kb clean repair-headings [--apply] 修「## ## 」重复标题（--include-evidence 连证据层）")
        print("  kb validate               frontmatter 校验")
        print("  kb schema check           对照知识契约（reference/kb-schema.json）查偏差")
        print("  kb schema fields          字段使用率（看契约实际覆盖）")
        print("  kb schema bitemporal      补双时态字段（事件时间/事务时间，默认 dry-run）")
        print("")
        print("置信与矛盾（P3）:")
        print("  kb gate check             写入前质量门禁（pass/quarantine/reject 三态）")
        print("  kb consolidate run        睡眠时整理（空闲期算近重复/冲突/陈旧，只产提议）")
        print("  kb consolidate status     整理水位与上次结果")
        print("  kb ladder                  L0–L5 反馈阶梯状态（只读）")
        print("  kb ladder propose          产出各级提案（不改笔记，交人工）")
        print("  kb ladder run              propose + 简报（cron 入口）")
        print("  kb ladder apply --level L1 --id N   人工确认后落地")
        print("  kb ladder adjudicate      本地模型自主裁决（LLM 语义 + 实测双闸）")
        print("  kb ladder verdicts        裁决账本（谁判的/判了什么/依据）")
        print("  kb confidence audit       复合置信分审计（四因子可解释）+ 分档")
        print("  kb confidence show --rel P 解释某一篇为什么是这个分")
        print("  kb decay                  原则性遗忘审计（只降层，从不删文件）")
        print("  kb contradict scan        矛盾扫描（区分真冲突 / 近似重复）")
        print("  kb contradict apply       把冲突标注写进两篇 + 入人工队列")
        print("")
        print("评测（P4）:")
        print("  kb eval run               RAGAS 式四指标评测（本地模型当裁判）")
        print("  kb eval gold --generate   生成 ground truth 草稿（30 题）")
        print("  kb eval gold --review     逐题人工确认（交互式）")
        print("  kb eval compare --baseline F  指标对比")
        print("")
        print("审计与修复（P5）:")
        print("  kb audit scan             结构审计（漂移/内聚/一致性/孤儿/死链，只读）")
        print("  kb audit fix              预览可安全修复的动作（dry-run）")
        print("  kb audit fix --confirm    确认后落盘（不含合并/删除）")
        print("  kb audit log              审计历史与修复日志")
        print("")
        print("标签治理（P6）:")
        print("  kb tagfix scan            扫三类越界（路径型/别名型/未收录）")
        print("  kb tagfix fix [--apply]   治理（默认 dry-run）")
        print("")
        print("验收（推荐每次改动后跑）:")
        print("  kb verify                  五项全绿：契约/校验/巡检/探针/单测")
        print("  kb verify --with-index     额外跑检索质量评测（慢）")
        print("")
        print("原记忆（证据层，按需调用）:")
        print("  kb raw list               列出证据层清单（raw/ + memory/）")
        print("  kb raw show <ID>          取原记忆真实内容（--full 看全部）")
        print("  kb raw find \"关键词\"      在证据层原文里全文搜索")
        print("  kb raw path <ID>          只解析原文件路径")
        print("")
        print("知识结晶（空闲本地 Agent 把日志炼成可调用正典）:")
        print("  kb curate plan            只读：选出最该结晶的笔记 + 模型可用性")
        print("  kb curate run [--apply]   跑结晶（默认只出提案；--apply 写回正典）")
        print("  kb curate verify          离线重校验提案（接地/守恒/引用三关）")
        print("  kb curate report          结晶进度与质量指标")
        print("  kb curate review          列出待人工裁决的低置信提案")
        print("")
        print("同步:")
        print("  kb sync dry-run           知识同步预览")
        print("  kb sync apply <note.md>   同步笔记（写操作）")
        print("  kb sync rollback          回滚上次 sync")
        print("  kb sync history           查看同步历史")
        print("")
        print("成长引擎（研究层，可选）:")
        print("  kb rsi                     RSI 只读探针（改进建议）")
        print("  kb compile query --query Q 查询→知识（写提案，人工在环）")
        print("  kb compile status          查看待审提案")
        print("  kb compile apply --all     写入全部待审提案（写操作）")
        print("")
        print("研究层只读视图（P2-3，全部只读）:")
        print("  kb research usage          真实使用信号（查询成功率 / 再问率）")
        print("  kb research fitness        外部锚定适应度（0-1，含四分量）")
        print("  kb research drift          晋升漂移度量（内容漂移率 / 出处保留率）")
        print("  kb research scale          规模档位与索引层覆盖")
        print("")
        print("其他:")
        print("  kb backup [daily|weekly]  全量快照")
        print("  kb list                   列出所有插件")
        print("  kb help                   本说明")
        return 0


def _extract_root(argv: List[str]) -> Tuple[str, List[str]]:
    """从 argv 中提取 --root，剩余返回。"""
    vault_root = os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[1])
    cleaned = []
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            vault_root = argv[i + 1]
            i += 2
        elif argv[i].startswith("--root="):
            vault_root = argv[i].split("=", 1)[1]
            i += 1
        else:
            cleaned.append(argv[i])
            i += 1
    return vault_root, cleaned


def main() -> int:
    """CLI 入口。"""
    argv = sys.argv[1:]
    vault_root, args = _extract_root(argv)
    launcher = KBLauncher(vault_root)
    return launcher.run(args)


if __name__ == "__main__":
    sys.exit(main())
