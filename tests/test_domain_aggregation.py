#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_domain_aggregation.py — P2-1 域聚合的完整性守卫。

背景：P2-1 把 39 个插件收敛成 5 个域聚合类（ops + retrieval / crystallization /
ingestion / organization），旧插件降为保留原名的 shim。这个重构的风险不在
"能不能跑"，而在**静默丢东西**：

  · 某条路由漏搬 → 那个 action 变成"不支持动作"，但没人调用就不会红；
  · 某个 cli 别名漏搬 → `kb ladder` 这类命令悄悄失效；
  · 某个 action 的进程内快路径被误标 → 性能退化，测试全绿（实测变异验证时
    `raw.show` 路由被删，整套测试毫无反应 —— 就是这条守卫要堵的洞）。

所以这里做**全量对账**：把 29 个原插件在 git HEAD 里的 `ACTION_MAP` 当作
"事实源"，逐条断言聚合类里都有对应路由，且快路径标记一致。
新增一个域/一条路由时，这份对账会自动覆盖到。
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
PLUGINS = PIPELINE / "plugins"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PLUGINS))

import plugins.crystallization_plugin          # noqa: E402,F401
import plugins.ingestion_plugin                # noqa: E402,F401
import plugins.organization_plugin             # noqa: E402,F401
import plugins.ops_plugin                      # noqa: E402,F401
import plugins.retrieval_plugin                # noqa: E402,F401
from plugins._agent_impl import (              # noqa: E402
    AgentDetectPluginImpl, AgentJSONPluginImpl, AgentMDPluginImpl, AgentSQLitePluginImpl)
from plugins._domain_plugin import DomainPlugin  # noqa: E402
from plugins.crystallization_plugin import CrystallizationPlugin  # noqa: E402
from plugins.ingestion_plugin import IngestionPlugin              # noqa: E402
from plugins.ops_plugin import OpsPlugin                          # noqa: E402
from plugins.organization_plugin import OrganizationPlugin        # noqa: E402
from plugins.retrieval_plugin import RetrievalPlugin              # noqa: E402

# 文件名 → 原插件名（shim 文件名与 meta.name 不同，需显式映射）
SHIM_FILE_OF = {
    "rag": "rag_plugin.py", "recall": "recall_plugin.py", "raw": "raw_plugin.py",
    "rerank": "rerank_plugin.py", "contextual": "contextual_plugin.py",
    "compile": "compile_plugin.py", "rsi": "rsi_plugin.py",
    "curate": "curate_plugin.py", "ladder": "ladder_plugin.py",
    "confidence": "confidence_plugin.py", "contradict": "contradict_plugin.py",
    "gate": "gate_plugin.py", "consolidate": "consolidate_plugin.py",
    "intake": "intake_plugin.py", "feedback": "feedback_plugin.py",
    "ingest_chat": "ingest_chat_plugin.py", "ingest_convert": "ingest_convert_plugin.py",
    "agent_registry": "agent_detect_plugin.py", "agent_json": "agent_json_plugin.py",
    "agent_md": "agent_md_plugin.py", "agent_sqlite": "agent_sqlite_plugin.py",
    "clean": "clean_plugin.py", "schema": "schema_plugin.py", "link": "link_plugin.py",
    "tagfix": "tagfix_plugin.py", "classify": "classify_plugin.py",
    "graph": "graph_plugin.py", "semantic_chunk": "semantic_chunk_plugin.py",
    "validate": "validate_plugin.py",
    # ops 域（第一期）
    "backup": "backup_plugin.py", "sync": "sync_plugin.py", "memory_sync": "memory_sync_plugin.py",
    "healthcheck": "healthcheck_plugin.py", "health_metrics": "health_metrics_plugin.py",
    "dashboard": "dashboard_plugin.py", "audit": "audit_plugin.py",
    "eval": "eval_plugin.py", "verify": "verify_plugin.py", "user_manager": "user_manager_plugin.py",
}

AGGREGATES = [OpsPlugin, RetrievalPlugin, CrystallizationPlugin,
              IngestionPlugin, OrganizationPlugin]


def _all_subclasses(base):
    out = []
    for sub in base.__subclasses__():
        out.append(sub)
        out.extend(_all_subclasses(sub))
    return out


