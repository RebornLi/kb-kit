---
---
tags: [meta, design, RSI, architecture]
status: active
domain: 管理
created: 2026-09-18
updated: 2026-09-18
importance: 1.0
kb_target: RSI-自我改进设计.md
kb_action: new
kb_summary: 用"递归式自我改进(RSI)"理念重构 kb-kit 的成长引擎——能做到什么程度、边界在哪、如何防塌
version: 1.0
importance: 1
---
---

# 🔄 kb-kit × RSI：递归式自我改进 设计

> 命题（用户提出）：把 kb-kit 用 RSI（Recursive Self-Improvement，递归式自我改进）
> 理念改进，能做到什么程度？DSH 能力来自本地模型，但有外网=知识无上限，故推论
> "KB 自我成长不设限"。本文先给**诚实的边界结论**，再给**可落地架构**。

---

## 一、先给结论（TL;DR）

1. **RSI 能让 kb-kit 很强，但绝不能"无约束、无限自我成长"。** 这是论文铁律，不是保守：
   - 《Model Collapse》(Shumailov 2023) / 《Synthetic Collapse》：系统只吃**自己生成的内容** → 质量崩塌、信息近亲繁殖。
   - 《The Self-Improvement Paradox》(ACL Findings 2025)：**没有外部真值/脚手架，LLM 几乎无法真正自我提升**。
   - 反过来能活下来的 RSI 都有同一套零件：**外部真值(grounding) + 可测的适应度(fitness) + 有界且可回滚的改动 + 多样性注入**。
2. **kb-kit 不是"从零搭 RSI"，它已经 70% 具备零部件**（见第四节），RSI 化 = 把"只告警"变成"有质量门的可回滚行动"，并把"纯内部信号"改成"外部接地信号"。
3. **用户"知识无上限"的直觉只对一半**：真正的天花板不是"知识量"，是"质量/对齐"。外网/用户/真实测量 = 持续注入的外部接地，**恰恰是让 kb-kit 不塌、从而可持续变强的唯一机制**。一个**完全封闭**的 kb-kit 必然塌；一个**持续开放**的 kb-kit 才能接近"无上限"。

---

## 二、RSI 是什么（一句话 + 循环）

一个系统**不断改进自己的设计**，每代都能更强地改进下一代，形成自我增强循环：

```
   适应度度量 ──(反馈)──▶ 提出改进 ──(质量门)──▶ 执行改动 ──(可回滚)──▶ 记录决策
      ▲                                                                    │
      └────────────────────────── 再度量 ─────────────────────────────────┘
```

kb-kit 天然同构于这个循环（它自己就把它设计成"有机体"：新陈代谢/免疫/自我连接/间隔回忆）。

**关键区分：闭合 vs 开放。**
- **闭合 RSI**（只喂自己的输出）→ 塌（Model Collapse）。
- **开放 RSI**（持续吃真实世界输入 + 用真实反馈当适应度）→ 可持续增长。
- 设计目标：让 kb-kit 永远做**开放 RSI**。

---

## 三、能到什么程度：三级阶梯（含边界）

### Tier 1 · 自我整理（自我代谢/免疫）—— 现在就能做，低风险高回报
把"告警"变成"提议→验证→执行→记录"，改动都是**低危、可回滚**的：
- 去重合并高度相似笔记（kb_health 已能高相似配对）
- 孤岛笔记补链或直接归档（link_engine / kb_health）
- 过期/失效笔记标记 retire（kb_health `check_policy_stale`）
- 收件箱 triage（intake_triage）
- **质量门**：每条提议附带置信度（文本相似度/过期天数/领域一致性），只有高置信才由 DSH agent 执行，且写前 git checkpoint、写后回滚锚点。

### Tier 2 · 自我加权（校准"什么重要"）—— 中期，需要外部接地
现有 `feedback_loop.py` 用**命中次数**提升 importance（只升不降、可回滚）——已是半闭环，但**命中信号全来自 vault 内部自检索 → 会"富者更富"式停滞**。
RSI 升级：把适应度换成**外部接地价值**：
- 用户显式反馈 useful/not-useful（已有 `record_hit`，权重 1.0/0.0）
- 跨域新颖性、新鲜度、回忆"还浮得上来"的成功率（recall 检索浮层）
- 让 importance 反映**真实世界价值**而非"内部人气"。
- 这正是论文 Meta-Rewarding（模型改进自己的评审器）在 kb-kit 的平替版。

