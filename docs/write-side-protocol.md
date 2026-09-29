---
tags: [sop, 知识治理, 写侧协议]
status: active
domain: 管理
category: sop
created: 2026-09-30
updated: 2026-09-30
importance: 0.8
kb_target: docs
kb_action: new
kb_summary: 写侧协议：任何会改库的命令都必须支持只读预览（总数+清单+应用方式）、写前 checkpoint、精确 git add、破坏性动作写 lineage。
---

# 写侧协议（Write-Side Protocol）

> 目的：**任何会改库的命令，都必须能先"什么都不改地跑一遍"**。
> 这是 P0 的统一约定；后续每个新写的命令都必须遵守（否则不允许合并）。

---

## 一、三条硬约定

### 约定 1 · 预览优先（read-only 模式）

| 命令 | 预览方式 | 写盘方式 |
|---|---|---|
| `kb clean repair / repair-headings / chunk / refine / taxonomy` | 默认 dry-run | `--apply` |
| `kb memory-sync promote` | **默认 dry-run** | `--apply` |
| `kb ingest move / trash` | `--dry-run` | 不带则写（原行为，见「例外」） |
| `kb link apply` | `--dry-run` | 不带则写 |
| `kb sync apply` | `kb sync dry-run` | `apply` |
| `kb curate run` | **默认只出提案** | `--apply` |
| `kb ingest agent` | `--dry-run` | 不带则写 |
| `kb clean report` / `kb curate review` / `kb raw *` / `kb healthcheck` / `kb validate` | 天然只读 | — |

> **例外说明**：`ingest move/trash` 与 `link apply` 的命令名本身就是"写动作"，
> 历史行为是「指名即执行」。为不破坏既有习惯，它们保留原名语义，但**必须支持 `--dry-run`**，
> 并且要能打印"将改哪些文件"。`memory-sync promote` 因为建的是**新笔记**（不可逆性更高），
> 所以改成默认 dry-run。

### 约定 2 · 预览必须可数、可指认

预览输出必须给出：
1. **总数**（拟改 N 处）
2. **逐项清单**（前若干条：`动作 + 路径 + 关键参数`）
3. **应用方式**（下一步该敲什么）

不允许只打印"完成"或只给一个数字。

### 约定 3 · 写入必须可回滚

- 写前 `git checkpoint`（`git add -u` + commit，不 sweep Obsidian 运行时态）
- 只 `git add -- <本次改动的精确路径>`，**绝不 `git add -A`**
- 破坏性动作（合并/归档/替代）必须写**墓碑 + lineage**（`.kb/state/lineage.jsonl`）
- 原文保号：结晶写回时原文先落 `raw/_curated/<原路径>`

---

## 二、命令作者检查清单

新增/修改任何会写库的命令，提交前逐条自检：

- [ ] 有只读路径（默认 dry-run，或独立 `dry-run`/`report`/`plan` 子命令）
- [ ] 预览输出含"总数 + 清单 + 应用方式"
- [ ] 写前 checkpoint；只精确 `git add`
- [ ] 有 `--apply`（或语义等价的写开关）
- [ ] 破坏性动作写 lineage / 墓碑
- [ ] 幂等：重复跑不会堆积副作用（如 link 的建议区块、curate 的内容指纹）
- [ ] 失败不静默：要么报错，要么明确打印"跳过 + 原因"

---

## 三、为什么要这条协议

P0 之前的状态：

```
clean        有 dry-run ✔
curate       默认提案、--apply 写 ✔
memory-sync  promote 直接写，无预览 ✘
ingest move  指名即写，无预览 ✘
link apply   指名即写，无预览 ✘
```

后果：**改库前无法"先看一眼"**；一旦误跑，只能靠 git 考古。

现在：所有会改笔记的命令，都能先跑一遍看清单，确认后再加写开关。

---

## 四、语义层纪律（与协议配套）

| 层 | 谁可以写 | 约束 |
|---|---|---|
| 证据层 `raw/`、`memory/` | 只有摄取流程 | **原文保真**：修正文缺陷时默认跳过（`repair-headings --include-evidence` 才动） |
| 正典层（`kb_layer: canon`） | 只有 `curate --apply` | 必须过三关校验（接地/守恒/引用）+ 置信闸门 |
| 判定（noise/raw-only） | 只有 `curate` | 写回 frontmatter，让索引降级生效 |
| 索引 `vector index/` | 只有 `rag index` | 纯可重建产物；任何正文改动之后都要重跑 |