# ── 基线快照（冻结于 P2-1 第二期改造前，commit eaa5708）─────────────
#
# 为什么把基线写死在测试里、而不是每次 `git show HEAD:` 读：
#   P2-1 的改动一旦提交，HEAD 就变成了 shim 版本 —— 用 HEAD 当"改造前基线"
#   会自指（实测：提交后对账 0 条，测试自己把自己判红）。
#   固化快照后，这条守卫与 git 历史解耦，永远比对"合并前的事实"。
#
# 结构：{插件名: {"routes": {action: 原子子命令或None},
#              "in_proc": [进程内快路径的 action],
#              "aliases": [cli 别名],
#              "meta_actions": [metadata().actions]}}
PLUGIN_SNAPSHOT = {
    "agent_json": {
        "routes": {},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['ingest'],
    },
    "agent_md": {
        "routes": {},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['extract', 'mirror', 'mirror-core', 'sync'],
    },
    "agent_registry": {
        "routes": {},
        "in_proc": [],
        "aliases": ['agent', 'ingest-agent'],
        "meta_actions": ['add', 'detect', 'enable', 'ingest', 'list', 'setup'],
    },
    "agent_sqlite": {
        "routes": {},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['ingest'],
    },
    "audit": {
        "routes": {},
        "in_proc": [],
        "aliases": ['audit'],
        "meta_actions": ['fix', 'log', 'scan'],
    },
    "backup": {
        "routes": {},
        "in_proc": [],
        "aliases": ['backup'],
        "meta_actions": ['backup'],
    },
    "classify": {
        "routes": {"classify": None},
        "in_proc": [],
        "aliases": ['classify'],
        "meta_actions": ['classify'],
    },
    "clean": {
        "routes": {"dry-run": 'dry-run', "apply": 'apply', "report": 'report', "chunk": 'chunk', "refine": 'refine', "taxonomy": 'taxonomy', "repair": 'repair', "repair-headings": 'repair-headings'},
        "in_proc": [],
        "aliases": ['clean'],
        "meta_actions": ['apply', 'chunk', 'dry-run', 'refine', 'repair', 'repair-headings', 'report', 'taxonomy'],
    },
    "compile": {
        "routes": {"query": 'query', "apply": 'apply', "status": 'status'},
        "in_proc": [],
        "aliases": ['compile'],
        "meta_actions": ['apply', 'query', 'status'],
    },
    "confidence": {
        "routes": {"show": 'show', "audit": 'audit', "apply": 'apply', "decay": 'decay'},
        "in_proc": ['audit', 'show'],
        "aliases": ['confidence'],
        "meta_actions": ['apply', 'audit', 'decay', 'show'],
    },
    "consolidate": {
        "routes": {"run": 'run', "status": 'status'},
        "in_proc": [],
        "aliases": ['consolidate'],
        "meta_actions": ['run', 'status'],
    },
    "contextual": {
        "routes": {"build": 'build', "show": 'show'},
        "in_proc": [],
        "aliases": ['contextual'],
        "meta_actions": ['build', 'show'],
    },
    "contradict": {
        "routes": {"scan": 'scan', "apply": 'apply'},
        "in_proc": [],
        "aliases": ['contradict'],
        "meta_actions": ['apply', 'scan'],
    },
    "curate": {
        "routes": {"plan": 'plan', "run": 'run', "verify": 'verify', "report": 'report', "review": 'review'},
        "in_proc": [],
        "aliases": ['curate'],
        "meta_actions": ['plan', 'report', 'review', 'run', 'verify'],
    },
    "dashboard": {
        "routes": {},
        "in_proc": [],
        "aliases": ['dashboard'],
        "meta_actions": ['generate'],
    },
    "eval": {
        "routes": {},
        "in_proc": [],
        "aliases": ['eval'],
        "meta_actions": ['compare', 'gold', 'run'],
    },
    "feedback": {
        "routes": {"ingest": 'ingest', "hit": 'hit', "apply": 'apply'},
        "in_proc": [],
        "aliases": ['feedback'],
        "meta_actions": ['apply', 'hit', 'ingest'],
    },
    "gate": {
        "routes": {"check": 'check'},
        "in_proc": ['check'],
        "aliases": ['gate'],
        "meta_actions": ['check'],
    },
    "graph": {
        "routes": {"build": None},
        "in_proc": [],
        "aliases": ['graph'],
        "meta_actions": ['build'],
    },
    "health_metrics": {
        "routes": {},
        "in_proc": [],
        "aliases": ['health-metrics'],
        "meta_actions": ['kpi', 'metrics'],
    },
    "healthcheck": {
        "routes": {},
        "in_proc": [],
        "aliases": ['healthcheck', 'kb-healthcheck'],
        "meta_actions": ['check'],
    },
    "ingest_chat": {
        "routes": {"parse": None},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['parse'],
    },
    "ingest_convert": {
        "routes": {"convert": None},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['convert'],
    },
    "intake": {
        "routes": {"review": 'review', "apply": 'apply', "move": 'apply', "trash": 'apply'},
        "in_proc": [],
        "aliases": ['ingest'],
        "meta_actions": ['apply', 'move', 'review', 'trash'],
    },
    "ladder": {
        "routes": {"status": 'status', "propose": 'propose', "run": 'run', "apply": 'apply', "adjudicate": 'adjudicate', "verdicts": 'verdicts'},
        "in_proc": [],
        "aliases": ['ladder'],
        "meta_actions": ['adjudicate', 'apply', 'propose', 'run', 'status', 'verdicts'],
    },
    "link": {
        "routes": {"suggestions": 'suggestions', "apply": 'apply', "moc": 'moc'},
        "in_proc": [],
        "aliases": ['link'],
        "meta_actions": ['apply', 'moc', 'suggestions'],
    },
    "memory_sync": {
        "routes": {},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['promote', 'review'],
    },
    "rag": {
        "routes": {"index": 'index', "query": 'query'},
        "in_proc": ['query'],
        "aliases": ['query', 'rag'],
        "meta_actions": ['index', 'query'],
    },
    "raw": {
        "routes": {"show": 'show', "find": 'find', "list": 'list', "path": 'path'},
        "in_proc": [],
        "aliases": ['raw'],
        "meta_actions": ['find', 'list', 'path', 'show'],
    },
    "recall": {
        "routes": {"deck": 'deck', "mark": 'mark', "status": 'status'},
        "in_proc": ['deck', 'status'],
        "aliases": ['recall'],
        "meta_actions": ['deck', 'mark', 'status'],
    },
    "rerank": {
        "routes": {"rerank": None},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['rerank'],
    },
    "rsi": {
        "routes": {"probe": None},
        "in_proc": [],
        "aliases": ['rsi'],
        "meta_actions": ['probe'],
    },
    "schema": {
        "routes": {"check": 'check', "fields": 'fields', "bitemporal": 'bitemporal'},
        "in_proc": ['check', 'fields'],
        "aliases": ['schema'],
        "meta_actions": ['bitemporal', 'check', 'fields'],
    },
    "semantic_chunk": {
        "routes": {"chunk": None},
        "in_proc": [],
        "aliases": [],
        "meta_actions": ['chunk'],
    },
    "sync": {
        "routes": {},
        "in_proc": [],
        "aliases": ['ingest-any', 'sync'],
        "meta_actions": ['apply', 'dry-run', 'history', 'ingest-any', 'rollback'],
    },
    "tagfix": {
        "routes": {"scan": 'scan', "fix": 'fix'},
        "in_proc": [],
        "aliases": ['tagfix'],
        "meta_actions": ['fix', 'scan'],
    },
    "user_manager": {
        "routes": {},
        "in_proc": [],
        "aliases": ['user'],
        "meta_actions": ['add', 'check-perm', 'list', 'remove'],
    },
    "validate": {
        "routes": {"validate": None},
        "in_proc": ['validate'],
        "aliases": ['validate'],
        "meta_actions": ['validate'],
    },
    "verify": {
        "routes": {},
        "in_proc": [],
        "aliases": ['verify'],
        "meta_actions": ['all'],
    },
}