### Tier 3 · 自我调参（改进"改进本身"）—— 最接近真 RSI，也最危险
kb-kit **调它自己的 pipeline 参数**（回忆间隔、MIN_HITS、去重阈值、importance 增量速率），
依据是**这些参数改动后的真实结果**（re-hit 率、过期复发率、用户反馈）。
这是"改进那个改进者"（meta-improvement），最接近真正的 RSI，**也最易塌**，必须：
- A/B 对照或前后对照度量 + 人工在环 + 完全可回滚 + 默认保守（小步、慢步）。

> **边界（诚实版）**：T1 可放心落地；T2 需要外部反馈持续输入；T3 在未建立可靠度量和人工在环前**不要自动开**，否则正是 Model Collapse 的温床。kb-kit 的"无上限"只能靠 **T1 保持洁净 + T2 持续接地 + 永远保持开放摄入真实知识**，而非"自己迭代自己到爆炸"。

---

## 四、与现有 pipeline 的映射（差距 = RSI 化切入点）

| 现有模块 | RSI 角色 | 现状 | RSI 化缺口 |
|---------|---------|------|-----------|
| `kb_health.py` | 适应度/度量 | ✅ 六维度度量 | 仅**告警**，不产出**可执行提议** |
| `feedback_loop.py` | 价值→加权 | ✅ 命中→importance（只升不降+checkpoint） | **信号闭合**（仅内部命中），缺外部接地 |
| `link_engine.py` | 连接/多样性 | ✅ 孤岛补链（建议+apply） | 建议/应用解耦，可接质量门 |
| `recall_schedule.py` | 外部接地 | ✅ 间隔回忆+检索浮层核对 | **这是防塌核心**，应升级为"自适应" |
| `intake_triage.py` | 新鲜摄入 | ✅ 收件箱 triage | — |

**核心缺口一句话**：所有模块都是"观察/建议"，**缺一层把观察串成"提议→质量门→执行→记录"的 RSI 编排器**，且**适应度需引入外部真值**。

---

## 五、防塌架构（开放 RSI 的四道闸门）

1. **外部接地闸**：适应度必须掺入真实世界信号（用户反馈、真实查询、外部新鲜知识）。若"外部新鲜摄入"枯竭 → 主动**降权**自我改进的激进度（本设计的自动安全阀）。
2. **适应度闸**：任何自我改动都要有可测指标（健康分、re-hit 率、过期率），无度量不动手。
3. **有界+回滚闸**：每次改动写前 git checkpoint、写后留回滚锚点；importance 只升不降+封顶（沿用现有纪律）。
4. **多样性闸**：跨域连接、新鲜摄入、冷门知识保护（命中低的未必无用，见 feedback_loop 的 zero_n 处理）。

---

## 六、落地路径（本仓库已做的）

- ✅ 本文件（设计，Tier 边界 + 防塌闸门）
- ✅ `pipeline/kb_rsi.py`（**只读探针**：在真实 vault 上跑，产出"自我改进提议+置信度+外部接地健康阀"，只写一份决策日志、绝不自动改笔记）
- ✅ `pipeline/kb_engine.py`（T1/T2/T3 apply 编排 + 每写必 git checkpoint 可回滚）
- ✅ `pipeline/kb_fitness.py`（**P1-A 外部锚定 fitness**，见下）
- ✅ `pipeline/kb_contradiction.py`（**P1-C 矛盾检测 lint**，见下）
- ✅ `pipeline/feedback_loop.py`（**P1-B T2 多样性感知加权**，见下）
- ✅ `pipeline/kb_query.py`（**P2-1 查询→知识 正回路**，见下）
- ✅ `pipeline/evolution_log.py`（**P2-2 统一演进日志**，见下）
- ✅ `pipeline/kb_scale.py`（**P3-1 规模档位桥接**：notes 量 → flat/indexed 两档，双层检索口子，见下）
- ✅ `pipeline/kb_calibrate.py`（**P3-2 阈值仿真标定**：在可控大语料上扫 F1 校准硬编码阈值，见下）

