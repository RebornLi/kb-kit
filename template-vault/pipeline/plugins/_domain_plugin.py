#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_domain_plugin.py — 领域聚合插件基类（P2-1 第二期）。

`OpsPlugin`（第一期，ops 域）用「action 加前缀消歧」的方式合并了 10 个零撞名插件。
本基类把它推广到**有 action 撞名**的 4 个域，核心差别只有一个：

    OpsPlugin：   action                 → 路由        （域内无撞名）
    DomainPlugin：(plugin_name, action)  → 路由        （域内有撞名，必须带插件名）

为什么不用「改名消歧」：撞名的 action 一旦改名（如 `rag.query` → `rag_query`），
`COMPAT_MAP` 里那 **87 条** value 全都要跟着改 —— 而 COMPAT_MAP 是对用户的
CLI 契约，改它就是 API 变更。用复合键后，**原插件名与原 action 名一个字都不用改**，
COMPAT_MAP 一条不动，CLI 行为 100% 不变。

两套 action 视图：
  · `metadata().actions`  —— 对外仍只列**本插件自己**的 action（不含插件名前缀）。
  · `domain_actions()`    —— 复合键列表，供聚合入口（`kb retrieval …`）使用。

分发有两条路，都能工作：
  1. 旧 shim（保留原插件名的子类）调 `execute(action)` → 用**自身的**
     `_PLUGIN_NAME` 拼复合键。调用方零改动。
  2. 聚合入口直接调 `execute("rag.query")` → 复合键直通。

