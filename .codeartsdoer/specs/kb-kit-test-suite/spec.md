# kb-kit 测试套件需求规格说明书

## 1. 项目概述

### 1.1 被测项目
- **项目名称**：kb-kit（知识库工具包）
- **项目路径**：`/home/mushan/workspace/kb-kit`
- **项目定位**：零依赖的 Python 3.8+ 知识库工具包，核心是 `template-vault/pipeline/` 下的 25 个 Python 模块构成的"成长引擎"
- **技术约束**：零 pip 依赖，仅使用 Python 标准库；无 Embedding 向量模型

### 1.2 测试目标
验证 kb-kit 项目中 25 个 Python 模块及安装引擎的功能正确性，确保：
- 各模块核心函数的输入输出符合预期
- 模块间协作流程正确衔接
- CLI 命令端到端流程可独立运行
- 不依赖任何外部 Embedding 服务

### 1.3 测试约束
- **不测试**：Embedding 向量模型相关功能（项目本身不使用 Embedding）
- **可测试**：`rag.py` 的 TF-IDF 余弦相似度检索（纯本地实现，零依赖）
- **测试框架**：pytest（优先）或 unittest（标准库备选）
- **Python 版本**：3.8+
- **隔离性**：测试不污染原项目文件，使用临时目录/fixtures

---

## 2. 测试范围

### 2.1 单元测试范围
对每个模块的核心函数进行独立测试，验证输入输出边界条件。

### 2.2 集成测试范围
测试模块间协作流程，如 `rag.py` 索引 → `feedback_loop.py` 命中 → `recall_schedule.py` 回忆的完整链路。

### 2.3 端到端测试范围
测试 CLI 命令完整流程，如 `python3 pipeline/rag.py index` → `python3 pipeline/rag.py query "问题"`。

### 2.4 排除范围
- Embedding 向量模型相关功能
- 外部 LLM API 调用（`rag.py` 的 `llm_answer` 中 ORNITH API 调用）
- 外部工具依赖（pdftotext、pandoc、openpyxl 等可选依赖）
- 真实 git 仓库操作（使用临时 git 仓库 fixture）

---

## 3. 功能需求清单

### 3.1 kb_common.py — 公共工具模块

#### FR-3.1.1: 分词函数 tokenize
- **WHEN** 输入纯拉丁文本 `s`，**THEN** 返回小写拉丁词列表
- **WHEN** 输入纯中文文本 `s`，**THEN** 返回中文字符 unigram + bigram 列表
- **WHEN** 输入混合中英文文本 `s`，**THEN** 返回拉丁词 + CJK unigram + bigram 的组合列表
- **WHEN** 输入空字符串，**THEN** 返回空列表

#### FR-3.1.2: frontmatter 解析 parse_frontmatter
- **WHEN** 输入无 frontmatter 的文本，**THEN** 返回 `({}, text)`
- **WHEN** 输入有 frontmatter 的文本，**THEN** 返回 `(fm_dict, body_str)`，value 做 strip + 去引号
- **WHEN** frontmatter 含嵌套键（行以 `:` 结尾），**THEN** 该键值设为 `None`
- **WHEN** frontmatter 含注释行（以 `#` 开头），**THEN** 注释行被跳过

#### FR-3.1.3: 文件元数据加载 load_meta
- **WHEN** 文件路径存在且可读，**THEN** 返回 `(fm_dict, body_str)`
- **WHEN** 文件路径不存在或读取失败，**THEN** 返回 `({}, "")`

#### FR-3.1.4: 笔记加载 load_note
- **WHEN** 文件路径存在，**THEN** 返回 `(fm_dict, text, body)` 三元组
- **WHEN** 文件不可读，**THEN** 抛出异常

#### FR-3.1.5: 笔记遍历 iter_notes
- **WHEN** 遍历含 `.md` 文件的目录，**THEN** 生成所有 `.md` 文件的 Path 对象
- **WHEN** 遍历含 EXCLUDE 目录的路径，**THEN** EXCLUDE 目录被跳过
- **WHEN** 遍历含嵌套 git 仓库的路径，**THEN** 嵌套 git 仓库内容被跳过

#### FR-3.1.6: token 计数 count_tokens
- **WHEN** 输入空文本，**THEN** 返回 `0`
- **WHEN** 输入纯 CJK 文本，**THEN** 返回 CJK 字符数（1 字 = 1 token）
- **WHEN** 输入纯拉丁文本，**THEN** 返回拉丁字符数 // 4

#### FR-3.1.7: 信息熵 text_entropy
- **WHEN** 输入空文本，**THEN** 返回 `0.0`
- **WHEN** 输入单字符重复文本，**THEN** 返回 `0.0`
- **WHEN** 输入每字符不同的文本，**THEN** 返回 `1.0`
- **WHEN** 输入一般文本，**THEN** 返回 0-1 之间的归一化 Shannon 熵

#### FR-3.1.8: 内容指纹 content_fingerprint
- **WHEN** 输入相同 body，**THEN** 返回相同的 64 字符十六进制 sha256 哈希
- **WHEN** 输入不同 body，**THEN** 返回不同的哈希值

#### FR-3.1.9: domain 解析 parse_domain
- **WHEN** 输入 `"开发"`，**THEN** 返回 `("开发", None)`
- **WHEN** 输入 `"开发/后端"`，**THEN** 返回 `("开发", "后端")`
- **WHEN** 输入 `None` 或 `""`，**THEN** 返回 `("综合", None)`