### P1 防 Goodhart 闭环（已落地，2026-09-18，commit ff9620c）

原隐患：`health_score = 100 − orphan 惩罚 − stale 惩罚 − 接地惩罚` 是**系统能自抬的数字**
——T1 补链↓orphan、T2 retire↓stale 直接抬高 health → T3 优化"自己就能刷"的数字 → 趋近 Model Collapse
（见 §2 第三闸 3.4 / §4 第三闸）。P1 拆成三块，**无一能被 T1/T2 编辑笔记而刷爆**：

- **P1-A fitness 与 T1/T2 解耦 + T3 改调 fitness 趋势**：新增外部锚定 `kb_fitness.fitness()`，
  三分量均须系统外输入才动 → 来源多样性(Simpson 指数·0.35) + 外部反馈率(feedback_state 显式
  覆盖·0.35) + 矛盾零分·0.30。`tier_t3` 改为**以 fitness 趋势为主调参依据**（health 仅作辅助显示）；
  读 prev 带 `fitness` 的记录作基线。实锤：给孤儿补链把 internal health 0→10，但 fitness 纹丝不动 →
  T3 在 fitness 停滞时 **TUNED(收缩)** 而非因 health 升而膨胀；真实摄入新领域 raw 后 fitness 上升才转
  **T3_STABLE**。T3 仍双保险默认 OFF，未经批准不自开。
- **P1-B T2 加多样性感知**：对**低权重相关**笔记给保底权重（`NOVELTY_FLOOR=0.06`，须≥MIN_BUMP 才
  生效），并加**正交「novelty」项**（与已有命中高度正交才加分，`NOVELTY_SIM=0.55`）——
  防回声室收窄长尾（保留既有单笔记累计权重封顶）。实锤：冷门(艺术)笔记得 novelty=0.058，主导(开发)
  三篇 novelty=0（刹车）。
- **P1-C 矛盾检测 lint**：`kb_contradiction.detect()` 周期跑语义相似但陈述相反的笔记对，交人工复核
  （"今天完全缺失的知识质量信号"）。**双闸**：余弦相似度(`CONTRA_SIM=0.40`) × 否定密度差（一篇
  ≥0.020、一篇 ≤0.015，否定词库含 `没/没有`）。**只读交人工**，绝不自动改/删笔记。用本地**单字/英文词
  字符向量**避免"中文整段当单 token"稀释余弦。

### P2 闭合"查询→知识"正回路 + 人可读演进日志（已落地 2026-09-18）

Karpathy 正增长回路：好答案应回存为知识，形成"查询→综合→持久化→更好查询"的正反馈。**但必须人工
在环 + 有界**，避免合成数据膨胀（Model Collapse 的另一入口）。

- **P2-1 好答案回存**：`kb_query.py query` 处理一次查询 → 检索 top-k 命中 → 规则综合出答案 → **耐久
  性评估** → 达标则**提议**存为一条编译笔记（`.kb_query_proposals.jsonl`），**绝不自动写库**（人工在
  环，`apply --id/--all` 确认后入库）。五道**有界**刹车防止合成膨胀：①长度门≥60字 ②词汇多样性
  TTR≥0.45 ③新异度门（与已有笔记最大余弦 <0.90，近乎整篇复制才拒）④接地阀（复用 kb_rsi 外接阀，自
  闭库不提议）⑤频率门（≤10条/天）。写入的笔记带 `source=query` + `sources` 关联，可审计可回滚。实
  锤：综合（新异 0.01）通过，整篇复制单篇（新异 1.0）被拒。坑：字符向量必须 **L2 归一化**——词频归一
  会把"相同文本"余弦稀释到 ~0.01 使门禁失效（改用 L2 范数后相同文本≈1.0、无关≈0）。
- **P2-2 人可读演进日志**：`evolution_log.py` 把 ingest/query/lint/engine 四类事件追加到**统一前缀**
  的演进日志——机器可读 `.kb_evolution.jsonl`（gitignore）+ **人可读 `EVOLVED.md`**。事件前缀
  `# [INGEST] [QUERY] [LINT] [T1] [T2] [T3] [HEALTH]`，按日期倒序分组。feedback_loop.ingest /
  kb_query / kb_contradiction / kb_engine 均已接入。让运营者一眼看到知识体"今天做了什么"。

