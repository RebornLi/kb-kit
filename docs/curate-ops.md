---
tags: [sop, curate, 知识治理]
status: active
domain: 管理
category: sop
created: 2026-09-29
updated: 2026-09-29
importance: 0.8
kb_target: docs
kb_action: new
kb_summary: 知识结晶层操作手册：三层结构、kb curate/kb raw 命令、三重质量关、空闲调度闸门、Agent 接口与验收指标。
---

# 知识结晶层 · 操作手册（kb curate / kb raw）

> 适用：`/home/mushan/kb-kit-pure`（真·KB 库）。源码同构于 `kb-kit-pure/template-vault/`。
> 目标状态：**知识层只放「无废话、逻辑清楚、可直接调用」的内容；需要时一键回到一手原记忆。**

---

## 一、三层结构（一次看懂）

```
L-1 证据层   raw/（网页抓取 2366）+ memory/（Agent 日志 30）     ← 原文保真，永不改写
              └─ 原记忆可能已被 Agent 侧压缩轮换 → 用注册源根兜底
L0  索引/判定 .kb/curate_state.json + _curate/ 提案 + 源索引      ← 每篇「是什么/判定/去哪了」
L1  正典层   kb_layer: canon 的笔记（结论→依据→操作→坑与边界）    ← 检索优先命中这里
L2  地图     _MOC.md / _INDEX.md                                ← 入口
```

**检索排序**：`canon ×1.25` > 普通 `×1.00` > `raw ×0.72`；分块索引页/目录页**不入索引**。
同一根文档的碎片（含 `-p2-p2` 级联）按 `chunk_of` 链归并，每个根家族最多出 2 条命中。

---

## 二、日常命令

```bash
cd /home/mushan/kb-kit-pure

# 1) 看下一批该结晶什么（只读，不调模型）
./kb curate plan --limit 12

# 2) 跑一轮结晶（默认只出提案，不写库；先看质量）
./kb curate run --limit 3
#    审提案： 70-知识治理 Governance/_curate/b<时间戳>.md

# 3) 认可后写回（低风险批次直接落盘）
./kb curate run --apply --limit 8 --max-seconds 1800

# 4) 离线重校验（不调模型）
./kb curate verify

# 5) 进度与质量
./kb curate report
./kb curate review          # 低置信待人工裁决

# 6) 回到一手原记忆
./kb raw list | head
./kb raw find "autotuner"
./kb raw show "openclaw/MEMORY.md::vLLM/SGLang 冷启动 & cron 坑（08-14）" --lines 40
./kb raw show "raw/安全/cwe-cwe-138-...-c1.md" --json      # 纯结构化（Agent 消费）
```

### 写回时到底发生了什么

1. 原文按 ID **保号**写入 `raw/_curated/<原路径>`（保真、可 diff、可回滚）；
2. 原位置**原位替换**为正典（结论/依据/操作/坑与边界），frontmatter 增加：
   `kb_layer: canon`、`source_ref`、`source_chars`、`canon_ratio`、`grounded_facts`、
   `archived_original`、`curated_by/at`、`curate_confidence`；
3. 写 `.kb/state/lineage.jsonl` 血缘；每批写前 `git checkpoint`，单批可整体回滚。

---

## 三、三重质量关（为什么 agent 写的东西可信）

| 关 | 机制 | 不通过怎么办 |
|---|---|---|
| **接地关** | Agent 必须把命令/路径/版本/时长/端口列进 `facts`，代码逐条回**原文全文**核对 | 找不到的 facts **一律丢弃**；≥3 条 facts 全不接地 → 判疑似编造，转人工 |
| **信息守恒关** | 正典/原文长度比必须落在 `[4%, 65%]`；<12% 只警告（长原文天然压缩比高） | 硬失败（<4% 或正典 <300 字）不写回 |
| **引用关** | `sources` 必须非空且指向可达原记忆 | 空 → 不写回 |
| **置信闸门** | 模型自评 `confidence < 0.6` | 不自动写回，进 `.kb/curate_review.jsonl` 人工裁决队列 |
| **截断自愈** | 输出被 `max_tokens` 截断时：先尝试**括号补齐修复**，再自动**紧凑重试**一次 | 仍失败 → 记 error，下次可重跑 |

---

