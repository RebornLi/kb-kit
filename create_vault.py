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
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = REPO_DIR / "template-vault"
STATE_IGNORE = shutil.ignore_patterns(
    ".git", ".git/*", "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info"
)


def parse_args():
    ap = argparse.ArgumentParser(description="一键搭建知识库 · 安装引擎")
    ap.add_argument("--vault", default=None,
                    help="目标知识库目录（绝对或相对路径；留空=当前目录/kb-kit）")
    ap.add_argument("--no-git", action="store_true", help="不初始化 git")
    ap.add_argument("--no-demo", action="store_true", help="不跑 demo（不建向量索引）")
    ap.add_argument("--force", action="store_true", help="目标目录非空时也覆盖安装")
    ap.add_argument("--interactive", action="store_true",
                    help="交互模式：agent 探测时提问选择（默认非交互，全部启用）")
    ap.add_argument("--no-verify", action="store_true", help="跳过安装自检")
    ap.add_argument("--no-open", action="store_true", help="不自动打开 Obsidian")
    ap.add_argument("--no-global-kb", action="store_true", help="不注册全局 kb 命令")
    return ap.parse_args()


def ensure_target(vault, force):
    """目标不存在则新建；为空则复用；非空且未 --force 则拒绝。"""
    if vault.exists():
        if vault.is_file():
            print(f"❌ 目标已存在且是一个文件: {vault}", file=sys.stderr)
            return False
        if list(vault.iterdir()) and not force:
            print(f"❌ 目标目录非空: {vault}", file=sys.stderr)
            print("   选一个空目录，或加 --force 覆盖（会清空其中的文件）。", file=sys.stderr)
            return False
        if force:
            for child in vault.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
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


def wire_agents(vault, interactive):
    """探测本机 agent 并写入 kb-agent.json（注册表，运行时态，gitignore）。"""
    pipe = vault / "pipeline"
    sys.path.insert(0, str(pipe))
    try:
        import agent_registry as ar
    except Exception as e:  # noqa: BLE001 - 注册表加载失败不应让安装崩在复制之后
        print(f"⚠️ 无法加载 agent_registry（{e}）；可稍后手动 `kb ingest-agent --setup`。",
              file=sys.stderr)
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
    """调用 verify.py 自检。返回 True 表示通过。"""
    verify_script = REPO_DIR / "scripts" / "verify.py"
    if not verify_script.exists():
        print("⚠️ verify.py 不存在，跳过自检。", file=sys.stderr)
        return True
    try:
        r = subprocess.run(
            [sys.executable, str(verify_script), "--vault", str(vault)],
            timeout=60,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print("⚠️ 自检超时（不影响安装）。", file=sys.stderr)
        return True
    except Exception as e:
        print(f"⚠️ 自检异常: {e}（不影响安装）", file=sys.stderr)
        return True


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
updated: 2026-09-22
tags: ["管理"]
---
# 🚀 如何使用你的知识库

## 你现在有什么

一个会**自己成长**的知识库：
- 往 `00-收件箱` 丢想法，引擎帮你整理、路由、补链
- 用 `kb query "问题"` 搜知识
- 每天自动回忆该复习的笔记

## 最常用的 5 件事

| 你想做 | 敲什么 |
|--------|--------|
| 提一个问题 | `kb query "你的问题"` |
| 写了新笔记后让它能被搜到 | `kb rag index` |
| 看收件箱要怎么整理 | `kb ingest` |
| 检查知识库健康度 | `kb healthcheck` |
| 今天该复习哪些 | `kb recall` |

> `kb` 会自动找到你的知识库，不用记路径。

## 在 Obsidian 里

- 用 Obsidian 打开这个文件夹
- 左侧文件树就是你的知识库结构
- `🏠-知识库首页.md` 是总览页
- `00-收件箱 Inbox/` 是你丢想法的地方

## 常见问题

- **kb 命令找不到？** 重启终端，或在知识库目录内用 `./kb`
- **搜不到结果？** 先跑 `kb rag index` 建索引
- **看所有功能？** `kb help` 或 `kb list`
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

    if not ensure_target(vault, args.force):
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

    generate_quickstart_card(vault)

    if not args.no_verify:
        ok = verify_install(vault)
        if not ok:
            print("⚠️ 自检有失败项，请按上方提示修复。", file=sys.stderr)

    if not args.no_open:
        open_obsidian(vault)

    launch = "./kb.cmd" if sys.platform == "win32" else "./kb"
    print("\n🎉 安装完成！")
    print(f"  知识库位置: {vault}")
    print(f"  提问:       {launch} query '如何备份知识库'")
    print(f"  快捷卡:     {vault}/如何使用.md")
    print("  更多命令:   kb help    巡检: kb healthcheck    仪表盘: kb dashboard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
