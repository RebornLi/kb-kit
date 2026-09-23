#!/usr/bin/env python3
# ============================================================
# agent_registry.py —— 多 agent 记忆摄取注册表（通用、跨 agent）
#
# 用途：kb-kit 现在支持 OpenClaw / Hermes / DSH / Codex / project-context 五种 agent。
# 安装时探测本机存在的 agent，让用户选择后写入注册表（kb-agent.json），
# 之后 `kb ingest-agent` 按注册表调度各自的 adapter 把记忆灌进 KB。
#
# 设计铁律：
#   1) 通用，绝不写死任何用户的私人路径。探测用“标准位置 + CLI + 环境变量覆盖”，
#      探测到私人/非标准位置时只“显示给用户确认”，绝不硬编码进代码或模板仓。
#   2) 注册表是运行时状态（gitignore），不是知识库内容，巡检不计入。
#
# 用法:
#   python3 agent_registry.py detect            # 探测本机存在的 agent
#   python3 agent_registry.py list --root R     # 列出已注册 agent
#   python3 agent_registry.py add  --root R --name OPENCLAW
#   python3 agent_registry.py add  --root R --name DSH --json ~/.dsh/storages/memory.json
#   python3 agent_registry.py enable --root R --name HERMES --enable true
#   python3 agent_registry.py ingest --root R [--agent NAME] [--list] [--dry-run]
# ============================================================
import argparse, os, re, sys, json, glob, shutil, importlib, datetime
from pathlib import Path

STATE_FILE = "kb-agent.json"

# 五种 agent 的形态:
#   md                 : 记忆是 .md 文件（OpenClaw 有日报 + 核心文件；Hermes 仅有核心文件）
#   json               : 记忆是纯 JSON（DSH 的 agent-memory + evolve 结晶）
#   project-context    : 事件记忆在 <项目>/.agents/memory/*.md（project-context 生成）
#   sqlite             : 记忆是 SQLite 数据库（Codex）
AGENT_TYPES = ("md", "json", "project-context", "sqlite")


# ------------------------------------------------------------
# 探测层：每种 agent 一个探测函数，返回 {name, type, found, sources, notes}
# 只读取系统信息，不写任何东西。
# ------------------------------------------------------------
def home():
    return Path(os.path.expanduser("~"))


def env_override(key, default=None):
    """允许用 KB_<AGENT>_<FIELD> 环境变量覆盖默认探测到的路径（供高级用户 / 非标准部署）。"""
    v = os.environ.get(key)
    return Path(v) if v else default


def detect_openclaw():
    res = {"name": "openclaw", "type": "md", "label": "OpenClaw",
           "found": False, "sources": [], "notes": []}
    # CLI 存在即“安装过”
    cli = shutil.which("openclaw")
    if cli:
        res["found"] = True
    # 记忆源：优先环境变量；否则在常见位置找 workspace/memory
    daily = env_override("KB_OPENCLAW_HOME")
    if daily:
        res["sources"] = {"root": str(Path(daily)), "daily_src": str(Path(daily) / "memory")}
        res["found"] = True
        return res
    # 试探几个常见 workspace 位置
    for cand in [home() / "桌面" / "桌面文件" / "openclaw1",
                 home() / "Documents" / "openclaw",
                 home() / ".openclaw"]:
        ws = cand / "workspace"
        if (ws / "MEMORY.md").exists() or (ws / "memory").is_dir():
            res["found"] = True
            res["sources"] = {"root": str(ws), "daily_src": str(ws / "memory")}
            res["notes"].append(f"workspace 位于 {ws}")
            return res
    if not res["found"]:
        res["notes"].append("检测到 openclaw 但未找到 workspace，可手动 --add")
    return res


def detect_hermes():
    res = {"name": "hermes", "type": "md", "label": "Hermes",
           "found": False, "sources": {}, "notes": []}
    memories = home() / ".hermes" / "memories"
    core = env_override("KB_HERMES_MEMORIES") or memories
    if (core / "MEMORY.md").exists():
        res["found"] = True
        res["sources"] = {"root": str(core), "daily_src": None}
    else:
        res["notes"].append(f"{core} 无 MEMORY.md")
    return res