## 四、空闲调度（把「空闲」变成预算）

`scripts/growth_cron.sh` 第 ⑧ 步已接好，**默认关闭**，三重闸门：

```bash
# crontab 里给环境变量即可启用（示例）
40 4 * * *  CURATE_ENABLE=1 CURATE_LIMIT=8 CURATE_SECONDS=1800 \
            CURATE_MAX_LOAD=4.0 ORNITH_API_KEY=sk-... \
            /home/mushan/kb-kit-pure/scripts/growth_cron.sh /home/mushan/kb-kit-pure
```

| 闸门 | 变量 | 默认 | 含义 |
|---|---|---|---|
| 开关 | `CURATE_ENABLE` | `0`（关） | 为 `1` 才结晶 |
| 密钥 | `ORNITH_API_KEY` | — | 缺失则跳过（`~/.bashrc` 已导出；cron 需显式带） |
| 空闲 | `CURATE_MAX_LOAD` | `4.0` | 1 分钟负载 ≥ 此值则让路 |
| 单轮上限 | `CURATE_LIMIT` / `CURATE_SECONDS` | `8` / `1800` | 到时停止，剩余留给下一轮 |

- **本地模型**：`ORNITH_BASE_URL`（默认 `http://127.0.0.1:8000/v1`）、`ORNITH_CHAT_MODEL`
  （默认 `ornith1.5-35b`）。端点不可达时 `curate` **自动降级为只读 plan**，不阻塞任何流水线。
- **吞吐参考**：单篇 60–115 s（12k 字原文、4k token 输出上限）。1100 篇知识层 ≈
  20–35 h 挂机时间；切成每轮 8 篇 × 30 min 的碎片，几天内自然完成，全程不抢资源。
- 结晶跑完 `growth_cron.sh` 会自动 `rag index`，新正典立即可检索。

---

## 五、给 Agent 用的接口（自动生效，无需额外配置）

| 工具 | 作用 | 关键参数 |
|---|---|---|
| `kb_query` | 检索知识库（正典优先） | `query`、`topN`、`full=true`（取整篇正典） |
| `kb_raw` | 按 `source_ref` 取**一手原文** | `id`、`maxChars` |

- 自动注入（每轮用户回合）：只注入 Top-N 命中片段，字节封顶 `KB_MAX_BYTES=700`。
- 调参在 systemd 用户服务环境：`systemctl --user set-environment KB_CONTENT_MAX=8000`
  → `systemctl --user restart dsh-web`（**重启后**新工具才注册：本次新增了 `kb_raw`）。

---

## 六、验收指标（`kb curate report`）

```
笔记总数 / 正典数 / 证据层数 / 索引页数
正典覆盖率（知识层）      ← 目标：逐步爬到 60%+
压缩比（正典/原文）        ← 目标区间 12%–65%
结晶运行次数 / 通过校验率   ← 目标：≥80%
累计丢弃未接地事实          ← 这是「防幻觉」的硬数字
待人工裁决条数
```

**回归基准**：固定 30 条黄金问题跑 `kb query`，看「命中正典占比」与「命中平均内容长度」。
P0 前的实测痛点是：`kb query "vLLM 部署 坑"` Top-10 **9 条是只指向别的文件的一行**；
现在同查询 Top-10 覆盖 10 个不同主题家族，且 `raw/` 空壳不再挤占。

---

## 七、出问题怎么办

| 症状 | 处置 |
|---|---|
| `curate run` 说模型不可用 | 检查 `ORNITH_API_KEY` 是否在**当前进程环境**（cron/systemd 不读 `~/.bashrc`） |
| 提案大量「过度压缩」 | 属正常（长原文）；看 `grounded_facts` 是否覆盖关键标识符即可 |
| 想撤销某批结晶 | `git log` 找该批 commit → `git revert`；或从 `raw/_curated/<原路径>` 复制回原位 |
| 想重跑某篇 | 从 `.kb/curate_state.json` 的 `done` 里删掉该 rel（键即相对路径） |
| 想调整准入 | `CONF_MIN`（置信闸门）、`HUGE_BODY`（超大跳过）、`SKIP_DIRS`（豁免目录） |
| 索引被改坏了 | `rm "vector index/df_idf.json" && ./kb rag index`（纯可重建产物） |
