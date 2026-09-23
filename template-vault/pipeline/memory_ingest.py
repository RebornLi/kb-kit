#!/usr/bin/env python3
# ============================================================
# memory_ingest.py —— 记忆写入侧（agent 实时记忆流 → 知识库 ETL）
#   mirror      : agent memory/*.md 镜像进 KB memory/ 层(喂 memory_sync 晋升), hash 幂等
#   sync        : 带 kb_target 标记的 agent 日报→按 kb_action(update/append/new)写进 KB 目标路径+去重
#   mirror-core : agent 核心文件(SOUL/USER/MEMORY/AGENTS/TOOLS)变更→KB reference/ 跟踪参考(hash 去重)
#   extract     : 抽取 MEMORY.md 结构条目(经验教训/关键决策)+ USER.md 偏好→结构化 KB 笔记(五问+去重)
#   共性: 只 git add 本次新建/被改的 KB 文件(绝不 git add -A sweep Obsidian);
#         状态记 .memory_ingest_state.json(不入库,与 feedback/recall state 同约定)
#   用法:
#     python3 pipeline/memory_ingest.py mirror   [--root R] [--agent-src S]
#     python3 pipeline/memory_ingest.py sync     [--root R] [--agent-src S]
#     python3 pipeline/memory_ingest.py mirror-core [--root R] [--agent-root P]
#     python3 pipeline/memory_ingest.py extract  [--root R] [--agent-root P] [--semantic]
# ============================================================
import argparse, os, re, sys, json, hashlib, subprocess, datetime
from pathlib import Path
from collections import Counter
from kb_common import ROOT_DEFAULT, get_dedup_thresholds
import rag
from memory_sync import signal_density, slug, NOVEL_SIM, DUP_SIM, load_index

AGENT_SRC_DEFAULT = os.environ.get("AGENT_MEMORY", "")
AGENT_ROOT_DEFAULT = os.environ.get("AGENT_ROOT", "")
MIN_CHARS_DEFAULT = 80
# SYNC_SIM / REPLACE_SIM / MERGE_SIM 已改为动态加载（Task 3）
# 保留模块级常量作为默认值，供 sync() 内部回退
SYNC_SIM = 0.85     # 默认值（实际使用时通过 get_dedup_thresholds 动态获取）
REPLACE_SIM = 0.85  # 默认值
MERGE_SIM = 0.70    # 默认值
STATE_FILE = ".memory_ingest_state.json"


def _text_sim(a: str, b: str, method: str = "tfidf") -> float:
    """两段文本的相似度，支持 tfidf / char_vec / jaccard 三种模式。

    Args:
        a, b: 待比较的文本
        method: "tfidf"（默认，token 频次余弦）/ "char_vec"（字符向量余弦）/ "jaccard"（token 集合交并比）

    Returns:
        0.0-1.0 的相似度值。空文本返回 0.0。
    """
    if method == "jaccard":
        ta, tb = set(rag.tokenize(a)), set(rag.tokenize(b))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)
    from kb_common import char_vec, cos, tokenize, norm
    if method == "char_vec":
        return max(0.0, cos(char_vec(a), char_vec(b)))
    # tfidf 模式（默认）：token 频次向量归一化后余弦
    ta, tb = Counter(tokenize(a)), Counter(tokenize(b))
    if not ta or not tb:
        return 0.0
    return max(0.0, cos(norm(dict(ta)), norm(dict(tb))))


def _audit_after_write(body, label):
    """写入后审计：信号密度<0.15 或 字符<80 → 警告（不阻断，因已写入）。"""
    dens = signal_density(body)
    nchars = len(body.replace("\n", ""))
    issues = []
    if dens < 0.15:
        issues.append(f"信号密度 {dens*100:.0f}%(<15%)")
    if nchars < 80:
        issues.append(f"字符 {nchars}(<80)")
    if issues:
        print(f"  ⚠️ 写入后审计 [{label}]: {' / '.join(issues)}")
    return issues


