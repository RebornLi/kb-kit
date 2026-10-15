#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""testenv.py — 开发仓的可复现测试环境（stdlib-only）。

## 为什么需要它

单测（`tests/test_*.py`）只覆盖**函数级**行为。但几类改动必须在**真实规模的库**上验证，
单测给不了信号：

  · 检索质量（排序公式、索引编码、分块策略）→ 要有语料 + 黄金集
  · 性能（索引加载/BM25/查询延迟）        → 要有量级相当的索引
  · 状态文件迁移、路径约定统一            → 要有真实目录结构与历史产物
  · 安装验收（`create_vault` 出来的库能不能过 `kb verify`）

以前这些只能**临时手造、用完删除**（`/tmp/kbeval`、`/tmp/kbnew`），既不持久也不可复现。
本脚本把环境固化到 `<repo>/.testenv/`（已 gitignore），一条命令即可重建与验证。

## 用法

    python3 tests/testenv.py build [--scale N] [--note-chars N] [--from-vault PATH] [--force]
    python3 tests/testenv.py check [--perf]
    python3 tests/testenv.py status
    python3 tests/testenv.py clean

## 环境内容

    .testenv/fresh/   create_vault 装出来的新库   → 安装验收（kb verify 必须全过）
    .testenv/synth/   合成库（有效笔记+索引+黄金集）→ 检索/性能/状态/治理全链路
    .testenv/clone/   可选：真实库副本（--from-vault）→ 真实规模的检索质量评测
    .testenv/ENV.md   本次构建参数（便于复现与对比）

## 隔离铁律

`KB_ROOT` 在本机全局指向**现役库**，一处疏忽就会改到生产数据。因此：
  · 所有子进程的 `KB_ROOT` 一律指向 `.testenv/` 内的目标；
  · 任何落在「保护路径」（`$KB_ROOT` / 现役库）内的构建目标**直接 abort**；
  · 子进程统一经 `run()` 发起，不允许绕过。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
TESTENV = REPO / ".testenv"
TEMPLATE = REPO / "template-vault"
PY = sys.executable
GROUPS = ("00-收件箱 Inbox", "10-项目 Projects", "20-技术 Technology", "30-决策日志 Decisions",
          "40-资源库 Resources", "50-模板 Templates", "60-运营 Operations",
          "70-知识治理 Governance", "90-归档 Archive")

# ── 保护路径：绝不允许在它们里面构建/写入 ──────────────────────
def protected_roots() -> List[Path]:
    out: List[Path] = []
    for cand in (os.environ.get("KB_ROOT"), "/home/mushan/kb-kit-pure"):
        if not cand:
            continue
        try:
            p = Path(cand).resolve()
        except OSError:
            continue
        if p not in out and p != REPO.resolve():   # 开发仓本身是允许的（环境在它下面）
            out.append(p)
    return out


def assert_sandbox(path: Path) -> Path:
    """目标必须位于 .testenv/ 内，且不得落在任何保护路径里。"""
    p = Path(path).resolve()
    try:
        p.relative_to(TESTENV.resolve())
    except ValueError:
        raise SystemExit(f"✗ 拒绝操作 {p}：测试环境只允许在 {TESTENV} 内")
    for prot in protected_roots():
        if p == prot or prot in p.parents:
            raise SystemExit(f"✗ 拒绝操作 {p}：它位于保护路径 {prot} 内")
    return p


def run(argv: List[str], cwd: Path, root: Optional[Path], timeout: int = 1800,
        quiet: bool = False) -> Dict:
    """统一的子进程入口：**强制**把 KB_ROOT 指到沙箱目标。"""
    env = dict(os.environ)
    if root is None:
        env.pop("KB_ROOT", None)
    else:
        env["KB_ROOT"] = str(assert_sandbox(root))
    t0 = time.time()
    r = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True,
                       text=True, timeout=timeout, env=env)
    out = {"code": r.returncode, "out": r.stdout or "", "err": r.stderr or "",
           "secs": round(time.time() - t0, 2)}
    if not quiet and r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-4:]
        for ln in tail:
            print(f"      ↳ {ln[:120]}")
    return out


