# 近重复 / 知识冲突 / 漂移检测调研（2025–2026）

面向 kb-kit（≈1500–4500 篇中文笔记，raw 证据层 / canon 编译层，纯 stdlib + 本地 LLM/embedding HTTP 端点）。
标注：**(A)** 纯 stdlib · **(B)** 需本地模型 · **(C)** 需新依赖。本文只谈与现有 `pipeline/dedup.py`、`kb_claim.py`、`kb_consistency.py` 的差额。

---

## 一、结论速览（按性价比排序）

| # | 动作 | 类别 |
|---|---|---|
| 1 | `dedup.py` 换 **bottom-k 签名**：每 shingle 只算 1 次哈希（现为 64 次），哈希量降 ~64×，并去掉 `h+i*GOLDEN` 线性置换的独立性偏差（[CS246 LSH](http://snap.stanford.edu/class/cs246-2011/slides/05-lsh.pdf)） | A |
| 2 | 候选生成加 **size filter**（Jaccard≥t ⇒ 长度比≥t）+ 用 bottom-k 值的倒排索引代替 banding | A |
| 3 | 区分**重复片段 vs 重复笔记**：containment ≥0.9 且 Jaccard<t → 出 "duplicate-of-section"，不合并 | A |
| 4 | 中文 shingle 由 k=5 改 **k=4**，同时报 char **3-gram Jaccard**；`_SIM_THRESHOLD=0.55` 必须逐模型标定 | A / B |
| 5 | 冲突三态闸：**同 (主体,属性) 槽位 + 时间窗重叠 + 值不同** 才叫矛盾；否则是"时序更新" | A |
| 6 | LLM **双调用**（solve 一次 / verify 一次）作为矛盾闸，比单次判断稳（[arXiv:2609.05928](https://arxiv.org/abs/2609.05928)） | B |
| 7 | 质量门禁三态 **pass / quarantine / reject**；启发式噪声只 flag 不拒（[Gopher 过滤器](https://github.com/huggingface/datatrove/blob/main/src/datatrove/pipeline/filters/gopher_quality_filter.py)） | A |
| 8 | 来源三字段 `source_tier / independent / recorded_at` 参与检索打分与冲突裁决（[IJCAI'25 CONFACT](https://www.ijcai.org/proceedings/2025/1073)） | A |
| 9 | 编译保真度存 per-note `coverage / fidelity / compression`，用**历史分位数**告警而非固定阈值 | A+B |
| 10 | 漂移检测用 PSI / KS / Page-Hinkley 对快照特征做双样本检验（[datadriftR](https://joss.theoj.org/papers/10.21105/joss.09481)） | A |

**一句话**：不要引入任何新库；缺口是"签名实现效率 + 时间槽位对齐 + 来源权重 + 三态门禁"，不是算法。

---

## 二、逐主题详解

### 1. 近重复算法取舍

**MinHash+LSH**：b 个 band、每 band r 行，命中概率 `P(s)=1-(1-s^r)^b`，拐点 `s*=(1/b)^(1/r)`。现有配置 64 哈希/16 band/4 行 ⇒ s\*=0.5，P(0.7)=98.8%、P(0.5)=64.4%——召回够，但候选对偏多，且每 shingle 做 64 次 `(h+i·GOLDEN)` 置换（4500 篇 × ~2000 shingle ≈ 5.8 亿次纯 Python 算术）。**改 bottom-k（KMV）**：

```python
def bottomk(shingles, k=64, salt=b"kb"):
    return heap.nsmallest(k, (int.from_bytes(
        hashlib.blake2b(s.encode(), digest_size=8, key=salt).digest(), "big")
        for s in shingles))
# 候选生成：倒排 {hash_value: [doc]}；两篇共享的 bottom-k 值个数 / |K_A ∪ K_B| ≈ Jaccard
# 复杂度：哈希 O(Σ|shingles|)、索引 O(N·k)、候选对只由共享值产生（近线性）
```

要点：① 仅当 `|K_A ∪ K_B| ≥ k` 时估计无偏，否则（短笔记）退回精确 Jaccard——4500 篇两两为 10 127 250 对，加 size filter 后可算；② 用 `blake2b(key=salt)`，别用内置 `hash()`（进程随机盐，跨进程不可比；现有代码这点是对的）；③ 若坚持多置换，用 `(a_i·h+b_i) mod 2^64`（a 奇数），线性加常数是已知有偏方案。

**SimHash**：它是随机超平面 LSH，估计的是**余弦**而非 Jaccard。64 位下 `d≈64θ/π`，故 Hamming k 与余弦对应为：k=3→cos 0.989、6→0.957、8→0.924、10→0.882、13→0.803、16→0.707。中文侵权检测文献惯例取 k=3（[Atlantis Press 双语义指纹](https://www.atlantis-press.com/article/125996012)，源自 [Manku WWW'07](https://dl.acm.org/doi/10.1145/1242572.1242592) 的 8×8 分块查表），那**只适合近似逐字**；知识笔记去重建议 **k=6 报"疑似重复"、6<k≤10 报"待复核"**。SimHash 的唯一优势是定长指纹可增量入库——kb-kit 规模下可有可无，不要与 Jaccard 混用阈值。

**中文上 char n-gram Jaccard vs 余弦**：没有中立的公开基准给出"你这份语料"上的对照数，别照抄别人的实测。可迁移的结论是性质差异：集合 Jaccard 与文档长度无关、但对长度比敏感；bigram/TF-IDF 余弦经 L2 归一化与 IDF 加权，**长文系统性偏高、短文偏低**，同一对文本的余弦通常高于 Jaccard（0.7 Jaccard 常对应 0.8+ 余弦），因此两套阈值不可互相平移。工程建议：**主判据用集合 Jaccard**（可解释、可算 size/containment 下界），余弦只作排序分。30 分钟自标定：抽 200 对（人工标 dup / not-dup），扫阈值画 P-R 曲线，取 precision≥0.95 的最低阈值。

### 2. 语义重复 vs 互补知识

阈值**不可跨模型迁移**（各模型各向异性/训练目标不同；跨模型映射需专门做，如 [arXiv:2608.05857](https://arxiv.org/abs/2608.05857)）。可参考的工程锚点：mneme 记忆库把 `NEAR_DUPLICATE_SIMILARITY=0.95` 定义为"近逐字重复 → 只出 duplicate_advisory"，绝不自动合并（[docs.rs](https://docs.rs/mneme-mcp/latest/mneme/memory/semantic/constant.NEAR_DUPLICATE_SIMILARITY.html)）；SemDeDup 在网页语料上用"聚类 + 余弦距离阈值"删掉 50% 数据而性能不掉（[arXiv:2303.09540](https://arxiv.org/abs/2303.09540)）——但那是**训练语料**目标，个人知识库删错不可逆，不可类比。建议 0.85–0.95 为"灰带"，只写 advisory 字段交人工/懒整理裁决。

**防误合并的硬规则（A，纯词法即可）**：候选对若在归一化后**数字/日期/单位/否定词/专名**上存在差异 → 标 `complementary` 或 `conflict_candidate`，禁止合并。依据：稠密检索对**否定**系统性失效（[arXiv:2603.17580](https://arxiv.org/abs/2603.17580)），纯相似度判等会吃掉"不/未/禁"这类字面差异。其次是**粒度**：文档级 0.9 相似的两篇可能各有一段独有内容，应在**段落级**去重后取并集（块级 dedup），而不是整篇合并。

### 3. 矛盾 / 冲突检测

2024–2026 的主流框架把知识冲突分三类：context-memory、inter-context、intra-memory（[Xu et al., EMNLP 2024](https://aclanthology.org/2024.emnlp-main.486/)），并强调冲突的两种成因**处理方式相反**：时序错位（新证据对）vs 误信息污染（同源冗余/可信度裁决）。

**claim 抽取 + 事实对齐**的现行做法是 `(subject, relation, value, temporal_state)` 四元组，`temporal_state ∈ {past, current, transient}`（2026 年 MDPI 流水线的原子 claim 抽取器即此结构）。kb-kit 的 `kb_claim.py` 已抽 `{claim,type,confidence}`，**缺 relation 槽位与时间态**——补上后：对齐在 `(主体,关系)` 上做（用现有受控标签词表当 relation 词表），只在**同一槽位**内比 value。

**时序不是矛盾**（双时态，见 [bitemporal edge validity](https://github.com/alphaparkinc/genpark-temporal-knowledge-graph-edge-validity-skill) 与姊妹调研）：每条断言带 `valid_from/valid_to/recorded_at`，判定：

```python
def classify(a, b):                    # 前提：a.subject==b.subject and a.relation==b.relation
    if not overlap(a.valid, b.valid):  # 区间不交（含一端开区间）
        return "update"                # 时序更新，不是矛盾
    return "dup" if norm(a.value) == norm(b.value) else "conflict"
```

**LLM 判矛盾的可靠性**：别信"高准确率"。ICTAI 2026 税务推理实验显示，最强模型在注入矛盾下仍有 **63–76% 概率直接算出干净答案、毫无冲突信号**；但改为要求"验证输入"时能抓出大部分矛盾，**干净样本准确率代价 ≤5pp**，验证器误判干净输入为矛盾的比例（false-flag）为 **0.0–5.5%**（[arXiv:2609.05928](https://arxiv.org/abs/2609.05928)）。IJCAI'25 的 CONFACT 显示：RAG 在来源可信度不一时最脆弱，**把来源背景注入检索与生成**可显著改善裁决。工程落法：抽取与判定分离、判定必须输出**冲突片段原文 + 置信度**、低于阈值进人工队列（kb-kit 的 `ok=False→pending` 方向正确）。

### 4. 知识漂移 / 信息损失度量

编译笔记（原始证据→综述笔记）的信息损失拆成三个可存字段：

- **coverage**（覆盖率，保真度上限）= 原文内容单元被笔记命中的比例。stdlib 版用字/词集召回（`kb_consistency._jaccard_recall` 已有），claim 级版用 LLM 抽 claim 后算 recall（B）。
- **fidelity**（保真度）= 笔记每条断言被原文蕴含的比例；工具链已成熟（[SummaC](https://arxiv.org/abs/2110.06918) / [AlignScore](https://arxiv.org/abs/2305.16739) / [FActScore](https://arxiv.org/abs/2305.14251)），本地端点可用同一套 prompt 实现。
- **compression** = len(笔记)/len(原文)。**没有公认的"合理压缩比区间"**——不要去找一个数字。可做的两件事：① 用 gzip 压缩比作信息密度代理（2026 年 Internet Archive 的大规模研究正是用它测"熵稀释"假设，结论是不显著——所以它是密度信号，不是 AI 检测器，见 [arXiv:2604.26965](https://arxiv.org/abs/2604.26965)）；② 对自己历史分布取 P5/P95 做异常告警。量级参照：摘要式压缩常规落在 10–20%，提示压缩方法 LLMLingua 宣称可到 20×（≈5%）而性能基本不损（[microsoft/LLMLingua](https://github.com/microsoft/LLMLingua)）——低于 ~3% 基本可判"只剩空话"。

**漂移检测**（对比写入时快照）：对每个时间窗采样特征（TF-IDF 主题分布、标签分布、来源 tier 占比、平均 claim 密度、平均压缩比），做 PSI `Σ(pᵢ-qᵢ)·ln(pᵢ/qᵢ)`（经验惯例：<0.1 稳、0.1–0.25 关注、>0.25 显著）、两样本 KS、或 Page-Hinkley 累积偏差（在线、单遍、纯 stdlib）。这三个都比"看曲线"靠谱，且都有明确告警阈值。

### 5. 来源可信度与 LLM 回灌污染

- **权重表（A）**：`source_tier`：1 官方文档/规范原文、2 同行评议/标准、3 工程博客、4 个人笔记、5 LLM 生成。检索打分乘 `w(tier) × independence × recency`；`independence` 用"同一事实的独立来源数"（Weller 的 answer-redundancy 思路，见综述 §3.3）——**同源转载不算独立**。
- **LLM 回灌防护（A+B）**：① LLM 产物一律 `derived=true`，且**必须挂 ≥1 条非 LLM `source_ref`**，否则 gate 拒收；② 落盘 `generator/model/version/prompt_hash`；③ LLM 生成内容不得作为冲突裁决的唯一证据；④ 检索默认对 `tier=5` 降权。
- **风险已被量化（2025–2026）**：截至 2025 年中，**约 35% 新发布网站被判为 AI 生成或辅助**（Internet Archive 抽样 + Pangram v3），AI 文本的平均语义相似度比人类文本高 **33%（ρ=0.47）**，即"语义收缩"——正对应"越回灌越同质"（[arXiv:2604.26965](https://arxiv.org/abs/2604.26965)）。递归训练退化有 Nature 2024 的模型坍缩证据（[s41586-024-07566-y](https://www.nature.com/articles/s41586-024-07566-y)）；溯源侧可参考 C2PA 式凭证与 [ProvenanceGuard](https://arxiv.org/abs/2606.18037)。但**不要**把 AI 文本检测器当入库判据：短文本不可靠、可改写绕过、误伤人类笔记。

### 6. 数据质量门禁：拒绝还是入库

分三态，不要二选一：

- **reject**（硬拒，仅限不变量）：schema/必填字段缺失、无 `source_ref`、正文为空或纯占位符、与来源逐字相同却标为"编译"。这类错误不可逆、会污染下游。
- **quarantine + flag**（软门）：长度异常、符号/停用词比例异常、重复行/重复 5-gram 比例过高、压缩比过低。**保留可检索但降权**，进人工复核队列。
- **pass**。

启发式阈值可照搬 [Gopher](https://arxiv.org/abs/2112.11446) / [FineWeb](https://proceedings.neurips.cc/paper_files/paper/2024/file/370df50ccfdf8bde18f8f9c2d9151bda-Paper-Datasets_and_Benchmarks_Track.pdf) / datatrove 那套（词数区间、平均词长、符号词比、以省略号结尾行占比、重复行占比、命中停用词数），中文把"词"换"字"并挂中文停用词表，代码见 [gopher_quality_filter.py](https://github.com/huggingface/datatrove/blob/main/src/datatrove/pipeline/filters/gopher_quality_filter.py)。

**取舍依据**：个人库"拒掉一篇真笔记"不可恢复，"放进噪声"可用降权/复核挽回，故**除不变量外宁入库不拒**；同时别过度过滤——FLUX 2026 证明激进过滤带来大量 token 损失（同一 dump 50B vs DCLM 40B），收益并不等比（[arXiv:2603.13972](https://arxiv.org/abs/2603.13972)）。

### 7. 不引入依赖时的中文相似度上限

- **能做的**：char bigram/trigram 的 TF-IDF/BM25；`unicodedata.normalize('NFKC')` 折叠全角/半角（中文笔记里 "！１：（）" 极常见，不做白丢几个点）；`sqlite3` 的 **FTS5 trigram tokenizer**（SQLite ≥3.34，`tokenize='trigram'`）不装 jieba 也能做中文子串检索 + BM25（[FTS5 文档](https://sqlite.org/fts5.html#the_trigram_tokenizer)，查询需 ≥3 字符）；`zlib` 做 NCD；`difflib` 做局部对齐。
- **差距量级**：中文 IR 的经典结论是**索引单元（字/双字/词）影响小于直觉**，词典与分词器差异有限（[Lexicon Effects on Chinese IR](https://aclanthology.org/W97-0316/)；站点检索用 bigram 也够，[CIKM'15](https://dl.acm.org/doi/10.1145/2838931.2838940)）。**近重复任务（字面重叠 >0.7）上字 n-gram 与 jieba/embedding 差距接近 0**；差距在两端：短查询高精度检索（几个到十几个点 nDCG）与**字面重叠 <0.3 的同义改写**（字面方法召回≈0，属 embedding 领域）。
- **明确上限**：不能做同义归并、词形还原、隐式指代、否定与词序语义——故第 2 节的"数字/否定守卫"必须词法显式兜住，别指望相似度。

---

## 三、不建议引入的，及理由

| 候选 | 结论 | 理由 |
|---|---|---|
| `datasketch` / scikit-learn | 不引入 | N≤4500 精确 Jaccard + size filter 足够，bottom-k 30 行可自写 |
| `jieba` | 不引入 | 去重几乎无收益；检索用 FTS5 trigram 即可，词典体积不值 |
| `simhash` 库 | 不引入 | 定长指纹在本地规模无意义，阈值语义常被误当 Jaccard |
| `sentence-transformers` | 不引入 | 与"调本地端点"重复且拖入 torch；应调同一 HTTP embedding 端点 |
| faiss / hnswlib / 向量库 | 不引入 | 4500×384 暴力余弦是几十毫秒级，ANN 只增运维面 |
| 照搬他人 embedding 阈值 | 不采用 | 随模型与语料漂移，必须本地 200 对标注标定 |
| LLM 自动合并/删除笔记 | 不采用 | 不可逆；改为 advisory + 懒整理 + INVALIDATE |
| AI 检测器 / 水印作入库判据 | 不采用 | 短文本不可靠、易绕过、误伤人类笔记 |
| 单一"合理压缩比"硬阈值 | 不采用 | 无文献支撑；用自家历史分位数 + 三指标联动 |