#### FR-3.1.10: 配置加载 load_config
- **WHEN** 配置文件存在，**THEN** 返回解析后的 dict（去除 `_meta` 字段）
- **WHEN** 配置文件不存在，**THEN** 返回 `{}`
- **WHEN** 同名配置二次加载，**THEN** 返回缓存结果

#### FR-3.1.11: domain 校验 is_valid_domain
- **WHEN** domain 在白名单内，**THEN** 返回 `True`
- **WHEN** domain 不在白名单内，**THEN** 返回 `False`
- **WHEN** 无 taxonomy 配置，**THEN** 返回 `True`（不校验）

#### FR-3.1.12: 标签校验 is_valid_tag
- **WHEN** 标签在白名单内，**THEN** 返回 `True`
- **WHEN** 层级标签的一级和二级都在白名单内，**THEN** 返回 `True`
- **WHEN** 标签不在白名单内，**THEN** 返回 `False`

#### FR-3.1.13: 关系字段提取 extract_relations
- **WHEN** frontmatter 含 `related_to` / `prerequisite` / `supersedes` / `superseded_by` 字段，**THEN** 返回非空字段字典
- **WHEN** frontmatter 不含关系字段，**THEN** 返回 `{}`

#### FR-3.1.14: 关系字段添加 add_relation
- **WHEN** 字段不存在，**THEN** 添加为字符串值
- **WHEN** 字段已存在且为字符串且目标不同，**THEN** 转为列表包含两个值
- **WHEN** 字段已存在且为列表且目标不在列表中，**THEN** 追加到列表
- **WHEN** 目标已存在，**THEN** 幂等不重复添加

#### FR-3.1.15: 三级去重 dedup_check
- **WHEN** 新内容与现有笔记 sha256 完全一致，**THEN** 返回 `action="skip"`
- **WHEN** 新内容与现有笔记 TF-IDF 相似度 ≥ 语义去重阈值，**THEN** 返回 `action="skip"`
- **WHEN** 新内容与现有笔记 TF-IDF 相似度在相关区间内，**THEN** 返回 `action="mark_related"`
- **WHEN** 新内容与现有笔记无高相似度匹配，**THEN** 返回 `action="new"`

---

### 3.2 rag.py — 本地语义检索模块

#### FR-3.2.1: 索引构建 cmd_index
- **WHEN** 执行全量索引构建，**THEN** 生成 `df_idf.json` 含 version/n_docs/vocab/idf/tf/meta/doc_hashes/doc_mtimes/inverted_index
- **WHEN** 笔记 frontmatter 含 `kb_action: retire`，**THEN** 该笔记不入索引
- **WHEN** 笔记正文无有效 token，**THEN** 该笔记不入索引

#### FR-3.2.2: 增量索引更新 _incremental_update
- **WHEN** 文件 mtime 未变，**THEN** 跳过该文件
- **WHEN** 文件 mtime 变化但内容 hash 不变，**THEN** 更新 mtime 但不重新处理
- **WHEN** 文件内容变化，**THEN** 重新计算 tf/meta
- **WHEN** 文件被删除，**THEN** 从索引中移除该文件

#### FR-3.2.3: 索引加载 load_index
- **WHEN** 索引文件不存在，**THEN** 返回 `(None, 0)`
- **WHEN** 索引文件损坏，**THEN** 返回 `(None, 0)`
- **WHEN** 索引文件无 version 字段（旧索引），**THEN** 返回 `(dict, 0)`
- **WHEN** 索引文件有 version 字段，**THEN** 返回 `(dict, version)`

#### FR-3.2.4: 向量计算 _vec
- **WHEN** 输入 Counter 和 vocab/idf，**THEN** 返回归一化的 TF-IDF 权重向量

#### FR-3.2.5: 余弦相似度 _cos
- **WHEN** 输入两个向量，**THEN** 返回余弦相似度值
- **WHEN** 向量无交集，**THEN** 返回 `0.0`

#### FR-3.2.6: 查询扩展 _expand_query
- **WHEN** 查询含同义词，**THEN** 扩展为包含规范词的查询
- **WHEN** 无同义词配置文件，**THEN** 返回原始查询的 token 拼接

#### FR-3.2.7: 最佳片段提取 best_sentence
- **WHEN** body 含与 query 高度匹配的句子，**THEN** 返回该句子
- **WHEN** body 含结论词（因此/所以/结论等），**THEN** 结论词句子获得加成
- **WHEN** body 为空或无有效句子，**THEN** 返回截断的 body 或空字符串

#### FR-3.2.8: 检索结果去重 _dedup_chunks
- **WHEN** 同一笔记多 chunk 命中，**THEN** 只保留最高分并标注 chunk_siblings
- **WHEN** 结果数超过 top，**THEN** 只返回前 top 条

#### FR-3.2.9: 过滤器 _apply_filters
- **WHEN** 指定 domain 过滤，**THEN** 只返回匹配 domain 的结果（支持两级 domain）
- **WHEN** 指定 content_type 过滤，**THEN** 只返回匹配 content_type 的结果
- **WHEN** 指定 author 过滤，**THEN** 只返回匹配 author 的结果
- **WHEN** 指定 min_importance 过滤，**THEN** 只返回 importance ≥ 阈值的结果
- **WHEN** 指定 enforce_level 过滤，**THEN** 只返回匹配 enforce_level 的结果
- **WHEN** 指定 tags 过滤，**THEN** 只返回包含任一指定标签的结果
- **WHEN** 指定 date_from/date_to 过滤，**THEN** 只返回日期范围内的结果

