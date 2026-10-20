#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_plugin.py — @deprecated shim（P2-1 域合并试点）。

实现已迁入 `ops_plugin.OpsPlugin`（name="ops"）；本文件只保留原插件名
`verify` 与 CLI 别名，保证 `kb verify` / `plugin-config.json` /
外部 import 继续可用。新代码请直接用 `ops` 域聚合入口。

注意：kb_verify.py 没有子命令，不能把 action 当位置参数传
（传了会报 'unrecognized arguments: all'）。
"""
from plugin_base import PluginMetadata
from plugins.ops_plugin import OpsPlugin


class VerifyPlugin(OpsPlugin):
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="verify", version="1.0", plugin_type="kb_module",
            actions=["all"],
            cli_aliases={"verify": "all"},
            description="一行验收：契约/校验/巡检/探针/单测 五项全绿（退出码 0=全绿）",
        )
