---
domain: 管理
status: active
importance: 0.5
created: 2026-09-19
updated: 2026-09-19
tags: ["管理"]
---
# 🚀 quickstart · 5 分钟上手

拿到 `kb-kit` 文件夹后，3 步拥有一个会自成长的知识库。

## 步骤

1. **装两个东西**
   - Python 3.8+（Windows 装时勾选 "Add to PATH"）
   - Obsidian（免费，用来打开/阅读笔记）

2. **一键安装**（会问你建在哪，留空=当前目录建 `KB`）
   - Windows：双击 `install.bat`  · 或 PowerShell：`.\install.ps1`
   - macOS/Linux：`bash install.sh`（或 `chmod +x install.sh && ./install.sh`）

3. **打开 + 试检索**
   - 用 Obsidian 打开装好的文件夹
   - 终端里：`kb query "怎么备份知识库"`

## 之后每天就这样

| 你想做 | 敲 |
|------|------|
| 写了一条新知识 | 丢进 `00-收件箱` |
| 让它进索引/能被问 | `kb rag index` |
| 提问 | `kb query "……"` |
| 看收件箱要不要路由 | `kb ingest` |
| 看有没有孤岛/坏链/缺字段 | `kb healthcheck` |
| 今天该回忆哪些 | `kb recall` |
| 看所有插件状态 | `kb list` |

> `kb`（Windows 用 `kb.cmd`）会自动找到你的 vault，不用记路径。
> 完整命令清单见 `README.md`。

## 插件化架构（v2.0.0+）

KB 的所有功能模块和 Agent 适配器都是插件，统一放在 `pipeline/plugins/` 目录。

**新增插件**：只需在 `pipeline/plugins/` 下创建 `.py` 文件，定义 `PluginBase` 子类，无需改核心代码：

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
kb my hello    # 立即可用
```

**禁用插件**：编辑 `reference/plugin-config.json`，设 `"enabled": false`。

## 想让知识库"自己跑"（可选）

把下面的命令加进定时任务（Linux cron / Windows 任务计划）：

```bash
40 4 * * *  /path/to/vault/scripts/growth_cron.sh /path/to/vault
```
它会每天自动：算命中信号、补链、出回忆 deck、刷新治理仪表盘。

## 一句话理念

你只管往收件箱丢想法；成长引擎帮你**清洗、路由、补链、回忆、巡检、备份**。
