#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_subprocess_plugin.py — 子进程封装基类。

为 KB 功能模块插件提供统一的 subprocess 调用封装，
保持与原 CLI 100% 兼容（原模块的 argparse + main() 不变）。

子类只需定义：
  - metadata() 返回 PluginMetadata
  - SCRIPT_NAME 原模块文件名（如 "rag.py"）
  - ACTION_MAP  action → 原模块子命令参数列表
"""
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_base import PluginBase, PluginContext, PluginMetadata


class SubprocessPlugin(PluginBase):
    """通过 subprocess 调用原脚本的插件基类。"""

    SCRIPT_NAME: str = ""               # 原模块文件名，如 "rag.py"
    ACTION_MAP: Dict[str, Optional[str]] = {}  # action → 原子命令（None 表示直接用 action）

    def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx

    def execute(self, action: str, params: dict) -> int:
        root = params.get("root", self._ctx.vault_root)
        pipeline_dir = Path(root) / "pipeline"
        script_path = pipeline_dir / self.SCRIPT_NAME

        if not script_path.exists():
            print(f"❌ 脚本不存在: {script_path}", file=sys.stderr)
            return 1

        # 构造命令行参数
        cmd = [sys.executable, str(script_path)]

        # 映射 action 到原子命令
        orig_sub = self.ACTION_MAP.get(action, action)
        if orig_sub:
            cmd.append(orig_sub)

        # 添加 --root
        cmd.extend(["--root", str(root)])

        # 添加其余参数（排除内部字段）
        skip_keys = {"root", "_positional"}
        for key, value in params.items():
            if key in skip_keys:
                continue
            if value is False or value is None:
                continue
            flag = "--" + key.replace("_", "-")
            if value is True:
                cmd.append(flag)
            else:
                cmd.extend([flag, str(value)])

        # 添加位置参数
        positional = params.get("_positional", [])
        cmd.extend(positional)

        # 调用原脚本
        try:
            result = subprocess.run(cmd)
            return result.returncode
        except KeyboardInterrupt:
            return 130
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"❌ 执行失败: {e}", file=sys.stderr)
            return 1