def kb(vault: Path, *args: str, timeout: int = 1800, quiet: bool = False) -> Dict:
    """在目标库自己的代码上跑命令（不用 PATH 上的 `kb` —— 那可能指向别的库）。"""
    launcher = vault / "pipeline" / "kb_launcher.py"
    return run([PY, launcher, *args, "--root", str(vault)], cwd=vault, root=vault,
               timeout=timeout, quiet=quiet)


# ── 合成语料 ────────────────────────────────────────────────
TOPICS = [
    ("vLLM 部署与显存调优", "开发", "20-技术 Technology/AI与LLM"),
    ("embedding 服务端口与健康检查", "开发", "20-技术 Technology/AI与LLM"),
    ("向量索引重建与增量更新", "开发", "20-技术 Technology/AI与LLM"),
    ("Python 并发与 GIL 实测", "开发", "20-技术 Technology/后端"),
    ("前端构建缓存与懒加载", "开发", "20-技术 Technology/前端"),
    ("Kubernetes 探针与滚动发布", "运维", "20-技术 Technology/部署运维"),
    ("cron 巡检任务编排", "运维", "20-技术 Technology/部署运维"),
    ("备份与恢复演练流程", "运维", "20-技术 Technology/部署运维"),
    ("磁盘占用排查与清理", "运维", "20-技术 Technology/部署运维"),
    ("SSH 隧道与端口转发", "运维", "20-技术 Technology/网络"),
    ("密钥轮换与最小权限", "安全", "20-技术 Technology/安全"),
    ("供应链依赖审计", "安全", "20-技术 Technology/安全"),
    ("SQLite 索引与查询计划", "数据", "20-技术 Technology/数据库"),
    ("日志聚合与指标口径", "数据", "20-技术 Technology/数据库"),
    ("需求拆解与验收标准", "产品", "10-项目 Projects"),
    ("用户访谈问题清单", "产品", "10-项目 Projects"),
    ("团队节奏与复盘机制", "管理", "10-项目 Projects"),
    ("知识库治理与命名约定", "管理", "70-知识治理 Governance"),
    ("标签体系与受控词表", "管理", "70-知识治理 Governance"),
    ("Obsidian 双链与别名迁移", "综合", "40-资源库 Resources"),
    ("读书笔记与概念卡片", "综合", "40-资源库 Resources"),
    ("决策日志与取舍依据", "综合", "30-决策日志 Decisions"),
]
TAGS = ("知识工程", "工具生态", "方案", "结论", "经验", "复盘", "机制", "原则")


