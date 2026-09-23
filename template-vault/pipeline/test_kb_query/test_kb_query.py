# 单元测试：kb_query 召回 + 耐久性纯逻辑（kb-kit RSI 引擎首个自动化测试）。
# 范围：_tokenize/_vec/_cos/_slug（纯函数）、durability() 三门、search() 排序。
# 刻意避开 query()/apply()/_grounded()（涉人工在环+git+真实 vault）与 embedding 路径。
import math
import pytest

import kb_query as q


# ── fixtures ───────────────────────────────────────────────────
def _write_note(root, rel, body, domain="test"):
    """写一条编译笔记（非 raw/），返回 rel。"""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ndomain: {domain}\nkind: compiled\nkb_action: keep\n---\n\n{body}\n",
                 encoding="utf-8")
    return rel


@pytest.fixture
def empty_vault(tmp_path_factory):
    return tmp_path_factory.mktemp("empty")


@pytest.fixture
def vault_with_notes(tmp_path_factory):
    root = tmp_path_factory.mktemp("notes")
    _write_note(root, "reference/a.md",
                "数据库索引设计要遵守最左前缀原则避免全表扫描浪费资源")
    _write_note(root, "reference/b.md",
                "前端性能优化建议用懒加载和代码_splitting减少首屏bundle体积")
    _write_note(root, "reference/c.md",
                "安全工程威胁建模用STRIDE分类注入与权限提升风险")
    return root


# ── US1：字符向量契约 ──────────────────────────────────────────
class TestVectorContract:
    def test_tokenize_splitter(self):
        # 中文字各计一 token，英文按词；顺序为中文在前、英文在后
        assert q._tokenize("你好world 测试") == ["你", "好", "测", "试", "world"]

    def test_tokenize_empty_and_none(self):
        assert q._tokenize("") == []
        assert q._tokenize(None) == []

    def test_vec_self_cosine_is_one(self):
        a = q._vec("数据库索引设计最左前缀")
        assert math.isclose(q._cos(a, a), 1.0, rel_tol=1e-9)

    def test_vec_unrelated_is_zero(self):
        # 取无交集汉字集（天干 vs 地支），确保零共享 token → 余弦 = 0
        a = q._vec("甲乙丙丁戊己庚辛壬癸")
        b = q._vec("子丑寅卯辰巳午未申酉")
        assert math.isclose(q._cos(a, b), 0.0, abs_tol=1e-9)

    def test_vec_is_l2_not_l1(self):
        # 多 token 文本的自余弦：L2 归一 = 1.0；若错用 sum=1 词频归一(L1) 则 = 1/n < 1。
        # 验到 1.0 即证明是 L2（这正是"相同文本余弦≈1"契约，旧坑是误用 L1 稀释至 ~0）。
        multi = q._vec("数据库索引设计最左前缀")
        assert math.isclose(q._cos(multi, multi), 1.0, rel_tol=1e-9)


# ── _slug ──────────────────────────────────────────────────────
class TestSlug:
    def test_slug_truncates_to_30(self):
        assert len(q._slug("用户查询" * 20)) <= 30

    def test_slug_fallback_when_empty(self):
        assert q._slug("") == "query"
        assert q._slug("   ") == "query"


# ── US2/US3/US4/US5：durability 三门 ──────────────────────────
class TestDurability:
    # US2：长度门，边界 >=60 通过，59 拒绝（唯一变量 = 长度）。
    @pytest.mark.parametrize("n,should_pass", [(59, False), (60, True)])
    def test_length_gate_boundary(self, n, should_pass, empty_vault):
        # n 个互不相同的中文字 → chars=n, ttr=1.0, novelty=0
        text = "".join(chr(0x4E00 + i) for i in range(n))
        score, ok, reasons = q.durability(text, str(empty_vault))
        assert ok == should_pass
        if not should_pass:
            assert any("过短" in r for r in reasons)

    # US3：多样性门，够长但词汇高度重复 → 拒绝（唯一变量 = TTR）。
    def test_ttr_gate_rejects_repetition(self, empty_vault):
        # 60 字，仅 3 类 → ttr=0.05 < 0.45；长度/新异度均达标，单独暴露 TTR 门
        text = "甲" * 30 + "乙" * 15 + "丙" * 15
        assert len(text) == 60
        score, ok, reasons = q.durability(text, str(empty_vault))
        assert ok is False
        assert any("词汇重复" in r for r in reasons)
        assert score["ttr"] == pytest.approx(0.05, abs=1e-6)

    # US4：新异度门，答案与某篇编译笔记近乎全同 → 拒绝（唯一变量 = novelty）。
    def test_novelty_gate_rejects_restatement(self, vault_with_notes):
        note_body = ("数据库索引设计必须遵守最左前缀规则才能利用覆盖索引避免全表扫描浪费io资源"
                     * 2)  # 够长、词汇多样
        _write_note(vault_with_notes, "reference/dup.md", note_body)
        score, ok, reasons = q.durability(note_body, str(vault_with_notes))
        assert ok is False
        assert any("复述" in r for r in reasons)
        assert score["novelty"] >= q.NOVELTY_MIN_SIM - 1e-9

    # US5：综合通过 —— 够长、词汇多样、与现有笔记不高度相似（无注场景）。
    def test_durability_pass_when_fresh(self, empty_vault):
        text = ("可观测系统必须覆盖延迟流量错误饱和度四条黄金信号并配套错误预算机制"
                "当误差预算耗尽时应当主动触发告警提醒团队关注可靠性而非单纯监控指标数值波动")
        assert len(text) == 70 >= 60
        score, ok, reasons = q.durability(text, str(empty_vault))
        assert ok is True, reasons
        assert score["pass"] is True

    def test_durability_reports_all_three_failures(self, empty_vault):
        # 又短又重复 → 同时触发长度 + 词汇重复两门
        text = "短重复" * 5  # 15 字，仅 2 类
        score, ok, reasons = q.durability(text, str(empty_vault))
        assert ok is False
        assert len([r for r in reasons if "过短" in r or "词汇重复" in r]) == 2


# ── US6：search 召回排序 ───────────────────────────────────────
class TestSearch:
    def test_search_returns_topk_descending_and_positive(self, vault_with_notes):
        hits = q.search(vault_with_notes, "数据库索引最左前缀", topk=2)
        assert len(hits) == 2
        scores = [s for s, _ in hits]
        assert scores == sorted(scores, reverse=True)       # 降序
        assert all(s > 0 for s in scores)                   # 仅 s>0

    def test_search_none_when_no_match(self, empty_vault):
        assert q.search(empty_vault, "完全不相关的内容xyz") == []

    def test_search_respects_topk_cap(self, vault_with_notes):
        # 3 篇都相关 → topk=1 只返回 1 篇
        assert len(q.search(vault_with_notes, "数据库", topk=1)) == 1