### P3 规模预留 + 阈值标定 + 集中度护栏操作化（2026-09-18）

上一节把"外部接地、适应度、多样性"都变成了可测信号；P3 做两件收尾：把"多样性/规模"从口号变成可观测护栏，
并"把上脑袋的硬编码阈值换成了在大语料上标定过的数字"——否则阈值是从 ~29 篇小库假设的，没有依据。

- **P3-1 规模预留（双层检索桥接）**：笔记涨到几百篇时，"markdown 全塞给 kb_rsi（给人读）"会臃肿，应上双层
  检索——markdown 层给人读，`vector index/`（rag.py 建，gitignore）给机器做查询/矛盾 lint。当前不动，但
  `kb_scale.py` 预留了档位开关：按 notes 数分档 —— `<100=flat`（单层，走现有全量路径）、`100–500=flat-warn`
  （超 100 篇先告警）、`≥2000=indexed`（切到索引层）。它只桥接不建检索（`fabric()` 统计 markdown/docs 两层的
  覆盖与索引版本，`scale()` 在 kb_engine health 与 kb_health 记录里挂上 `bracket/indexed_available/switch_ready/
  coverage`）。实锤：真实 vault（51 篇）→ `flat`，`indexed_available=false`，开关已开但未启用，规模到 2000 即可
  切换。每篇笔记的 `path`/`meta` 字段已为"索引层机器查询"留好桥接面。
