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


def index_freshness(root, payload=None):
    """索引相对笔记目录的新鲜度（P1-4：`kb query` 陈旧索引退出码为 0 的根因修复）。

    ## 问题

    索引文件存在、version 也对，但**笔记已经改了/增了/删了而没重建索引**。
    此时 `kb query` 照常返回 0 —— 调用方（脚本、Agent、cron）无法区分
    "查到了"与"拿的是过期答案"。实测：新增一篇高度相关的笔记后，
    `kb query` 搜不到它，退出码仍是 0。

    ## 数据来源

    索引里**本来就有** `doc_mtimes`（rel → mtime）与 `doc_hashes`（rel → sha256），
    只是查询路径从没用过。这里以 mtime 为主判据（便宜，一次 stat 都不用额外算），
    缺 mtime 的条目退回 hash 比对（内容变了但 mtime 被刻意还原的场景）。

    Returns: dict，字段见下；`stale=False` 不代表"内容一致"，只代表"没发现不一致"。
      stale     bool   是否有笔记比索引新 / 索引缺失的笔记 / 索引多出的笔记
      changed   list   内容与索引不一致的 rel（前 N 条）
      missing   list   磁盘上存在但索引里没有的 rel（新增未建索引）
      orphaned  list   索引里有但磁盘上已删除的 rel
      checked   int    实际比对过的笔记数
      reason    str    人读结论（"索引比笔记新" / "N 篇待重建"）
    """
    root = Path(root)
    out = {"stale": False, "changed": [], "missing": [], "orphaned": [],
           "checked": 0, "reason": ""}
    if payload is None:
        try:
            import rag
            idx = rag.index_file(root / "vector index")
            if idx is None:
                out["reason"] = "索引不存在"
                return out
            payload, _ver = rag.load_index(idx)
        except Exception:
            out["reason"] = "索引不可读"
            return out
    if not payload:
        out["reason"] = "索引为空"
        return out

    idx_mtimes = payload.get("doc_mtimes") or {}
    idx_hashes = payload.get("doc_hashes") or {}
    if not isinstance(idx_mtimes, dict):
        idx_mtimes = {}
    if not isinstance(idx_hashes, dict):
        idx_hashes = {}

    from kb_io import iter_notes, load_meta

    seen = set()
    for p in iter_notes(str(root)):
        try:
            rel = os.path.relpath(str(p), str(root))
        except (ValueError, OSError):
            continue
        rel = rel.replace(os.sep, "/")
        # 关键：必须套用**索引自己的收录规则**（两道闸都要），否则会把 raw/、
        # 报告产物、分块索引页等"本就不该进索引"的文件算成 missing ——
        # 实测那样会让 freshness 在健康库上长期误报（首轮 6 篇假阳性）。
        #   ① is_generated_report(rel)  —— 按文件名（运行期报告，无 frontmatter 标记）
        #   ② index_excluded(fm)        —— 按 frontmatter（retire/raw/索引页…）
        if is_generated_report(rel):
            continue
        try:
            fm, _body = load_meta(p)
            if index_excluded(fm):
                continue
        except Exception:
            continue
        seen.add(rel)
        out["checked"] += 1
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        old = idx_mtimes.get(rel)
        if old is not None:
            # 容差只允许**单向**：磁盘 mtime 比索引旧（old > mtime）时容忍 ~2s，
            # 覆盖"文件系统/复制把 mtime 抹成整秒"这类回拨。
            # 反过来（mtime > old，磁盘比索引新）必须**严格判定** ——
            # 曾用 abs(diff) > 1.0，实测"建索引后立即改笔记"差 0.06s 也被判一致，
            # 于是那次修改永远不进 changed，strict 模式漏报。
            if mtime > float(old) + 0.001 or float(old) - mtime > 2.0:
                out["changed"].append(rel)
            continue
        # 索引里没有该 rel 的 mtime 记录：用内容哈希兜底判定
        if rel in idx_hashes:
            try:
                import kb_text
                if kb_text.content_fingerprint(p.read_text(encoding="utf-8")) != idx_hashes[rel]:
                    out["changed"].append(rel)
            except Exception:
                pass
        else:
            out["missing"].append(rel)

    for rel in idx_mtimes:
        if rel not in seen:
            out["orphaned"].append(rel)

    out["stale"] = bool(out["changed"] or out["missing"] or out["orphaned"])
    if out["stale"]:
        n = len(out["changed"]) + len(out["missing"]) + len(out["orphaned"])
        out["reason"] = f"{n} 篇笔记与索引不一致（改 {len(out['changed'])} / "
        out["reason"] += f"新增 {len(out['missing'])} / 已删 {len(out['orphaned'])}）"
    else:
        out["reason"] = f"索引与 {out['checked']} 篇笔记一致"
    return out

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

