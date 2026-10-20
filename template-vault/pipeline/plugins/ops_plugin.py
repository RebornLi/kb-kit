#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops_plugin.py — 运维域聚合插件（P2-1 领域合并试点）。

把 backup / sync / memory_sync / healthcheck / health_metrics / dashboard /
audit / eval / verify / user_manager 十个运维能力收敛到**单一插件**：原来 10 个
~20 行的 `SubprocessPlugin` 薄壳各写一份 `SCRIPT_NAME` + `ACTION_MAP`，
现在合成一张 `_ROUTES` 路由表。

设计要点（决定能不能安全合并的关键，改动前先读）：

1. **action 统一加 `{plugin}_` 前缀消歧**。`PluginRegistry.execute()` 按
   `action in meta.actions` 精确匹配后调用 `plugin.execute(action, params)`，
   一个类内 action 名即唯一键。运维域本身 0 个撞名，但仍统一命名 ——
   与其余 4 个域（retrieval 有 query/show/status、crystallization 有
   apply/run/status 等真实撞名）保持一致规则，也便于将来整域替换。

2. **cli_aliases 已在合并前做过撞名检查**。10 个原插件的别名并集共 11 个键，
   无重复（`user_manager.user` 与 `audit.audit` 不撞；`ingest-any` 是 `sync`
   独有别名）。新增域合并前**必须重跑**这项检查。

3. **两套 action 键并存**：`_ROUTES` 用消歧后的聚合名（新入口），
   `_COMPAT_ACTIONS` 把 shim 的原 CLI 契约名映射过来（兼容面）。
   缺了后者 → 所有 shim 执行都报"不支持动作"（实施时实际踩到，见
   `test_p0_fixes.py::OpsAggregatePluginTest`）。

4. **行为不变是本插件的硬约束**。`execute()` 用**实例属性**遮蔽类属性来选路，
   再委托 `SubprocessPlugin.execute()` —— CLI 构造（`[python, script, orig_sub,
   --root, root, ...flags]`）与原子子命令透传逻辑逐字节复用原实现，
   不复制也不改写。`IN_PROCESS_ACTIONS` 仍是 per-action 白名单，
   只有原本就走快路径的 `healthcheck` / `audit scan` / `audit log` 保留。

5. **类级 `IN_PROCESS_ACTIONS` 保持空 `set()`**。写动作不得进程内化这条
   静态护栏（`test_p0_fixes.py::test_write_actions_are_not_in_process`）靠扫
   `vars(module)` 里的类属性实现；实例级遮蔽不被静态扫描看到，护栏仍然有效。