- **P3-2 阈值仿真标定**：`kb_calibrate.py` 生成可控仿真语料（已知 ground truth 重复对 + 过期对），扫候选阈值
  算 precision/recall/**F1**，给推荐值并与当前默认对比。在 /tmp 独立 vault 上跑 `synth --n 300 --pairs 40`
  （300 篇 / 40 对重复 / 121 篇过期）：`retire >= 190days`（F1≈1.0）→ 当前默认 180 天已近最优，保守合理；
  `dup`/`merge` 在敏感带 0.60–0.99 内扫得 F1 曲线（推荐区间而非唯一值——仿真负样本由短词表随机组合，cos 被人为
  压低，F1 绝对值偏低；实际取"保守默认 + 人工复核"更稳）。默认不改任何阈值（只读标定，人工决策后再写入
  `.kb_rsi_config.json` 或常量）；标定用的 `gt_dup.json/gt_over.json/*.csv` 已 gitignore（瞬态可重生成）。坑
  （复用 P1 的）：双轨字符向量仍需 L2 归一化，否则词频归一会稀释余弦。
- **P3-3 集中度护栏操作化**：多样性的"防塌"不能只是口号。`kb_rsi.metrics()` 新增 `concentration`，量化
  importance 分布的集中度——Simpson 多样性指数 `1−Σp²`（避免 Shannon 在双域饱和为 1.0 的旧坑）+ 归一化
  Shannon `H/ln(k)` + 单篇最高占比 `top_share`。塌界告警：`top_share>0.5` 或 `simpson<0.5` → 写入
  `.kb_evolution.jsonl` 的 `ALERT`（detail=`importance 集中度护栏`），逼运营者注入新鲜领域。实锤：真实 vault
  （29 篇）→ Simpson 0.963 / 归一化 Shannon 0.989 / 单篇最高 0.039（多样性良好，无告警）；若某一篇 importance
  吃掉 >50% 分布则立刻告警。

### P4 加权依据可审计 + 健康分权重配置化（2026-09-18，快速小修）

防塌三闸的"有界+回滚"承诺需要两份**打分/加权依据**本身可审计可回滚——否则 T2 的 importance 提升来源和
T3 的趋势对照都建立在黑盒数字上。

- **P4-1 加权依据入库**：`feedback_state.json`（T2 importance 加源的命中/显式反馈记录）已纳入 git 版本控制
  （`.kb/state/feedback_state.json`，P1 `ff9620c`）。它不再是 gitignore 的瞬态文件——每次 T2 提升 importance 的
  来源、增量、封顶都可 `git diff`/`reset --hard` 回溯，补上"每次改动可回滚"承诺的最后一块洞。fallback 路径
  `pipeline/.feedback_state.json` 仅在缺失 state_manager 时写入，仍不入库。
- **P4-2 健康分权重配置化**：原 `kb_engine.py:health_score` 的三项惩罚（孤儿×3 / 过期×3 / 未接地×15、单类封顶 45）
  是"上脑袋"的硬编码取值，且直接影响 T3 做趋势对照时的判断。抽为带理由常量 `HEALTH_SCORE_WEIGHTS`（每项对应一种
  知识库塌缩形态：孤儿↑=孤岛断链、过期↑=坏血病、未接地=自闭第一塌），`health_score(m, cfg)` 允许
  `.kb_rsi_config.json` 的 `health_weights` **部分覆盖**（缺省走常量；`CONFIG_DEFAULT["health_weights"]={}` 表示用
  默认）。实锤：默认 58 分；把 orphan 权重提到 6 且封顶降到 30 → 46 分；孤儿占 40% 时 cap 在 45/30 下区分出
  55/70 分。让调参者可据更大语料重新取值而不改代码（呼应 P3-2 标定精神：取值要有依据，别从 29 篇假设）。

### 方案 C 落地路径（适应度闸 → 真外部测量 · 2026-09-18，用户选定）

诚实边界：fitness/health 是**启发式代理**，不是"预测误差/环境奖励"。要真正的外部测量只能"让现实当裁判"——
本仓库已接本地 vLLM（chat `ornith1.5-35b` + embedding 模型），接线**沿用 rag.py 铁律**：读 `ORNITH_BASE_URL`/
`ORNITH_API_KEY`、`Bearer` 认证、**端点不可达即静默降级、绝不连写死 LAN 地址**（P4 起一以贯之）。分三层，每层
独立可用、只读优先、可回滚：

- **Layer 0 · 语义地基（已落地 2026-09-18）**：新增 `kb_embed.py`——`_embed()` 调 vLLM `/embeddings`、L2 归一化；
  `kb_rsi.py:vec()` 自动探测、**可用即切语义向量、失败原样退回 char-vec（默认行为零变化）**；`cos()` 兼容
  dict(char-vec) 与 list(embedding)；`USE_EMBEDDING=1/0` 可覆盖。CLI `kb_embed.py probe/vec` 供人验证地基是否接通。
  这是方案 C 的精度底座：没有真语义对错，上层"现实验证"无从谈起。本轮真测接通：`kb_embed.py probe` 命中独立
  embedding vLLM，返回 `Qwen3-Embedding-8B` 的 4096 维 L2 归一向量；`kb_rsi.py:vec()` 在真实 template-vault 上对两条
  不同文本返回真语义向量（非 char-vec 退化），二者余弦 **0.9208**（高相关但可区分、非退化）→ Layer 0 从「待验证」落成「真地基」。⚠️ 换向量层后**去重/检索阈值语义含义会变**，
  须等 Layer 2 用仿真标定重定（呼应 P3-2，别从 29 篇假设）。
- **Layer 1 · 声明验证循环（已落地 2026-09-19）**：新增 `kb_claim.py`——建库/周期时 LLM 把每条**编译笔记**抽成结构化
  "可证伪声明"+抽取日期，落 `.kb/state/claims.json`（StateStore，与 feedback_state 同机制 → 可审计/可回滚）；新
  `raw/` 数据流入时 embedding 匹配"新数据 ↔ 旧声明"（签名 Jaccard 预过滤 + 余弦两档、有界 top-k）、LLM 判
  "证实/证伪/无关"；适应度闸 = `verified_ratio`（被现实支持）vs `falsified_ratio`（被现实推翻）= 真·对现实的预测误差。
  铁律：日期硬约束只取 `date(raw) > date(claim)`（防用未来数据泄露）；LLM/chat 接线照搬 rag.py（`ORNITH_CHAT_MODEL`
  缺省 `ornith1.5-35b`）；best-effort 降级（LLM/embedding 不可用→记 pending，绝不锁死流水线）。CLI: sync/validate/report/stats。
  签名匹配+日期约束+pending 计数已回归通过；LLM 抽声明/判定真值路径待 probe 接通后验。
- **Layer 2 · 真实使用结果捕获（2026-09-19 三块全落地）**：新增 `kb_usage.py`——读
  `feedback_state.json` 的 per-query 事件 `agent_hits`（由 rag.py `_record_hits` 在
  `--session` 非空时写入），检测「相似 query 短期重问 = 前次没答上」，输出 **再问率
  (requery_rate)** 与 **查询成功率代理(query_success_proxy ≈ 1−再问率)**。CLI
  `analyze/report/stats/mark`（同 `--root/--window/--sim`）。铁律：① analyze/report/stats
  只读（绝不改 rag.py 检索路径）；② best-effort（无事件→`no_events`，绝不报错；embedding
  不可用→仅精确匹配）；③ 窗口/相似度两档（默认 1h / 0.55）；④ 唯一写入 `mark` 是 **opt-in**
  （不自动跑，re-query 只是「再互动=相关」启发式、false-positive 高，手动确认再用）。三块
  按用户拍板逐一落地并已提交、回归通过：
  - **(a) 捕获打通**（commit `17bd55e`）：`rag.py` query 默认带 session（`_gen_session()`），
    每次查询写 per-query 事件；命中走缓存的返回正确不计为再问。→ `agent_hits` 开始有数据。
  - **(b) fitness 纳入再问率**（commit `8110858`）：`kb_fitness.py` 新增第 4 分量
    `query_success`（W_QUERY=0.20），原三分量权重重配（source 0.35→0.28、feedback 0.35→0.28、
    contradiction 0.30→0.24）；无 per-query 数据时中性=1.0（不因「未采集」而惩罚）；
    `query_success_component` 仅精确匹配（确定、不连 vLLM）。→ T3 的优化目标多一个真实外部接地。
  - **(c) 自动 useful**（commit `33a909e`）：`mark` 子命令把「窗口内被重复询问」的首次命中
    回写 `record_hit(useful=True)` 提升 importance；自动隔 `raw/`（`record_hit` 不 bump 不可变层）、
    已标记项幂等（不重复回写）。
  本机现状：已跑真实 query --session，`agent_hits` 自然有数据，只读 CLI 如实算再问率与成功率代理（语义匹配 + 真实使用信号均生效）；
  合成数据已验精确重复命中、窗口外排除、embedding 关时降级、
  mark 的 raw 隔除与幂等、fitness 组件随再问率下降，均正确。→ Layer 2 作为 north star 的
  真值路径已打通：本轮跑真实 query --session 让 `agent_hits` 自然积累，`analyze` 输出 **use_embedding=True**，
  换词重问（字面不同、语义相似，如"能否自我优化升级"→"如何自我改进"，余弦 **0.909**）被 embedding 检出——
  这是精确匹配做不到的，Layer 2 从「就位」变为「真生效」。真实数百查询复标（把换词重问误判口径钉死）仍待积累。

> 哲学收束（回应"知识库是原始知识总结+认知归纳"）：RSI 化的 kb-kit
 从一个"**知识储物柜**"变成"**有元认知有机体**"——(1) 知道自己知道什么（外部锚定**适应度**，非自抬）；(2) 知道什么有价值（外部接地加权）；(3) 会调自己怎么长（自适应回忆/参数）；(4) **靠持续摄入真实知识 + 定期人工回顾矛盾与综合来保持头脑清醒**（防塌）。越开放，越强；越封闭，越塌。



---

## 七、参考论文（精读清单）
- The Self-Improvement Paradox: Can LLMs Bootstrap without External Scaffolding? — ACL Findings 2025
- Model Collapse — Shumailov et al., 2023（arXiv 2305.10403 一线）
- Synthetic Collapse: Global Risk of Information Inbreeding in AI Ecosystems — 2025
- SPaR: Self-Play with Tree-Search Refinement — Zhipu GLM, arXiv 2412.11605（有验证器→成功）
- Meta-Rewarding LLMs: Self-Improving Alignment with LLM-as-a-Meta-Judge — EMNLP 2025
- GSM-R1 / 数学推理自改进 — 2024–2025（有参考答案→成功）
- Gödel Machine（Hofstadter）— 理论上的有界自我改进