def _audit_log(root, action, target, detail=""):
    """记录 ingest 写操作审计日志到 logs/ingest_audit.log（只追加，不读不改 KB 内容）。
    用于追踪 ingest-agent 的所有写操作，便于事后审计核查。"""
    log = Path(root) / "logs" / "ingest_audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat()
    with log.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {action} → {target} {detail}\n")

DOMAIN_BUCKET = {"运维": "20-技术 Technology", "安全": "20-技术 Technology", "开发": "20-技术 Technology",
                 "数据": "20-技术 Technology", "产品": "20-技术 Technology", "管理": "10-项目 Projects", "综合": "40-资源库 Resources"}
CORE_FILES = ["MEMORY.md", "USER.md", "SOUL.md", "AGENTS.md", "TOOLS.md", "IDENTITY.md"]

# 命名空间归一表: 摄入路径里的简写 / 带与不带空格变体 → 标准 PARA 命名空间
# 防重根因: 此前 DOMAIN_BUCKET 只输出 "20-技术" / "10-项目"(无英文后缀),
# sync()/extract() 用 Path(root)/target 原样落盘, 凭空造出与标准域并行的游离目录。
_BUCKET_ALIASES = {
    "10-项目": "10-项目 Projects", "20-技术": "20-技术 Technology",
    "10-项目Projects": "10-项目 Projects", "20-技术Technology": "20-技术 Technology",
    "10-项目 Projects": "10-项目 Projects", "20-技术 Technology": "20-技术 Technology",
}


def normalize_bucket(target):
    """归一 bucket/kb_target: 无论来源写简写还是带/不带空格, 都映射到标准 PARA 命名空间。
    未知 bucket(如 "40-资源库 Resources")原样返回, 保证不写死私人路径。"""
    if not target:
        return target
    t = target.strip()
    if t in _BUCKET_ALIASES:
        return _BUCKET_ALIASES[t]
    m = re.match(r'^(\d\d)-(.+)$', t)
    if m and m.group(2).strip().startswith(("项目", "技术")):
        return {"10": "10-项目 Projects", "20": "20-技术 Technology"}[m.group(1)]
    return t


def _safe_kb_path(root, target):
    """把 kb_target 解析为 root 内的绝对路径，拒绝路径穿越。
    防止恶意/失误的 kb_target（如 "../etc/cron.d/x" 或绝对路径）写到 vault 之外。
    返回 Path 对象；若越界则返回 None。"""
    if not target:
        return None
    root_resolved = Path(root).resolve()
    if Path(target).is_absolute():
        return None
    candidate = (root_resolved / target).resolve()
    try:
        if candidate == root_resolved or root_resolved in candidate.parents:
            return candidate
    except (OSError, ValueError):
        pass
    return None


LESSON_RE = re.compile(r"\[importance::\s*([\d.]+)\]\s*\[domain::\s*([^\]]+)\]\s*\*\*(.+?)\*\*[:：]\s*(.+)")
DATE_ENTRY_RE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2}|\d{4}-\d{2})[:：]")


def git(args, cwd):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)


