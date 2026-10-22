#!/usr/bin/env python3
# ============================================================
# create_vault.py —— 一键搭建知识库 · 安装引擎
#
# 三个平台安装器（install.sh / install.ps1 / install.bat）内部都调这个引擎，
# 所以它等价、全平台。也可以直接调用：
#     python3 create_vault.py --vault "我的知识库"     # 装到目标目录
#     python3 create_vault.py --vault kb-kit --no-git      # 不初始化 git
#     python3 create_vault.py --vault kb-kit --no-demo     # 不跑 demo（建向量索引）
#     python3 create_vault.py --vault kb-kit --force       # 目标非空也覆盖
#
# 职责（对应 README 的“把模板仓复制 + 接线 + 探测 agent + 跑 demo”）：
#   1) 复制 template-vault/ 到目标目录（跳过 .git / 缓存等运行时态）
#   2) 接线：用 pipeline/agent_registry.py 探测本机已装 agent，写入
#      kb-agent.json（运行时注册表，gitignore，不含任何写死的私人路径）
#   3) 跑 demo：建本地向量索引，装完即可 `kb query`
#   4) 初始化 git（默认；--no-git 跳过）
#
# 依赖：仅 Python 标准库（3.8+）。绝不写死任何用户的私人路径。
# ============================================================
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = REPO_DIR / "template-vault"
STATE_IGNORE = shutil.ignore_patterns(
    ".git", ".git/*", "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info",
    "vector index",  # 运行时向量索引：不随模板分发，安装时由 run_demo 重建
    # 运行态**绝不随模板分发** —— 否则新库会带上模板作者跑测试留下的
    # 反馈命中、查询缓存、RSI 提议（指向并不存在的笔记）。以前这里漏了 .kb/，
    # 于是 template-vault/.kb/state/*.json 被原样复制进每个新库。
    ".kb", ".agents", ".trash", "_curate", "*.jsonl",
)


def parse_args():
    ap = argparse.ArgumentParser(description="一键搭建知识库 · 安装引擎")
    ap.add_argument("--vault", default=None,
                    help="目标知识库目录（绝对或相对路径；留空=当前目录/kb-kit）")
    ap.add_argument("--no-git", action="store_true", help="不初始化 git")
    ap.add_argument("--no-demo", action="store_true", help="不跑 demo（不建向量索引）")
    ap.add_argument("--force", action="store_true",
                    help="目标目录非空时也覆盖安装（**会先完整备份原库到 <目标>/backups/ "
                         "pre-reinstall-<时间戳>/，备份失败则整体放弃**）")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="跳过 --force 的交互确认（cron / 无人值守场景用；仍会先备份）")
    ap.add_argument("--interactive", action="store_true",
                    help="交互模式：agent 探测时提问选择（默认非交互，全部启用）")
    ap.add_argument("--no-verify", action="store_true", help="跳过安装自检")
    ap.add_argument("--no-open", action="store_true", help="不自动打开 Obsidian")
    ap.add_argument("--no-global-kb", action="store_true", help="不注册全局 kb 命令")
    return ap.parse_args()


# ── P0-2：--force 的破坏性删除必须「先备份、再确认」──────────────────
#
# 事故背景（2026-10-22 审计发现）：原实现是裸 `shutil.rmtree(child)` /
# `child.unlink()`，用户手滑加一个 --force（或从旧文档复制命令行）→
# 几百篇笔记 + 全部 .git 历史一次性消失，**无备份、无确认**。
#
# 更矛盾的是：项目自己的 `scripts/verify.py` 早就写明了这个风险——
#   「绝不能建议用户跑 create_vault.py --force，那条路径在 create_vault.py:63-68
#     会先 rmtree/unlink 目标目录的每个子项（= 用户全部笔记 + .git 历史）」
# 也就是说风险已被记录，但代码一直没修。
#
# 与 `sync.py:167 _backup_before_purge` 完全相同的纪律（那个模块已经做对了）：
#   「删除前把即将被清理的未跟踪路径备份到 backups/rollback-<ts>/；
#    任何一项备份失败即返回 None —— 宁可不清，也不丢数据。」
# 本修复把同一条纪律搬到安装器上。

PRE_REINSTALL_BACKUP = "pre-reinstall"