def _aggregate_routes():
    """全部聚合类的 (plugin, action) → (script, sub, in_proc) 合并视图。"""
    merged = {}
    for cls in AGGREGATES:
        for key, val in (getattr(cls, "_ROUTES", {}) or {}).items():
            merged[key] = val
    return merged


def _aggregate_delegates():
    """全部聚合类的委派表合并视图（自定义 execute 的插件走这里）。"""
    merged = {}
    for cls in AGGREGATES:
        for key, val in (getattr(cls, "CUSTOM_DELEGATES", {}) or {}).items():
            merged[key] = val
    return merged


class AggregateRouteCompletenessTest(unittest.TestCase):
    """全量对账：快照里每个 (plugin, action) 都必须仍能路由。"""

    def test_every_snapshot_action_is_still_routable(self):
        routes = _aggregate_routes()
        delegates = _aggregate_delegates()
        missing = []
        checked = 0
        for plug, info in sorted(PLUGIN_SNAPSHOT.items()):
            for action, sub in info["routes"].items():
                checked += 1
                key = (plug, action)
                if key in delegates:
                    continue
                if key not in routes:
                    missing.append(f"{plug}.{action}")
                    continue
                got = routes[key]
                # 原子子命令必须一致（None 表示不传位置参数 —— 传错会 invalid choice）
                if len(got) >= 2 and got[1] != sub:
                    missing.append(f"{plug}.{action}: 子命令 {got[1]!r} != 基线 {sub!r}")
        self.assertEqual(missing, [], f"路由丢失或退化: {missing}")
        self.assertGreaterEqual(checked, 60, f"只对账了 {checked} 条动作（应 ≥60）")

    def test_every_snapshot_alias_is_still_present(self):
        """cli 别名漏搬 → `kb ladder` 这类命令悄悄失效。"""
        missing = []
        for plug, info in sorted(PLUGIN_SNAPSHOT.items()):
            for cli in info["aliases"]:
                # 两种聚合形态：DomainPlugin 用 `_ALIASES`；第一期 OpsPlugin
                # 的别名直接写在 metadata().cli_aliases 里，故两处都要查。
                found = any(cli in (getattr(cls, "_ALIASES", None) or {})
                            or cli in (cls().metadata().cli_aliases or {})
                            for cls in AGGREGATES)
                if not found:
                    missing.append(f"{plug}: 别名 {cli!r} 丢失")
        self.assertEqual(missing, [], f"cli 别名丢失: {missing}")

    def test_every_custom_execute_action_is_delegated(self):
        """自定义 execute 的 4 个 agent 插件：每个 action 都必须有委派条目。

        只查"插件名在不在委派表里"是不够的 —— 实测把 `("agent_json","ingest")`
        整行删掉，既有测试全绿（插件名确实还在表里，但那个 action 已不可达）。
        这里按快照逐 action 对账。
        """
        delegates = _aggregate_delegates()
        missing = []
        for plug in ("agent_registry", "agent_json", "agent_md", "agent_sqlite"):
            for action in PLUGIN_SNAPSHOT[plug]["meta_actions"]:
                # agent_json 的 metadata 声明是 "ingest"，但原实现的 execute()
                # 从不检查 action 名；历史调用方（含测试）写的是 "json"。
                # **两个名字都必须各自有委派条目**（不能互相兜底）——
                # 实测只删 "ingest" 那条时测试全绿，因为 "json" 还能路由到，
                # 于是按 metadata 契约调用的 `execute("ingest")` 静默失效。
                keys_to_require = [(plug, action)]
                if plug == "agent_json" and action == "ingest":
                    keys_to_require.append(("agent_json", "json"))
                for k in keys_to_require:
                    if k not in delegates:
                        missing.append(f"{k[0]}.{k[1]}")
        self.assertEqual(missing, [], f"自定义 execute 的动作没有委派条目: {missing}")

    def test_in_process_whitelist_matches_snapshot(self):
        """进程内快路径标记必须与基线一致。

        误标成 False = 静默性能退化（rag.query 每次多 spawn 解释器 ~40ms）；
        误标成 True = 写动作进进程内，隔离性丢失。
        """
        routes = _aggregate_routes()
        wrong = []
        for plug, info in sorted(PLUGIN_SNAPSHOT.items()):
            head_inproc = set(info["in_proc"])
            for action in info["routes"]:
                key = (plug, action)
                if key not in routes:
                    continue
                got = routes[key][2] if len(routes[key]) >= 3 else False
                if bool(got) != (action in head_inproc):
                    wrong.append(f"{plug}.{action}: in_proc={got}，基线={action in head_inproc}")
        self.assertEqual(wrong, [], f"进程内快路径标记不一致: {wrong}")