def detect_dsh():
    res = {"name": "dsh", "type": "json", "label": "DeepSeek Harness",
           "found": False, "sources": {}, "notes": []}
    mem = env_override("KB_DSH_MEMORY") or (home() / ".dsh" / "storages" / "memory.json")
    # dsh-evolve 的结晶记忆（同 schema：{unit,global,tables.records}）另存 evolve_memory.json，
    # 也归一到同一个 vault：用 KB_DSH_EVOLVE_MEMORY 覆盖，缺省探测标准位置。
    evolve = env_override("KB_DSH_EVOLVE_MEMORY") or (
        home() / ".dsh" / "storages" / "evolve_memory.json")
    if mem.exists():
        res["found"] = True
        sources = {"json": str(mem)}
    else:
        res["notes"].append(f"{mem} 不存在")
        sources = {}
    if evolve.exists() and (not mem.exists() or str(Path(evolve)) != str(Path(mem))):
        sources["evolve_json"] = str(evolve)
    res["sources"] = sources
    return res


def detect_codex():
    res = {"name": "codex", "type": "sqlite", "label": "OpenAI Codex",
           "found": False, "sources": {}, "notes": []}
    data = env_override("KB_CODEX_DATA") or (home() / ".codex")
    if data and glob.glob(str(Path(data) / "*.sqlite")):
        res["found"] = True
        res["sources"] = {"data_dir": str(data)}
    else:
        res["notes"].append(f"{data} 无 .sqlite 记忆库")
    return res


def detect_agents():
    """探测本机存在的 agent。"""
    import shutil
    return [
        detect_openclaw(),
        detect_hermes(),
        detect_dsh(),
        detect_codex(),
    ]


# ------------------------------------------------------------
# 注册表读写（kb-agent.json，在 vault 根目录）
# ------------------------------------------------------------
def load_registry(root):
    p = Path(root) / STATE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "agents": {}}


def save_registry(root, reg):
    p = Path(root) / STATE_FILE
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def cmd_detect(root, out):
    agents = detect_agents()
    for a in agents:
        flag = "✅" if a["found"] else "⚪"
        out.append(f"{flag} {a['label']}（{a['type']}）")
        for k, v in a.get("sources", {}).items():
            out.append(f"     - {k}: {v}")
        for n in a.get("notes", []):
            out.append(f"     · {n}")
    return 0


def cmd_list(root, out):
    reg = load_registry(root)
    agents = reg.get("agents", {})
    if not agents:
        out.append("（尚未注册任何 agent，用 `kb ingest-agent --setup` 或手动 add）")
        return 0
    out.append(f"已注册 {len(agents)} 个 agent：")
    for name, cfg in agents.items():
        state = "on" if cfg.get("enabled", True) else "OFF"
        out.append(f"  - {name}（{cfg.get('type')}）[{state}]")
    return 0


def cmd_add(root, name, agent_type, out, **fields):
    reg = load_registry(root)
    name = name.lower()
    if name in reg["agents"]:
        out.append(f"❌ 已注册 {name}，无需重复 add")
        return 1
    reg["agents"][name] = {"type": agent_type, "enabled": True, **fields}
    save_registry(root, reg)
    out.append(f"✅ 已注册 {name}（{agent_type}）")
    return 0


def cmd_enable(root, name, enabled, out):
    reg = load_registry(root)
    name = name.lower()
    if name not in reg["agents"]:
        out.append(f"❌ 未注册 {name}")
        return 1
    reg["agents"][name]["enabled"] = bool(enabled)
    save_registry(root, reg)
    out.append(f"✅ {name} 已{'启用' if enabled else '禁用'}")
    return 0


def cmd_setup(root, out):
    """安装时探测 + 让用户选择要启用的 agent，写进注册表。"""
    out.append("🔎 探测本机 agent……")
    agents = detect_agents()
    found = [a for a in agents if a["found"]]
    if not found:
        out.append("（未探测到任何已知 agent，可稍后用 `kb ingest-agent --add` 手动添加）")
        return 0
    # 探测到的信息先落到注册表，enabled 由用户选择
    for a in found:
        reg = load_registry(root)
        reg["agents"][a["name"]] = {
            "type": a["type"],
            "label": a["label"],
            "enabled": True,
            "sources": a.get("sources", {}),
        }
        save_registry(root, reg)
    for a in found:
        out.append(f"  可选启用：{a['label']}（{a['type']}）")
    out.append("（启用/禁用在注册表中设置；缺省全部启用）")
    return 0