旧插件文件（`backup_plugin.py` 等 10 个）改为继承本类的 shim，保留原
`meta.name` 与 CLI 契约；COMPAT_MAP 继续指向原名，故 CLI 行为 100% 不变。
"""
from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class OpsPlugin(SubprocessPlugin):
    """ops 域聚合插件 — 一个类承载 10 个运维能力的路由。"""

    # action → (SCRIPT_NAME, 原子子命令, 是否走进程内快路径)
    #   原子子命令 None = 原脚本无子命令，不能把 action 当位置参数传
    #   in_process  仅对**只读** action 开启（healthcheck check / audit scan|log）
    _ROUTES = {
        #                     SCRIPT_NAME           原子子命令      in_proc
        "backup":             ("backup.py",          None,           False),
        "sync_dry-run":       ("sync.py",            "dry-run",      False),
        "sync_apply":         ("sync.py",            "apply",        False),
        "sync_rollback":      ("sync.py",            "rollback",     False),
        "sync_history":       ("sync.py",            "history",      False),
        "sync_ingest-any":    ("sync.py",            "ingest-any",   False),
        "memory_sync_review":  ("memory_sync.py",    "review",       False),
        "memory_sync_promote": ("memory_sync.py",    "promote",      False),
        "healthcheck":         ("kb-healthcheck.py", None,          True),
        "health_metrics":      ("kb_health.py",      None,           False),
        "health_metrics_kpi":  ("kb_health.py",      "kpi",          False),
        "dashboard_generate":  ("dashboard.py",      None,           False),
        "audit_scan":          ("kb_audit.py",       "scan",         True),
        "audit_fix":           ("kb_audit.py",       "fix",          False),
        "audit_log":           ("kb_audit.py",       "log",          True),
        "eval_run":            ("kb_eval_judge.py",  "run",          False),
        "eval_gold":           ("kb_eval_judge.py",  "gold",         False),
        "eval_compare":        ("kb_eval_judge.py",  "compare",      False),
        "verify_all":          ("kb_verify.py",      None,           False),
        "user_add":            ("user_manager.py",   "add",          False),
        "user_list":           ("user_manager.py",   "list",         False),
        "user_remove":         ("user_manager.py",   "remove",       False),
        "user_check-perm":     ("user_manager.py",   "check-perm",   False),
    }

    # ── 兼容层：原 per-plugin 的 action 名 → 消歧后的聚合 action 名 ──────────
    # 为什么需要：`<name>_plugin.py` shim 保留原 `meta.actions`（那是 CLI 契约：
    # `kb verify` 无子命令、`kb audit scan` 传 "scan"），而 shim 继承本类的
    # execute()，只认 `_ROUTES` 的键。若只登记聚合名前缀，所有 shim 调用都会
    # "不支持动作" —— 踩过一次（kb verify 的巡检项实测报错才发现）。
    # 故两套键都登记，指向同一路由：原名保 CLI 兼容，聚合名是新入口。
    _COMPAT_ACTIONS = {
        "backup":   "backup",
        "dry-run":  "sync_dry-run",
        "apply":    "sync_apply",
        "rollback": "sync_rollback",
        "history":  "sync_history",
        "ingest-any": "sync_ingest-any",
        "review":   "memory_sync_review",
        "promote":  "memory_sync_promote",
        "check":    "healthcheck",
        "metrics":  "health_metrics",
        "kpi":      "health_metrics_kpi",
        "generate": "dashboard_generate",
        "scan":     "audit_scan",
        "fix":      "audit_fix",
        "log":      "audit_log",
        "run":      "eval_run",
        "gold":     "eval_gold",
        "compare":  "eval_compare",
        "all":      "verify_all",
        "add":      "user_add",
        "list":     "user_list",
        "remove":   "user_remove",
        "check-perm": "user_check-perm",
    }

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ops",
            version="1.0",
            plugin_type="kb_module",
            actions=sorted(self._ROUTES),
            cli_aliases={
                # 来自各原插件 metadata().cli_aliases，值改为消歧后的 action 名
                "backup": "backup",
                "sync": None,
                "ingest-any": "sync_ingest-any",
                "healthcheck": "healthcheck",
                "kb-healthcheck": "healthcheck",
                "health-metrics": "health_metrics",
                "dashboard": "dashboard_generate",
                "audit": "audit_scan",
                "eval": "eval_run",
                "verify": "verify_all",
                "user": None,
            },
            description="运维域：备份/同步/巡检/度量/仪表盘/审计/评测/验收/用户管理",
        )

    def initialize(self, ctx) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        # 兼容动作名（来自各 shim 的原 CLI 契约）→ 聚合路由键
        action = self._COMPAT_ACTIONS.get(action, action)
        route = self._ROUTES.get(action)
        if route is None:
            print(f"❓ ops 不支持动作: {action}", flush=True)
            return 2
        script, orig_sub, in_proc = route
        # 实例属性遮蔽类属性：只在本次调用生效，不污染其他 action 的默认值
        self.SCRIPT_NAME = script
        self.ACTION_MAP = {action: orig_sub}
        self.IN_PROCESS_ACTIONS = {action} if in_proc else set()
        return SubprocessPlugin.execute(self, action, params)