def _backup_vault(vault: Path) -> "tuple":
    """把整个目标库备份到 `<vault>/backups/<PRE_REINSTALL_BACKUP>-<时间戳>/`。

    返回 `(备份目录, 文件数)`；**任何一项失败返回 None** —— 调用方必须整体放弃清理。

    备份放在目标库自己的 `backups/` 下有两个原因：
      1. `backups/` 已在 `template-vault/.gitignore` 里，不会污染 git；
      2. 与被覆盖的库同盘，`shutil.move`（同分区 rename）比跨盘 copy 快得多。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = vault / "backups" / f"{PRE_REINSTALL_BACKUP}-{stamp}"
    n = 0
    for child in sorted(vault.iterdir()):
        name = child.name
        # 备份目的地自身不备份（否则递归）
        if name == "backups":
            continue
        tgt = dst / name
        try:
            if child.is_symlink():
                tgt.parent.mkdir(parents=True, exist_ok=True)
                tgt.symlink_to(os.readlink(child))
                n += 1
            elif child.is_dir():
                shutil.copytree(child, tgt)
                n += 1
            else:
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, tgt)
                n += 1
        except (OSError, shutil.Error) as e:
            print(f"  ⚠️ 备份失败：{name}（{e}）", file=sys.stderr)
            # 清掉半截备份，别留下一个"看起来能恢复其实不能"的目录
            shutil.rmtree(dst, ignore_errors=True)
            return None
    return dst, n


def _count_notes(vault: Path) -> int:
    """粗略统计目标库里的笔记数（给用户的确认提示用）。"""
    n = 0
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in (".git", ".obsidian", "backups",
                                            "__pycache__", "vector index")]
        n += sum(1 for f in fn if f.endswith(".md"))
    return n


def ensure_target(vault, force, assume_yes=False):
    """目标不存在则新建；为空则复用；非空且未 --force 则拒绝。

    `--force` 路径（P0-2 修复）：**先完整备份，再要求确认，最后才删除**。
      1. 备份失败 → 直接放弃安装（宁可装不上，也不能丢数据）
      2. 非 `--yes` 且是交互终端 → 要求输入目标库名确认（防手滑）
      3. `--yes` → 跳过确认（cron/无人值守），但备份仍然强制
    """
    if vault.exists():
        if vault.is_file():
            print(f"❌ 目标已存在且是一个文件: {vault}", file=sys.stderr)
            return False
        if list(vault.iterdir()) and not force:
            print(f"❌ 目标目录非空: {vault}", file=sys.stderr)
            print("   选一个空目录，或加 --force 覆盖（会先备份再清空）。", file=sys.stderr)
            return False
        if force:
            # ── 1. 先备份（失败即整体放弃）──
            print(f"⛳ --force：先完整备份现有库 → {vault}/backups/{PRE_REINSTALL_BACKUP}-<时间戳>/ …")
            res = _backup_vault(vault)
            if res is None:
                print("⛔ 备份不完整 → 已放弃覆盖安装，**未删除任何文件**。", file=sys.stderr)
                print("   请检查磁盘空间/权限后重试；或改用空目录安装，"
                      "再用 `kb backup` 迁移。", file=sys.stderr)
                return False
            backup_dir, n_files = res
            print(f"   已备份 {n_files} 个顶层条目 → {backup_dir}")

            # ── 2. 确认（除非 --yes）──
            n_notes = _count_notes(vault)
            if not assume_yes and sys.stdin.isatty():
                print(f"\n⚠️  即将**清空并覆盖**：{vault}")
                print(f"   其中约 {n_notes} 篇 .md 笔记（含 .git 历史）将被删除。")
                print(f"   备份已就绪：{backup_dir}（可手动恢复）")
                try:
                    ans = input(f"   确认请输入目标库名 `{vault.name}`（其他任何输入 = 取消）: ").strip()
                except EOFError:
                    ans = ""
                if ans != vault.name:
                    print("⛔ 已取消（未删除任何文件；备份保留，可手动删除）。")
                    return False
            elif not assume_yes:
                # 非交互（cron / 管道）且没给 --yes：不猜，直接拒绝
                print("⛔ 非交互环境下的 --force 需要同时给 `--yes`（仍会先备份）。",
                      file=sys.stderr)
                return False

            # ── 3. 确认过才删 ──
            for child in vault.iterdir():
                if child.name == "backups":
                    continue        # 备份要留着！
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            print(f"   已清空（备份保留于 {backup_dir}）")
    else:
        vault.mkdir(parents=True, exist_ok=True)
    return True


def copy_template(vault):
    if not TEMPLATE_DIR.is_dir():
        print(f"❌ 模板目录不存在，create_vault.py 需与 template-vault/ 同目录: {TEMPLATE_DIR}",
              file=sys.stderr)
        return False
    shutil.copytree(TEMPLATE_DIR, vault, dirs_exist_ok=True, ignore=STATE_IGNORE)
    return True


def _template_rel_files():
    """按 STATE_IGNORE 语义列出模板将被复制的文件（相对路径，/ 分隔）。"""
    out = []
    for dp, dn, fn in os.walk(TEMPLATE_DIR):
        ignored = set(STATE_IGNORE(dp, dn + fn))
        dn[:] = [d for d in dn if d not in ignored]
        for f in fn:
            if f in ignored:
                continue
            out.append(os.path.relpath(os.path.join(dp, f), TEMPLATE_DIR).replace(os.sep, "/"))
    return sorted(out)


def write_install_manifest(vault):
    """写安装基线清单（供 scripts/verify.py 按清单核对文件完整性）。

    清单 = 本次从模板复制的文件 + 安装器生成的文件（kb-agent.json / 兜底文档）。
    只记「安装基线」，不含用户后续新增的笔记，故用户增删自己的内容不会误报。
    """
    files = set(_template_rel_files())
    for name in ("kb-agent.json", "如何使用.md", "🏠-知识库首页.md"):
        if (vault / name).exists():
            files.add(name)
    manifest = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": sorted(files),
    }
    dest = vault / ".kb" / "install-manifest.json"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 已写安装清单: {dest.relative_to(vault)}（{len(files)} 个基线文件）")
    except OSError as e:
        print(f"⚠️ 写安装清单失败: {e}", file=sys.stderr)


def _consume_agent_disabled_prefs():
    """读取并消费（删除）precheck 写入的全局 agent 偏好，返回禁用集合（无则 None）。"""
    prefs = Path.home() / ".kb-kit-agent-prefs.json"
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
        return set(data.get("disabled", []))
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            prefs.unlink()
        except OSError:
            pass


def _write_empty_registry(vault):
    """写空运行时注册表，保证自检依赖的 kb-agent.json 始终存在。"""
    try:
        (vault / "kb-agent.json").write_text(
            json.dumps({"version": 1, "agents": {}}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass


def wire_agents(vault, interactive):
    """探测本机 agent 并写入 kb-agent.json（注册表，运行时态，gitignore）。"""
    pipe = vault / "pipeline"
    sys.path.insert(0, str(pipe))
    try:
        import agent_registry as ar
    except Exception as e:  # noqa: BLE001 - 注册表加载失败不应让安装崩在复制之后
        print(f"⚠️ 无法加载 agent_registry（{e}）；可稍后手动 `kb ingest-agent --setup`。",
              file=sys.stderr)
        _write_empty_registry(vault)
        return

    agents = ar.detect_agents()
    found = [a for a in agents if a.get("found")]
    if not found:
        ar.save_registry(vault, {"version": 1, "agents": {}})
        print("🔎 未探测到任何已知 agent；稍后可用 `kb ingest-agent --add` 手动添加。")
        return

    # 优先遵循 precheck 在本安装会话中写入的 agent 选择（一次性偏好文件）
    disabled_prefs = _consume_agent_disabled_prefs()
    selected = {
        a["name"]: {"type": a["type"], "label": a["label"],
                    "enabled": (disabled_prefs is None or a["name"] not in disabled_prefs),
                    "sources": a.get("sources", {})}
        for a in found
    }

    if interactive:
        print("\n探测到以下 agent（回车=全部启用；空格分隔输入要禁用的名字）：")
        for a in found:
            mark = "x" if selected[a["name"]]["enabled"] else " "
            src = a.get("sources") or {}
            print(f"  [{mark}] {a['label']}（{a['type']}）")
            if isinstance(src, dict):
                for k, v in src.items():
                    print(f"        - {k}: {v}")
        try:
            ans = input("\n禁用哪些（空格分隔，留空=全启用）: ").strip()
        except EOFError:
            ans = ""
        for name in ans.split():
            if name in selected:
                selected[name]["enabled"] = False

    ar.save_registry(vault, {"version": 1, "agents": selected})
    enabled = [n for n, c in selected.items() if c.get("enabled")]
    print(f"✅ 已注册 {len(selected)} 个 agent；启用 {len(enabled)}："
          f"{', '.join(enabled) if enabled else '（无）'}")
    if len(selected) != len(enabled):
        print("   （已禁用: " + ", ".join(n for n, c in selected.items() if not c.get("enabled")) + "）")


def run_demo(vault):
    """建本地向量索引，使 `kb query` 装完即用。"""
    rag = vault / "pipeline" / "rag.py"
    if not rag.exists():
        print("⚠️ rag.py 缺失，跳过 demo。", file=sys.stderr)
        return
    print("▶ 跑 demo：建本地向量索引（kb rag index）……")
    try:
        r = subprocess.run([sys.executable, str(rag), "index", "--root", str(vault)],
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("⚠️ demo 建索引超时。", file=sys.stderr)
        return
    tail = ((r.stdout + r.stderr).strip().splitlines() or [""])
    for line in tail[-6:]:
        print("   " + line)
    if r.returncode != 0:
        print("⚠️ 建索引返回非 0 退出码（见上）；不影响其它功能。", file=sys.stderr)


def init_git(vault):
    try:
        subprocess.run(["git", "init", "-q", str(vault)], check=True)
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True)
        # 用本地 config 设身份（跨 git 版本稳定；-m/-c 组合在不同版本有差异）
        subprocess.run(["git", "-C", str(vault), "config", "user.name", "kb-installer"], check=True)
        subprocess.run(["git", "-C", str(vault), "config", "user.email", "kb@installer.local"], check=True)
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-q", "-m",
             "chore: 初始知识库（由 create_vault.py 安装）"],
            check=True,
        )
        print("✅ 已 git init + 首次提交。")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️ git 不可用或未配置：跳过提交（安装本身不受影响）。", file=sys.stderr)


def register_global_kb(vault):
    """注册全局 kb 命令（任意目录可用 kb）。"""
    import os
    if sys.platform == "win32":
        # Windows: 把 vault 路径加入用户 PATH（不覆盖现有）
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 f"[Environment]::GetEnvironmentVariable('Path', 'User')"],
                capture_output=True, text=True, timeout=10,
            )
            current = r.stdout.strip() if r.returncode == 0 else ""
            if str(vault) not in current:
                new_path = f"{current};{vault}" if current else str(vault)
                subprocess.run(
                    ["powershell", "-Command",
                     f"[Environment]::SetEnvironmentVariable('Path', '{new_path}', 'User')"],
                    capture_output=True, timeout=10,
                )
                print(f"✅ 已将 vault 加入用户 PATH（重启终端生效）")
            else:
                print("✅ vault 已在 PATH 中")
        except Exception as e:
            print(f"⚠️ 注册全局 kb 失败: {e}（不影响安装）", file=sys.stderr)
        return

    # Linux/macOS: 符号链接到 ~/.local/bin/kb
    try:
        bin_dir = Path.home() / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        link = bin_dir / "kb"
        target = vault / "kb"
        if link.exists() or link.is_symlink():
            if link.resolve() == target.resolve():
                print(f"✅ 全局 kb 已存在: {link}")
                return
            link.unlink()
        link.symlink_to(target)
        # 确保 ~/.local/bin 在 PATH
        shell_rc = Path.home() / (".zshrc" if "zsh" in os.environ.get("SHELL", "") else ".bashrc")
        if shell_rc.exists():
            content = shell_rc.read_text()
            if str(bin_dir) not in content:
                with open(shell_rc, "a") as f:
                    f.write(f"\n# kb-kit\nexport PATH=\"$HOME/.local/bin:$PATH\"\n")
                print(f"✅ 全局 kb 已注册: {link}（已更新 {shell_rc.name}）")
            else:
                print(f"✅ 全局 kb 已注册: {link}")
        else:
            print(f"✅ 全局 kb 已注册: {link}（确保 {bin_dir} 在 PATH 中）")
    except OSError as e:
        print(f"⚠️ 注册全局 kb 失败: {e}（不影响安装）", file=sys.stderr)


def verify_install(vault):
    """调用 verify.py 自检。返回 True 表示通过。

    注意：超时/异常一律按**失败**返回。以前它们都 `return True`（timeout 只有 60s，
    而 verify.py 内部自己就要跑 `kb help` 15s + `rag query` 30s），于是「装坏了也算成功」，
    安装器的退出码失去意义 —— 这也正是 kb.cmd 的 Windows 缺陷长期没被发现的原因。
    """
    verify_script = REPO_DIR / "scripts" / "verify.py"
    if not verify_script.exists():
        print("⚠️ verify.py 不存在，跳过自检。", file=sys.stderr)
        return True
    try:
        r = subprocess.run(
            [sys.executable, str(verify_script), "--vault", str(vault)],
            timeout=180,   # 要容纳 verify.py 内部 15s + 30s 的子步骤并留余量
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print("⚠️ 自检超时 —— 无法确认安装完整性，按失败处理。", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠️ 自检异常，按失败处理: {e}", file=sys.stderr)
        return False


def open_obsidian(vault):
    """通过 obsidian:// 协议唤起 Obsidian 打开 vault。"""
    import urllib.parse
    url = f"obsidian://open?path={urllib.parse.quote(str(vault))}"
    try:
        if sys.platform == "win32":
            os.startfile(url)  # noqa: S605
        elif sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        else:
            subprocess.run(["xdg-open", url], check=False)
        print(f"✅ 已尝试打开 Obsidian")
    except Exception:
        print("⚠️ 无法自动打开 Obsidian，请手动打开 Obsidian 后选择该文件夹。", file=sys.stderr)


