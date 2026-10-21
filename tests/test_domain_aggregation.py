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


def _extract_dict_body(src: str, name: str):
    r"""从源码里取 `name = { ... }` 的**字典体**（括号配平，单行多行都认）。

    为什么不用非贪婪正则：`ACTION_MAP` 有单行（`{"a": "a", "b": "b"}`）与多行
    （每行一个键）两种写法，非贪婪 `\{.*?\}` 在单行场景下匹配不到（实测：
    37 个插件里 36 个解析为空，对账直接失效）。
    """
    m = re.search(rf"\b{name}\s*=\s*\{{", src)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(src) and depth:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.end():i - 1] if depth == 0 else None


def _head_action_map(shim_file: str) -> dict:
    """从 git HEAD 解析原插件的 ACTION_MAP（P2-1 之前的事实源）。

    返回 {action: 原子子命令 或 None}。解析失败返回 {}（该插件本来就没有
    ACTION_MAP，如 ops 域那些用 `SCRIPT_NAME`+`ACTION_MAP` 的薄壳都有的除外）。
    """
    try:
        src = subprocess.run(
            ["git", "show", f"HEAD:template-vault/pipeline/plugins/{shim_file}"],
            capture_output=True, text=True, cwd=str(REPO), timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    if not src:
        return {}
    import re
    body = _extract_dict_body(src, "ACTION_MAP")
    if body is None:
        return {}
    out = {}
    for mm in re.finditer(r'"([^"]+)"\s*:\s*(None|"[^"]*"|\'[^\']*\')', body):
        v = mm.group(2)
        out[mm.group(1)] = None if v == "None" else v.strip("\"'")
    return out


def _aggregate_routes():
    """全部聚合类的 (plugin, action) → (script, sub, in_proc) 合并视图。"""
    merged = {}
    for cls in AGGREGATES:
        for key, val in (getattr(cls, "_ROUTES", {}) or {}).items():
            merged[key] = val
    return merged


def _aggregate_delegates():
    merged = {}
    for cls in AGGREGATES:
        for key, val in (getattr(cls, "CUSTOM_DELEGATES", {}) or {}).items():
            merged[key] = val
    return merged


class AggregateRouteCompletenessTest(unittest.TestCase):
    """全量对账：HEAD 里每个 (plugin, action) 都必须仍能路由。"""

    def test_every_head_action_is_still_routable(self):
        routes = _aggregate_routes()
        delegates = _aggregate_delegates()
        missing = []
        checked = 0
        for plug, shim_file in sorted(SHIM_FILE_OF.items()):
            for action, sub in _head_action_map(shim_file).items():
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
                    missing.append(f"{plug}.{action}: 子命令 {got[1]!r} != HEAD {sub!r}")
        self.assertEqual(missing, [], f"路由丢失或退化: {missing}")
        # 65 条 = 29 个插件里 25 个用 ACTION_MAP 的动作总数
        # （4 个 agent 插件走自定义 execute，其动作由 CUSTOM_DELEGATES 覆盖，
        #   单独在 DelegationTest 里校验）
        self.assertGreaterEqual(checked, 60,
                                f"只对账了 {checked} 条动作，解析可能失败（应 ≥60）")

    def test_every_head_alias_is_still_present(self):
        """cli 别名漏搬 → `kb ladder` 这类命令悄悄失效。"""
        import re
        missing = []
        for plug, shim_file in sorted(SHIM_FILE_OF.items()):
            try:
                src = subprocess.run(
                    ["git", "show", f"HEAD:template-vault/pipeline/plugins/{shim_file}"],
                    capture_output=True, text=True, cwd=str(REPO), timeout=30).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            body = _extract_dict_body(src or "", "cli_aliases")
            if body is None:
                continue
            for cli in re.findall(r'"([^"]+)"\s*:', body):
                # 两种聚合形态：DomainPlugin 用 `_ALIASES`（第一期 OpsPlugin 没有，
                # 它的别名直接写在 metadata().cli_aliases 里），故两处都要查。
                found = any(cli in (getattr(cls, "_ALIASES", None) or {})
                            or cli in (cls().metadata().cli_aliases or {})
                            for cls in AGGREGATES)
                if not found:
                    missing.append(f"{plug}: 别名 {cli!r} 丢失")
        self.assertEqual(missing, [], f"cli 别名丢失: {missing}")

    def test_in_process_whitelist_matches_head(self):
        """进程内快路径标记必须与 HEAD 的 IN_PROCESS_ACTIONS 一致。

        误标成 False = 静默性能退化（rag.query 每次多 spawn 一个解释器 ~40ms）；
        误标成 True = 写动作进进程内，隔离性丢失。
        """
        import re
        routes = _aggregate_routes()
        wrong = []
        for plug, shim_file in sorted(SHIM_FILE_OF.items()):
            try:
                src = subprocess.run(
                    ["git", "show", f"HEAD:template-vault/pipeline/plugins/{shim_file}"],
                    capture_output=True, text=True, cwd=str(REPO), timeout=30).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            m = re.search(r"IN_PROCESS_ACTIONS\s*=\s*\{([^}]*)\}", src or "")
            head_inproc = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
            for action in _head_action_map(shim_file):
                key = (plug, action)
                if key not in routes:
                    continue
                got = routes[key][2] if len(routes[key]) >= 3 else False
                if bool(got) != (action in head_inproc):
                    wrong.append(f"{plug}.{action}: in_proc={got}，HEAD={action in head_inproc}")
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