#### FR-3.2.10: enforce_level 优先级加成
- **WHEN** 笔记 content_type=policy 且 enforce_level=must，**THEN** 分数乘以 1.10
- **WHEN** 笔记 content_type=reference，**THEN** 分数乘以 0.95
- **WHEN** 笔记 content_type=chat，**THEN** 分数乘以 0.90

#### FR-3.2.11: JSON 输出 cmd_query (--json)
- **WHEN** 以 `--json` 查询，**THEN** 输出含 query/hits/meta 的 JSON 结构
- **WHEN** 指定 `--context-budget`，**THEN** 输出含 context 字段（拼接 snippet 不超预算）

#### FR-3.2.12: 倒排索引 _build_inverted_index
- **WHEN** 构建倒排索引，**THEN** 含 tag/domain/content_type/author/kb_summary_token 五个字段的倒排映射

#### FR-3.2.13: 倒排索引命中加成 _inverted_lookup
- **WHEN** 查询 token 命中 tag 或 kb_summary_token，**THEN** 对应笔记获得命中加成

---

### 3.3 intake_triage.py — 摄入 triage 引擎

#### FR-3.3.1: 信号密度计算 density
- **WHEN** 输入含大量字母数字和 CJK 字符的文本，**THEN** 返回较高的密度值
- **WHEN** 输入纯空白/标点文本，**THEN** 返回接近 0 的密度值

#### FR-3.3.2: 四维打分 evaluate
- **WHEN** 笔记信号字符 < 15，**THEN** 强制返回 `action="trash"`
- **WHEN** 笔记缺必填字段，**THEN** 结构分降低并记录原因
- **WHEN** 笔记与现有笔记高相似度（≥0.85），**THEN** 记录合并建议
- **WHEN** 笔记含价值关键词，**THEN** 价值信号分增加

#### FR-3.3.3: triage 主流程 triage
- **WHEN** 收件箱有笔记，**THEN** 对每篇笔记执行四维打分并返回结果列表
- **WHEN** 收件箱为空，**THEN** 返回空结果列表

#### FR-3.3.4: 按内容类型路由 triage_by_content_type
- **WHEN** content_type 为 policy/decision/lesson/knowledge/reference，**THEN** 返回 `action="direct"` 和对应目标目录
- **WHEN** content_type 为 chat/chat_todo/chat_decision/chat_knowledge，**THEN** 返回 `action="aggregate"`
- **WHEN** content_type 为 noise，**THEN** 返回 `action="trash"`
- **WHEN** content_type 未知，**THEN** 按 domain 兜底路由

#### FR-3.3.5: domain → PARA 映射 _domain_to_para
- **WHEN** domain 为"开发"/"安全"，**THEN** 映射到 "20-技术 Technology"
- **WHEN** domain 为"产品"/"运营"/"管理"，**THEN** 映射到 "10-项目 Projects"
- **WHEN** domain 为"综合"，**THEN** 映射到 "30-资源 Resources"
- **WHEN** domain 未知，**THEN** 兜底映射到 "30-资源 Resources"

---

### 3.4 feedback_loop.py — 命中信号引擎

#### FR-3.4.1: 命中检索 hits_for
- **WHEN** 索引存在且查询有效，**THEN** 返回 score > 0 的 top-k 命中 rel 列表
- **WHEN** 索引为 None 或查询为空，**THEN** 返回空列表

#### FR-3.4.2: importance 提升建议 bumps
- **WHEN** 笔记命中次数 ≥ MIN_HITS，**THEN** 生成提升建议
- **WHEN** 笔记命中次数 < MIN_HITS，**THEN** 不生成提升建议
- **WHEN** 提升值 < MIN_BUMP，**THEN** 不生成提升建议
- **WHEN** 累计提升超过 BUMP_CAP，**THEN** 截断到 BUMP_CAP

#### FR-3.4.3: importance 写入 _write_importance
- **WHEN** frontmatter 含 importance 字段，**THEN** 替换为新值
- **WHEN** frontmatter 不含 importance 字段，**THEN** 在 frontmatter 开头添加

#### FR-3.4.4: 命中反馈记录 record_hit
- **WHEN** useful=True，**THEN** 权重为 1.0，importance 累积增加
- **WHEN** useful=False，**THEN** 权重为 0.0，记录但不累积
- **WHEN** 记录命中反馈，**THEN** state 文件含 explicit_feedback 列表

#### FR-3.4.5: importance 累积 _accumulate_importance
- **WHEN** 累积后 importance ≤ 1.0，**THEN** 写入新值
- **WHEN** 累积后 importance > 1.0，**THEN** 截断到 1.0
- **WHEN** 笔记不存在，**THEN** 返回 False

---

### 3.5 link_engine.py — 孤岛补链引擎

#### FR-3.5.1: 出链提取 outbound
- **WHEN** 文本含 `[[Target]]`，**THEN** 返回含 "Target" 的集合
- **WHEN** 文本含 `[[Target|alias]]`，**THEN** 返回含 "Target" 的集合
- **WHEN** 文本含 `[[Target#section]]`，**THEN** 返回含 "Target" 的集合
- **WHEN** 文本无 wikilink，**THEN** 返回空集合