def _note(i: int, topic: str, domain: str, note_chars: int) -> str:
    """生成一篇**结构合法**的笔记：能被 validate/schema 通过，且主题词可检索。"""
    title = f"{topic}-{i:04d}"
    tag = TAGS[i % len(TAGS)]
    body_words = topic.replace(" ", "")
    para = (f"{body_words}的实践记录。结论是先把边界条件写清楚，再谈优化。"
            f"第 {i} 篇，用于测试检索、分块与治理链路。")
    extra = ""
    if note_chars > 1000:
        extra = "\n\n" + "\n".join(
            f"## 小节 {j}\n{body_words} 的细节 {j}：包含可复用的检查清单与失败案例。"
            for j in range(1, max(2, note_chars // 400)))
    return (f"---\n"
            f"tags: [\"{domain}\", {tag}]\n"
            f"status: active\n"
            f"domain: {domain}\n"
            f"category: knowledge\n"
            f"created: 2026-01-{(i % 28) + 1:02d}\n"
            f"updated: 2026-02-{(i % 28) + 1:02d}\n"
            f"importance: {round(0.3 + (i % 7) * 0.1, 2)}\n"
            f"kb_target: {title}.md\n"
            f"kb_action: new\n"
            f"kb_summary: {topic}（第 {i} 篇）\n"
            f"---\n"
            f"# {title}\n\n"
            f"{para}\n\n"
            f"相关：[[{TOPICS[(i + 1) % len(TOPICS)][0]}-{(i + 1) % len(TOPICS):04d}]]"
            f"{extra}\n")


def build_synth(vault: Path, scale: int, note_chars: int) -> Path:
    """合成库：create_vault 作底 → 灌入 scale 篇有效笔记 → 建索引 → 写黄金集。"""
    if vault.exists():
        shutil.rmtree(vault)
    r = run([PY, REPO / "create_vault.py", "--vault", vault, "--no-git", "--no-demo",
             "--no-verify", "--no-open", "--no-global-kb"], cwd=REPO, root=None)
    if r["code"] != 0:
        raise SystemExit(f"✗ create_vault 失败：\n{(r['err'] or r['out'])[-500:]}")

    gold = []
    for i in range(scale):
        topic, domain, subdir = TOPICS[i % len(TOPICS)]
        d = vault / subdir
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{topic}-{i:04d}.md"
        f.write_text(_note(i, topic, domain, note_chars), encoding="utf-8")
        if i % 10 == 0:                      # 每 10 篇抽 1 篇做黄金集（共 scale/10 题）
            gold.append({"q": f"{topic} 第 {i} 篇", "sources": [str(f.relative_to(vault))]})
    # 证据层样本（raw/ 不参与检索降级测试）
    rawd = vault / "raw" / "web"
    rawd.mkdir(parents=True, exist_ok=True)
    for i in range(max(3, scale // 50)):
        (rawd / f"capture-{i:03d}.md").write_text(
            f"---\ntags: [reference]\nstatus: active\ndomain: 综合\ncategory: reference\n"
            f"created: 2026-01-01\nupdated: 2026-01-01\nimportance: 0.2\n---\n\n"
            f"# 抓取原文 {i}\n原始网页内容，证据层，不参与默认检索排序。\n", encoding="utf-8")
    ev = vault / ".kb" / "eval"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "gold_set.jsonl").write_text(
        "\n".join(json.dumps(g, ensure_ascii=False) for g in gold) + "\n", encoding="utf-8")

    r = kb(vault, "rag", "index", "--full")
    if r["code"] != 0:
        raise SystemExit(f"✗ 合成库建索引失败：\n{(r['err'] or r['out'])[-500:]}")
    print(f"  ✓ synth：{scale} 篇笔记（每篇约 {note_chars} 字）+ {len(gold)} 题黄金集 + 索引")
    return vault


def build_clone(src: Path, vault: Path) -> Path:
    """真实库副本（隐私敏感）：复制 → 覆盖 dev 代码 → 重建索引。

    用途：检索**质量**评测（合成语料的黄金集只能做冒烟，真实语料的才有区分度）。
    """
    src = Path(src).resolve()
    if not (src / "pipeline").is_dir():
        raise SystemExit(f"✗ {src} 不是 kb-kit 库")
    for prot in protected_roots():
        if src == prot:
            print(f"  ⚠️ 源是保护路径 {prot}；只**读取**它，所有写入都在 {vault}")
    if vault.exists():
        shutil.rmtree(vault)
    # 用 shutil 而非 `tar | tar`：tar 的 stdout 是二进制，走 text=True 会 UnicodeDecodeError
    shutil.copytree(src, vault,
                    ignore=shutil.ignore_patterns(".git", "backups", "logs", "__pycache__",
                                                  ".pytest_cache", "*.pyc"))
    # 覆盖成 dev 代码（要测的是开发仓的代码，不是源库里的旧副本）
    for sub in ("pipeline", "scripts"):
        if (TEMPLATE / sub).is_dir():
            shutil.copytree(TEMPLATE / sub, vault / sub, dirs_exist_ok=True)
    for pc in vault.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    shutil.rmtree(vault / "vector index", ignore_errors=True)
    r = kb(vault, "rag", "index", "--full")
    if r["code"] != 0:
        raise SystemExit(f"✗ clone 建索引失败：\n{(r['err'] or r['out'])[-500:]}")
    print(f"  ✓ clone：{src} 的副本 + dev 代码 + 重建索引")
    return vault


def write_env_md(args, fresh: bool, synth: bool, clone: bool) -> None:
    idx = _idx_file(TESTENV / "synth")
    rows = [
        "# 测试环境构建参数",
        "",
        f"- 构建时间：{time.strftime('%F %T')}",
        f"- 仓库：`{REPO}`",
        f"- 保护路径（只读）：{'、'.join(str(p) for p in protected_roots()) or '（无）'}",
        f"- `--scale`：{args.scale}　`--note-chars`：{args.note_chars}",
        f"- 合成索引大小：{(idx.stat().st_size / 1048576):.1f} MB" if idx.exists() else "- 合成索引：未构建",
        "",
        "## 目录",
        "",
        f"- `fresh/`：{'已构建' if fresh else '未构建'} — 安装验收（`kb verify` 必须全过）",
        f"- `synth/`：{'已构建' if synth else '未构建'} — 检索/性能/状态/治理全链路",
        f"- `clone/`：{'已构建' if clone else '未构建'} — 真实规模检索质量评测",
        "",
        "## 复现",
        "",
        "```bash",
        f"python3 tests/testenv.py build --scale {args.scale} --note-chars {args.note_chars}"
        + (f" --from-vault {args.from_vault}" if args.from_vault else ""),
        "python3 tests/testenv.py check --perf",
        "```",
        "",
        "## 验证",
        "",
        "```bash",
        "python3 tests/testenv.py check          # 全套（单测 + 安装验收 + 合成库链路）",
        "python3 tests/testenv.py check --perf   # 追加索引/查询耗时基线",
        "```",
        "",
    ]
    (TESTENV / "ENV.md").write_text("\n".join(rows), encoding="utf-8")


# ── 检查 ────────────────────────────────────────────────────
def _idx_file(vault: Path) -> Optional[Path]:
    """索引文件解析：v6 起是 df_idf.pkl（旧库仍是 .json）。"""
    d = Path(vault) / "vector index"
    for name in ("df_idf.pkl", "df_idf.json"):
        p = d / name
        if p.exists():
            return p
    return None


def _ok(pass_: bool) -> str:
    return "✅" if pass_ else "❌"


def check_unit() -> Tuple[bool, str]:
    r = run([PY, "-m", "unittest", "discover", "-s", "tests"], cwd=REPO, root=None, quiet=True)
    txt = r["out"] + r["err"]
    m = re.search(r"Ran\s+(\d+)\s+tests?", txt)
    n = m.group(1) if m else "?"
    ok = r["code"] == 0 and bool(re.search(r"^OK", txt, re.M))
    fails = re.findall(r"^(?:FAIL|ERROR):\s*(\S+)", txt, re.M)
    return ok, f"{n} 个测试" + ("" if ok else f"，失败：{fails[:3]}")


def check_fresh() -> Tuple[bool, str]:
    vault = TESTENV / "fresh"
    if not (vault / "pipeline" / "kb_verify.py").exists():
        return False, "未构建（先 `build`）"
    r = kb(vault, "verify", "--json", quiet=True)
    try:
        d = json.loads(r["out"][r["out"].find("{"):])
    except (ValueError, json.JSONDecodeError):
        return False, f"验收输出无法解析（rc={r['code']}）"
    fails = [c["name"] for c in d["checks"] if c.get("pass") is False]
    n_skip = d.get("n_skip", 0)
    ok = d.get("verdict") == "PASS"
    return ok, f"verdict={d.get('verdict')} 跳过{n_skip}项" + (f" 失败:{fails}" if fails else "")


def check_synth() -> Tuple[bool, str]:
    vault = TESTENV / "synth"
    if not (vault / "pipeline").is_dir():
        return False, "未构建（先 `build`）"
    problems = []
    r = kb(vault, "validate", quiet=True)
    m = re.search(r"硬错误\s*(\d+)", r["out"])
    if r["code"] != 0 or (m and int(m.group(1)) != 0):
        problems.append(f"validate 硬错误 {m.group(1) if m else '?'}")
    r = kb(vault, "schema", "check", quiet=True)
    if r["code"] != 0:
        problems.append("schema 不合契约")
    idx = _idx_file(vault)
    if not idx.exists():
        problems.append("索引缺失")
    r = kb(vault, "query", "向量索引重建", "--top", "3", "--json", "--no-record", quiet=True)
    try:
        hits = (json.loads(r["out"][r["out"].find("{"):]) or {}).get("hits") or []
    except (ValueError, json.JSONDecodeError):
        hits = []
    if not hits:
        problems.append("查询无命中")
    r = rb = None
    ev = vault / "scripts" / "eval_retrieval_gold.py"
    recall = None
    if ev.exists():
        rr = run([PY, ev, "--root", str(vault), "--top", "10", "--json"],
                 cwd=vault, root=vault, quiet=True)
        try:
            recall = json.loads(rr["out"][rr["out"].find("{"):])["recall"]["@10"]
        except (ValueError, KeyError, json.JSONDecodeError):
            problems.append("黄金集评测失败")
    # recall 只作**冒烟下限**：合成语料自造，query 与 kb_summary 同源，自然是满分。
    # 它只能证明「检索链路没坏」，不能用来判断质量好坏 —— 质量要看 clone（真实语料）。
    if recall is not None and recall < 0.9:
        problems.append(f"冒烟 recall@10={recall:.1%} 低于 90%")
    return (not problems), ("全部通过" if not problems else "；".join(problems)) + \
        (f"　冒烟recall@10={recall:.1%}" if recall is not None else "")


def check_clone() -> Optional[Tuple[bool, str]]:
    vault = TESTENV / "clone"
    if not (vault / "pipeline").is_dir():
        return None
    ev = vault / "scripts" / "eval_retrieval_gold.py"
    if not ev.exists():
        return False, "缺 eval_retrieval_gold.py"
    rr = run([PY, ev, "--root", str(vault), "--top", "10", "--json"],
             cwd=vault, root=vault, quiet=True)
    try:
        d = json.loads(rr["out"][rr["out"].find("{"):])
    except (ValueError, json.JSONDecodeError):
        return False, "评测失败"
    return True, (f"{d['n_questions']} 题 · top1={d['top1']:.1%} · "
                  f"recall@10={d['recall']['@10']:.1%} · MRR={d['mrr']:.3f}")


def _perf_for(vault: Path, queries: List[str]) -> List[Tuple[str, str]]:
    """单库性能基线：索引体积 / 加载 / 建索引 / 查询延迟。

    注意查询延迟**含解释器启动**（`rag.py` 是独立进程），量级 ~90ms 起；
    要比较排序算法本身的开销，看 `索引加载`（load_index 含解析）这一行。
    """
    idx = _idx_file(vault)
    if idx is None or not idx.exists():
        return []
    size_mb = idx.stat().st_size / 1048576
    # 用**目标库自己的加载器**计时：v6 起索引是 pickle（直接 json.loads 会崩），
    # 而且真实成本本来就是 load_index 本身。
    code = ("import sys,time;sys.path.insert(0,{pl!r});import rag;"
            "t=time.time();p,v=rag.load_index({ix!r});"
            "print(round((time.time()-t)*1000,1), (p or {{}}).get('n_docs',0))").format(
                pl=str(vault / "pipeline"), ix=str(vault / "vector index"))
    rr = run([PY, "-c", code], cwd=vault, root=vault, quiet=True)
    try:
        load_ms, n_docs = rr["out"].strip().split()
        load_ms = float(load_ms)
    except (ValueError, AttributeError):
        load_ms, n_docs = -1.0, "?"
    payload = {"n_docs": n_docs}
    t0 = time.time()
    r = kb(vault, "rag", "index", "--full", quiet=True)
    build_s = time.time() - t0
    lat = []
    for q in queries:
        t0 = time.time()
        kb(vault, "query", q, "--top", "5", "--json", "--no-record", quiet=True)
        lat.append((time.time() - t0) * 1000)
    lat.sort()
    return [
        ("索引大小", f"{size_mb:.2f} MB（{payload.get('n_docs', '?')} 篇）"),
        ("索引加载 load_index", f"{load_ms:.0f} ms"),
        ("全量建索引", f"{build_s:.1f} s" + ("" if r["code"] == 0 else "（失败）")),
        ("查询延迟 p50", f"{lat[len(lat)//2]:.0f} ms（含进程启动）"),
        ("查询延迟 max", f"{lat[-1]:.0f} ms"),
    ]


def check_perf() -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    synth = TESTENV / "synth"
    if _idx_file(synth) is not None:
        for k, v in _perf_for(synth, [f"第 {i} 篇 实践记录" for i in range(5)]):
            out.append(("synth", k, v))
    clone = TESTENV / "clone"
    if _idx_file(clone) is not None:
        qs = ["vLLM 部署有哪些坑", "OpenClaw 失忆 embedding 服务排查", "怎么重建向量索引",
              "知识库备份怎么恢复", "Codex CLI 部署 自启"]
        for k, v in _perf_for(clone, qs):
            out.append(("clone", k, v))
    return out


def _sync_pipeline() -> int:
    """把开发仓的 pipeline 同步进各沙箱，**检查前必做**。

    为什么：沙箱是"构建一次、反复检查"的，若不同步，检查跑的是**构建当时的旧代码** ——
    实测踩到：改了 kb_common 之后 synth 仍用旧版，于是 check 报出一条已经修掉的
    validate 硬错误，把我引去查一个不存在的问题。检查环境本身必须可信。
    """
    n = 0
    for vault in ("fresh", "synth", "clone"):
        d = TESTENV / vault
        if not (d / "pipeline").is_dir():
            continue
        assert_sandbox(d)
        for src in sorted((TEMPLATE / "pipeline").iterdir()):
            if src.is_file() and src.name.endswith(".py"):
                shutil.copy2(src, d / "pipeline" / src.name)
                n += 1
        for sub in ("plugins",):
            s, t = TEMPLATE / "pipeline" / sub, d / "pipeline" / sub
            if s.is_dir():
                (t).mkdir(parents=True, exist_ok=True)
                for src in sorted(s.iterdir()):
                    if src.is_file() and src.name.endswith(".py"):
                        shutil.copy2(src, t / src.name)
        pc = d / "pipeline" / "__pycache__"
        if pc.exists():
            shutil.rmtree(pc, ignore_errors=True)
    return n


def cmd_check(args) -> int:
    print(f"🔎 测试环境检查　root={TESTENV}")
    prot = "、".join(str(p) for p in protected_roots()) or "（无）"
    print(f"   保护路径（全程只读，不会写入）：{prot}")
    if not getattr(args, "no_sync", False):
        n = _sync_pipeline()
        print(f"   已从开发仓同步 pipeline（{n} 个文件）—— 确保检查的是当前代码"
              f"，而非构建时的旧版\n")
    else:
        print("   ⚠️ --no-sync：使用沙箱里的**既有** pipeline（可能不是当前代码）\n")
    rows = []
    rows.append(("单元测试", *check_unit()))
    rows.append(("安装验收 fresh", *check_fresh()))
    rows.append(("合成库链路 synth", *check_synth()))
    cl = check_clone()
    if cl:
        rows.append(("真实库 clone", *cl))
    width = max(len(n) for n, _ in [(r[0], r[1]) for r in rows])
    for name, ok, detail in rows:
        print(f"   {_ok(ok)} {name:<{width}}  {detail}")
    if args.perf:
        print("\n   ⏱  性能基线")
        for where, name, val in check_perf():
            print(f"      [{where:<5}] {name:<16} {val}")
        if not (TESTENV / "clone" / "pipeline").is_dir():
            print("      ⚠️ 合成库只作冒烟；检索**质量**与真实规模性能需 "
                  "`build --from-vault <真实库>`")
    n_fail = sum(1 for _, ok, _ in rows if not ok)
    print(f"\n   {'✅ 全部通过' if n_fail == 0 else f'❌ {n_fail} 项未通过'}"
          f"（{len(rows)} 项）")
    return 1 if n_fail else 0


def cmd_build(args) -> int:
    TESTENV.mkdir(parents=True, exist_ok=True)
    assert_sandbox(TESTENV)
    print(f"🔧 构建测试环境　{TESTENV}\n")
    have_fresh = have_synth = have_clone = False
    if args.force or not (TESTENV / "fresh" / "pipeline").is_dir():
        vault = TESTENV / "fresh"
        if vault.exists():
            shutil.rmtree(vault)
        r = run([PY, REPO / "create_vault.py", "--vault", vault, "--no-git", "--no-demo",
                 "--no-verify", "--no-open", "--no-global-kb"], cwd=REPO, root=None)
        if r["code"] != 0:
            raise SystemExit(f"✗ fresh 构建失败：\n{(r['err'] or r['out'])[-500:]}")
        print("  ✓ fresh：create_vault 装出的新库")
        have_fresh = True
    else:
        have_fresh = True
        print("  · fresh：已存在（--force 可重建）")
    if args.force or not (TESTENV / "synth" / "pipeline").is_dir():
        build_synth(TESTENV / "synth", args.scale, args.note_chars)
        have_synth = True
    else:
        have_synth = True
        print("  · synth：已存在（--force 可重建）")
    if args.from_vault:
        build_clone(Path(args.from_vault), TESTENV / "clone")
        have_clone = True
    elif (TESTENV / "clone" / "pipeline").is_dir():
        have_clone = True
    write_env_md(args, have_fresh, have_synth, have_clone)
    print(f"\n✅ 环境就绪 · 参数见 {TESTENV / 'ENV.md'}")
    print("   验证：python3 tests/testenv.py check --perf")
    return 0


def cmd_status(args) -> int:
    if not TESTENV.exists():
        print("（尚未构建；先 python3 tests/testenv.py build）")
        return 1
    print(f"📦 测试环境　{TESTENV}\n")
    for name in ("fresh", "synth", "clone"):
        d = TESTENV / name
        if not d.is_dir():
            print(f"   · {name:<6} 未构建")
            continue
        n = sum(1 for _ in d.rglob("*.md"))
        idx = _idx_file(d)
        size = f"{idx.stat().st_size / 1048576:.1f} MB" if idx else "无索引"
        print(f"   · {name:<6} {n} 篇笔记 · 索引 {size}")
    env = TESTENV / "ENV.md"
    if env.exists():
        print("\n" + env.read_text(encoding="utf-8"))
    return 0


def cmd_clean(args) -> int:
    assert_sandbox(TESTENV)
    if TESTENV.exists():
        shutil.rmtree(TESTENV)
        print(f"🧹 已删除 {TESTENV}")
    else:
        print("（无环境可删）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="开发仓测试环境（build/check/status/clean）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="构建环境")
    b.add_argument("--scale", type=int, default=300, help="合成笔记篇数（默认 300）")
    b.add_argument("--note-chars", type=int, default=700, dest="note_chars",
                   help="每篇正文约多少字（默认 700；调大以逼近真实索引体积）")
    b.add_argument("--from-vault", default=None, dest="from_vault",
                   help="额外复制一个真实库做检索质量评测（只读源；副本在 .testenv/clone）")
    b.add_argument("--force", action="store_true", help="已存在也重建")
    c = sub.add_parser("check", help="跑全套验证")
    c.add_argument("--perf", action="store_true", help="追加性能基线")
    sub.add_parser("status", help="查看环境状态")
    sub.add_parser("clean", help="删除环境")
    args = ap.parse_args()
    return {"build": cmd_build, "check": cmd_check, "status": cmd_status,
            "clean": cmd_clean}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
