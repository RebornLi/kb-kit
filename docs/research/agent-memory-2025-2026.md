# Agent 长期记忆系统实现技术调研（2025–2026）

面向 kb-kit（raw 证据层 / canon 编译层 / 使用信号 / L1–L5 反馈阶梯）。约束：Python stdlib + 本地 LLM/embedding HTTP 端点。
标注：**(A)** 纯 stdlib · **(B)** 需本地模型 · **(C)** 需新依赖。

---

## 一、结论速览（按性价比排序）

| # | 建议 | 类别 |
|---|---|---|
| 1 | raw 保持不可变 append-only，"整理"只发生在编译层 | A |
| 2 | 断言用「追加 + 失效」而非覆盖/删除：带 `valid_from/valid_to/recorded_at` | A |
| 3 | 不做硬去重：cos ≥0.95 只标记候选，0.85–0.95 交 L 阶梯裁决 | A+B |
| 4 | 冲突：写入打标、读取裁决，两条都留 | A+B |
| 5 | 懒整理：空闲 + 阈值触发，而非文档路径上同步执行 | A+B |
| 6 | 排序用 `recency × relevance × importance` 复合分 | A+B |
| 7 | canon 强制带 `source_ref`，并在使用时校验 | A |
| 8 | 矛盾加速衰减（被取代条目 decay×3），而非均匀 TTL | A |
| 9 | 按能力分项评测，不报单一准确率 | A+B |

**一句话**：kb-kit 的分层已命中 2025–2026 主流架构的共同骨架；**不需要图库或向量库**，缺的是时间字段、溯源指针、复合打分与阈值门控。

---

## 二、逐主题详解

### 1. Mem0 的 ADD/UPDATE/DELETE/NOOP