#### FR-3.5.2: 补链建议 suggestions
- **WHEN** 两篇笔记相似度 ≥ threshold 且无互链，**THEN** 生成建议链接
- **WHEN** 两篇笔记不同 domain 且相似度 ≥ threshold，**THEN** 生成跨域建议链接（cross=True）
- **WHEN** 两篇笔记同 domain 且相似度 ≥ threshold，**THEN** 生成同域建议链接（cross=False）
- **WHEN** 两篇笔记相似度 < threshold，**THEN** 不生成建议

#### FR-3.5.3: 建议链接写入 apply_links
- **WHEN** 笔记已有「🔗 智能建议链接」区块，**THEN** 跳过（幂等）
- **WHEN** 笔记无建议区块，**THEN** 追加建议区块
- **WHEN** 指定 --limit，**THEN** 只取分最高的前 N 对

---

### 3.6 recall_schedule.py — 间隔回忆引擎

#### FR-3.6.1: 基础间隔 base_interval
- **WHEN** importance ≥ 0.9，**THEN** 返回 180 天（L4）
- **WHEN** importance ≥ 0.5，**THEN** 返回 30 天（L3）
- **WHEN** importance ≥ 0.3，**THEN** 返回 14 天（L2）
- **WHEN** importance < 0.3，**THEN** 返回 7 天

#### FR-3.6.2: 回忆 deck 构建 build
- **WHEN** 笔记 importance < 0.1，**THEN** 不参与回忆
- **WHEN** 笔记 kb_action=retire，**THEN** 不参与回忆
- **WHEN** 笔记已到期（overdue ≥ 0），**THEN** 进入 deck
- **WHEN** deck 按 urgency 降序排列，**THEN** urgency 高的排前面

#### FR-3.6.3: 检索核对 retrievable
- **WHEN** 以 summary 查询且笔记命中顶层，**THEN** 返回 True
- **WHEN** 索引为 None 或 summary 为空，**THEN** 返回 False
- **WHEN** 笔记不在索引中，**THEN** 返回 False

#### FR-3.6.4: 标记已回忆 mark
- **WHEN** 标记笔记已回忆，**THEN** last_seen 更新为今天，stability 递增（×1.4，上限 180），recalls +1

---

### 3.7 kb_health.py — 成长性度量引擎

#### FR-3.7.1: 六维度量 metrics
- **WHEN** 计算连接度，**THEN** 返回 avg_outbound_links / orphan_notes / orphan_pct / cross_domain_ratio
- **WHEN** 计算时效，**THEN** 返回 stale_notes / stale_pct
- **WHEN** 计算新鲜，**THEN** 返回近 30 天新增笔记数
- **WHEN** 计算沉淀质量，**THEN** 返回收件箱积压数
- **WHEN** 计算涌现，**THEN** 返回 MOC/综合类笔记数
- **WHEN** 计算去重候选，**THEN** 返回相似度 > 0.9 的笔记对

#### FR-3.7.2: 评分与告警 score_and_alerts
- **WHEN** orphan_pct > 5，**THEN** 生成孤儿笔记告警
- **WHEN** avg_out < 1.5，**THEN** 扣 20 分并生成孤岛症预警
- **WHEN** stale_pct > 10，**THEN** 扣分并生成坏血病预警
- **WHEN** 收件箱有积压，**THEN** 扣分并生成积压告警
- **WHEN** 有涌现笔记，**THEN** 加分（最多 +10）
- **WHEN** score ≥ 80，**THEN** label 为"成长性良好"
- **WHEN** 55 ≤ score < 80，**THEN** label 为"成长需推动"
- **WHEN** score < 55，**THEN** label 为"需重点治理"

#### FR-3.7.3: 制度知识过期检测 check_policy_stale
- **WHEN** policy 笔记有 review_cycle 且 last_reviewed + review_cycle < now，**THEN** 返回过期列表
- **WHEN** policy 笔记无 last_reviewed，**THEN** 直接过期
- **WHEN** 非 policy 笔记，**THEN** 不检测

---

### 3.8 dashboard.py — 治理仪表盘

#### FR-3.8.1: 校验数据获取 run_validate
- **WHEN** 调用 validate.py --json，**THEN** 返回校验结果 dict
- **WHEN** validate.py 输出非 JSON，**THEN** 返回 `{}`

#### FR-3.8.2: 备份检查 check_backup
- **WHEN** 无备份文件，**THEN** 返回 `{"ok": False, "msg": "无备份"}`
- **WHEN** 有备份且 sha256 校验通过，**THEN** 返回 `verified="✓"`
- **WHEN** 备份超过 30 小时，**THEN** `fresh=False`

#### FR-3.8.3: 仪表盘生成 main
- **WHEN** 生成仪表盘，**THEN** 输出文件含运行告警/概况/备份健康/成长性/状态分布/领域分布
- **WHEN** 有硬错误，**THEN** 生成告警
- **WHEN** 无告警，**THEN** 显示"知识库运行正常"

---

### 3.9 kb-healthcheck.py — 健康巡检

#### FR-3.9.1: 死链检测 check_deadlinks
- **WHEN** wikilink 指向存在的文件，**THEN** 不计为死链
- **WHEN** wikilink 指向不存在的文件，**THEN** 计为死链
- **WHEN** wikilink 指向系统目录（memory/reference 等），**THEN** 不计为死链
- **WHEN** 模板目录中的占位符链接，**THEN** 不参与死链统计

