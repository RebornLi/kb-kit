# evolution_log.py — RSI 统一演进日志（P2-2 基础设施）
#
# 把 RSI 四类事件追加到两处，做到"既有机器可读 jsonl，又有人类可读演进史"：
#   1. `.kb_evolution.jsonl`  机器可读、可 diff（gitignore，瞬态缓存）
#   2. `EVOLVED.md`           人类可读、按日期分组（**提交入库**，RSI 演进史）
#
# 事件类型用统一前缀标注，便于人眼扫读与机器过滤：
#   INGEST | QUERY | LINT | ENGINE | HEALTH | T1 | T2 | T3 | NOTE
#
# 用法：
#   from evolution_log import append
#   append(root, "INGEST", "喂入 12 篇 · 命中 29 笔记", detail="external=1 fresh=8")
#   append(root, "QUERY", "查询"..." → 提议存 1 条编译笔记")
#   append(root, "LINT", "矛盾检测 0 对", detail="只读交人工")
#   append(root, "T2", "加权 3 篇 · 提议 8 条")
import json
from datetime import datetime
from pathlib import Path

# 统一前缀：ingest/query/lint/engine 四类 + 细分 tier/health/note
EVENTS = ("INGEST", "QUERY", "LINT", "ENGINE", "HEALTH", "T1", "T2", "T3", "NOTE")
# 人读颜色前缀（同事件固定配色语义，仅文档）
_EVENT_LABEL = {
    "INGEST": "摄入记忆", "QUERY": "查询综合", "LINT": "质量lint",
    "ENGINE": "引擎整理", "HEALTH": "健康快照", "T1": "T1整理",
    "T2": "T2加权", "T3": "T3调参", "NOTE": "备注",
}
_HEADER = [
    "# 📚 RSI 演进日志（人可读）", "",
    "> 汇总 `ingest` / `query` / `lint` / `engine` 四类事件，统一前缀标注。",
    "> 机器可读 → `.kb_evolution.jsonl`。自动追加，勿手动编辑。",
]


def _root(root):
    return Path(root)


def pipeline_dir(root):
    return _root(root) / "pipeline"


def append(root, event, summary, *, detail=None, category="info"):
    """追加一条演进事件。event 必须是 EVENTS 之一。
    同时写机器可读 jsonl 与人类可读 EVOLVED.md；人类版本按日期分组。"""
    if event not in EVENTS:
        raise ValueError(f"未知 event 前缀: {event}（可用: {', '.join(EVENTS)}）")
    now = datetime.now()
    rec = {
        "ts": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "event": event,
        "category": category,
        "summary": summary,
        "detail": detail,
    }
    pdir = pipeline_dir(root)
    pdir.mkdir(parents=True, exist_ok=True)

    # 1) 机器可读（可 diff、可脚本过滤）
    with open(pdir / ".kb_evolution.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 2) 人类可读（按日期分组插入）
    _append_md(pdir / "EVOLVED.md", rec["date"], rec)
    return rec


def _append_md(path, date, rec):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    line = f"- `{rec['time']}` **`[{rec['event']}]`** {rec['summary']}"
    if rec.get("detail"):
        line += f"\n  - {rec['detail']}"

    # 找到最近的 `## <date>` 段（倒序匹配，命中则插在其后）
    idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("## " + date):
            idx = i
            break

    if idx == -1:
        # 新日期段：插在当前第一个段落之前（保留头部 # 标题）
        insert_at = 1 if (lines and lines[0].startswith("# ")) else 0
        seg = [f"", f"## {date}", ""] + [line]
        lines[insert_at:insert_at] = seg
    else:
        lines[idx + 1:idx + 1] = [line]

    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def tail(root, n=30):
    """读最近 n 条演进事件（机器可读），供 CLI/status 展示。"""
    p = pipeline_dir(root) / ".kb_evolution.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()[-n:]]


def render_md(root, limit=40):
    """把最近 limit 条渲染成人可读摘要（供 --status 或测试断言）。"""
    recs = tail(root, limit)
    if not recs:
        return "(暂无演进事件)"
    L = ["## 最近演进事件", ""]
    for r in recs:
        detail = f" — {r['detail']}" if r.get("detail") else ""
        L.append(f"- `{r['time']}` **`[{r['event']}]`{detail}: {r['summary']}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    # 自测：append 两条并回显
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    a = ap.parse_args()
    r = Path(a.root)
    append(r, "NOTE", "evolution_log 自测")
    append(r, "INGEST", "自测喂入", detail="external=1")
    print(render_md(r))
