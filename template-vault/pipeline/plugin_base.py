#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin_base.py — 插件基协议 + 上下文对象 + 元数据值对象。

所有 KB 功能模块和 Agent 适配器插件都必须继承 PluginBase，
通过 PluginContext 访问共享服务，用 PluginMetadata 声明元信息。

设计原则：
  - 零 pip 依赖，仅使用 Python 标准库（abc, logging, typing）
  - Python 3.8+ 兼容（不使用 3.9+ 独有语法）
  - 依赖注入：插件不直接 import 其他模块，通过 context 访问共享服务
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import logging


class PluginContext:
    """插件上下文对象 — 依赖注入容器。

    插件通过此对象访问共享服务（状态管理、配置、日志），
    不直接 import 其他 pipeline 模块。
    """

    def __init__(self, vault_root: str,
                 state_store: Any = None,
                 config: Optional[dict] = None,
                 logger: Optional[logging.Logger] = None):
        self.vault_root: str = vault_root
        self.state_store = state_store          # StateStore 实例（可选）
        self.config: dict = config or {}        # 插件配置 dict
        self.logger: logging.Logger = logger or logging.getLogger("kb.plugin")


class PluginMetadata:
    """插件元数据 — 不可变值对象。

    字段：
      name         — 插件名（唯一标识，用于注册和查找）
      version      — 插件版本
      plugin_type  — 插件类型："agent" | "kb_module"
      actions      — 支持的动作列表（对应原 CLI 子命令）
      dependencies — 依赖的其他插件名列表（默认空）
      description  — 人类可读描述
      cli_aliases  — CLI 兼容映射 {旧命令名: action 或 None}
    """

    def __init__(self, name: str, version: str, plugin_type: str,
                 actions: List[str],
                 dependencies: Optional[List[str]] = None,
                 description: str = "",
                 cli_aliases: Optional[Dict[str, Optional[str]]] = None):
        self.name: str = name
        self.version: str = version
        self.type: str = plugin_type            # "agent" | "kb_module"
        self.actions: List[str] = list(actions)
        self.dependencies: List[str] = list(dependencies or [])
        self.description: str = description
        self.cli_aliases: Dict[str, Optional[str]] = dict(cli_aliases or {})


class PluginBase(ABC):
    """插件基类 — 所有插件必须继承。

    子类必须实现：
      metadata()    — 返回 PluginMetadata
      initialize()  — 初始化插件（注册中心启动时调用）
      execute()     — 执行插件动作

    子类可选实现：
      shutdown()    — 清理资源（默认空实现）
    """

    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """返回插件元信息。"""
        ...

    @abstractmethod
    def initialize(self, ctx: PluginContext) -> None:
        """初始化插件（注册中心启动时调用）。"""
        ...

    @abstractmethod
    def execute(self, action: str, params: dict) -> Any:
        """执行插件动作。返回值由具体插件定义（通常为 int 退出码）。"""
        ...

    def shutdown(self) -> None:
        """清理资源（默认空实现，子类可选覆盖）。"""
        pass