#### FR-3.9.2: 骨架笔记检测 check_skeletons
- **WHEN** 文件 < 500B 且创建 > 7 天，**THEN** 计为骨架笔记
- **WHEN** 文件在系统目录或模板目录，**THEN** 不检测

#### FR-3.9.3: 标签越界检测 check_tags
- **WHEN** 标签在白名单内，**THEN** 不报越界
- **WHEN** 标签不在白名单内且非噪声标签，**THEN** 报越界
- **WHEN** 标签为噪声标签（空/-/ai 等），**THEN** 报无意义

#### FR-3.9.4: 空目录检测 check_empty
- **WHEN** PARA 分区下有空目录，**THEN** 报空目录
- **WHEN** 系统目录（.git/.obsidian 等），**THEN** 不检测

#### FR-3.9.5: frontmatter 缺失检测 check_frontmatter
- **WHEN** 笔记有 frontmatter 但缺必填字段，**THEN** 报缺失
- **WHEN** 笔记无 frontmatter，**THEN** 不检测（跳过）
- **WHEN** 系统目录笔记，**THEN** 不检测

#### FR-3.9.6: 顶层目录纪律 check_discipline
- **WHEN** 顶层目录以 `NN-` 编号格式命名，**THEN** 不报违规
- **WHEN** 顶层目录不以 `NN-` 格式命名且非系统目录，**THEN** 报违规

#### FR-3.9.7: 孤儿笔记检测 check_orphans
- **WHEN** 笔记无 `[[...]]` 出链且非 MOC/索引页，**THEN** 计为孤儿
- **WHEN** 笔记含 `[[...]]` 出链，**THEN** 不计为孤儿
- **WHEN** MOC/索引/首页类笔记，**THEN** 不计为孤儿

#### FR-3.9.8: 超长笔记检测 check_overlong
- **WHEN** 笔记正文 > 2000 字，**THEN** 报超长
- **WHEN** 笔记正文 ≤ 2000 字，**THEN** 不报

---

### 3.10 clean.py — 清洗/分块模块

#### FR-3.10.1: 分块策略路由 route_chunk_strategy
- **WHEN** 正文 < 200 字，**THEN** 返回 "aggregate"
- **WHEN** 正文 200-2000 字，**THEN** 返回 "direct"
- **WHEN** 正文 2000-10000 字，**THEN** 返回 "chunk_by_heading"
- **WHEN** 正文 > 10000 字，**THEN** 返回 "semantic_chunk"

#### FR-3.10.2: domain 推断 infer_domain
- **WHEN** frontmatter domain 在白名单内，**THEN** 直接返回该 domain
- **WHEN** 路径含已知文件夹名，**THEN** 按文件夹推断 domain
- **WHEN** 正文含关键词，**THEN** 按关键词推断 domain
- **WHEN** 无法推断，**THEN** 返回 "综合"

#### FR-3.10.3: status 推断 infer_status
- **WHEN** 路径含"归档"/"trash"，**THEN** 返回 "archived"
- **WHEN** frontmatter status 在白名单内，**THEN** 返回该 status
- **WHEN** 文件 mtime > 180 天，**THEN** 返回 "archived"
- **WHEN** 文件 mtime > 90 天，**THEN** 返回 "legacy"

#### FR-3.10.4: 笔记清洗 clean_note
- **WHEN** 清洗笔记，**THEN** frontmatter 含全部必填字段（tags/status/domain/created/updated/importance/kb_target/kb_action/kb_summary）
- **WHEN** 原有非必填字段，**THEN** 保留在清洗结果中

#### FR-3.10.5: 聚合桶 aggregate_bucket
- **WHEN** 聚合桶文件不存在，**THEN** 创建带 frontmatter 的新桶文件
- **WHEN** 聚合桶文件存在，**THEN** 追加内容到现有桶
- **WHEN** 指定 author，**THEN** 条目含 author 前缀

---

### 3.11 validate.py — frontmatter 校验模块

#### FR-3.11.1: frontmatter 解析 parse_frontmatter
- **WHEN** 文本有 frontmatter，**THEN** 返回 `(fm_dict, start_line)`
- **WHEN** 文本无 frontmatter，**THEN** 返回 `(None, 1)`

#### FR-3.11.2: 文件校验 validate_file
- **WHEN** 缺必填字段，**THEN** 报硬错误
- **WHEN** status 不在枚举内，**THEN** 报硬错误
- **WHEN** domain 不在白名单内，**THEN** 报硬错误
- **WHEN** importance 不在 0-1 范围，**THEN** 报硬错误
- **WHEN** 正文 > 2000 字，**THEN** 报软告警
- **WHEN** kb_action=retire 但 status=active/stable，**THEN** 报软告警
- **WHEN** enforce_level 非标准值，**THEN** 报软告警

---

### 3.12 sync.py — 知识同步管道

#### FR-3.12.1: domain 路由 route_domain
- **WHEN** domain 为"开发"，**THEN** 返回 `("20-技术 Technology", "后端开发")`
- **WHEN** domain 为"安全"，**THEN** 返回 `("20-技术 Technology", "安全")`
- **WHEN** domain 未知，**THEN** 返回 `("40-资源库 Resources", domain)`

#### FR-3.12.2: dry-run cmd_dry
- **WHEN** 有硬错误且未 --allow-existing-issues，**THEN** 中止并提示
- **WHEN** 无硬错误，**THEN** 输出拟执行步骤