def sha(path):
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def load_state(root):
    # P2: 改用 state_manager（FR-3.3.6 + FR-3.1.7 文件锁）
    default = {"daily_hashes": {}, "core_hashes": {}, "synced": [], "extracted": []}
    try:
        from state_manager import StateStore
        store = StateStore(root)
        if store.exists("memory_state.json"):
            return store.load("memory_state.json", default)
        # 旧文件迁移
        legacy = Path(root) / STATE_FILE
        if legacy.exists():
            try:
                data = json.loads(legacy.read_text(encoding="utf-8"))
                store.save("memory_state.json", data)
                return data
            except (OSError, json.JSONDecodeError):
                pass
        return default
    except ImportError:
        try:
            return json.loads((Path(root) / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default


def save_state(root, state):
    try:
        from state_manager import StateStore
        StateStore(root).save("memory_state.json", state)
    except ImportError:
        (Path(root) / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def commit_new_files(root, rels):
    """只 add 本次新建/被改的 KB 文件(绝不 git add -A),有变化才 commit。"""
    if not rels:
        return 0
    git(["add", "--", *rels], root)
    if git(["status", "--porcelain"], root).stdout.strip():
        git(["commit", "-q", "-m", rels[0].split("/")[-1]], root)
    return 0


def frontmatter_block(fm):
    def fv(v):
        if isinstance(v, list):
            return "[" + ", ".join(str(x) for x in v) + "]"
        return str(v)
    return "---\n" + "\n".join("%s: %s" % (k, fv(v)) for k, v in fm.items()) + "\n---\n\n"


def strip_h1(text):
    """去掉首行 H1 标题（镜像/同步时避免与标题重复）。"""
    out, started = [], False
    for ln in text.splitlines():
        if not started and ln.strip().startswith("#"):
            continue
        started = True
        out.append(ln)
    return "\n".join(out).lstrip("\n")


def parse_agent(p):
    """解析 agent 记忆文件 → (fm_dict, frontmatter_str, body)。"""
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^---\s*$", text, re.M)
    if not m:
        return {}, "", text
    end = re.search(r"^---\s*$", text[m.end():], re.M)
    block = text[m.end():m.end() + (end.start() if end else 0)]
    body = text[m.end() + (end.end() if end else 0):] if end else text[m.end():]
    fm, cur = {}, None
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":"); v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k.strip()] = v.strip().strip('"\'')
    return fm, block, body


# ------------------------------------------------------------
# mirror : agent 每日记忆 → KB memory/ 层（喂 memory_sync 晋升）
# ------------------------------------------------------------
def mirror(root, agent_src, agent_name="openclaw"):
    src = Path(agent_src)
    state = load_state(root)
    dh = state.setdefault("daily_hashes", {})
    content_seen = state.setdefault("content_hashes", {})  # FR-3.3.9 内容去重
    touched = []
    if not src.is_dir():
        print(f"✗ agent 记忆源不存在: {src}")
        return 1
    mirrored, deduped = 0, 0
    for p in sorted(src.glob("*.md")):
        h = sha(p)
        if dh.get(p.name) == h:
            continue
        fm, block, body = parse_agent(p)
        # FR-3.3.9 内容指纹去重
        from kb_common import content_fingerprint
        source_hash = content_fingerprint(body)
        if source_hash in content_seen:
            deduped += 1
            dh[p.name] = h  # 标记已处理避免重复检查
            continue
        content_seen[source_hash] = f"{agent_name}/{p.name}"
        fm.setdefault("kb_source", agent_name)
        fm["memory_source"] = f"{agent_name}/{p.name}"
        fm["memory_ingested"] = datetime.date.today().strftime("%F")
        fm["source_hash"] = source_hash  # FR-3.3.9 保真镜像
        # 补全 validate 必填字段（agent 记忆源通常缺这些）
        fm.setdefault("status", "active")
        fm.setdefault("domain", "综合")
        fm.setdefault("created", fm.get("date", datetime.date.today().strftime("%F")))
        fm.setdefault("updated", fm.get("date", datetime.date.today().strftime("%F")))
        fm.setdefault("importance", 0.5)
        fm.setdefault("tags", ["knowledge"])
        out = Path(root) / "memory" / p.name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(frontmatter_block(fm) + strip_h1(body), encoding="utf-8")
        dh[p.name] = h
        touched.append(str(Path("memory") / p.name))
        _audit_log(root, "mirror", str(out.relative_to(Path(root).resolve())),
                   f"agent={agent_name} src={p.name} hash={source_hash[:12]}")
        mirrored += 1
    save_state(root, state)
    if touched:
        commit_new_files(root, touched)
        print(f"✅ 镜像 {mirrored} 条 → memory/（供 memory_sync 晋升）  去重跳过 {deduped} 条")
    else:
        print(f"✅ 无新增记忆（已全部镜像）  去重跳过 {deduped} 条")
    return 0


# ------------------------------------------------------------
# mirror_project_context : 归一摄入 project-context 的 .agents/memory/*.md
#
# project-context 把每个项目的事件记忆写到 <项目>/.agents/memory/{MEMORY,CONTEXT,
# HANDOFF}.md。这些文件散落在各项目（各项目都有同名 MEMORY.md），直接 ingest 会撞名。
# 本函数按「<发现根名>/<项目内相对路径》」镜像到 reference/project-context/，用项目根名
# 做命名空间消除撞名；content-hash 去重；dry_run 严格只读。
# ------------------------------------------------------------
def _discover_pc_roots(extra=None):
    """发现 project-context 的项目根。优先环境变量 KB_DSH_PROJECT_CONTEXT_ROOT
    （逗号分隔，可多根）；否则探测几个标准位置。只返回已存在的目录。"""
    roots = []
    extra = extra if extra is not None else os.environ.get("KB_DSH_PROJECT_CONTEXT_ROOT")
    if extra:
        for part in str(extra).split(","):
            p = Path(part).expanduser().strip()
            if p:
                roots.append(str(p))
    if not roots:
        h = Path("~").expanduser()
        for cand in ("workspace", ".dsh", "桌面", "deepseek-harness", "知识库"):
            roots.append(str(h / cand))
    return [Path(r).expanduser() for r in roots if Path(r).expanduser().is_dir()]


def mirror_project_context(root, roots=None, agent_name="project-context", dry_run=False):
    root = Path(root)
    state = load_state(root)
    ch = state.setdefault("pc_hashes", {})
    if roots is None:
        roots = _discover_pc_roots()
    touched, mirrored = [], 0
    for r in roots:
        r = Path(r)
        base = r.name or "root"
        for p in sorted(r.rglob("**/.agents/memory/*.md")):
            rel = p.relative_to(r)                 # .agents/memory/MEMORY.md
            key = f"{base}/{rel}"
            if ch.get(key) == sha(p):
                continue                           # 内容未变，跳过
            mirrored += 1                          # 待镜像计数（dry-run 与真实摄取都计）
            if not dry_run:
                fm, _block, body = parse_agent(p)
                body = strip_h1(body)
                fm.setdefault("kb_source", agent_name)
                fm["memory_source"] = f"{agent_name}/{rel}"
                fm["kb_target"] = f"reference/project-context/{base}/{rel}"
                fm.setdefault("status", "active")
                fm.setdefault("domain", "综合")
                fm.setdefault("created", datetime.date.today().strftime("%F"))
                fm.setdefault("updated", datetime.date.today().strftime("%F"))
                fm.setdefault("importance", 0.5)
                fm.setdefault("tags", ["project-context"])
                out = root / "reference" / "project-context" / base / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(frontmatter_block(fm) + body, encoding="utf-8")
                touched.append(str(out.relative_to(root)))
                ch[key] = sha(p)
    if dry_run:
        print(f"✅ 将镜像 {mirrored} 条项目上下文（dry-run，未落盘）")
        return mirrored
    save_state(root, state)
    if touched:
        commit_new_files(root, touched)
        print(f"✅ 镜像 {mirrored} 条项目上下文 → reference/project-context/（{', '.join(sorted(set(touched)))}）")
    else:
        print("✅ 项目上下文无变更")
    return mirrored


# ------------------------------------------------------------
# sync : 带 kb_target 的 agent 日报 → 按 kb_action 写进 KB 目标路径
# ------------------------------------------------------------
def heading_exists(text, heading):
    h = heading.strip(" \t#")
    for ln in text.splitlines():
        s = ln.strip(" \t#")
        if s.startswith("##") and (s == heading or s.startswith(heading)):
            return True
    return False


def sync(root, agent_src, agent_name="openclaw"):
    src = Path(agent_src)
    state = load_state(root)
    synced = state.setdefault("synced", [])
    touched, written = [], 0
    if not src.is_dir():
        print(f"✗ agent 记忆源不存在: {src}（先跑 mirror）")
        return 1
    # Task 6: 从 dedup-config.json 读取相似度计算方法
    from kb_common import load_dedup_config
    sim_method = load_dedup_config(root).get("sim_method", "tfidf")
    for p in sorted(src.glob("*.md")):
        fm, block, body = parse_agent(p)
        target = fm.get("kb_target")
        summary = fm.get("kb_summary", p.stem)
        date = fm.get("date", datetime.date.today().strftime("%F"))
        if not target:
            continue  # 纯日常,不参与知识同步(由 mirror 进 memory/)
        key = f"{target}::{summary}"
        if key in synced:
            continue
        entry = (f"## {summary}（{date}）\n\n{strip_h1(body)}"
                 f"\n\n> 来源：[[{fm.get('kb_source', agent_name)}]]  ·  同步自 agent 日报（{p.name}）\n")
        target = normalize_bucket(target)
        # target 是 PARA 区目录（无 .md 后缀）→ 用 summary 生成文件名
        if not target.endswith(".md"):
            fname = slug(summary) + ".md" if summary else p.stem + ".md"
            target = f"{target}/{fname}"
        kb_path = _safe_kb_path(root, target)
        if kb_path is None:
            print(f"  ⚠️ 跳过越界 kb_target '{target}'（不在 vault 内或为绝对路径）")
            continue
        rel = str(kb_path.relative_to(Path(root).resolve()))
        if kb_path.exists():
            existing = kb_path.read_text(encoding="utf-8")
            if heading_exists(existing, summary):
                synced.append(key)  # 已同步过(按标题去重)
                continue
            # 写入方式决策（记忆写入路由协议）：sims vs 已有内容
            # Task 3: 动态阈值
            _thr = get_dedup_thresholds(fm.get("domain"), root)
            _replace_sim = _thr["replace_sim"]
            _merge_sim = _thr["merge_sim"]
            sim = _text_sim(entry, existing, sim_method)
            if sim >= _replace_sim:
                # 替换：sims≥replace_sim → 覆盖同标题 section（若存在）或整文件
                new_text = re.sub(rf"## {re.escape(summary)}.*?(?=\n## |\Z)", entry.rstrip() + "\n\n",
                                  existing, count=1, flags=re.DOTALL) if heading_exists(existing, summary) else entry
                kb_path.write_text(new_text, encoding="utf-8")
                print(f"  ↻ 替换 {rel}（sims {sim:.2f}≥{_replace_sim}）")
            else:
                # 合并/新建：sims<replace_sim → 追加（merge_sim≤sims<replace_sim 合并 / sims<merge_sim 新建 section）
                kb_path.write_text(existing + "\n" + entry, encoding="utf-8")
                if sim >= _merge_sim:
                    print(f"  + 合并 {rel}（sims {sim:.2f}∈[{_merge_sim},{_replace_sim})）")
                else:
                    print(f"  + 新建 section {rel}（sims {sim:.2f}<{_merge_sim}）")
            _audit_after_write(entry, summary)
        else:
            kb_path.parent.mkdir(parents=True, exist_ok=True)
            kb_path.write_text(frontmatter_block({"tags": [str(t) for t in (fm.get("tags") or ["knowledge"])],
                                                  "status": "active", "kb_source": agent_name,
                                                  "kb_synced_from": f"{agent_name}/{p.name}"}) +
                               f"# {summary}\n\n{entry}", encoding="utf-8")
            _audit_after_write(entry, summary)
        _audit_log(root, "sync", rel, f"agent={agent_name} action={fm.get('kb_action','sync')} src={p.name}")
        touched.append(rel)
        synced.append(key)
        written += 1
    save_state(root, state)
    if touched:
        commit_new_files(root, touched)
        print(f"✅ 同步写入 {written} 条到 KB（{', '.join(sorted(set(touched)))}）")
    else:
        print("✅ 无新增知识同步（已全部同步 / 无 kb_target 标记）")
    return 0


# ------------------------------------------------------------
# mirror-core : agent 核心文件变更 → KB reference/ 跟踪参考
# ------------------------------------------------------------
def mirror_core(root, agent_root, agent_name="openclaw"):
    ar = Path(agent_root)
    state = load_state(root)
    ch = state.setdefault("core_hashes", {})
    touched, mirrored = [], 0
    for name in CORE_FILES:
        p = ar / name
        if not p.exists():
            continue
        h = sha(p)
        if ch.get(name) == h:
            continue
        fm, block, body = parse_agent(p)
        fm.setdefault("kb_source", f"{agent_name}-core")
        fm["memory_source"] = f"{agent_name}/{name}"
        fm["mirror_date"] = datetime.date.today().strftime("%F")
        out = Path(root) / "reference" / f"{agent_name}-{name}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(frontmatter_block(fm) + body.lstrip("\n"), encoding="utf-8")
        ch[name] = h
        touched.append(str(Path("reference") / f"openclaw-{name}"))
        _audit_log(root, "mirror-core", str(out.relative_to(Path(root).resolve())),
                   f"agent={agent_name} core={name}")
        mirrored += 1
    save_state(root, state)
    if touched:
        commit_new_files(root, touched)
        print(f"✅ 核心记忆镜像 {mirrored} 条 → reference/（SOUL/USER/MEMORY/AGENTS/TOOLS 更新同步）")
    else:
        print("✅ 核心记忆无变更（全部同步）")
    return 0


# ------------------------------------------------------------
# extract : MEMORY.md 结构条目 + USER.md 偏好 → 结构化 KB 笔记
# ------------------------------------------------------------
def top_sim(vocab, idf, rel2vec, body):
    if not rel2vec:
        return 0.0, None
    from rag import _vec, _cos
    qv = _vec(Counter(rag.tokenize(body)), vocab, idf)
    best = (0.0, None)
    for r, v in rel2vec.items():
        c = _cos(v, qv)
        if c > best[0]:
            best = (c, r)
    return best[0], best[1]


def gate(body, density_thr, min_chars):
    dens = signal_density(body)
    nchars = len(body.replace("\n", ""))
    return {"density": round(dens, 2), "pass": dens >= density_thr and nchars >= min_chars}


def extract(root, agent_root, density_thr, min_chars, semantic, agent_name="openclaw"):
    ar = Path(agent_root)
    state = load_state(root)
    extracted = state.setdefault("extracted", [])
    vocab, idf, rel2vec, meta_all = load_index(root)
    touched, written = [], 0
    for name in ("MEMORY.md", "USER.md"):
        p = ar / name
        if not p.exists():
            continue
        rels = extract_entries(p)
        for title, body, domain in rels:
            if f"{name}::{title}" in extracted:
                continue
            g = gate(body, density_thr, min_chars)
            if not g["pass"]:
                continue
            sim, rel = top_sim(vocab, idf, rel2vec, body)
            # Task 3: 动态 dup_sim 阈值
            _dup_sim = get_dedup_thresholds(domain, root)["dup_sim"]
            if rel is not None and sim >= _dup_sim:
                continue  # 与 KB 高度重复 → 增量,不新建
            bucket = DOMAIN_BUCKET.get(domain, "40-资源库 Resources")
            fname = slug(title)
            rel_target = "%s/%s-%s.md" % (bucket, fname, datetime.date.today().strftime("%m%d"))
            kb_path = Path(root) / rel_target
            if kb_path.exists():
                rel_target = "%s/%s-%d.md" % (bucket, fname, len(touched) + 1)
                kb_path = Path(root) / rel_target
            fm = {"tags": [domain], "status": "active", "domain": domain,
                  "created": datetime.date.today().strftime("%F"), "updated": datetime.date.today().strftime("%F"),
                  "importance": 0.6, "kb_target": bucket, "kb_action": "new",
                  "kb_summary": title, "kb_source": agent_name,
                  "memory_source": f"{agent_name}/{name}::{title}"}
            kb_path.parent.mkdir(parents=True, exist_ok=True)
            kb_path.write_text(frontmatter_block(fm) + f"# {title}\n\n{body}\n\n> 自动抽取自 {name}（记忆写入侧 extract）",
                               encoding="utf-8")
            _audit_log(root, "extract", rel_target, f"agent={agent_name} domain={domain} src={name}::{title}")
            touched.append(rel_target)
            extracted.append(f"{name}::{title}")
            written += 1
    save_state(root, state)
    if touched:
        commit_new_files(root, touched)
        print(f"✅ 抽取写入 {written} 条 → KB（{', '.join(sorted(set(touched)))}）")
    else:
        print("✅ 无新增抽取（核心文件未变更 / 全门槛未过 / 已抽取）")
    return 0


def split_sections(text):
    """按 ## 标题拆分正文 → {标题: 内容}（每段首行标题,余为正文）。"""
    parts = re.split(r"(?=^#{1,3}\s)", text, flags=re.M)
    secs = {}
    for seg in parts:
        if not seg.strip():
            continue
        lines = seg.split("\n")
        hi = 0
        while hi < len(lines) and not lines[hi].strip().startswith("#"):
            hi += 1
        if hi < len(lines):
            heading = re.sub(r"^#+\s*", "", lines[hi].strip()).split("\n")[0].strip()
            body = "\n".join(lines[hi + 1:]).strip()
        else:
            heading, body = "_body_", "\n".join(lines).strip()
        secs[heading] = body
    return secs


def extract_entries(p):
    """从 MEMORY.md/USER.md 抽取 (title, body, domain) 条目。"""
    text = p.read_text(encoding="utf-8")
    out, name = [], p.name
    if name == "MEMORY.md":
        sections = split_sections(text)
        for sec in ("经验教训", "关键决策"):
            body = sections.get(sec, "")
            if not body.strip():
                continue
            for m in LESSON_RE.finditer(body):
                dom, title, content = m.group(2).strip(), m.group(3).strip(), m.group(4).strip()
                out.append((title, f"[{dom}] {content}", dom))
            for ln in body.splitlines():
                if not DATE_ENTRY_RE.match(ln):
                    continue
                rest = ln.strip().lstrip("- ").strip()
                title = rest.split("—", 1)[0].split("：", 1)[0].split("：", 1)[0].strip()
                content = rest.split("：", 1)[-1] if "：" in rest else rest
                if len(title) > 3:
                    out.append((title, content, "综合" if sec == "关键决策" else "综合"))
    elif name == "USER.md":
        for sec_body in split_sections(text).values():
            if not sec_body or sec_body.startswith("#"):
                continue
            for ln in sec_body.splitlines():
                s = ln.strip()
                if s.startswith("- ") and len(s) > 10 and not s.startswith("- - "):
                    content = re.sub(r"^[-*\s]+", "", s).strip()
                    title = re.sub(r"^[\**]", "", content.split("：", 1)[0].split("（", 1)[0].split("，", 1)[0]).strip()
                    if len(title) < 2:
                        title = "用户偏好"
                    out.append((title, content, "管理"))
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    P = {}
    for cmd in ("mirror", "sync", "mirror-core", "extract"):
        P[cmd] = sub.add_parser(cmd)
        P[cmd].add_argument("--root", default=ROOT_DEFAULT)
        P[cmd].add_argument("--agent-name", default="openclaw")
    P["mirror"].add_argument("--agent-src", default=AGENT_SRC_DEFAULT)
    P["sync"].add_argument("--agent-src", default=AGENT_SRC_DEFAULT)
    P["mirror-core"].add_argument("--agent-root", default=AGENT_ROOT_DEFAULT)
    P["extract"].add_argument("--agent-root", default=AGENT_ROOT_DEFAULT)
    P["extract"].add_argument("--density", type=float, default=0.15)
    P["extract"].add_argument("--min-chars", type=int, default=MIN_CHARS_DEFAULT)
    P["extract"].add_argument("--semantic", action="store_true")
    args = ap.parse_args()
    if args.cmd == "mirror":
        return mirror(args.root, args.agent_src, args.agent_name)
    if args.cmd == "sync":
        return sync(args.root, args.agent_src, args.agent_name)
    if args.cmd == "mirror-core":
        return mirror_core(args.root, args.agent_root, args.agent_name)
    return extract(args.root, args.agent_root, args.density, args.min_chars, args.semantic, args.agent_name)


if __name__ == "__main__":
    sys.exit(main())
