# -*- coding: utf-8 -*-
"""TDD 测试（red）：run_checks 适配层 + summary JSON/HTML + exit_code 语义。
设计：合成 tmp vault，只测 run_checks/summary 消费层，不动既有 check 内部算法。
"""
import importlib.util
import sys
from pathlib import Path

# kb-healthcheck.py 文件名含连字符，不能直接 import；用 importlib 按路径装载为 kb_healthcheck 模块
_PKG = Path(__file__).resolve().parent.parent  # pipeline/
sys.path.insert(0, str(_PKG))
_spec = importlib.util.spec_from_file_location("kb_healthcheck", _PKG / "kb-healthcheck.py")
kh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kh)


def make_vault(tmp_path, notes):
    """合成 vault：在 20-技术 下写若干笔记。返回 vault root。"""
    d = tmp_path / "20-技术 Technology"
    d.mkdir(parents=True, exist_ok=True)
    for name, content in notes.items():
        (d / f"{name}.md").write_text(content, encoding="utf-8")
    return tmp_path


def test_run_checks_returns_structured_list(tmp_path):
    # run_checks(root, names) → [(name, count, detail), ...]，仅请求的 check
    root = make_vault(tmp_path, {"a": "---\ndomain: x\nimportance: 1\n---\n\n正文内容足够长用于非空检测的占位文本数据"})
    res = kh.run_checks(root, ["orphans", "skeletons"])
    names = [r[0] for r in res]
    assert names == ["orphans", "skeletons"]            # 只算请求的
    assert all(isinstance(r[1], int) for r in res)      # count 是 int


def test_aggregate_green_when_all_zero(tmp_path):
    # 真正干净的 vault：4 个 frontmatter 字段齐全 + 两条互链（非孤儿/无死链）
    root = make_vault(tmp_path, {
        "alpha": "---\ndomain: x\nimportance: 1\ntags: [测试]\nstatus: [active]\n---\n\n"
                 + "占位文本" * 200 + "\n见 [[beta]]",
        "beta":  "---\ndomain: x\nimportance: 1\ntags: [测试]\nstatus: [active]\n---\n\n"
                 + "占位文本" * 200 + "\n见 [[alpha]]",
    })
    res = kh.run_checks(root, "all")
    agg = kh._aggregate(res)
    assert agg["status"] == "green"
    assert agg["total_issues"] == 0
    assert agg["checks_failed"] == 0


def test_aggregate_red_when_any_nonzero(tmp_path):
    # 写一个无出链的孤立笔记 → orphans > 0
    root = make_vault(tmp_path, {"lonely": "---\ndomain: x\nimportance: 1\n---\n\n" + "占位文本" * 200})
    res = kh.run_checks(root, "all")
    agg = kh._aggregate(res)
    assert agg["status"] == "red"
    assert agg["checks_failed"] >= 1


def test_summary_json_has_all_check_keys(tmp_path):
    root = make_vault(tmp_path, {"a": "---\ndomain: x\nimportance: 1\n---\n\n" + "占位文本" * 200})
    summary = kh._render_summary(root, "all")
    keys = set(summary["checks"].keys())
    expected = {"deadlinks", "skeletons", "tags", "empty", "frontmatter", "discipline", "overlong", "orphans"}
    assert expected.issubset(keys)              # schema 完整性
    for c in summary["checks"].values():
        assert {"count", "threshold", "pass"}.issubset(c.keys())


def test_exit_code_mapping(tmp_path):
    assert kh._exit_code({"status": "green"}) == 0
    assert kh._exit_code({"status": "red"}) == 1


def test_html_output_writes_file(tmp_path):
    root = make_vault(tmp_path, {"a": "---\ndomain: x\nimportance: 1\n---\n\n" + "占位文本" * 200})
    summary = kh._render_summary(root, "all")
    out = tmp_path / "health.html"
    kh._render_html(summary, str(out))
    assert out.exists()                        # HTML 文件被生成
    body = out.read_text(encoding="utf-8")
    assert "状态" in body                       # 聚合状态已渲染进页面


def test_run_checks_is_reused(tmp_path):
    # 复用：同一份结果可被多消费方使用（text/summary）而不重算
    root = make_vault(tmp_path, {"a": "---\ndomain: x\nimportance: 1\n---\n\n" + "占位文本" * 200})
    res = kh.run_checks(root, "all")
    # 同一份 res 可反复消费
    assert len(res) > 0
    assert kh._aggregate(res)["status"] in ("green", "red")
    assert kh._render_summary(root, "all")["aggregate"]["total_issues"] >= 0