#### FR-3.12.3: 统一摄取 cmd_ingest_any
- **WHEN** 文件不存在，**THEN** 返回错误码 1
- **WHEN** 质量门禁未通过，**THEN** 丢弃并返回 0
- **WHEN** content_type 为 noise，**THEN** 丢弃
- **WHEN** content_type 为 chat 类，**THEN** 进聚合桶
- **WHEN** content_type 为 knowledge/decision 等，**THEN** 直达目标目录

---

### 3.13 state_manager.py — 状态管理 + 文件锁

#### FR-3.13.1: 状态加载 load
- **WHEN** 状态文件存在，**THEN** 返回解析后的 dict
- **WHEN** 状态文件不存在，**THEN** 返回 default 或 `{}`
- **WHEN** 状态文件损坏，**THEN** 返回 default 或 `{}`

#### FR-3.13.2: 状态保存 save
- **WHEN** 保存状态，**THEN** 原子写入（临时文件 → rename）
- **WHEN** 保存状态，**THEN** 文件内容为 JSON 格式

#### FR-3.13.3: 原子更新 update
- **WHEN** 调用 update，**THEN** 执行 load → fn(data) → save
- **WHEN** fn 返回 None，**THEN** 不写入

#### FR-3.13.4: 旧路径迁移 migrate_legacy_state
- **WHEN** 旧路径有状态文件且新路径无，**THEN** 迁移到新路径
- **WHEN** 新路径已有状态文件，**THEN** 不迁移

---

### 3.14 classify.py — 内容分类器

#### FR-3.14.1: 内容分类 classify
- **WHEN** 正文 < 15 字，**THEN** 返回 "noise"
- **WHEN** 信号密度 < 0.05 且 < 200 字，**THEN** 返回 "noise"
- **WHEN** 聊天上下文且含决策信号词，**THEN** 返回 "chat_decision"
- **WHEN** 非聊天且含 policy 信号词，**THEN** 返回 "policy"
- **WHEN** 无匹配信号词，**THEN** 返回兜底类型（"chat" 或 "reference"）

#### FR-3.14.2: 质量门禁 classify_quality
- **WHEN** 正文 < 15 字，**THEN** should_ingest=False
- **WHEN** 信号密度 < 0.05 且 < 200 字，**THEN** should_ingest=False
- **WHEN** < 200 字且聊天样内容，**THEN** 返回 "chat" 进聚合桶
- **WHEN** 密度 ≥ 0.15 且熵 ≥ 0.7，**THEN** 返回 "knowledge" 高质量
- **WHEN** 其他，**THEN** 返回 "reference"

#### FR-3.14.3: 聊天上下文检测 _detect_chat_context
- **WHEN** frontmatter 含 participants/chat_time/channel，**THEN** 返回 True
- **WHEN** body 匹配聊天行模式 ≥ 3 行，**THEN** 返回 True
- **WHEN** 无聊天特征，**THEN** 返回 False

---

### 3.15 graph.py — 知识图谱构建

#### FR-3.15.1: 节点收集 collect_nodes
- **WHEN** 笔记有关系型字段，**THEN** 收录为节点
- **WHEN** 指定 domain_filter，**THEN** 只收录匹配 domain 的节点
- **WHEN** 笔记无关系型字段，**THEN** 不收录

#### FR-3.15.2: Mermaid 图生成 build_mermaid
- **WHEN** 有节点和边，**THEN** 生成合法的 Mermaid graph LR 语法
- **WHEN** 节点数超过 max_nodes，**THEN** 截断
- **WHEN** content_type 为 policy，**THEN** 使用方括号形状
- **WHEN** content_type 为 decision，**THEN** 使用圆括号形状

---

### 3.16 rerank.py — 重排序模块

#### FR-3.16.1: token 重叠度 _token_overlap
- **WHEN** 两 token 集合有交集，**THEN** 返回 Jaccard 相似度
- **WHEN** 两 token 集合无交集，**THEN** 返回 0.0

#### FR-3.16.2: frontmatter 匹配 _frontmatter_match
- **WHEN** query 含 domain 值，**THEN** 加 0.4 分
- **WHEN** query 含任一 tag，**THEN** 加 0.3 分
- **WHEN** query 含 content_type 值，**THEN** 加 0.3 分

#### FR-3.16.3: 重排 rerank
- **WHEN** 对 hits 重排，**THEN** 按 5 维度加权新分数降序排列
- **WHEN** 笔记 enforce_level=must，**THEN** enforce 分数为 1.0
- **WHEN** 笔记近 30 天更新，**THEN** recency 分数为 1.0 → 0.5 线性递减

---

### 3.17 semantic_chunk.py — 语义分块

#### FR-3.17.1: 滑动窗口分块 _sliding_window
- **WHEN** 正文 < chunk_size，**THEN** 返回单块
- **WHEN** 正文 > chunk_size，**THEN** 按段落累积分块，相邻块 overlap 字符重叠
- **WHEN** 正文为空，**THEN** 返回空列表

#### FR-3.17.2: 语义分块主入口 chunk
- **WHEN** 分块数 ≤ 1，**THEN** 返回空列表（无需分块）
- **WHEN** 分块数 > 1，**THEN** 返回分块笔记列表 + 父索引页
- **WHEN** 分块笔记 frontmatter，**THEN** 含 chunk_of / chunk_index / chunk_total 字段

---

### 3.18 memory_ingest.py — 记忆写入侧