def _segments(rel):
    """把相对路径切成小写路径段 —— **先归一分隔符**。

    为什么必须先 `\\` → `/`：`pathlib.PurePosixPath("a\\raw\\b.md")`
    把整串当成**一个文件名**，于是 `raw` 段永远匹配不到。Windows 传入的
    相对路径（`str(Path(...).relative_to(...))` 在 Windows 上返回反斜杠）
    会让下面所有"证据层/系统目录"判定**静默失效**——不报错，只是保护没了。
    macOS 上盘符是 `:`（HFS 传统路径），只在无 `/` 时才回归，避免误伤
    正常文件名里的冒号。
    """
    if rel is None:
        return []
    try:
        text = str(rel)
    except (TypeError, ValueError, AttributeError):
        return []
    if not text:
        return []
    if "\\" in text:
        text = text.replace("\\", "/")
    elif "/" not in text and len(text) > 2 and text[1] == ":":
        text = text[0] + "/" + text[2:].replace(":", "/")
    return [seg.lower() for seg in text.split("/") if seg not in ("", ".")]


def is_raw_path(rel) -> bool:
    """rel 是否位于 raw/ 证据层内 —— **全库唯一的判定实现**（P2-1 统一）。

    语义：**任意层级**出现 `raw` 目录段即算（`raw/a.md`、`x/raw/a.md`、
    `40-资源库 Resources/raw/a.md` 全部为 True）。

    ## 为什么从「只看顶层」改成「任意层」

    历史上这里有**四份实现、两套语义**，互相注释「与某某同约定」但实际相反：

      · kb_metautil.is_raw_path  —— `parts[0] == "raw"`（只看顶层）
      · feedback_loop.is_raw_rel —— `"raw" in parts[:-1]`（任意层）
      · kb_rsi.is_raw            —— `"raw" in parts[:-1]`（任意层）
      · _research/kb_engine.is_raw_rel —— 同任意层

    实测分歧（2026-10-22）：
        `20-技术 Technology/raw/a.md`  →  kb_metautil=False，其余三处=True

    后果是**保护范围不一致**：同一篇笔记，`kb clean` 认为它可以改写，
    `kb feedback` 却拒绝它加权 —— 取决于哪个模块先碰到它。

    选「任意层」而不是「顶层」的理由：`is_raw_path` 的 20 处调用**全是
    保护性判断**（"这篇不该被清洗/结晶/改标签/进索引"），保护范围应当
    **宁大勿小**。漏判一个嵌套 `raw/` 就会真的改写证据层原文，而误判一个
    正常目录只是让那篇笔记少被处理一次。代价不对称，取宽。

    ## 反例：确实需要「只在顶层」时

    用 `is_raw_root(rel)`（本文件同处定义）。目前只有"判断某笔记是否属于
    证据层顶层分区"这类展示性场景需要，写入守卫一律用本函数。
    """
    parts = _segments(rel)
    # parts[:-1]：文件名本身叫 raw.md 不算证据层
    return "raw" in parts[:-1]


def is_raw_root(rel) -> bool:
    """rel 是否在 **顶层** `raw/` 分区内（严格版）。

    仅用于需要区分"证据层顶层分区"与"别的目录里恰好叫 raw 的子目录"的
    展示/统计场景。**写入守卫不要用这个** —— 那是 `is_raw_path` 的职责。
    """
    parts = _segments(rel)
    return bool(parts) and parts[0] == "raw"

