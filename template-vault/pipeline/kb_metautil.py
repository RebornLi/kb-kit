"""kb_metautil — 领域元数据工具（从 kb_common.py 拆分，P1-1）。

提供笔记层级判定（kb_layer_of）、路径关系（parent_of / root_of）、新鲜度/陈旧度
（freshness / is_stale）、可检索状态（retrievable_status）、索引排除判定
（index_excluded / is_index_stub / is_pointer_page）、谱系记录（record_lineage）、
别名（add_alias_text）等 vault 领域语义函数。

命名为 kb_metautil 以与现有研究层 kb_meta.py（L3 元调参）区分。

依赖 kb_io 的 load_meta / FM 常量。
"""
import re
import os
import json
from pathlib import Path
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple, Union

from kb_io import load_meta, FM


# ── 分块族：根文档解析（父子块 / 同族排除 共用）──────────────────
_CHUNK_SUFFIX = re.compile(r"-(?:p|c)\d+$")

def parent_of(rel: str, fm: dict, root: Optional[Union[str, Path]] = None) -> str:
    """块的直接父文档（chunk_of 优先，文件名逐层兜底）；非块返回空串。"""
    base = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
    co = str((fm or {}).get("chunk_of", "") or "").strip()
    if co:
        cand = co if co.endswith(".md") else co + ".md"
        d = str(Path(rel).parent)
        p1 = f"{d}/{cand}" if d != "." else cand
        if (base / p1).exists():
            return p1
        if (base / cand).exists():
            return cand
    cur = Path(rel)
    for _ in range(8):
        if not _CHUNK_SUFFIX.search(cur.stem):
            return ""
        nxt = cur.with_name(_CHUNK_SUFFIX.sub("", cur.stem) + ".md")
        if (base / str(nxt)).exists():
            return str(nxt)
        cur = nxt
    return ""

def root_of(rel: str, fm: dict, root: Optional[Union[str, Path]] = None) -> str:
    """沿 parent 链回溯到最顶层文档（无父则返回自身）。"""
    base = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
    cur, cur_fm, seen = rel, fm or {}, set()
    for _ in range(8):
        if cur in seen:
            break
        seen.add(cur)
        par = parent_of(cur, cur_fm, base)
        if not par or par == cur:
            break
        try:
            cur_fm = load_meta(base / par)[0]
        except Exception:
            break
        cur = par
    return cur

# ── 索引纳入判定（raw/来源归档不参与检索，防噪声与超长污染）──────
def is_source_note(fm) -> bool:
    """是否为「原始来源」笔记（如 raw/ 抓取物）：`is_source: true` 或 `kind: source`。"""
    v = fm.get("is_source")
    if v is True or str(v).strip().lower() in ("true", "yes", "1"):
        return True
    return str(fm.get("kind", "")).strip().lower() == "source"

# ── 来源权威 + 新鲜度（生命周期治理）────────────────────────
RETRIEVABLE_STATUSES = {"active", "stable", "legacy"}  # 可检索状态（draft/archived 除外）

DEFAULT_STALE_DAYS = 180                      # updated/created 超过此天数视为陈旧

def retrievable_status(fm) -> bool:
    """来源权威：仅 active/stable 可检索；无 status 宽松放行（兼容旧笔记）。"""
    st = str(fm.get("status", "") or "").strip().lower()
    return True if not st else st in RETRIEVABLE_STATUSES