#### FR-3.18.1: 文本相似度 _text_sim
- **WHEN** 两段文本 token 集合有交集，**THEN** 返回 Jaccard 相似度
- **WHEN** 任一文本无 token，**THEN** 返回 0.0

#### FR-3.18.2: bucket 归一化 normalize_bucket
- **WHEN** 输入简写（如 "10-项目"），**THEN** 映射到标准 PARA 命名空间
- **WHEN** 输入标准名（如 "10-项目 Projects"），**THEN** 原样返回
- **WHEN** 未知 bucket，**THEN** 原样返回

#### FR-3.18.3: 路径安全 _safe_kb_path
- **WHEN** target 为绝对路径，**THEN** 返回 None（拒绝）
- **WHEN** target 含 `..` 路径穿越到 vault 外，**THEN** 返回 None
- **WHEN** target 在 vault 内，**THEN** 返回解析后的 Path

#### FR-3.18.4: 镜像 mirror
- **WHEN** agent 记忆文件 hash 未变，**THEN** 跳过
- **WHEN** agent 记忆文件 hash 变化，**THEN** 镜像到 KB memory/ 层
- **WHEN** 内容指纹已存在，**THEN** 去重跳过

---

### 3.19 memory_sync.py — 记忆同步

#### FR-3.19.1: 信号密度 signal_density
- **WHEN** body 含信号词/命令前缀的行比例高，**THEN** 返回较高密度
- **WHEN** body 无信号词，**THEN** 返回 0.0

#### FR-3.19.2: slug 生成 slug
- **WHEN** 输入含特殊字符的名称，**THEN** 替换为连字符
- **WHEN** 输入为空或全特殊字符，**THEN** 返回 "memory-note"

#### FR-3.19.3: 五问门禁 grade
- **WHEN** 密度 < 阈值，**THEN** 不通过
- **WHEN** 长度 < min_chars，**THEN** 不通过
- **WHEN** 与 KB 顶 sims ≥ DUP_SIM，**THEN** 不通过（重复）
- **WHEN** 与 KB 顶 sims ≥ NOVEL_SIM，**THEN** 不通过（已覆盖）
- **WHEN** 软门 ①②③ 未通过，**THEN** 标记 review_needed 但不阻断

---

### 3.20 memory_ingest_json.py — JSON 记忆源 adapter

#### FR-3.20.1: JSON 摄取 ingest
- **WHEN** JSON 含 tables.records，**THEN** 逐条处理记录
- **WHEN** 记录已处理（seen），**THEN** 跳过
- **WHEN** 记录 content 为空，**THEN** 跳过
- **WHEN** dry_run=True，**THEN** 不落盘

---

### 3.21 memory_ingest_sqlite.py — SQLite 记忆源 adapter

#### FR-3.21.1: turn 提取 _extract_turns
- **WHEN** SQLite 含 thread_items 表，**THEN** 按 (thread_id, turn_id) 聚合 user/reply/reasoning
- **WHEN** item_type 为 userMessage，**THEN** 提取 content.text 为 user
- **WHEN** item_type 为 agentMessage，**THEN** 提取 text 为 reply

#### FR-3.21.2: SQLite 摄取 ingest
- **WHEN** data_dir 含 .sqlite 文件，**THEN** 逐文件逐 turn 处理
- **WHEN** turn 已处理（seen），**THEN** 跳过
- **WHEN** user 或 reply 为空，**THEN** 跳过

---

### 3.22 agent_registry.py — 多 agent 摄取注册表

#### FR-3.22.1: agent 探测 detect_agents
- **WHEN** 调用 detect_agents，**THEN** 返回 4 种 agent 的探测结果列表
- **WHEN** OpenClaw CLI 存在或 workspace 找到，**THEN** found=True
- **WHEN** DSH memory.json 存在，**THEN** found=True
- **WHEN** Codex .sqlite 文件存在，**THEN** found=True

#### FR-3.22.2: 注册表读写 load_registry / save_registry
- **WHEN** 注册表文件存在，**THEN** 返回解析后的 dict
- **WHEN** 注册表文件不存在，**THEN** 返回 `{"version": 1, "agents": {}}`
- **WHEN** 保存注册表，**THEN** 写入 JSON 格式

#### FR-3.22.3: agent 添加 cmd_add
- **WHEN** agent 已注册，**THEN** 返回错误码 1
- **WHEN** agent 未注册，**THEN** 添加到注册表并返回 0

---

### 3.23 ingest_chat.py — 对话记录摄入

#### FR-3.23.1: 平台探测 _detect_platform
- **WHEN** 文件名含 "wechat"/"wework"/"企业微信"，**THEN** 返回 "wechat_work"
- **WHEN** 文件名含 "dingtalk"/"钉钉"，**THEN** 返回 "dingtalk"
- **WHEN** 文件名含 "feishu"/"lark"/"飞书"，**THEN** 返回 "feishu"
- **WHEN** 文件名含 "slack"，**THEN** 返回 "slack"
- **WHEN** 扩展名为 .csv，**THEN** 兜底返回 "wechat_work"
- **WHEN** 扩展名为 .json，**THEN** 兜底返回 "slack"

#### FR-3.23.2: 对话分段 _segment
- **WHEN** 相邻消息间隔 > 30 分钟，**THEN** 切分为不同段
- **WHEN** 单段总字符 > 5000，**THEN** 二次切分