def cmd_ingest(root, name, dry_run, out):
    """ingest 是显式写操作：把 agent 记忆灌进 KB（mirror/sync/extract 均写）。
    dry_run=True 时只读不写（默认闸门）。
    非 dry_run 时：写 memory/ + reference/ + KB 笔记，git add 精确文件 + commit。
    "只读纪律"指默认只读 + 写操作需显式触发，非"永不写"；ingest 的写是设计意图。"""
    reg = load_registry(root)
    agents = reg.get("agents", {})
    if name:
        agents = {k: v for k, v in agents.items() if k == name.lower()}
    enabled = {k: v for k, v in agents.items() if v.get("enabled", True)}
    if not enabled:
        out.append("（没有已启用的 agent；用 list 查看或用 enable 开启）")
        return 0
    if not dry_run:
        out.append("⚠️ ingest-agent 将写入 KB（mirror/sync/extract）；加 --dry-run 可只读预览")
    # 按类型分发到 adapter
    done = []
    for aname, cfg in enabled.items():
        atype = cfg.get("type")
        srcs = cfg.get("sources", {})
        if atype == "md":
            done.append(_ingest_md(root, aname, cfg, srcs, dry_run, out))
        elif atype == "json":
            done.append(_ingest_json(root, aname, srcs, dry_run, out))
        elif atype == "project-context":
            done.append(_ingest_project_context(root, aname, srcs, dry_run, out))
        elif atype == "sqlite":
            done.append(_ingest_sqlite(root, aname, srcs, dry_run, out))
        else:
            out.append(f"⚠️ 未知 agent 类型 {atype}，跳过 {aname}")
    return 0


def _ingest_md(root, aname, cfg, srcs, dry_run, out):
    import subprocess
    daily_src = srcs.get("daily_src") or srcs.get("root")
    root_p = srcs.get("root")
    out.append(f"→ {aname}（md）: daily={daily_src} root={root_p}（kb_source={aname}）")
    if dry_run:
        return f"{aname}: dry-run"
    mi = Path(root) / "pipeline" / "memory_ingest.py"
    if not mi.exists():
        out.append(f"  ⚠️ {mi} 不存在，跳过")
        return f"{aname}: skip(missing memory_ingest.py)"

    def _run_extra(*args):
        """跑命令并把（吞掉的）标准输出回读出来，让用户能看到结果。"""
        r = subprocess.run([sys.executable, str(mi), *args,
                            "--agent-name", aname], capture_output=True, text=True)
        txt = (r.stdout + r.stderr).strip()
        if txt:
            out.append(f"     {txt}")
        return r.returncode

    # daily 镜像 + sync（如果有 daily_src）
    if daily_src and Path(daily_src).is_dir():
        _run_extra("mirror", "--root", root, "--agent-src", daily_src)
        _run_extra("sync", "--root", root, "--agent-src", daily_src)
    # 核心文件 mirror-core + extract
    if root_p and Path(root_p).is_dir():
        _run_extra("mirror-core", "--root", root, "--agent-root", root_p)
        _run_extra("extract", "--root", root, "--agent-root", root_p)
    return f"{aname}: md done"


def _ingest_json(root, aname, srcs, dry_run, out):
    try:
        from memory_ingest_json import ingest as ingest_json
    except (ImportError, ModuleNotFoundError):
        out.append(f"  ⚠️ memory_ingest_json 缺省，跳过 {aname}")
        return f"{aname}: skip(no json adapter)"
    src_list = _json_sources(srcs)
    if not src_list:
        out.append(f"  ⚠️ 缺 json 路径，跳过 {aname}")
        return f"{aname}: skip(no json)"
    total = 0
    for src in src_list:
        n = ingest_json(root, Path(src), dry_run)
        total += n
        out.append(f"     · {Path(src).name} → {n} 条")
    out.append(f"→ {aname}: 摄取 {total} 条（{len(src_list)} 个 json 源）")
    return f"{aname}: json done ({len(src_list)} sources)"


def _json_sources(srcs):
    """DSH 的 json 记忆源（支持原生 memory.json + dsh-evolve 结晶 evolve_memory.json）。
    显式写在 sources 里的优先；否则回落到环境变量 KB_DSH_*（与 detect_dsh 探测一致）。
    去重：同一物理路径只摄取一次。"""
    ordered = []
    for key in ("json", "evolve_json", "evolve_json_path"):
        v = srcs.get(key)
        if v:
            ordered.append(str(v))
    # 回落到环境变量覆盖（允许只配 evolve，不配原生 memory.json）
    if not srcs.get("json") and os.environ.get("KB_DSH_MEMORY"):
        ordered.append(os.environ["KB_DSH_MEMORY"])
    if not srcs.get("evolve_json") and not srcs.get("evolve_json_path") and os.environ.get("KB_DSH_EVOLVE_MEMORY"):
        ordered.append(os.environ["KB_DSH_EVOLVE_MEMORY"])
    # 去重保序
    seen, out_paths = set(), []
    for p in ordered:
        rp = str(Path(p))
        if rp not in seen:
            seen.add(rp)
            out_paths.append(rp)
    return out_paths