流程：新消息对 → 异步会话摘要 + 最近 m=10 条消息 → LLM 抽取候选事实 → 每候选取 top-s=10 相似记忆 → **LLM 用 function call 在四操作中选一**（不训分类器）。Mem0g 改为实体-关系有向图，矛盾关系**标记 invalid 而非物理删除**（[arXiv:2504.19413](https://arxiv.org/abs/2504.19413)）。

收益（LoCoMo，LLM-as-Judge）：Mem0 66.9% / Mem0g 68.4% vs OpenAI memory 52.9%；p95 延迟 1.44s/2.59s vs full-context 17.12s；token 成本降 >90%。图记忆**只在时序推理上明显加分**（58.13 vs 55.51），单跳/多跳反降。

代价：① 每条写入 1 次抽取 + 每候选 1 次决策，写放大高；② 硬删丢审计；③ 无衰减；④ 写入路径可被投毒。后继 Memory-R1 用 RL 训策略，LoCoMo 上 F1 +48%/BLEU-1 +69%，仅需 152 条 QA 微调，但需训练管线（[arXiv:2508.19828](https://arxiv.org/html/2508.19828v1)）。

**采纳点**：显式操作枚举这个**接口设计**（LLM 只输出决策、stdlib 落盘），但 DELETE 换 INVALIDATE，决策移到懒整理。

### 2. Zep / Graphiti：双时态知识图谱

三层：**episodes**（非有损原始输入）→ **entities/facts**（边带 `tvalid/tinvalid`）→ **communities**（聚类摘要）。核心是**事件发生时间（valid time）与记录/失效时间（transaction time）分开存**。检索三段：search（cosine + BM25 + 图 BFS）→ rerank（RRF/MMR/可选 cross-encoder）→ 构造 context（事实 + 有效期 + 实体摘要）（[arXiv:2501.13956](https://arxiv.org/abs/2501.13956)）。

实测：LongMemEval 上把平均上下文压到 ~1.6k token、延迟降 ~90%，在偏好/跨会话/时序类超过 full-context；DMR 上 94.8% vs MemGPT 93.4%（Zep 自研自评）。

**小规模是否值得**：图谱的增量价值集中在「同一实体的事实随时间演变」与矛盾，个人知识库恰是这类冲突密集处（偏好会变、工具会换、结论会被推翻）。但**不必上图库**：canon frontmatter 加 `valid_from/valid_to/recorded_at/superseded_by/source_ref` 即获得双时态全部语义，纯 stdlib 可解析可 diff。这是本报告**最推荐的单点改造**。

### 3. Letta / MemGPT：分层 + 自编辑记忆

MemGPT 把记忆当 OS 分页：小 in-context 窗口 ↔ 大外部存储（[arXiv:2310.08560](https://arxiv.org/abs/2310.08560)）。Letta 的 **memory blocks** 是 in-context 且**可被 agent 用工具编辑**的具名块，另配 recall（对话史）与 archival（显式保存）。

关键演进是 **MemGPT 2.0 的 sleep-time agent**：主 agent **不再持有编辑 core memory 的工具**，编辑权交给独立、可配更强模型的 sleep-time agent。动机：① 对话路径不背记忆管理的延迟与不可靠；② 增量形成的记忆会变脏，独立整理者能持续产出干净浓缩的记忆（[Letta 博客](https://www.letta.com/blog/sleep-time-compute/)）。

**对 kb-kit**：这是 L1–L5 阶梯的组织学依据——**只读主流程 + 有写权限的整理流程**，两者可用不同模型。

### 4. A-MEM：Zettelkasten 式记忆

每条记忆为结构化 note `{c 原文, t 时间, K 关键词, G 标签, X 上下文描述, e 向量, L 链}`。写路径三步：note construction（LLM 生成 K/G/X）→ link generation（向量 top-k 召回，LLM 判定建链）→ **memory evolution（新记忆触发回头改写旧记忆的 K/G/X）**。检索命中的 note 会带出同链邻居（[arXiv:2502.12110](https://arxiv.org/abs/2502.12110)）。

实测（LoCoMo，GPT-4o-mini）：平均 F1 优于 MemGPT/ReadAgent/MemoryBank，且**回答 token 从 ~16,910 降到 ~1,216–2,520**；多跳提升最大（45.85 vs MemGPT 25.52）。

风险：**evolution 是静默回溯改写**，破坏可审计性（无来源评估其是否留痕）；重复 re-summarize 会累积失真（仅二手来源）。

**采纳点**：原子笔记 + 显式链 + 双链检索；evolution 改为**只追加 revision、不改写原文**。

### 5. 去重 / 合并 / 遗忘 / 冲突

- **阈值不是重点，删除才是风险**。建议 **cos ≥0.95 自动标 `dup_candidate`，0.85–0.95 交 L 阶梯确认，<0.85 保留**。依据：朴素四操作会犯**把两条相关但不矛盾的事实误判为矛盾、DELETE 后毁掉前一条**的错误（[arXiv:2508.19828](https://arxiv.org/html/2508.19828v1)）。Jaccard 词集（阈值 ~0.75）可作 stdlib 预筛。
- **合并 vs 保留**：合并判据是**同一主张的不同表述**；保留判据是**同一主张的不同证据/时点/语境**——后者价值恰在后续矛盾裁决。来自不同语境的两条偏好陈述本就不是矛盾（[Zenodo 22182350](https://zenodo.org/records/22182350)）。
- **遗忘**：均匀 TTL 与纯 recency 是主流做法，但基准显示**复合信号（时新+频次+结果效用）优于单信号**（EMG-RAG 图剪枝 +~10%；AssoMem 多信号融合领先）。最强一招是**矛盾加速衰减**：被取代后 decay×3.0，零 token 成本的确定性抑制，84.8% vs STATICMEMORY 48.3%、陈旧记忆使用率 52.4%→15.2%（[Zenodo 21033238](https://zenodo.org/records/21033238)）。它是降权而非删除，**契合非破坏性原则**。
- **冲突检测**：写入时对「同主语+同谓词」的既有断言做一次 LLM 矛盾判定，命中则打 `conflict_with` 并**两条都留**；读取时按 recency + 证据强度排序。GitHub 选择**读时再校验**（更快更省），因其有"易验证"锚点；kb-kit 的 raw 网页原文具备同样属性，值得照搬。另需预警**模型版本漂移**（模型换代后同一记忆的解释可能改变）与**世界状态陈旧**两个衰减维度（[arXiv:2512.13564](https://arxiv.org/abs/2512.13564)）。GitHub 的 A/B（PR 合并率 +7pp，p<0.00001）说明这类投资在生产可测。

### 6. 睡眠时计算 / 后台巩固

[arXiv:2504.13171](https://arxiv.org/abs/2504.13171)（Letta + Berkeley）：空闲期**预判用户会问什么并预计算**，把 raw context 转成 learned context。结果：同等精度所需 test-time 算力降 **~5×**；继续加大 sleep-time 算力可再涨 **+13%（Stateful GSM-Symbolic）/ +18%（Stateful AIME）**；跨相关查询摊薄后**单问平均成本降 2.5×**。**效力与"查询可预测性"高度相关**——kb-kit 查询分布集中，收益预期乐观。

生产实现：双 agent（primary + sleep-time）；编辑权在睡 agent，主用快模型、睡用强模型；睡 agent 按频率运行，token 消耗随频率上升；文档摄取也是 anytime，主 agent 随时可读、不等它跑完。

**落地形态**：纯 stdlib 的 `consolidate.py`，由「距上次整理新增 raw 条数 ≥ N」或空闲心跳触发，做四件事：抽取候选断言 → 标记重复/矛盾 → 生成更新摘要 → 用复合分重算权重落盘，全程不阻塞查询。

### 7. 长上下文 vs 检索

- **LaRA（ICML 2025，2326 用例/11 模型）**：没有银弹，最优选择取决于**模型能力 × 上下文长度 × 任务类型 × 检索质量**的交互（[ICML poster](https://icml.cc/virtual/2025/poster/46069)）。
- **Context Rot（Chroma，18 模型）**：固定任务复杂度、只变输入长度，**所有模型性能都随长度下降且非均匀**。三个可用结论：① 针-问相似度越低退化越快（kb-kit 的语义化查询正属此类）；② **干扰项**（主题相关但不作答）比无关内容杀伤力大得多；③ 「完整对话史 vs 只给相关片段」平均 token 为 113k vs ~300，所有模型在 focused 输入上显著更好（[Chroma 报告](https://www.trychroma.com/research/context-rot)）。

**结论**：「全塞上下文」仅在**几千 token 量级的结构化 canon 摘要**下成立，且应作为**检索的补充**——即"检索出候选 + 该主题小摘要整体塞入"。raw 到十万 token 级后，长上下文会同时输在准确率与成本上。

### 8. 评价指标

- **LongMemEval（ICLR 2025，500 题）**：拆成 **5 项独立能力**——信息抽取、跨会话推理、时序推理、知识更新、弃答；商用助手与长上下文 LLM 在持续交互上掉 ~30%（[ICLR](https://proceedings.iclr.cc/paper_files/paper/2025/hash/d813d324dbf0598bbdc9c8e79740ed01-Abstract-Conference.html)）。
- **MemoryAgentBench** 另设四项：准确检索、test-time 学习、长程理解、**冲突解决**；报告称无任何架构同时掌握四项，**冲突解决与 test-time 学习最弱**（[arXiv:2507.05257](https://arxiv.org/abs/2507.05257)）。
- **DynamicMem（15 个月/2.2M token/5 个季度检查点）**：**profile 重建准确率随历史变长稳步下降，而服务类任务准确率持平**——"记不住"不会自动表现为"做不好"；五个系统**没有一个能同时保住仍为真的事实并覆盖已改变的事实**，错误集中在**用户从不显式陈述的偏好**与**精确实体指代**；**>93% 的失败归因于检索而非作答模型**（[arXiv:2606.22877](https://ar5iv.labs.arxiv.org/html/2606.22877)）。
- **共同盲点**：三大基准**都不测溯源保真度**（citation 读取时是否仍支持该主张），只测答案正确性（[评估框架综述](https://davidamitchell.github.io/Research/research/2026-07-20-agent-memory-evaluation-framework.html)）。

**kb-kit 四项指标**（全部 A/B 可实现）：**记忆精确率** = 被引用断言中"使用者未更正"比例（用 L 阶梯修正事件作标签）；**遗忘误伤率** = 被压制/标记失效的断言中事后被重新引用的比例；**跨会话延续率** = 同一实体 N 天后被再次命中且未产生重复 canon 笔记的比例（"再问率"倒数）；**溯源保真率** = 抽样断言的 `source_ref` 仍能支持该主张的比例。

---

## 三、不建议引入的

1. **图数据库/专用向量库** **(C)**：Zep 的价值来自双时态语义而非图引擎；Mem0g 只在时序推理略胜。frontmatter + stdlib 倒排即可覆盖。
2. **实时写入路径上的 LLM 决策（Mem0 式同步四操作）**：写放大严重且会误删；改为阈值门控懒整理。
3. **硬删除/覆盖式更新**：丢审计轨迹；换 `invalidated` + 矛盾加速衰减。
4. **无阈值保护的自动合并**：0.895–0.907 的窄带说明边际收益有限，误合并代价不可逆（[dev.to 实测](https://dev.to/haoning_kan_20d7ddb19e07c/i-benchmarked-6-ai-agent-memory-strategies-top-score-worst-experience-35gj)）。
5. **RL/微调式记忆管理器** **(C)**：需带奖励信号的训练管线，对个人知识库属过度工程。
6. **把「全部记忆塞进上下文」当主方案**：Context Rot 表明固定复杂度下性能仍随长度单调退化。
7. **照搬 A-MEM 的静默 evolution**：丢审计；改为 append-only revision。
8. **单一准确率作为记忆质量指标**：掩盖索引/巩固/遗忘的失效。
9. **均匀 TTL 作为主要遗忘机制**：复合信号与矛盾加速衰减有更强基准证据。

---

## 附：kb-kit 最小改造清单

1. canon frontmatter 增补 `valid_from/valid_to/recorded_at/superseded_by/source_ref/conflict_with`。**(A)**
2. `consolidate.py`：阈值触发的懒整理。**(A+B)**
3. 检索排序改为复合分，权重在 L 阶梯可调。**(A+B)**
4. 矛盾加速衰减：被取代条目 decay×3。**(A)**
5. 溯源保真抽样校验。**(A)**
6. 写权限分离：只读编译主流程 + 有写权限的整理流程。**(A+B)**
7. 四项指标埋点接入 L1–L5。**(A)**