#### FR-3.23.3: 聊天解析主入口 parse_chat
- **WHEN** 指定 platform，**THEN** 使用指定适配器
- **WHEN** 未指定 platform，**THEN** 自动探测
- **WHEN** 适配器解析失败，**THEN** 回退到通用适配器

---

### 3.24 ingest_convert.py — 格式转换

#### FR-3.24.1: 格式路由 convert
- **WHEN** 文件不存在，**THEN** 返回 `success=False, convert_needed=True`
- **WHEN** 未知格式，**THEN** 返回 `success=False, convert_needed=True`
- **WHEN** 已知格式且转换成功，**THEN** 返回 `success=True, markdown=...`
- **WHEN** 外部工具未安装，**THEN** 返回 `success=False, convert_needed=True`

#### FR-3.24.2: JSON schema 识别 detect_schema
- **WHEN** JSON 含 tables.records，**THEN** 返回 "dsh"
- **WHEN** JSON 含 thread_items，**THEN** 返回 "codex"
- **WHEN** JSON 顶层数组或嵌套数组，**THEN** 返回 "nested_array"
- **WHEN** 其他 JSON，**THEN** 返回 "generic"

#### FR-3.24.3: 镜像保真存储 _save_mirror
- **WHEN** 保存镜像，**THEN** 原样复制文件到镜像目录
- **WHEN** 同名冲突，**THEN** 加数字后缀

---

### 3.25 user_manager.py — 多用户管理

#### FR-3.25.1: 用户添加 cmd_add
- **WHEN** 用户已存在，**THEN** 返回错误码 1
- **WHEN** 角色不在 ROLES 内，**THEN** 返回错误码 1
- **WHEN** 用户不存在且角色合法，**THEN** 添加用户并创建收件箱目录

#### FR-3.25.2: 用户列表 cmd_list
- **WHEN** 无用户，**THEN** 输出"（无用户）"
- **WHEN** 有用户，**THEN** 输出用户名/角色/显示名/权限数/收件箱

#### FR-3.25.3: 用户删除 cmd_remove
- **WHEN** 用户不存在，**THEN** 返回错误码 1
- **WHEN** 用户为 "default"，**THEN** 拒绝删除
- **WHEN** 用户存在且非 default，**THEN** 删除并返回 0

#### FR-3.25.4: 权限检查 cmd_check_perm
- **WHEN** 用户角色含指定权限，**THEN** 返回 0
- **WHEN** 用户角色不含指定权限，**THEN** 返回 1

---

### 3.26 create_vault.py — 安装引擎

#### FR-3.26.1: 安装主流程 main
- **WHEN** 目标目录已存在且有内容且未 --force，**THEN** 拒绝覆盖并返回 1
- **WHEN** 目标目录不存在或为空，**THEN** 创建并拷贝物料
- **WHEN** 拷贝完成，**THEN** 初始化运行时状态文件和向量索引目录
- **WHEN** demo=True，**THEN** 跑一轮成长引擎 demo
- **WHEN** init_git=True 且 git 可用，**THEN** git init + 首次提交

---

## 4. 非功能需求

### 4.1 测试框架
- **WHEN** 运行测试，**THEN** 使用 pytest 作为测试框架
- **WHEN** pytest 不可用，**THEN** 可回退到 unittest（标准库）

### 4.2 测试覆盖率
- **WHEN** 测试完成，**THEN** 核心模块（kb_common/rag/validate/classify）覆盖率 ≥ 80%
- **WHEN** 测试完成，**THEN** 其他模块覆盖率 ≥ 60%

### 4.3 测试执行时间
- **WHEN** 运行全部单元测试，**THEN** 执行时间 < 30 秒
- **WHEN** 运行全部集成测试，**THEN** 执行时间 < 60 秒
- **WHEN** 运行端到端测试，**THEN** 执行时间 < 120 秒

### 4.4 测试隔离性
- **WHEN** 测试执行，**THEN** 不修改原项目文件
- **WHEN** 测试需要文件系统操作，**THEN** 使用 tmp_path / tmpdir fixture
- **WHEN** 测试需要 git 仓库，**THEN** 在临时目录初始化
- **WHEN** 测试需要配置文件，**THEN** 使用临时配置不依赖项目实际配置

### 4.5 零依赖约束
- **WHEN** 运行测试，**THEN** 不安装任何 pip 包（pytest 除外）
- **WHEN** 测试涉及外部工具（pdftotext/pandoc 等），**THEN** 使用 skip 跳过

---

## 5. 验收标准

### 5.1 测试案例完整性
- **WHEN** 测试套件完成，**THEN** 覆盖所有 25 个模块 + create_vault.py 的核心函数
- **WHEN** 测试套件完成，**THEN** 每个功能需求（FR-3.x.x.x）至少有 1 个测试案例

### 5.2 测试可执行性
- **WHEN** 执行 `pytest tests/`，**THEN** 所有测试通过
- **WHEN** 执行测试，**THEN** 不依赖外部 Embedding 服务
- **WHEN** 执行测试，**THEN** 不依赖网络连接

### 5.3 测试独立性
- **WHEN** 单独执行任一模块的测试，**THEN** 不依赖其他模块测试先执行
- **WHEN** 测试失败，**THEN** 不影响其他测试执行

### 5.4 测试可维护性
- **WHEN** 测试案例使用 fixtures 共享测试数据，**THEN** 避免重复代码
- **WHEN** 测试案例命名，**THEN** 遵循 `test_<功能>_<条件>_<预期>` 命名规范