def generate_homepage(vault):
    """知识库首页兜底生成（模板已含则跳过）。

    快捷卡会引用 `🏠-知识库首页.md`；精简分发包不含内容文件时，这里补一个最小
    总览页，保证卡片引用有效、上手路径完整。绝不覆盖模板自带首页。
    """
    page = vault / "🏠-知识库首页.md"
    if page.exists():
        return
    content = """\
---
tags: [moc, dashboard, toc]
status: active
domain: 综合
created: 2026-08-05
updated: 2026-08-05
importance: 1
kb_target: 🏠-知识库首页.md
kb_action: new
kb_summary: 🏠 知识库首页（总览 + 规则速览）
cssclass: dashboard
category: knowledge
---
# 🏠-知识库首页

> 这是一个**会自成长**的 Obsidian 知识库：你只管写，**成长引擎**负责维护。

## 结构（PARA + 治理层）

| 分区 | 用途 |
|------|------|
| `00-收件箱` | 原始输入，自然积压 |
| `10-项目` | 进行中的项目 |
| `20-技术` | 可复用技术沉淀 |
| `30-决策日志` | 决策 + 理由 |
| `40-资源库` | 通用方法论/资料 |
| `50-模板` | 模板（新建笔记时套用） |
| `60-运营` | 清洗/流转 SOP |
| `70-知识治理` | 元层：约定/指标/仪表盘 |
| `90-归档` | 收尾/过期 |

## 三条铁律

1. **写前 checkpoint**：任何 `apply`/`promote` 先 `--dry-run` 看清单。
2. **importance 只升不降**：防刷票。
3. **不 sweep Obsidian**：脚本只 `git add` 自己改的文件。

## 现在做什么

- 看 [[01-如何开始.md|🚀 如何开始]]
- 不懂技术也能看 [[如何使用.md|🙋 如何使用（面向第一次使用）]]
- 规则见 [[📖-知识库管理方案.md|📖-知识库管理方案]]
- 巡检见 `70-知识治理 Governance/_INDEX.md`（跑 `kb dashboard` 生成）
"""
    try:
        page.write_text(content, encoding="utf-8")
        print(f"✅ 已生成知识库首页: {page.name}")
    except OSError as e:
        print(f"⚠️ 生成知识库首页失败: {e}", file=sys.stderr)