class ShimContractTest(unittest.TestCase):
    """shim 必须保留原插件名与 actions —— 这是 CLI 契约的根。"""

    def test_every_shim_keeps_original_name(self):
        import plugin_registry
        from plugin_base import PluginContext
        import contextlib, io
        reg = plugin_registry.PluginRegistry(str(PLUGINS), str(REPO / "template-vault"))
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            reg.discover()
            reg.initialize_all(PluginContext(vault_root=str(REPO / "template-vault")))
        missing = [n for n in SHIM_FILE_OF if n not in reg._registry]
        self.assertEqual(missing, [], f"原插件名在注册表里消失了: {missing}")

    def test_shim_metadata_actions_unchanged(self):
        """shim 的 actions 必须与 HEAD 一致（不能因为合并而增删）。"""
        import re
        import plugin_registry
        from plugin_base import PluginContext
        import contextlib, io
        reg = plugin_registry.PluginRegistry(str(PLUGINS), str(REPO / "template-vault"))
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            reg.discover()
            reg.initialize_all(PluginContext(vault_root=str(REPO / "template-vault")))
        drift = []
        for plug, shim_file in sorted(SHIM_FILE_OF.items()):
            try:
                src = subprocess.run(
                    ["git", "show", f"HEAD:template-vault/pipeline/plugins/{shim_file}"],
                    capture_output=True, text=True, cwd=str(REPO), timeout=30).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            m = re.search(r"actions=\[([^\]]*)\]", src or "", re.S)
            if not m:
                continue
            head_acts = sorted(x.strip().strip("\"'") for x in
                               re.split(r"[,\n]", m.group(1)) if x.strip().strip("\"'"))
            entry = reg._registry.get(plug)
            if entry is None:
                drift.append(f"{plug}: 未注册")
                continue
            cur = sorted(entry.metadata.actions)
            if cur != head_acts:
                drift.append(f"{plug}: {cur} != {head_acts}")
        self.assertEqual(drift, [], f"shim actions 漂移: {drift}")