这是 P2-1 第二期的关键设计：**聚合是为了让域边界可见，不是为了改契约。**
"""
from typing import Any, Dict, List, Optional

from plugin_base import PluginMetadata
from plugins._subprocess_plugin import SubprocessPlugin


class DomainPlugin(SubprocessPlugin):
    """域聚合插件基类：`(plugin_name, action)` 复合键 → 具体脚本路由。

    子类必须定义：
      DOMAIN         领域名（如 "retrieval"）
      _PLUGIN_NAME   聚合入口自身在元数据里的 name（= DOMAIN）
      _ROUTES        {(plugin, action): (script_name, 原子子命令, 是否进程内)}
      _ALIASES       {cli 命令名: (plugin, action)}   —— 来自各原插件的 cli_aliases
      _DESCRIPTIONS  {plugin: 一句话描述}
    """

    DOMAIN: str = ""
    _PLUGIN_NAME: str = ""          # 聚合入口的 meta.name（= DOMAIN）
    _ROUTES: Dict[Any, Any] = {}
    _ALIASES: Dict[str, Any] = {}
    _DESCRIPTIONS: Dict[str, str] = {}

    # 少数原插件在 ACTION_MAP 之外还有额外参数改写：{(plugin, action): fn(params)->params}
    _ACTION_HOOKS: Dict[Any, Any] = {}

    # ── 兼容层：旧 shim 用自身的原插件名拼复合键 ──────────────
    SHIM_PLUGIN: Optional[str] = None      # 由 shim 子类覆盖为原插件名

    def metadata(self) -> PluginMetadata:
        """聚合入口的元信息。

        `actions` 只列**本域各原插件的 action 去重并集**（不加插件名前缀）——
        因为它们本来就来自不同插件，同名是历史事实，不是冲突。
        真正消歧用 `domain_actions()` 的复合键。
        """
        acts: List[str] = []
        for (_plug, act) in self._ROUTES:
            if act not in acts:
                acts.append(act)
        # 保留原插件里出现过的 cli 别名，值改为复合键（供 resolve_alias 用）
        #
        # `actions` = 裸 action ∪ 复合键。为什么两者都放：
        #   · 裸 action —— 旧 shim 走 `execute("<action>")`（自身 SHIM_PLUGIN 拼键），
        #     需要它仍在 actions 里，否则 PluginRegistry.execute 的
        #     `action in metadata.actions` 校验会先把请求挡掉；
        #   · 复合键 —— 聚合入口 `kb retrieval rag.query` 的显式调用形态。
        # 两者并存后，两条路都能通过注册表校验（实测：只放裸 action 时复合键被拒）。
        composite = [f"{p}.{a}" for (p, a) in sorted(self._ROUTES)]
        if getattr(self, "CUSTOM_DELEGATES", None):
            composite += [f"{p}.{a}" for (p, a) in sorted(self.CUSTOM_DELEGATES)]
        return PluginMetadata(
            name=self._PLUGIN_NAME,
            version="1.0",
            plugin_type="kb_module",
            actions=sorted(set(acts) | set(composite)),
            cli_aliases={k: f"{v[0]}.{v[1]}" for k, v in self._ALIASES.items()},
            description=f"{self.DOMAIN} 域聚合："
                        + " / ".join(self._DESCRIPTIONS.get(p, p)
                                     for p in self._domain_plugin_names()),
        )

    @classmethod
    def _domain_plugin_names(cls) -> List[str]:
        seen: List[str] = []
        for (plug, _act) in cls._ROUTES:
            if plug not in seen:
                seen.append(plug)
        return seen

    def domain_actions(self) -> List[str]:
        """复合键形式：`["rag.query", "rag.index", …]`，供聚合入口分派。"""
        return sorted(f"{p}.{a}" for (p, a) in self._ROUTES)

    def initialize(self, ctx) -> None:
        self._ctx = ctx

    @staticmethod
    def _json_path(params: dict):
        """解析 JSON 记忆源路径（原 `agent_detect_plugin._json_path`，移包后归此）。

        `--json` 在 BOOL_FLAGS 里（全局布尔旗标），所以 `kb agent add --json <path>`
        会被解析成 `json=True`，真正的路径落到 `_positional`。这里把它回收回来；
        取不到就返回 None，由调用方给出可读报错 —— 绝不写出 `sources.json = true`。

        P2-1 第二期注意：这是**纯参数解析辅助**（无实例状态），放基类里而不是
        shim 里，这样 `AgentDetectPlugin._json_path` 靠继承仍然可用
        （`tests/test_p0_plugins.py` 直接调它）。
        """
        v = params.get("json_path")
        if isinstance(v, str) and v:
            return v
        v = params.get("json")
        if isinstance(v, str) and v:
            return v
        if v is True:
            rest = params.get("_positional") or []
            if rest:
                return rest[0]
        return None

    def execute(self, action: str, params: dict) -> int:
        """两路分发：`"<plugin>.<action>"` 或（旧 shim）裸 `"<action>"`。"""
        key: Optional[Any] = None
        if "." in action:
            plug, _, act = action.partition(".")
            key = (plug, act)
            if key not in self._ROUTES:
                # 允许 action 名本身含点（如 "dry-run" 不含，但保险起见回退）
                key = None
        if key is None:
            # 旧 shim 姿态：用自身原插件名拼键
            own = self.SHIM_PLUGIN
            if own is not None and (own, action) in self._ROUTES:
                key = (own, action)
        if key is None or key not in self._ROUTES:
            print(f"❓ {self._PLUGIN_NAME} 不支持动作: {action}", flush=True)
            return 2
        script, orig_sub, in_proc = self._ROUTES[key]
        # 个别原插件在 `ACTION_MAP` 之外还有额外行为（见 `_ACTION_HOOKS`）：
        # 例如 intake 的 move/trash 都是落到 `apply` 子命令，但要**额外注入
        # `--move` / `--trash`** 布尔旗标才能区分语义。原 `IntakePlugin.execute`
        # 就是这么做的；合并时若只照抄 ACTION_MAP 会丢掉这段，
        # 表现为 `kb ingest move` 静默不生效（返回 0 但什么都没挪）。
        hook = self._ACTION_HOOKS.get(key)
        if hook is not None:
            params = hook(params)
        # 实例属性遮蔽类属性：只在本次调用生效，不污染其他 action
        self.SCRIPT_NAME = script
        self.ACTION_MAP = {action: orig_sub}
        self.IN_PROCESS_ACTIONS = {action} if in_proc else set()
        return SubprocessPlugin.execute(self, action, params)


class DomainShim(DomainPlugin):
    """旧插件的兼容垫片基类：保留原 `meta.name` 与 `actions`，只是实现来自聚合类。

    子类只需给出 `SHIM_PLUGIN`（原插件名）与 `metadata()`（原样照抄原插件）。
    `import *` 之类的写法不存在 —— 所有插件名字都是显式声明的。
    """

    def metadata(self) -> PluginMetadata:      # pragma: no cover - 子类实现
        raise NotImplementedError


class DomainPluginWithDelegates(DomainPlugin):
    """带「自定义 execute 委派」的域聚合插件（摄入域用）。

    摄入域里 `agent_registry` / `agent_json` / `agent_md` / `agent_sqlite` 四个
    原插件不是 `SubprocessPlugin`：它们直接 import 引擎函数、自己校验参数、
    自己归一化退出码（例如 `agent_json` 要把"写入条数"折成 0/1）。把这四段逻辑
    搬进聚合类只会增加分叉风险，故聚合类**转交**给原插件类执行。

    委派表 `CUSTOM_DELEGATES: {(plugin, action): 原插件名}`；目标类通过
    `_delegate_targets()` 惰性解析（避免 import 环：原插件文件反过来 import 本模块）。
    """

    CUSTOM_DELEGATES: Dict[Any, str] = {}

    @staticmethod
    def _delegate_targets() -> Dict[str, Any]:
        """{原插件名: 原始实现实例}。

        必须指向 `_agent_impl.*Impl`，**不能**指向 `agent_*_plugin.Agent*Plugin`：
        后者是本聚合类的 shim 子类，委派回去会形成

            shim → IngestionPlugin.execute → 委派 → shim → …

        无限递归（P2-1 第二期实测 RecursionError）。实现与门面分开是本设计的前提。
        """
        from plugins._agent_impl import (AgentDetectPluginImpl, AgentJSONPluginImpl,
                                         AgentMDPluginImpl, AgentSQLitePluginImpl)
        return {"agent_registry": AgentDetectPluginImpl(),
                "agent_json": AgentJSONPluginImpl(),
                "agent_md": AgentMDPluginImpl(),
                "agent_sqlite": AgentSQLitePluginImpl()}

    def execute(self, action: str, params: dict) -> int:
        key: Any
        if "." in action:
            plug, _, act = action.partition(".")
            key = (plug, act)
        else:
            own = self.SHIM_PLUGIN
            key = (own, action) if own is not None else None
        target = self.CUSTOM_DELEGATES.get(key)
        if target is not None:
            inst = self._delegate_targets()[target]
            inst.initialize(self._ctx)
            return inst.execute(key[1], params)
        return super().execute(action, params)
