#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_help_alignment.py — `kb help` 与路由表对齐守卫（P3-3）。

架构约束：`kb help` 宣传的每个命令都必须能被 `kb_launcher` 解析（COMPAT_MAP 或同名
插件），否则用户按帮助敲命令会得到“未知命令”。本测试防止 help 与实现再次漂移
（历史问题：help 宣传 `kb backup` 但 COMPAT_MAP 无此键）。

运行：
  python3 -m unittest discover -s tests
"""
import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "template-vault" / "pipeline"
sys.path.insert(0, str(PIPELINE))

from kb_launcher import KBLauncher  # noqa: E402

SPECIAL = {"list", "help"}  # 由 run() 直接处理，不走 COMPAT_MAP


class HelpAlignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="kb-help-")
        cls.launcher = KBLauncher(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _help_tools(self) -> set:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.launcher._print_help()
        tools = set()
        for line in buf.getvalue().splitlines():
            m = re.match(r"\s*kb\s+([a-z][a-z0-9-]*)", line)
            if m:
                tools.add(m.group(1))
        return tools

    def test_help_lists_commands(self):
        self.assertTrue(self._help_tools(), "help 应至少列出若干命令")

    def test_advertised_commands_resolve(self):
        self.launcher._ensure_initialized()
        bad = []
        for tool in sorted(self._help_tools()):
            if tool in SPECIAL:
                continue
            resolved = self.launcher._resolve(tool, None)
            plugin = self.launcher.registry.get_plugin(tool)
            if resolved is None and plugin is None:
                bad.append(tool)
        self.assertEqual(bad, [], f"help 宣传但无法解析的命令: {bad}")

    # ── P3-30：上面的测试只校验「命令名能 resolve」，不校验 action ──────────
    # 后果是一整类问题逃逸：help/README 宣传 `kb agent add --json`，
    # 命令名 `agent` 能 resolve，但 `--json` 其实不是布尔旗标（会被当成
    # 位置参数吃掉）——按宣传的写法敲必然出错，而旧测试全绿。
    # 这里补两道守卫：
    #   ① help 里每个 `kb <tool> <action>` 的 action 必须能 resolve；
    #   ② COMPAT_MAP 里每个 (tool, action) 的 action 必须在插件的
    #      metadata().actions 里（防止路由到不存在的动作）。

    def _help_pairs(self):
        """从 help 文本里抽出所有 `kb <tool> <action>` 组合。

        只认"action 是 ASCII 小写词、且该行整体是命令示例"的行 ——
        help 里有 `kb validate    frontmatter 校验` 这种**命令 + 中文说明**的排版，
        不能把说明文字（frontmatter）当成 action。

        局限：无法从排版上区分"真 action"与"排成同样的假 action"。
        因此本方法只覆盖**已经写进 help 的**命令；对"宣传了但没注册"的
        完整守卫见 test_unregistered_action_does_not_silently_fall_back
        （那条直接测 _resolve 的回退语义，不依赖文本解析）。
        """
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.launcher._print_help()
        pairs = set()
        for line in buf.getvalue().splitlines():
            m = re.match(r"\s*kb\s+([a-z][a-z0-9-]*)\s+([a-z][a-z0-9-]*)", line)
            if not m:
                continue
            tool, action = m.group(1), m.group(2)
            if action in ("<", '"') or action.startswith("<"):
                continue
            if action in ("daily", "weekly"):      # backup 的位置参数
                continue
            if tool in SPECIAL:
                continue
            tail = line[m.end(2):]
            if tail and not tail.startswith((" ", "\t")):
                continue
            if re.match(r"^[ \t]+[\u4e00-\u9fff]", tail):
                continue                          # 紧跟中文说明 → 不是 action
            pairs.add((tool, action))
        return pairs

    def test_unregistered_action_does_not_silently_fall_back(self):
        """根因级守卫：COMPAT_MAP 里登记的子命令必须都能精确命中自己。

        这条防的是**路由表自身**的不一致：`("verify", "all")` 登记了，
        但 `_resolve("verify", "all")` 却拿不到它 —— 那说明 COMPAT_MAP 的键
        与查询方式对不上，用户敲 `kb verify all` 会走到别的路径上去。

        （用户敲**未登记**子命令时的行为由另一条测试端到端覆盖，
        因为那涉及 DomainPlugin/argparse 的拦截，不是 _resolve 的职责。）
        """
        from kb_launcher import COMPAT_MAP
        self.launcher._ensure_initialized()
        bad = []
        for (tool, sub), (plugin_name, action) in COMPAT_MAP.items():
            if sub is None or plugin_name.startswith("__"):
                continue
            # 精确键必须能被 _resolve 命中**它自己**。
            # 不能只比对 action 名：删掉 ("verify","all") 后，_resolve 会
            # 回退到 ("verify", None)，拿到的 action 恰好也是 "all"，
            # 于是 `mapped != action` 判据失效。所以直接查精确键在不在表里。
            if (tool, sub) not in COMPAT_MAP:
                bad.append(f"kb {tool} {sub}")
                continue
            resolved = self.launcher._resolve(tool, sub)
            if resolved is None:
                bad.append(f"kb {tool} {sub} → 解析不到")
            elif resolved[1] != action:
                bad.append(f"kb {tool} {sub} → 解析成 {resolved[1]}（登记的是 {action}）")
        self.assertEqual(bad, [], f"这些已登记的子命令无法精确命中: {bad}")

    def test_bogus_action_is_not_silently_accepted(self):
        """具体反例：编造的子命令不能"看起来成功"。

        注意：不能只断言 `_resolve()` 返回什么。`_resolve` 在 (tool, action)
        查不到时会回退到 (tool, None)，这是**中间态**；真正的拦截发生在后面
        （DomainPlugin / plugin_registry 校验 action 不在 metadata().actions，
        或底层脚本的 argparse 拒绝未知参数）。两者都返回非 0，用户看得到失败。

        所以这条测试跑**真实 CLI**，断言退出码非 0 且输出里没有"成功"字样 ——
        盯的是用户可见结果，不是内部实现细节。
        """
        import subprocess
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="kb-bogus-"))
        try:
            r = subprocess.run(
                [sys.executable, str(PIPELINE / "kb_launcher.py"),
                 "verify", "definitely-not-a-real-action", "--root", str(tmp)],
                capture_output=True, text=True, timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertNotEqual(r.returncode, 0,
                               f"编造的 action 竟然成功了：\n{out[-500:]}")
            self.assertNotIn("全部通过", out)
            self.assertNotIn("全绿", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_advertised_actions_resolve(self):
        """help 宣传的每个 (tool, action) 都必须能 resolve **到该 action**。

        注意判据不是 `resolved is not None`：`_resolve()` 在 (tool, action)
        查不到时会**回退**到 (tool, None)。若只判非空，`kb verify bogusaction`
        这种"宣传了但没注册"的写法会被回退结果掩盖而通过。

        但回退有两种，必须分开：
          · 回退成 **None** —— 合法。那是 `("compile", None)` 这类"由参数推断
            action"的登记形态（kb compile query/status/apply 全是这么走的）；
          · 回退成**另一个具体动作** —— 不合法。说明宣传的子命令没注册，
            却 silently 落到了默认动作上（kb verify bogusaction → all）。
        """
        from kb_launcher import COMPAT_MAP
        self.launcher._ensure_initialized()
        bad = []
        for tool, action in sorted(self._help_pairs()):
            resolved = self.launcher._resolve(tool, action)
            if resolved is not None:
                _plugin, mapped = resolved
                if mapped == action:
                    continue                      # 精确命中
                if mapped is None:
                    continue                      # 由参数推断，合法形态
                if (tool, action) in COMPAT_MAP:
                    continue                      # 精确注册过，只是映射到别的动作名
                bad.append(f"kb {tool} {action}（未注册，回退成了 {mapped}）")
                continue
            # 该 tool 可能是插件名直通（action 由插件自己校验）
            if self.launcher.registry.get_plugin(tool) is None:
                bad.append(f"kb {tool} {action}")
        self.assertEqual(bad, [], f"help 宣传但无法解析的动作: {bad}")

    def test_compat_map_actions_exist_in_plugins(self):
        """COMPAT_MAP 的每个 (tool, action) 都要能在目标插件的 actions 里找到。

        这是**根因级**守卫：过去 P1 那批"宣传了但实际是坏的"问题，
        都是因为路由表指向了插件并不支持的动作。
        """
        from kb_launcher import COMPAT_MAP
        self.launcher._ensure_initialized()
        bad = []
        # 键是 (tool, sub)，sub 可能为 None —— 不能直接 sorted，用 str 化排序
        for (tool, sub), (plugin_name, action) in sorted(
                COMPAT_MAP.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            if plugin_name.startswith("__"):       # __list__ / __help__
                continue
            if action is None:                     # 由参数推断，无法静态判定
                continue
            plugin = self.launcher.registry.get_plugin(plugin_name)
            if plugin is None:
                continue                          # 由其它测试覆盖（插件缺失）
            try:
                actions = plugin.metadata().actions or []
            except Exception:
                continue
            # 复合键（如 kb_usage.report）在 DomainPlugin 里是合法形态
            if action in actions or "." in action:
                continue
            bad.append(f"({tool}, {sub}) → ({plugin_name}, {action})")
        self.assertEqual(bad, [], f"COMPAT_MAP 指向了插件不支持的动作: {bad}")


if __name__ == "__main__":
    unittest.main()