class DelegationTest(unittest.TestCase):
    """自定义 execute 的 4 个 agent 插件：委派不能回到 shim（否则无限递归）。"""

    def test_delegates_target_impl_not_shim(self):
        from plugins.ingestion_plugin import IngestionPlugin
        targets = IngestionPlugin._delegate_targets()
        for name, inst in targets.items():
            self.assertIsInstance(
                inst, (AgentDetectPluginImpl, AgentJSONPluginImpl,
                       AgentMDPluginImpl, AgentSQLitePluginImpl),
                f"{name} 的委派目标不是 *Impl（会递归回 shim）")

    def test_delegate_targets_do_not_inherit_domain_plugin(self):
        """*Impl 与聚合类无继承关系 —— 这是"不会递归"的结构性保证。"""
        for cls in (AgentDetectPluginImpl, AgentJSONPluginImpl,
                    AgentMDPluginImpl, AgentSQLitePluginImpl):
            self.assertFalse(issubclass(cls, DomainPlugin),
                             f"{cls.__name__} 继承了 DomainPlugin，委派会递归")

    def test_all_four_agents_are_delegated(self):
        from plugins.ingestion_plugin import IngestionPlugin
        plugs = {k[0] for k in IngestionPlugin.CUSTOM_DELEGATES}
        self.assertEqual(plugs, {"agent_registry", "agent_json", "agent_md", "agent_sqlite"})


class AggregateEntryTest(unittest.TestCase):
    """聚合入口 `kb <domain> <plugin>.<action>` 必须能通过注册表校验。"""

    def test_composite_keys_are_declared_in_metadata(self):
        for cls in AGGREGATES:
            meta_actions = set(cls().metadata().actions)
            for key in (getattr(cls, "_ROUTES", {}) or {}):
                if not isinstance(key, tuple):
                    continue
                composite = f"{key[0]}.{key[1]}"
                self.assertIn(composite, meta_actions,
                              f"{cls.__name__} 的 metadata.actions 缺复合键 {composite}"
                              f"（PluginRegistry.execute 会先校验再分发）")


if __name__ == "__main__":
    unittest.main()