def generate_quickstart_card(vault):
    """快捷卡兜底生成（模板已含则跳过）。"""
    card = vault / "如何使用.md"
    if card.exists():
        return
    content = """\
---
domain: 管理
status: active
importance: 0.5
created: 2026-09-22
updated: 2026-09-27
tags: ["管理"]
---
# 🚀 如何使用你的知识库

> 面向**第一次使用**的朋友，不需要技术背景。

## 你现在有什么

一个会**自己成长**的知识库：

- 往 `00-收件箱` 丢想法，引擎帮你**整理、路由、补链**
- 用 `kb query "问题"` 搜知识
- 每天自动**回忆**该复习的笔记
- 自动**巡检**死链、空目录、缺字段
- 自动**备份**

## 最常用的 5 件事

| 你想做 | 敲什么 |
|--------|--------|
| 提一个问题 | `kb query "你的问题"` |
| 写了新笔记后让它能被搜到 | `kb rag index` |
| 看收件箱要怎么整理 | `kb ingest` |
| 检查知识库健康度 | `kb healthcheck` |
| 今天该复习哪些 | `kb recall` |

> `kb` 默认以自身所在目录为知识库；安装器已把它注册为全局命令。
> Windows 用 `kb.cmd`，或直接 `kb`（已加入 PATH）。

> 进阶：`kb clean refine`（一键整理：结构化+去重→分块→重建索引→校验）、
> `kb link moc`（生成知识地图）、`kb recall mark --grade good`（记录复习，SM-2 排期）。

## 在 Obsidian 里

1. 用 Obsidian 打开这个文件夹（安装时已自动打开）
2. 左侧文件树就是你的知识库结构
3. `🏠-知识库首页.md` 是总览页，从这里开始浏览
4. `00-收件箱 Inbox/` 是你丢想法的地方
5. `70-知识治理 Governance/` 有仪表盘与巡检报告

### 知识库的分区

| 文件夹 | 放什么 |
|--------|--------|
| `00-收件箱 Inbox` | 原始想法，随手丢进来 |
| `10-项目 Projects` | 进行中的项目 |
| `20-技术 Technology` | 可复用的技术沉淀 |
| `30-决策日志 Decisions` | 重要决策 + 理由 |
| `40-资源库 Resources` | 通用方法论、资料 |
| `50-模板 Templates` | 笔记模板 |
| `60-运营 Operations` | 清洗、流转 SOP |
| `70-知识治理 Governance` | 指标、巡检、仪表盘 |
| `90-归档 Archive` | 收尾、过期的内容 |

## 让知识库「自己跑」（可选）

把下面加进定时任务（Linux/macOS）：

```
40 4 * * *  /path/to/vault/scripts/growth_cron.sh /path/to/vault
```

它会每天自动：建索引 → 整理收件箱 → 算命中信号 → 出回忆清单 → 补链 → 刷仪表盘 →
同步/摄取 Agent 记忆。

> Windows（无 bash）不适用 `growth_cron.sh`，可用任务计划自行编排 `kb` 子命令。

## 常见问题

### kb 命令找不到？
- **Linux/macOS**：重启终端，或在知识库目录内用 `./kb`
- **Windows**：重启终端，或在知识库目录内用 `kb.cmd`

### 搜不到结果？
先跑 `kb rag index` 建索引。写了新笔记后也要重跑（增量）。

### 想看所有功能？
```
kb help     # 显示帮助
kb list     # 列出所有插件
```

### 想摄取其他 agent 的记忆？
```
kb agent detect            # 探测本机 agent
kb ingest agent --dry-run  # 预览（只读）
kb ingest agent            # 摄取
```

### 想备份？
```
kb backup daily    # 日常备份
kb backup weekly   # 周备份
```

## 一句话理念

**你只管往收件箱丢想法；引擎帮你清洗、路由、补链、回忆、巡检、备份。**

完整文档见 `README.md` 的「命令参考」。
"""
    try:
        card.write_text(content, encoding="utf-8")
        print(f"✅ 已生成快捷卡: {card.name}")
    except OSError as e:
        print(f"⚠️ 生成快捷卡失败: {e}", file=sys.stderr)