def _parse_day(v):
    s = str(v or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None

def freshness(fm, today=None) -> dict:
    """新鲜度：内容年龄 + review_after 到期。返回 {age_days, review_after, review_due, stale}。"""
    today = today or date.today()
    upd = _parse_day(fm.get("updated")) or _parse_day(fm.get("created"))
    age = (today - upd).days if upd else None
    ra = _parse_day(fm.get("review_after"))
    review_due = bool(ra and ra <= today)
    stale = review_due or (age is not None and age > DEFAULT_STALE_DAYS)
    return {"age_days": age, "review_after": str(fm.get("review_after") or ""),
            "review_due": review_due, "stale": stale}

def is_stale(fm, today=None) -> bool:
    """笔记是否陈旧（review_after 到期，或 updated/created 超过 DEFAULT_STALE_DAYS）。"""
    return freshness(fm, today)["stale"]

def index_excluded(fm) -> bool:
    """是否应从 RAG 索引排除：归档(retire)、显式 kb_index:false、非可检索状态、
    原始来源(source)、分块索引页/指针空壳（防空壳顶替真内容）。"""
    if fm.get("kb_action") == "retire":
        return True
    if str(fm.get("kb_index", "")).strip().lower() in ("false", "no", "0"):
        return True
    if not retrievable_status(fm):
        return True
    if is_source_note(fm):
        return True
    # P0：分块索引页（父页被改写成"指向子块的目录"）不入索引——
    #   它们与子块文本高度同构、TF-IDF 分数几乎相同，会把真内容顶下去。
    return is_index_stub(fm)

# ── 空壳/指针页识别（P0：检索降噪 + 防二次分块）─────────────────
STUB_MARKERS = ("{name} · 块{i}", "已按 §3.3 颗粒度分块为")

# 指标题/表头/链接行：说明这一行只是"指向别处"，不承载知识
_POINTER_LINE = re.compile(
    r"^\s*(?:"
    r"#{1,6}\s*\S{0,80}"                       # 孤单标题
    r"|[-*>]\s*\[\[[^\]]+\]\]"                 # 列表 + wikilink
    r"|[-*>]\s*\S{0,80}→\s*\S+"                # 列表 + 箭头指针
    r"|\[\[[^\]]+\]\]"                         # 纯 wikilink
    r"|\S{0,80}→\s*\S+\.md"                    # A → B.md
    r"|\|.*\|"                                 # 表格行
    r"|---+"                                   # 表格分隔
    r")\s*$"
)

def is_index_stub(fm, body: str = "") -> bool:
    """是否「分块索引页 / 目录页」（无正文、只指向子块）：
    显式 `is_chunk_index: true`，或 `chunk_of` 存在但无 `chunk: N`（=父页被改写成目录）。

    注意：子块（`chunk: 1..N`）**不是** stub，它们携带正文。
    """
    v = fm.get("is_chunk_index")
    if v is True or str(v).strip().lower() in ("true", "yes", "1"):
        return True
    if fm.get("chunk_of") and not fm.get("chunk"):
        return True
    # 级联分块的"中间层目录页"：chunk_of 存在、正文却极短（正文都指向别的块）
    if fm.get("chunk_of") and body and len(str(body).replace("\n", "").strip()) < 200:
        return True
    if str(fm.get("kb_layer", "")).strip().lower() == "index":
        return True
    return False

def is_pointer_page(body: str, rel: str = "", min_lines: int = 3) -> bool:
    """正文是否为「空壳/指针页」：几乎全是链接/标题/表格行，或带分块模板残留。

    判据（启发式，供 kb clean / kb curate 提示用，不做物理删除）：
      · 正文含分块模板残留标记（如未插值的 `## {name} · 块{i}`）
      · 去空行后行数 < min_lines，且每行都是指针型
      · 指针型行占比 >= 0.6
    文件名以 `-index.md` 结尾的直接视为指针页。
    """
    if Path(str(rel)).name.endswith("-index.md"):
        return True
    lines = [l.strip() for l in str(body or "").splitlines() if l.strip()]
    if not lines:
        return True
    for mk in STUB_MARKERS:
        if mk in "\n".join(lines[:6]):  # 只在开头附近命中，避免正文引用该句式时误判
            return True
    ptr = sum(1 for l in lines if _POINTER_LINE.match(l))
    if len(lines) < min_lines:
        return ptr == len(lines)
    return ptr / len(lines) >= 0.6

# ── 知识分层（kb_layer）：排序权重 + 检索降级 ─────────────────
#   canon  正典层（agent 结晶后的干净知识）        → 排序提升
#   index  分块索引页/目录页                       → 不入索引
#   raw    原记忆/网页抓取等证据层                 → 保留可调用，默认排序降级
STUB_WORDS = ("未过滤", "原始记录")

LAYER_MULTIPLIER = {
    "canon": 1.25,
    "": 1.00,
    "page": 1.00,
    "raw": 0.72,
    "index": 0.60,
    # ⚠️ "noise" 这一项**按契约永远不可达**，别据此以为噪声笔记"降权可检索"：
    #   `kb_schema` 强制 noise 层必须带 `kb_index: false`（curate 判定噪声时就写入
    #   `{"kb_layer": "noise", "kb_index": "false"}`），而 `index_excluded` 见到
    #   `kb_index:false` 直接不入索引 —— 于是 rag.py 里 `layer in ("index","noise")`
    #   的降权分支对 noise 永远不会触发。保留仅为防御性默认值（万一有人手写
    #   `kb_layer: noise` 却忘了 `kb_index: false`）。
    "noise": 0.60,
}

DEFAULT_LAYER = "page"

def kb_layer_of(fm, rel: str = "", body: str = "") -> str:
    """笔记所属知识层：显式 `kb_layer` 优先，否则按 is_source / index stub 推断。"""
    v = str(fm.get("kb_layer", "") or "").strip().lower()
    if v:
        return v
    if is_source_note(fm):
        return "raw"
    if is_index_stub(fm, body):
        return "index"
    if rel and is_raw_path(rel):
        return "raw"
    return DEFAULT_LAYER

def layer_multiplier(fm, rel: str = "") -> float:
    """按知识层返回排序权重乘数（canon 提升 / raw、空壳降级）。"""
    return float(LAYER_MULTIPLIER.get(kb_layer_of(fm, rel), 1.0))

def is_raw_path(rel) -> bool:
    """rel 是否位于 raw/ 证据层内（只看顶层目录，避免 `a/raw/b` 误判）。"""
    try:
        parts = [str(x).lower() for x in Path(str(rel)).parts]
        return bool(parts) and parts[0] == "raw"
    except (TypeError, ValueError, AttributeError):
        return False

# 运行期生成的报告文件（非知识内容）：不入检索 / 不参与补链 / 不再分块
GENERATED_REPORTS = {
    "intake_triage.md", "feedback_hits.md", "recall_deck.md",
    "recall_schedule.md", "link_suggestions.md", "_INDEX.md",
    "clean_suggestions.md", "_MOC.md", "_graph.md", "_graph_kg.md",
}

def is_generated_report(rel) -> bool:
    """rel 是否为运行期生成的报告产物：按文件名判定，或位于 _curate/（审计/提案目录）。

    注：`_MOC.md` / `_INDEX.md` 属于生成产物（语义不变）。
    但它们是**知识导航**、最容易积累悬空链接，所以**死链巡检单独把它们纳入**
    （见 kb-healthcheck 的 NAV_FILES），而不是改这里的语义。
    """
    if Path(str(rel)).name in GENERATED_REPORTS:
        return True
    # 结晶提案（_curate/b*.md）与审计产物（_audit/*.md 含 log.md）：人读产物，不是知识笔记
    _parts = [str(x) for x in Path(str(rel)).parts[:-1]]
    if "_curate" in _parts or "_audit" in _parts:
        return True
    return False

# ── 归档生命周期：别名重定向 + lineage（合并/去重可回溯，旧链接不失效）──
def _alias_items(val: str):
    s = str(val or "").strip().strip("[]")
    return [t.strip().strip('"\'') for t in re.split(r"[,\s]+", s) if t.strip()]

def add_alias_text(text: str, alias: str):
    """在 frontmatter 的 aliases 中追加 alias（幂等）；无 frontmatter 返回 None。"""
    m = FM.search(text)
    if not m:
        return None
    end = FM.search(text, m.end())
    if not end:
        return None
    block = text[m.end():end.start()]
    body = text[end.end():]
    mm = re.search(r"^aliases:.*$", block, re.M)
    if mm:
        items = _alias_items(mm.group(0).split(":", 1)[1])
        if alias not in items:
            items.append(alias)
        block = re.sub(r"^aliases:.*$", "aliases: [" + ", ".join(items) + "]",
                       block, count=1, flags=re.M)
    else:
        block = f"aliases: [{alias}]\n" + block.lstrip("\n")
    return "---\n" + block + "\n---\n" + body

def record_lineage(root, src: str, dst: str, action: str) -> None:
    """记录合并/去重的血缘（from→to），写入 .kb/state/lineage.jsonl。"""
    try:
        p = Path(root) / ".kb" / "state" / "lineage.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "action": action, "from": src, "to": dst},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass

# ── P2: frontmatter 关系型字段（FR-3.3.1）──────────────────
RELATION_FIELDS = ("related_to", "prerequisite", "supersedes", "superseded_by")