# 运行期生成的报告文件（非知识内容）：不入检索 / 不参与补链 / 不再分块
GENERATED_REPORTS = {
    "intake_triage.md", "feedback_hits.md", "recall_deck.md",
    "recall_schedule.md", "link_suggestions.md", "_INDEX.md",
    "clean_suggestions.md", "_MOC.md", "_graph.md", "_graph_kg.md",
}
# 图谱/报告产物会被**分批生成**：`_graph_kg.md`、`_graph_kg-c1.md`、`_graph_kg-c2.md`…
# 只按精确文件名匹配会漏掉带 `-cN` 后缀的那批（生产库实测踩到：
# `_graph_kg-c1.md` 被 check_skeletons 报成"骨架笔记"）。
_GENERATED_STEM_RE = re.compile(
    r"^_(?:graph|graph_kg|MOC|INDEX)(?:-c\d+)?$")


def is_generated_report(rel) -> bool:
    """rel 是否为运行期生成的报告产物：按文件名判定，或位于 _curate/（审计/提案目录）。

    注：`_MOC.md` / `_INDEX.md` 属于生成产物（语义不变）。
    但它们是**知识导航**、最容易积累悬空链接，所以**死链巡检单独把它们纳入**
    （见 kb-healthcheck 的 NAV_FILES），而不是改这里的语义。
    """
    p = Path(str(rel))
    if p.name in GENERATED_REPORTS:
        return True
    # 分批生成变体：`_graph_kg-c1.md` 等（stem 去掉 .md 后匹配）
    if _GENERATED_STEM_RE.match(p.stem):
        return True
    # 结晶提案（_curate/b*.md）与审计产物（_audit/*.md 含 log.md）：人读产物，不是知识笔记
    _parts = [str(x) for x in p.parts[:-1]]
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


# ── 日期解析（parse_date / parse_date_safe）──────────────────
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def parse_date(v: Optional[str]) -> Optional[datetime]:
    """解析 YYYY-MM-DD 格式日期字符串。

    Args:
        v: 日期字符串（如 "2024-01-15"），None 或空字符串返回 None

    Returns:
        datetime 对象，解析失败返回 None。

    Examples:
        >>> parse_date("2024-01-15")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> parse_date(None) is None
        True
        >>> parse_date("invalid") is None
        True
    """
    if not v:
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        # 尝试从字符串中提取日期（如 "2024-01-15T10:30:00"）
        m = _DATE_RE.search(str(v))
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, TypeError):
                return None
        return None


def parse_date_safe(
    v: Optional[str], default: Optional[datetime] = None
) -> datetime:
    """安全解析日期，失败返回 default。

    Args:
        v: 日期字符串
        default: 解析失败时的默认值（None 时使用 datetime(1970,1,1)）

    Returns:
        datetime 对象。

    Examples:
        >>> parse_date_safe("2024-01-15")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> parse_date_safe("invalid")
        datetime.datetime(1970, 1, 1, 0, 0)
    """
    result = parse_date(v)
    if result is not None:
        return result
    return default if default is not None else datetime(1970, 1, 1)


# ── 重要性解析（parse_importance）─────────────────────────────
_IMPORTANCE_RE = re.compile(r"[-+]?\d*\.?\d+")


def parse_importance(s: Any) -> float:
    """从 frontmatter importance 字段解析浮点值。

    支持纯数字字符串、带前缀的字符串（如 "P0"、"0.85"）、数字类型。

    Args:
        s: 重要性值（str / int / float / None）

    Returns:
        浮点值，解析失败返回 0.0。

    Examples:
        >>> parse_importance("0.85")
        0.85
        >>> parse_importance("P0")
        0.0
        >>> parse_importance(None)
        0.0
    """
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    m = _IMPORTANCE_RE.search(str(s))
    if not m:
        return 0.0
    try:
        return float(m.group())
    except (ValueError, TypeError):
        return 0.0