def _ingest_project_context(root, aname, srcs, dry_run, out):
    """摄入 project-context 的 .agents/memory/*.md（各项目 MEMORY/CONTEXT/HANDOFF）。
    roots 可来自 sources.roots（列表）或 sources.root（单根）或环境变量
    KB_DSH_PROJECT_CONTEXT_ROOT；缺省时按 .agents/memory 自动探测各项目根。"""
    import memory_ingest as mi
    roots = srcs.get("roots") or (srcs.get("root") and [srcs.get("root")]) or None
    out.append(f"→ {aname}（project-context）: roots={roots if roots else 'env/默认探测'}")
    if dry_run:
        return f"{aname}: dry-run"
    n = mi.mirror_project_context(root, roots, "project-context", dry_run=False)
    out.append(f"→ {aname}: 镜像 {n} 条 → reference/project-context/")
    return f"{aname}: project-context done ({n} files)"


def _ingest_sqlite(root, aname, srcs, dry_run, out):
    try:
        from memory_ingest_sqlite import ingest as ingest_sqlite
    except (ImportError, ModuleNotFoundError):
        out.append(f"  ⚠️ memory_ingest_sqlite 缺省，跳过 {aname}")
        return f"{aname}: skip(no sqlite adapter)"
    n = ingest_sqlite(root, Path(srcs.get("data_dir", "")), dry_run)
    out.append(f"→ {aname}: 摄取 {n} 条")
    return f"{aname}: sqlite done"


def main():
    ap = argparse.ArgumentParser(description="多 agent 记忆摄取注册表")
    sub = ap.add_parser("sub") if False else ap
    sub = ap.add_subparsers(dest="cmd", required=True)
    P = {}
    for c in ("detect", "list", "setup", "ingest", "add", "enable"):
        P[c] = sub.add_parser(c)
        P[c].add_argument("--root", default=os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[1]))
    P["detect"].add_argument("--out", action="store_true", help="把人类可读信息写到 stdout")
    P["add"].add_argument("--name", required=True)
    P["add"].add_argument("--type", required=True, choices=list(AGENT_TYPES))
    P["add"].add_argument("--json", dest="json_path")
    P["add"].add_argument("--evolve-json", dest="evolve_json_path")
    P["add"].add_argument("--project-context-root", dest="project_context_root", nargs="+")
    P["add"].add_argument("--daily-src")
    P["add"].add_argument("--root-src")
    P["enable"].add_argument("--name", required=True)
    P["enable"].add_argument("--enable", type=str, default="true")
    P["ingest"].add_argument("--name")
    P["ingest"].add_argument("--list", action="store_true")
    P["ingest"].add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out = []
    if args.cmd == "detect":
        r = cmd_detect(args.root, out)
        print("\n".join(out)); return r
    if args.cmd == "list":
        r = cmd_list(args.root, out); print("\n".join(out)); return r
    if args.cmd == "setup":
        r = cmd_setup(args.root, out); print("\n".join(out)); return r
    if args.cmd == "add":
        enabled = True
        fields = {}
        if args.json_path: fields["sources"] = {"json": args.json_path}
        if args.evolve_json_path: fields.setdefault("sources", {})["evolve_json"] = args.evolve_json_path
        if args.project_context_root: fields.setdefault("sources", {})["roots"] = list(args.project_context_root)
        if args.daily_src: fields.setdefault("sources", {})["daily_src"] = args.daily_src
        if args.root_src: fields.setdefault("sources", {})["root"] = args.root_src
        r = cmd_add(args.root, args.name, args.type, out, **fields); print("\n".join(out)); return r
    if args.cmd == "enable":
        r = cmd_enable(args.root, args.name, (args.enable.lower() not in ("false", "0", "no")), out)
        print("\n".join(out)); return r
    if args.cmd == "ingest":
        r = cmd_ingest(args.root, args.name, args.dry_run, out)
        # 始终打印摄取结果（md agents 的输出依赖这里）
        print("\n".join(out))
        return r
    return 1


if __name__ == "__main__":
    sys.exit(main())
