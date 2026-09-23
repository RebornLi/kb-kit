#!/usr/bin/env python3
# ============================================================
# precheck.py —— 环境自检 + 自动补齐
#
# 检测 Python / Obsidian / git 三项依赖，缺失项自动安装。
# 供三平台安装器调用，也可独立运行：
#     python3 scripts/precheck.py
# ============================================================
import os
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional, List

# 同目录导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _download_utils import Platform, download, check_network, SHA256_HASHES
import setup_obsidian

# ============================================================
# 数据模型
# ============================================================
class CheckResult(NamedTuple):
    name: str
    found: bool
    path: Optional[str]
    version: Optional[str]
    auto_installed: bool
    error: Optional[str]


class PrecheckReport:
    def __init__(self, python: CheckResult, obsidian: CheckResult, git: CheckResult):
        self.python = python
        self.obsidian = obsidian
        self.git = git

    @property
    def all_passed(self) -> bool:
        return self.python.found  # Obsidian/git 均为可选，不再影响是否通过

    @property
    def failures(self) -> List[str]:
        result = []
        if not self.python.found:
            result.append(self.python.error or "Python 不可用")
        if not self.obsidian.found:
            result.append(self.obsidian.error or "Obsidian 不可用")
        return result


# ============================================================
# Python 检测
# ============================================================
def check_python() -> CheckResult:
    """检测 Python ≥ 3.8。"""
    candidates = [sys.executable]
    for name in ("python3", "python", "py"):
        p = shutil.which(name)
        if p and p not in candidates:
            candidates.append(p)

    for py in candidates:
        if not py or not Path(py).exists():
            continue
        try:
            r = subprocess.run([py, "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                continue
            output = (r.stdout + r.stderr).strip()
            m = re.match(r"Python\s+(\d+)\.(\d+)\.(\d+)", output)
            if not m:
                continue
            major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
            version = f"{major}.{minor}.{patch}"
            if major >= 3 and minor >= 8:
                return CheckResult("Python", True, py, version, False, None)
            return CheckResult("Python", False, py, version, False,
                               f"Python 版本过低（{version}），需要 3.8+。请升级: https://www.python.org/downloads/")
        except (subprocess.SubprocessError, OSError):
            continue

    return CheckResult("Python", False, None, None, False,
                       "未找到 Python。请安装 Python 3.8+: https://www.python.org/downloads/")


# ============================================================
# Python 自动安装
# ============================================================
def install_python() -> CheckResult:
    """按平台自动安装 Python。"""
    plat = Platform.current()
    print("▶ Python 未安装，尝试自动安装……")

    if not check_network():
        print(f"❌ 网络不可达，无法自动安装 Python。", file=sys.stderr)
        print(f"   请手动安装: https://www.python.org/downloads/", file=sys.stderr)
        return CheckResult("Python", False, None, None, False,
                           "网络不可达，请手动安装 Python 3.8+: https://www.python.org/downloads/")

    if plat == "linux":
        return _install_python_linux()
    if plat == "macos":
        return _install_python_macos()
    if plat == "windows":
        return _install_python_windows()
    return CheckResult("Python", False, None, None, False, f"不支持的平台: {plat}")


def _install_python_linux() -> CheckResult:
    """Linux: 优先 apt-get / yum / dnf。"""
    for cmd in (
        ["sudo", "apt-get", "install", "-y", "python3"],
        ["sudo", "yum", "install", "-y", "python3"],
        ["sudo", "dnf", "install", "-y", "python3"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                result = check_python()
                if result.found:
                    print(f"✅ Python 已自动安装: {result.version}")
                    return result._replace(auto_installed=True)
        except (subprocess.SubprocessError, OSError):
            continue
    print("❌ 自动安装失败。请手动安装:", file=sys.stderr)
    print("   Ubuntu/Debian: sudo apt-get install python3", file=sys.stderr)
    print("   CentOS/RHEL:   sudo yum install python3", file=sys.stderr)
    print("   或下载: https://www.python.org/downloads/", file=sys.stderr)
    return CheckResult("Python", False, None, None, False,
                       "自动安装失败，请手动安装 Python 3.8+: https://www.python.org/downloads/")


def _install_python_macos() -> CheckResult:
    """macOS: 优先 brew。"""
    for cmd in (
        ["brew", "install", "python@3.12"],
        ["brew", "install", "python3"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                result = check_python()
                if result.found:
                    print(f"✅ Python 已自动安装: {result.version}")
                    return result._replace(auto_installed=True)
        except (subprocess.SubprocessError, OSError):
            continue
    print("❌ 自动安装失败。请手动安装:", file=sys.stderr)
    print("   安装 Homebrew: https://brew.sh/", file=sys.stderr)
    print("   然后: brew install python3", file=sys.stderr)
    print("   或下载: https://www.python.org/downloads/", file=sys.stderr)
    return CheckResult("Python", False, None, None, False,
                       "自动安装失败，请手动安装 Python 3.8+: https://www.python.org/downloads/")


def _install_python_windows() -> CheckResult:
    """Windows: 下载官方安装器静默安装。"""
    import urllib.request
    import json

    # 获取最新版本
    version = "3.12.7"
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/python/cpython/releases/latest",
            headers={"User-Agent": "kb-kit-installer/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            if tag.startswith("v"):
                version = tag[1:]
    except Exception:
        pass

    url = f"https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe"
    tmp = Path(os.environ.get("TEMP", ".")) / f"python-{version}-installer.exe"
    expected = SHA256_HASHES.get("python_windows_x86_64")

    print(f"   下载: {url}")
    if not download(url, tmp, expected_sha256=expected):
        print(f"❌ 下载失败。请手动安装: https://www.python.org/downloads/", file=sys.stderr)
        return CheckResult("Python", False, None, None, False,
                           "下载失败，请手动安装 Python 3.8+: https://www.python.org/downloads/")

    try:
        r = subprocess.run(
            [str(tmp), "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=0"],
            timeout=180, capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"❌ 静默安装失败（退出码 {r.returncode}）", file=sys.stderr)
            return CheckResult("Python", False, None, None, False,
                               "静默安装失败，请手动安装: https://www.python.org/downloads/")
        tmp.unlink(missing_ok=True)
    except Exception as e:
        print(f"❌ 安装失败: {e}", file=sys.stderr)
        return CheckResult("Python", False, None, None, False,
                           f"安装失败: {e}。请手动安装: https://www.python.org/downloads/")

    # 重新检测（刷新 PATH）
    result = check_python()
    if result.found:
        print(f"✅ Python 已自动安装: {result.version}")
        return result._replace(auto_installed=True)
    return CheckResult("Python", False, None, None, False,
                       "安装后未探测到，请重启终端后重试: https://www.python.org/downloads/")


# ============================================================
# Obsidian 检测
# ============================================================
def check_obsidian() -> CheckResult:
    """检测 Obsidian，未装则自动安装。"""
    found, path = setup_obsidian.detect()
    if found:
        return CheckResult("Obsidian", True, str(path), None, False, None)

    print("  Obsidian 未安装，尝试自动安装……")
    ok, path = setup_obsidian.download_and_install()
    if ok and path:
        return CheckResult("Obsidian", True, str(path), None, True, None)
    return CheckResult("Obsidian", False, None, None, False,
                       f"Obsidian 自动安装失败，请手动安装: {setup_obsidian.OBSIDIAN_DOWNLOAD_PAGE}")


# ============================================================
# git 检测（可选）
# ============================================================
def check_git() -> CheckResult:
    """检测 git（可选，缺失不中断）。"""
    p = shutil.which("git")
    if p:
        try:
            r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5)
            version = r.stdout.strip() if r.returncode == 0 else None
            return CheckResult("git", True, p, version, False, None)
        except (subprocess.SubprocessError, OSError):
            return CheckResult("git", True, p, None, False, None)
    return CheckResult("git", False, None, None, False,
                       "git 为可选项，不影响安装（仅跳过版本管理）")


# ============================================================
# Agent 选择与外部插件安装
# ============================================================
# kb-kit 支持的 agent 列表 + 对应的外部插件（kb-kit 仓库内的子目录）
# 外部插件是给 agent 主机用的钩子/扩展，不是 kb-kit 内部插件
AGENT_PLUGINS: dict[str, dict] = {
    "dsh": {
        "label": "DeepSeek Harness (DSH)",
        "plugin_dir": "kb-context-dsh",       # kb-kit-pure/ 下的子目录
        "deploy_doc": "DEPLOY.md",
        "detect_hint": "~/.dsh/storages/memory.json",
    },
    "openclaw": {
        "label": "OpenClaw",
        "plugin_dir": None,                    # 暂无内置外部插件
        "deploy_doc": None,
        "detect_hint": "~/桌面/桌面文件/openclaw1/workspace",
    },
    "codex": {
        "label": "OpenAI Codex",
        "plugin_dir": None,
        "deploy_doc": None,
        "detect_hint": "~/.codex/*.sqlite",
    },
    "hermes": {
        "label": "Hermes",
        "plugin_dir": None,
        "deploy_doc": None,
        "detect_hint": "~/.hermes/memories/MEMORY.md",
    },
}


# 安装期 agent 选择的全局临时偏好（$HOME 标准位置，非写死私人路径）：
# precheck 写入"要禁用的 agent"，create_vault 读取后一次性消费（删除），
# 仅对本次 install 会话生效；precheck 时未探测到的 agent 默认启用。
_PREFS_PATH = Path.home() / ".kb-kit-agent-prefs.json"


def _save_agent_disabled_prefs(disabled_names) -> None:
    """把安装期明确要禁用的 agent 写入全局临时偏好文件。"""
    try:
        _PREFS_PATH.write_text(
            json.dumps({"disabled": sorted(disabled_names)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # 偏好文件不可写不影响安装


def select_and_install_agents(vault: Optional[Path] = None) -> int:
    """
    探测本机 agent，让用户选择要接入的，选定后：
    1. 写入 kb-agent.json 注册表（启用选中的）
    2. 对有外部插件的 agent（如 DSH → kb-context-dsh），部署插件到 agent 主机
    返回选中的 agent 名称列表。
    """
    # 导入 agent_registry（在 template-vault/pipeline/ 下）
    repo_dir = Path(__file__).resolve().parent.parent
    pipe_dir = repo_dir / "template-vault" / "pipeline"
    sys.path.insert(0, str(pipe_dir))
    try:
        import agent_registry as ar
    except ImportError as e:
        print(f"⚠️ 无法加载 agent_registry: {e}，跳过 agent 选择", file=sys.stderr)
        return 0

    print("\n" + "=" * 50)
    print("  Agent 接入选择")
    print("=" * 50)

    agents = ar.detect_agents()
    found = [a for a in agents if a.get("found")]
    not_found = [a for a in agents if not a.get("found")]

    if not found:
        print("  未探测到任何已安装的 agent。")
        if not_found:
            print("  支持的 agent 及探测位置：")
            for a in not_found:
                info = AGENT_PLUGINS.get(a["name"], {})
                hint = info.get("detect_hint", "")
                print(f"    · {a['label']}：{hint}")
        print("  安装对应 agent 后可重新运行 `kb agent detect` 探测。")
        return 0

    # 显示探测到的 agent
    print("  探测到以下 agent：")
    for i, a in enumerate(found, 1):
        info = AGENT_PLUGINS.get(a["name"], {})
        plugin_tag = "（含外部插件）" if info.get("plugin_dir") else ""
        print(f"    [{i}] {a['label']}{plugin_tag}")
        srcs = a.get("sources", {})
        if isinstance(srcs, dict):
            for k, v in srcs.items():
                print(f"        - {k}: {v}")

    # 交互选择
    print("\n  选择要接入的 agent：")
    print("    - 回车 = 全部启用")
    print("    - 输入序号（逗号分隔）= 启用指定项，如 1,3")
    print("    - 输入 0 = 不启用任何 agent")
    try:
        ans = input("  你的选择: ").strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""

    if ans == "0":
        print("  不启用任何 agent。")
        _save_agent_disabled_prefs([a["name"] for a in found])
        if vault:
            ar.save_registry(vault, {"version": 1, "agents": {}})
        return 0

    # 解析选择
    if ans == "":
        selected = found
    else:
        indices = set()
        for part in ans.split(","):
            part = part.strip()
            if part.isdigit():
                indices.add(int(part))
        selected = [a for i, a in enumerate(found, 1) if i in indices]
        if not selected:
            print("  无有效选择，默认全部启用。")
            selected = found

    # 写入注册表
    if vault:
        reg_agents = {
            a["name"]: {
                "type": a["type"],
                "label": a["label"],
                "enabled": True,
                "sources": a.get("sources", {}),
            }
            for a in selected
        }
        ar.save_registry(vault, {"version": 1, "agents": reg_agents})
        print(f"\n  ✅ 已注册 {len(selected)} 个 agent: {', '.join(a['name'] for a in selected)}")

    # 部署外部插件
    print("\n  部署外部插件……")
    for a in selected:
        info = AGENT_PLUGINS.get(a["name"], {})
        plugin_dir = info.get("plugin_dir")
        if not plugin_dir:
            print(f"    · {a['label']}: 无外部插件，跳过")
            continue
        _deploy_agent_plugin(a["name"], info)

    # 把"安装期要禁用的 agent"写入全局临时偏好，供 create_vault 消费
    _save_agent_disabled_prefs(
        [a["name"] for a in found if a["name"] not in {s["name"] for s in selected}]
    )

    return len(selected)


def _deploy_agent_plugin(agent_name: str, info: dict) -> bool:
    """部署 agent 外部插件到 agent 主机。"""
    repo_dir = Path(__file__).resolve().parent.parent
    plugin_path = repo_dir / info["plugin_dir"]

    print(f"    · {info['label']}: 外部插件 {info['plugin_dir']}")

    if not plugin_path.is_dir():
        print(f"      ⚠️ 插件目录不存在: {plugin_path}，跳过")
        return False

    # 显示部署说明
    deploy_doc = plugin_path / (info.get("deploy_doc") or "DEPLOY.md")
    if deploy_doc.exists():
        print(f"      📖 部署说明: {deploy_doc}")

    # DSH 插件特殊处理：提示用户如何部署到 DSH 主机
    if agent_name == "dsh":
        print(f"      部署方式：")
        print(f"        1. 将插件路径加入 DSH profile:")
        print(f"           dsh.profile.bundles += \"{plugin_path}\"")
        print(f"        2. 重载 DSH profile:")
        print(f"           dsh profile reload")
        # 检查是否已部署（简单检查：DSH 配置中是否含该路径）
        dsh_config = Path.home() / ".dsh" / "config.json"
        if dsh_config.exists():
            try:
                content = dsh_config.read_text()
                if str(plugin_path) in content or info["plugin_dir"] in content:
                    print(f"      ✅ 已部署到 DSH 主机")
                    return True
            except OSError:
                pass
        print(f"      ⚠️ 未检测到部署，请按上述步骤手动部署")
        return False

    # 通用：提示插件路径
    print(f"      插件路径: {plugin_path}")
    return True


# ============================================================
# 主入口
# ============================================================
def run() -> int:
    print("=" * 50)
    print("  环境检查")
    print("=" * 50)

    # 1. Python
    py = check_python()
    if py.found:
        print(f"  ✅ Python {py.version} ({py.path})")
    else:
        print(f"  ❌ Python: {py.error}")
        py = install_python()
        if not py.found:
            print(f"\n❌ Python 环境无法就绪，安装终止。", file=sys.stderr)
            return 1

    # 2. Obsidian（可选：缺失不阻断安装，kb query 不依赖它）
    obs = check_obsidian()
    if obs.found:
        tag = "（自动安装）" if obs.auto_installed else ""
        print(f"  ✅ Obsidian{tag}: {obs.path}")
    else:
        print(f"  ⚠️ Obsidian 未检测到（可选，不影响 kb query；可稍后手动打开）：{obs.error}")

    # 3. git（可选）
    git = check_git()
    if git.found:
        print(f"  ✅ git {git.version or ''}")
    else:
        print(f"  ⚠️ {git.error}")

    print("=" * 50)
    report = PrecheckReport(py, obs, git)
    if not report.all_passed:
        return 1 if not py.found else 2

    # 4. Agent 选择与外部插件安装
    select_and_install_agents()

    print("\n  环境就绪，继续安装知识库……")
    return 0


if __name__ == "__main__":
    sys.exit(run())
