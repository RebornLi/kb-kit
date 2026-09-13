#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin_registry.py — 插件注册中心。

职责：
  1. 发现：扫描 plugins/ 目录，动态加载所有 PluginBase 子类
  2. 注册：检查依赖、冲突、启用/禁用状态
  3. 生命周期：按依赖拓扑排序初始化，按逆序关闭
  4. 分发：查找插件并执行动作
  5. 查询：列出插件、解析 CLI 别名

零依赖，仅使用 Python 标准库。
"""
import importlib
import importlib.util
import json
import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugin_base import PluginBase, PluginContext, PluginMetadata


# ── 内部条目 ────────────────────────────────────────────────
class _PluginEntry:
    """注册表内部条目。"""

    __slots__ = ("plugin", "metadata", "status", "error_msg")

    def __init__(self, plugin: PluginBase, metadata: PluginMetadata,
                 status: str = "loaded", error_msg: str = ""):
        self.plugin = plugin
        self.metadata = metadata
        self.status = status          # "loaded" | "disabled" | "error"
        self.error_msg = error_msg


# ── 注册中心 ────────────────────────────────────────────────
class PluginRegistry:
    """插件注册中心 — 发现、注册、分发、生命周期管理。"""

    def __init__(self, plugins_dir: str, vault_root: str,
                 config_path: Optional[str] = None):
        self._plugins_dir: Path = Path(plugins_dir)
        self._vault_root: str = vault_root
        self._registry: Dict[str, _PluginEntry] = {}
        self._logger = logging.getLogger("kb.registry")
        # 插件配置（启用/禁用）
        self._config: dict = {}
        if config_path is None:
            config_path = str(Path(vault_root) / "reference" / "plugin-config.json")
        self._config_path = config_path
        self._load_config()

    # ── 配置加载 ───────────────────────────────────────────
    def _load_config(self) -> None:
        """从 plugin-config.json 加载启用/禁用配置。"""
        try:
            p = Path(self._config_path)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                # 去掉 _meta 等元字段
                self._config = {k: v for k, v in data.items()
                                if not k.startswith("_")}
        except (OSError, json.JSONDecodeError) as e:
            self._logger.warning(f"插件配置加载失败: {e}")
            self._config = {}

    def _is_enabled(self, plugin_name: str) -> bool:
        """检查插件是否启用（默认启用）。"""
        cfg = self._config.get(plugin_name, {})
        return cfg.get("enabled", True)

    # ── 发现与加载 ─────────────────────────────────────────
    def discover(self) -> None:
        """扫描 plugins_dir，加载所有插件模块。"""
        if not self._plugins_dir.is_dir():
            self._logger.warning(f"插件目录不存在: {self._plugins_dir}")
            return

        # 确保 plugins 目录在 sys.path 中（用于 importlib.import_module）
        plugins_parent = str(self._plugins_dir.parent)
        if plugins_parent not in sys.path:
            sys.path.insert(0, plugins_parent)
        plugins_pkg = str(self._plugins_dir)
        if plugins_pkg not in sys.path:
            sys.path.insert(0, plugins_pkg)

        # 扫描所有 .py 文件（排除 __init__.py、__pycache__）
        py_files = sorted(self._plugins_dir.glob("*.py"))
        for py_file in py_files:
            if py_file.name.startswith("__"):
                continue
            self._load_plugin_module(py_file)

    def _load_plugin_module(self, module_path: Path) -> Optional[PluginBase]:
        """加载单个插件模块，查找 PluginBase 子类并实例化。"""
        module_name = module_path.stem
        try:
            # 优先用 import_module（支持包内 import）
            mod = importlib.import_module(module_name)
        except Exception as e:
            self._logger.warning(f"插件模块 {module_name} 加载失败: {e}")
            return None

        # 遍历模块属性，查找 PluginBase 的非抽象子类
        found_plugin = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name, None)
            if (attr is None or not isinstance(attr, type)
                    or attr is PluginBase):
                continue
            try:
                if issubclass(attr, PluginBase):
                    # 跳过抽象类（有未实现的 abstractmethod）
                    abstract_methods = getattr(attr, "__abstractmethods__", set())
                    if abstract_methods:
                        continue
                    found_plugin = attr
                    break
            except TypeError:
                continue

        if found_plugin is None:
            self._logger.warning(f"{module_name}.py 中未找到 PluginBase 子类")
            return None

        try:
            instance = found_plugin()
            self.register(instance)
            return instance
        except Exception as e:
            self._logger.warning(f"插件 {module_name} 实例化失败: {e}")
            return None

    # ── 注册 ───────────────────────────────────────────────
    def register(self, plugin: PluginBase) -> bool:
        """注册插件到内部注册表。"""
        try:
            meta = plugin.metadata()
        except Exception as e:
            self._logger.warning(f"插件元数据获取失败: {e}")
            return False

        name = meta.name

        # 检查启用/禁用
        if not self._is_enabled(name):
            self._registry[name] = _PluginEntry(
                plugin, meta, status="disabled",
                error_msg="配置中已禁用")
            self._logger.info(f"插件 {name} 已禁用（配置）")
            return False

        # 检查名冲突
        if name in self._registry:
            self._logger.warning(f"插件名冲突: {name} 被覆盖")

        self._registry[name] = _PluginEntry(plugin, meta, status="loaded")
        self._logger.debug(f"已注册插件: {name}（{meta.type}）")
        return True

    # ── 生命周期 ───────────────────────────────────────────
    def initialize_all(self, ctx: PluginContext) -> None:
        """按依赖拓扑顺序初始化所有已注册插件。"""
        # 构建依赖图（仅对 status=loaded 的插件）
        loaded_names = {n for n, e in self._registry.items()
                        if e.status == "loaded"}
        deps: Dict[str, set] = {}
        for name in loaded_names:
            entry = self._registry[name]
            d = set(entry.metadata.dependencies) & loaded_names
            deps[name] = d

        # Kahn 拓扑排序
        in_degree = {n: len(deps[n]) for n in loaded_names}
        queue = deque(sorted(n for n in loaded_names if in_degree[n] == 0))
        order: List[str] = []
        while queue:
            n = queue.popleft()
            order.append(n)
            for m in loaded_names:
                if n in deps[m]:
                    in_degree[m] -= 1
                    if in_degree[m] == 0:
                        queue.append(m)

        # 有环的插件（in_degree > 0）标记 disabled
        cyclic = loaded_names - set(order)
        for n in cyclic:
            self._registry[n].status = "disabled"
            self._registry[n].error_msg = "依赖环"
            self._logger.warning(f"插件 {n} 存在依赖环，已禁用")

        # 按拓扑顺序初始化
        for name in order:
            entry = self._registry[name]
            # 检查依赖是否都可用
            missing = [d for d in entry.metadata.dependencies
                       if d not in self._registry
                       or self._registry[d].status != "loaded"]
            if missing:
                entry.status = "disabled"
                entry.error_msg = f"依赖缺失: {missing}"
                self._logger.warning(f"插件 {name} 依赖缺失: {missing}，已禁用")
                continue
            try:
                entry.plugin.initialize(ctx)
            except Exception as e:
                entry.status = "error"
                entry.error_msg = str(e)
                self._logger.warning(f"插件 {name} 初始化失败: {e}")

    def shutdown_all(self) -> None:
        """按逆依赖顺序关闭所有插件。"""
        # 简化：按注册逆序关闭
        for name in reversed(list(self._registry.keys())):
            entry = self._registry[name]
            if entry.status == "loaded":
                try:
                    entry.plugin.shutdown()
                except Exception as e:
                    self._logger.warning(f"插件 {name} 关闭失败: {e}")

    # ── 查找与分发 ─────────────────────────────────────────
    def execute(self, plugin_name: str, action: str, params: dict) -> Any:
        """查找插件并执行动作。返回插件的执行结果（通常为 int 退出码）。"""
        entry = self._registry.get(plugin_name)
        if entry is None:
            print(f"❓ 未知插件: {plugin_name}", file=sys.stderr)
            self._print_available()
            return 2
        if entry.status == "disabled":
            print(f"⚠️ 插件 {plugin_name} 已禁用: {entry.error_msg}",
                  file=sys.stderr)
            return 3
        if entry.status == "error":
            print(f"❌ 插件 {plugin_name} 初始化失败: {entry.error_msg}",
                  file=sys.stderr)
            return 1
        if action not in entry.metadata.actions:
            print(f"❓ 插件 {plugin_name} 不支持动作 {action}，"
                  f"可用: {', '.join(entry.metadata.actions)}", file=sys.stderr)
            return 2
        try:
            return entry.plugin.execute(action, params)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ 插件 {plugin_name} 执行 {action} 失败: {e}",
                  file=sys.stderr)
            return 1

    def get_plugin(self, name: str) -> Optional[PluginBase]:
        """按名获取插件实例。"""
        entry = self._registry.get(name)
        return entry.plugin if entry else None

    # ── 查询 ───────────────────────────────────────────────
    def list_plugins(self, plugin_type: Optional[str] = None) -> List[Tuple[PluginMetadata, str, str]]:
        """列出所有插件元信息及状态。

        Args:
            plugin_type: 可选类型过滤（"agent" | "kb_module"）
        Returns:
            [(metadata, status, error_msg), ...]
        """
        result = []
        for entry in self._registry.values():
            if plugin_type and entry.metadata.type != plugin_type:
                continue
            result.append((entry.metadata, entry.status, entry.error_msg))
        return result

    def resolve_alias(self, cli_command: str) -> Optional[Tuple[str, Optional[str]]]:
        """CLI 兼容：将旧命令名解析为 (plugin_name, action)。"""
        for entry in self._registry.values():
            if cli_command in entry.metadata.cli_aliases:
                action = entry.metadata.cli_aliases[cli_command]
                return (entry.metadata.name, action)
        return None

    def _print_available(self) -> None:
        """打印可用插件列表到 stderr。"""
        plugins = self.list_plugins()
        if not plugins:
            print("（无可用插件）", file=sys.stderr)
            return
        print("可用插件:", file=sys.stderr)
        for meta, status, _ in plugins:
            print(f"  - {meta.name}（{meta.type}）[{status}] "
                  f"actions: {', '.join(meta.actions)}", file=sys.stderr)

    # ── CLI 入口 ───────────────────────────────────────────
    def cmd_list(self) -> int:
        """kb list 命令：列出所有插件。"""
        plugins = self.list_plugins()
        if not plugins:
            print("（无已注册插件）")
            return 0
        agents = [p for p in plugins if p[0].type == "agent"]
        kb_mods = [p for p in plugins if p[0].type == "kb_module"]
        if agents:
            print("Agent 插件:")
            for meta, status, err in agents:
                flag = {"loaded": "✅", "disabled": "⚪", "error": "❌"}.get(status, "❓")
                print(f"  {flag} {meta.name}（v{meta.version}）— {meta.description} [{status}]")
        if kb_mods:
            print("KB 模块插件:")
            for meta, status, err in kb_mods:
                flag = {"loaded": "✅", "disabled": "⚪", "error": "❌"}.get(status, "❓")
                print(f"  {flag} {meta.name}（v{meta.version}）— {meta.description} [{status}]")
        return 0


def _main():
    """命令行入口：python3 plugin_registry.py [list] --root R"""
    import argparse
    ap = argparse.ArgumentParser(description="插件注册中心")
    ap.add_argument("cmd", nargs="?", default="list", choices=["list"])
    ap.add_argument("--root", default=os.environ.get("KB_ROOT")
                    or str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--plugins-dir", default=None)
    args = ap.parse_args()

    vault_root = args.root
    plugins_dir = args.plugins_dir or str(Path(__file__).resolve().parent / "plugins")
    config_path = str(Path(vault_root) / "reference" / "plugin-config.json")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    registry = PluginRegistry(plugins_dir, vault_root, config_path)
    registry.discover()
    ctx = PluginContext(vault_root=vault_root)
    registry.initialize_all(ctx)

    if args.cmd == "list":
        return registry.cmd_list()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
