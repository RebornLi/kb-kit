#!/usr/bin/env python3
# ============================================================
# create_vault.py —— 「知识库一键套件」的安装引擎（跨平台，仅用标准库）
#
# 由 install.sh / install.ps1 / install.bat 调用，也可单独运行：
#   python create_vault.py --vault "我的知识库"
#
# 它把 template-vault/ 这份"模板仓"复制成一个全新的、已接线好的知识库，
# 并立即跑一遍成长引擎，让交付的知识库开箱即用（带示例 + 示例报表）。
#
# 步骤:
#   1) 校验/创建目标目录
#   2) 拷贝物料(分区/模板/reference/.obsidian!d/pipeline/scripts/启动器/.gitignore)
#   3) 初始化运行时状态文件(.memory_*_state.json 等)
#   4) 建首份向量索引(vector index/)
#!  5) 跑一轮成长引擎 demo(intake/feedback/link/recall/dashboard)
#   6) 可选 git init + 首次提交
# ============================================================
import argparse, os, shutil, subprocess, sys, stat
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
TPL = KIT_ROOT / "template-vault"
STATE_FILES = [
    ".memory_sync_state.json", ".memory_ingest_state.json",
    ".feedback_state.json", ".recall_state.json",
]

def log(msg): print(msg, flush=True)