def main():
    args = parse_args()
    if not TEMPLATE_DIR.is_dir():
        print(f"❌ 模板目录不存在: {TEMPLATE_DIR}", file=sys.stderr)
        return 2

    vault = Path(args.vault).expanduser() if args.vault else Path.cwd() / "kb-kit"
    vault = vault.resolve()

    if not ensure_target(vault, args.force, assume_yes=args.yes):
        return 2
    if not copy_template(vault):
        return 2
    print(f"✅ 模板已复制到 {vault}")

    wire_agents(vault, interactive=args.interactive)

    if not args.no_demo:
        run_demo(vault)

    if not args.no_git:
        init_git(vault)

    if not args.no_global_kb:
        register_global_kb(vault)

    generate_homepage(vault)
    generate_quickstart_card(vault)
    write_install_manifest(vault)

    verify_ok = True
    if not args.no_verify:
        verify_ok = verify_install(vault)
        if not verify_ok:
            print("⚠️ 自检有失败项 → 本次安装以非 0 退出码结束（便于自动化发现「装坏了」）。\n"
                  "   请按上方提示修复；确认无碍可加 --no-verify 重跑。", file=sys.stderr)

    if not args.no_open:
        open_obsidian(vault)

    launch = "./kb.cmd" if sys.platform == "win32" else "./kb"
    print("\n🎉 安装完成！" if verify_ok else "\n📦 安装步骤已执行完，但自检未全绿：")
    print(f"  知识库位置: {vault}")
    print(f"  提问:       {launch} query '如何备份知识库'")
    print(f"  快捷卡:     {vault}/如何使用.md")
    print("  更多命令:   kb help    巡检: kb healthcheck    仪表盘: kb dashboard")
    return 0 if verify_ok else 1


if __name__ == "__main__":
    sys.exit(main())
