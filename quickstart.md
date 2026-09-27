---
domain: 管理
status: active
importance: 0.5
created: 2026-09-19
updated: 2026-09-27
tags: ["管理"]
---
# 🚀 quickstart · 5 分钟上手

拿到 `kb-kit` 文件夹后，3 步拥有一个会自成长的知识库。

## 步骤

1. **装两个东西**
   - Python 3.8+（Windows 装时勾选 “Add to PATH”）
   - Obsidian（免费，用来打开/阅读笔记；可选）

2. **一键安装**（会问你建在哪）
   - Windows：双击 `install.bat`，或 PowerShell `.\install.ps1`
   - macOS/Linux：`bash install.sh`
   - 也可直接：`python3 create_vault.py --vault "我的知识库"`

3. **打开 + 试检索**
   - 用 Obsidian 打开装好的文件夹
   - 终端里：`kb query "怎么备份知识库"`

## 之后每天就这样

| 你想做 | 敲 |
|--------|-----|
| 写了一条新知识 | 丢进 `00-收件箱 Inbox` |
| 让它进索引/能被问 | `kb rag index` |
| 提问 | `kb query "……"` |
| 看收件箱要不要路由 | `kb ingest` |
| 看有没有孤岛/坏链/缺字段 | `kb healthcheck` |
| 今天该回忆哪些 | `kb recall`（`kb recall mark --grade good` 记录复习） |
| 生成治理仪表盘 | `kb dashboard` |
| 一键整理（结构化+去重+分块+重建索引） | `kb clean refine` |
| 生成知识地图 | `kb link moc` |
| 备份 | `kb backup daily` |
| 看所有插件状态 | `kb list` |

> `kb`（Windows 用 `kb.cmd`）默认以自身所在目录为知识库，安装器会把它注册为全局命令。
> 需要操作其它库：`--root <路径>`，或设 `KB_ROOT`（优先级 `--root` > `KB_ROOT` > 脚本目录）。
> 完整命令清单见 `README.md` 的「命令参考」。

## 插件化架构

所有功能模块和 Agent 适配器都是插件，统一放在 `pipeline/plugins/`。新增插件只需放一个 `.py`
文件、定义 `PluginBase` 子类，无需改核心代码：

```python
# pipeline/plugins/my_plugin.py
from plugin_base import PluginBase, PluginContext, PluginMetadata

class MyPlugin(PluginBase):
    def metadata(self):
        return PluginMetadata(name="my", version="1.0",
                              plugin_type="kb_module", actions=["hello"])
    def initialize(self, ctx): self._ctx = ctx
    def execute(self, action, params):
        print("Hello!"); return 0
```

```bash
kb my hello    # 立即可用（插件名直接当命令）
kb list        # 查看所有插件与状态
```

**禁用插件**：编辑 `reference/plugin-config.json`，设 `"enabled": false`。

## 想让知识库「自己跑」（可选）

把定时任务指向模板自带的节拍脚本：

```bash
40 4 * * *  /path/to/vault/scripts/growth_cron.sh /path/to/vault
```

它会每天自动：建索引 → 整理收件箱 → 算命中信号 → 出回忆清单 → 补链 → 刷仪表盘 →
同步/摄取 Agent 记忆。

> Windows（无 bash）不适用 `growth_cron.sh`，可用任务计划自行编排 `kb` 子命令。

## 一句话理念

你只管往收件箱丢想法；成长引擎帮你**清洗、路由、补链、回忆、巡检、备份**。