def run_py(vault, script, args):
    """调用 <vault>/pipeline/<script>.py，cwd=vault。"""
    p = Path(vault) / "pipeline" / f"{script}.py"
    r = subprocess.run([sys.executable, str(p), *args], cwd=str(vault),
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        tail = out.splitlines()[-1] if out else "[ok]"
        log(f"  ✅ [ok] {script} {' '.join(args)}  →  {tail}")
    else:
        log(f"  ⚠️  [warn] {script} 退出码 {r.returncode}:\n      {out[:400]}")
    return r.returncode

def git_available():
    return subprocess.run(["git", "--version"], capture_output=True, text=True).returncode == 0


def _select_agents_interactive(found, out):
    """交互选择：打印编号清单，读用户输入（编号/全部/都不），回落到全部。"""
    n = len(found)
    # 清单和提示必须在 input() 之前即时打印，否则用户看不到选项
    log("")
    log("🔎 检测到本机已安装 {} 个 agent，请选择要安装的：".format(n))
    for i, a in enumerate(found, 1):
        log("   {}）{}（{})".format(i, a["label"], a["type"]))
    log("   输入方式：单个编号、逗号分隔多项、或 all/全部=全选、none/都不=不选。留空=全选。")
    try:
        raw = input("   请选择: ").strip().lower()
    except EOFError:
        log("  ℹ️  未读到输入（非交互环境），默认全选")
        return list(found)
    if raw in ("", "all", "全部", "all/all", "1,2,3,4,5,6,7,8,9,10"):
        return list(found)
    if raw in ("none", "no", "都不", "不选", "取消", "quit"):
        return []
    wanted = set(p.strip() for p in raw.split(",") if p.strip())
    # 支持编号(序号)、英文小写名、中文简称(精确匹配)
    idx_map = {str(i + 1): a for i, a in enumerate(found)}
    chosen = []
    for w in wanted:
        wl = w.strip().lower()
        matched = idx_map.get(wl)          # 编号，如 1 / 2 / 3
        if not matched:
            for a in found:                # 精确匹配 name 或整个 label
                if wl == a["name"].lower() or wl == a["label"].lower():
                    matched = a
                    break
        if matched and matched not in chosen:
            chosen.append(matched)
    if not chosen:
        log("  ⚠️  未匹配到任何已知 agent，默认全选")
        return list(found)
    return chosen


def _select_agents(found):
    """选择 agent：交互选择优先；无 tty / 非交互则回退 AGENT_SELECT 环境变量。"""
    interactive = sys.stdin is not None and sys.stdin.isatty()
    if interactive:
        try:
            from agent_registry import detect_agents, load_registry, save_registry
            out = []
            chosen = _select_agents_interactive(found, out)
            for line in out:
                log(line)
            return chosen
        except Exception as e:
            log("  ⚠️  交互选择异常，回退环境变量：{}".format(e))

    # 非交互（CI / 管道注入 / 脚本）：回退环境变量
    select = os.environ.get("AGENT_SELECT", "").strip().lower()
    if select:
        wanted = {s.strip().lower() for s in select.split(",") if s.strip()}
        chosen = [a for a in found if a["name"].lower() in wanted]
        if not chosen:
            log("  ⚠️  AGENT_SELECT={} 未匹配任何已探测 agent，回退全部".format(select))
            return list(found)
        return chosen
    # 默认全部
    return list(found)


def _setup_agents(vault):
    """安装时探测本机 agent，由用户选择后写入新 vault 根的 kb-agent.json 注册表。

    探测完全通用（agent_registry.py detect()：标准位置 + 环境变量覆盖），
    绝不写死任何私人路径。仅注册用户"选中的"已探测 agent；用户可随时
    `kb ingest-agent --setup` 重扫并勾选。
    """
    sys.path.insert(0, str(vault / "pipeline"))
    from agent_registry import detect_agents, load_registry, save_registry

    regs = load_registry(vault)
    if regs.get("agents"):
        log("  ℹ️  kb-agent.json 已有注册，保留现状（不自动重写）")
        return 0

    found = [a for a in detect_agents() if a.get("found")]
    if not found:
        log("  ℹ️  未探测到已知 agent；稍后可用 `kb ingest-agent --setup` 手动安装")
        return 0

    # 已探测到但用户一个都不选——写空表，不落任何 agent
    chosen = _select_agents(found)
    if not chosen:
        log("  ℹ️  你未选择任何 agent，注册表留空")
        return 0

    for a in chosen:
        cfg = {"type": a["type"], "enabled": True, "sources": a.get("sources", {})}
        regs["agents"][a["name"]] = cfg
        log("  ✅ 注册 agent：{}（{})".format(a["label"], a["type"]))
    save_registry(vault, regs)
    log("✅ 已写入注册表 {}（{} 个 agent）".format(vault / "kb-agent.json", len(chosen)))
    return 0

def main():
    ap = argparse.ArgumentParser(description="一键搭建一个会自成长的知识库")
    ap.add_argument("--vault", required=True,
                    help="目标 vault 目录(不存在会自动创建；已有内容且非 --force 会拒绝)")
    ap.add_argument("--name", default=None, help="vault 显示名(写入首页 frontmatter，可选)")
    ap.add_argument("--no-git", dest="init_git", action="store_false", default=True,
                    help="跳过 git 初始化")
    ap.add_argument("--no-demo", dest="demo", action="store_false", default=True,
                    help="跳过交付时的成长引擎 demo（仅建仓+建索引）")
    ap.add_argument("--force", action="store_true",
                    help="目标目录已有内容也覆盖(谨慎)")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser().resolve()

    # 1) 目标目录
    if vault.exists() and any(vault.iterdir()) and not args.force:
        log("❌ 目标目录已存在且有内容，拒绝覆盖：")
        log(f"      {vault}")
        log("   请指向一个空目录，或加 --force 强制。")
        return 1
    vault.mkdir(parents=True, exist_ok=True)
    log(f"🏗️  目标 vault: {vault}")

    if not TPL.exists() or not (TPL / "pipeline").is_dir():
        log(f"❌ 模板仓不存在: {TPL}")
        return 1

    # 2) 拷贝物料
    shutil.copytree(TPL, vault, dirs_exist_ok=True, symlinks=False)
    # 拷贝 kb-kit 根目录的 README.md（template-vault 里没有，但提示用户读）
    kit_readme = KIT_ROOT / "README.md"
    if kit_readme.exists():
        shutil.copy2(kit_readme, vault / "README.md")
    log(f"✅ 已拷贝物料 → {vault}")

    # 2.5) 如果指定了 vault 显示名，写入首页 frontmatter 的 kb_summary
    if args.name:
        homepage = vault / "🏠-知识库首页.md"
        if homepage.exists():
            lines = homepage.read_text(encoding="utf-8").splitlines(keepends=True)
            for i, ln in enumerate(lines):
                if ln.startswith("kb_summary:"):
                    lines[i] = f"kb_summary: {args.name}\n"
                    break
            homepage.write_text("".join(lines), encoding="utf-8")

    # 3) 安装时探测本机 agent（通用探测，绝不写死私人路径），
    #    让用户选择后写入新 vault 根的 kb-agent.json 注册表。
    _setup_agents(vault)

    # 4) 初始化运行时状态（均为 gitignore，供引擎记录进度）
    for s in STATE_FILES:
        (vault / s).write_text("{}\n", encoding="utf-8")
    (vault / "vector index").mkdir(exist_ok=True)
    log("✅ 已初始化运行时状态 + 向量索引目录")

    # 4) 权限：unix 上让启动器/脚本可执行
    if os.name != "nt":
        for f in (vault / "kb", *sorted((vault / "scripts").glob("*.sh"))):
            try:
                f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass

    # 6) 建首份向量索引（必做，否则 kb query 无数据）
    log("🔎 构建首份向量索引……")
    run_py(vault, "rag", ["index"])

    if args.demo:
        log("⚙️  跑一轮成长引擎 demo（让你立刻看到效果）……")
        run_py(vault, "intake_triage", ["review"])
        run_py(vault, "feedback_loop", ["ingest"])
        run_py(vault, "link_engine", ["suggestions"])
        run_py(vault, "recall_schedule", ["deck"])
        run_py(vault, "kb_health", [])
        # 交付前跑一次真实门禁巡检（成长引擎②之外）：
        # demo 默认只跑 kb_health 成长性度量，此处补齐死链/缺字段/越界/空目录 门禁。
        run_py(vault, "kb-healthcheck", [])
        run_py(vault, "dashboard", [])

    # 7) 可选 git
    if args.init_git and git_available():
        subprocess.run(["git", "init"], cwd=str(vault),
                       capture_output=True, text=True)
        subprocess.run(["git", "add", "-A"], cwd=str(vault),
                       capture_output=True, text=True)
        # git 身份：KB_GIT_EMAIL/KB_GIT_NAME > 全局 git 配置 > 占位符(!带提示)
        def _cfg(env_name, git_name):
            v = os.environ.get(env_name)
            if not v:
                r = subprocess.run(["git", "config", "--global", git_name],
                                   capture_output=True, text=True)
                v = r.stdout.strip()
            return v or {"email": "kb@example.com", "name": "KB Owner"}[git_name.split(".")[-1]]
        git_email, git_name = _cfg("KB_GIT_EMAIL", "user.email"), _cfg("KB_GIT_NAME", "user.name")
        if git_email == "kb@example.com" or git_name == "KB Owner":
            log("  ⚠️  未检测到 git 身份，使用占位署名；可设 KB_GIT_EMAIL/KB_GIT_NAME 或"
                " `git config --global user.*` 后重跑")
        subprocess.run(["git", "config", "user.email", git_email],
                       cwd=str(vault), capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", git_name],
                       cwd=str(vault), capture_output=True, text=True)
        subprocess.run(["git", "commit", "-q", "-m", "chore: 用 kb-kit 初始化知识库"],
                       cwd=str(vault), capture_output=True, text=True)
        log("✅ 已 git init 并完成首次提交（.gitignore 已生效）")
    elif args.init_git and not git_available():
        log("  ⚠️  未检测到 git，跳过版本化（可选：`git init` 后手动 add/commit）")

    log("")
    log("🎉 知识库已就绪！下一步：")
    log(f"   1) 用 Obsidian 打开文件夹 {vault}")
    log("      → 设置→核心插件 开启 dataview / calendar / tasks；社区插件装这三个")
    log("   2) 在终端试检索：")
    if os.name == "nt":
        log(f"      cd \"{vault}\"  &&  kb query \"怎么备份知识库\"")
    else:
        log(f"      cd \"{vault}\"  &&  ./kb query \"怎么备份知识库\"")
    log("   3) 读根目录的 README.md 与 01-如何开始.md")
    log("   4) 写第一条知识进 00-收件箱，然后 `kb rag index` + `kb query`")
    return 0

if __name__ == "__main__":
    sys.exit(main())
