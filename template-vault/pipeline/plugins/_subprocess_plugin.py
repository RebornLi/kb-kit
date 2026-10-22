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
    # P3-24：子进程超时（秒）。默认 2h —— 足够跑完最慢的全量索引重建，
    # 又不让死锁的子进程永久占用终端。子类可覆盖；**None 才表示不限制**
    # （0 在 subprocess.run 里是"立即超时"，别拿它当"不限"用）。
    TIMEOUT_SECS: Optional[int] = 7200

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

        # P3-25（审计建议"加 capture 开关"，结论：**不实现**）
        #   理由一：无真实消费者。全仓的 capture_output 用法都在**模块内部**
        #   调 git（kb_query/memory_sync/memory_ingest_json），没有一处是插件层
        #   需要回读子进程输出。
        #   理由二：会破坏长任务体验。`clean refine` / `curate run` 靠实时进度
        #   让用户知道"还在跑"；一旦捕获，用户眼里就是静默挂起，反而更糟。
        #   若将来真需要（例如聚合插件要汇总多个子命令输出），正确做法是加一个
        #   显式的 `capture=True` 参数并按调用点开启，而不是改默认行为。

        # 进程内快路径（只读命令）：省一次解释器启动 + import
        if action in self.IN_PROCESS_ACTIONS:
            rc = self._execute_in_process(cmd, script_path)
            if rc is not None:
                return rc
            # 回退 subprocess（import 失败 / main 抛异常，且无副作用）

        # 调用原脚本
        try:
            # P3-24：默认超时。此前没有 timeout=，长任务（clean refine /
            # curate run / eval run / rag index --full）会把插件层无限挂起，
            # 而 kb_verify.py 自己另实现了一套 TimeoutExpired→124，风格分裂。
            # 这里给一个宽默认值（2h）：足够跑完最慢的全量重建，
            # 又不至于让一个死锁的子进程永久占用终端。
            # 单插件可在自己的类里覆盖 TIMEOUT_SECS；**只有 None 表示不限制** ——
            # 不用 0/False 表示"不限制"，因为 timeout=0 在 subprocess 里
            # 语义是"立即超时"，两者混用会让 `TIMEOUT_SECS = 0` 静默失效。
            timeout = self.TIMEOUT_SECS
            result = (subprocess.run(cmd, timeout=timeout)
                      if timeout is not None else subprocess.run(cmd))
            return result.returncode
        except KeyboardInterrupt:
            return 130
        except subprocess.TimeoutExpired:
            # 与 kb_verify.py 的约定一致：124 = 超时，与业务失败(1)区分开
            print(f"❌ 执行超时（{timeout}s）：{self.SCRIPT_NAME}", file=sys.stderr)
            return 124
        except OSError as e:
            # P3-23：曾写 `except (OSError, subprocess.CalledProcessError)`，
            # 但上面没传 check=True，CalledProcessError 永不抛出 —— 是死代码。
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
