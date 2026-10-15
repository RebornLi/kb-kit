#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_subprocess_plugin.py — 子进程封装基类。

为 KB 功能模块插件提供统一的 subprocess 调用封装，
保持与原 CLI 100% 兼容（原模块的 argparse + main() 不变）。

子类只需定义：
  - metadata() 返回 PluginMetadata
  - SCRIPT_NAME 原模块文件名（如 "rag.py"）
  - ACTION_MAP  action → 原模块子命令参数列表

## 进程内快路径（IN_PROCESS_ACTIONS）

默认一切走 subprocess（隔离，安全）。但对**只读、高频、延迟敏感**的命令（如 `query`），
每次 spawn 一个 Python 解释器 + 重新 import 一遍模块是纯开销（实测 ~40ms，占 query 的 21%）。
子类可用 `IN_PROCESS_ACTIONS = {"query", ...}` 声明哪些 action 允许**在当前进程内**直接
`import 模块 + main()`。安全保证：

  · 只对**只读** action 开启（写 action 走 subprocess，隔离永远在）；
  · import 失败 / 没有 main() → 回退 subprocess（此时无任何副作用）；
  · main() 抛异常 → 丢弃已捕获的输出，回退 subprocess（读命令无副作用，回退安全）；
  · stdout/stderr 全程捕获，成功才回放 —— 杜绝"半截输出 + 回退再跑一遍"的双份输出。
"""
import contextlib
import importlib
import io
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_base import PluginBase, PluginContext, PluginMetadata


class SubprocessPlugin(PluginBase):
    """通过 subprocess 调用原脚本的插件基类。"""

    SCRIPT_NAME: str = ""               # 原模块文件名，如 "rag.py"
    ACTION_MAP: Dict[str, Optional[str]] = {}  # action → 原子命令（None 表示直接用 action）
    # 允许**进程内执行**的 action 白名单（只放只读命令）；默认空 = 全部走 subprocess
    IN_PROCESS_ACTIONS: set = set()

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
            if key.startswith("-"):
                cmd.append(key)          # 短选项（`-h`）原样透传，不改写成 `--h`
                continue
            flag = "--" + key.replace("_", "-")
            if value is True:
                cmd.append(flag)
            elif isinstance(value, (list, tuple)):
                for v in value:          # nargs="+" 类旗标：重复该旗标逐个传值
                    cmd.extend([flag, str(v)])
            else:
                cmd.extend([flag, str(value)])

        # 添加位置参数
        positional = params.get("_positional", [])
        cmd.extend(positional)

        # 进程内快路径（只读命令）：省一次解释器启动 + import
        if action in self.IN_PROCESS_ACTIONS:
            rc = self._execute_in_process(cmd, script_path)
            if rc is not None:
                return rc
            # 回退 subprocess（import 失败 / main 抛异常，且无副作用）

        # 调用原脚本
        try:
            result = subprocess.run(cmd)
            return result.returncode
        except KeyboardInterrupt:
            return 130
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"❌ 执行失败: {e}", file=sys.stderr)
            return 1

    @staticmethod
    def _execute_in_process(cmd: List[str], script_path: Path) -> Optional[int]:
        """在当前进程内 import 脚本并调 main()。返回 None 表示"无法/不应"，调用方回退。

        纯函数（不依赖实例状态），方便单测与复用。
        """
        mod_name = script_path.stem
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            return None                  # import 失败：无副作用，回退
        main = getattr(mod, "main", None)
        if main is None:
            return None

        old_argv, old_out, old_err = sys.argv, sys.stdout, sys.stderr
        buf_out, buf_err = io.StringIO(), io.StringIO()
        try:
            sys.argv = [str(script_path)] + cmd[2:]
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                rc = main()
            # 成功：原样回放输出（此时 sys.stdout 已恢复为真实 stdout）
            sys.stdout.write(buf_out.getvalue())
            sys.stderr.write(buf_err.getvalue())
            return int(rc or 0)
        except SystemExit as e:
            sys.stdout.write(buf_out.getvalue())
            sys.stderr.write(buf_err.getvalue())
            return int(e.code or 0)      # argparse --help / 脚本显式 sys.exit
        except Exception:
            return None                  # 丢弃输出，回退 subprocess（读命令无副作用）
        finally:
            sys.argv, sys.stdout, sys.stderr = old_argv, old_out, old_err
