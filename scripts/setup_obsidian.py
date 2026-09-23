#!/usr/bin/env python3
# ============================================================
# setup_obsidian.py —— Obsidian 探测 / 下载 / 静默安装 / 协议注册
#
# 仅用 Python 标准库。供 precheck.py 调用，也可独立运行：
#     python3 scripts/setup_obsidian.py
# ============================================================
import os
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple

# 同目录导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _download_utils import Platform, download, verify_sha256, SHA256_HASHES, check_network

# ============================================================
# 常量
# ============================================================
OBSIDIAN_DOWNLOAD_PAGE = "https://obsidian.md/download"
OBSIDIAN_GITHUB_LATEST = "https://api.github.com/repos/obsidianmd/obsidian-releases/releases/latest"

# 已知安装路径（用 ~ 展开，不写死私人路径）
OBSIDIAN_PATHS: dict[str, list[str]] = {
    "linux": [
        "~/.local/bin/obsidian",
        "/usr/bin/obsidian",
        "/usr/local/bin/obsidian",
        "/opt/Obsidian/obsidian",
        "~/Applications/Obsidian.AppImage",
    ],
    "macos": [
        "/Applications/Obsidian.app",
        "~/Applications/Obsidian.app",
    ],
    "windows": [
        "%LOCALAPPDATA%\\Programs\\Obsidian\\Obsidian.exe",
        "%PROGRAMFILES%\\Obsidian\\Obsidian.exe",
        "%PROGRAMFILES(X86)%\\Obsidian\\Obsidian.exe",
    ],
}

# 回落版本（GitHub API 失败时用）
FALLBACK_VERSION = "1.4.16"


# ============================================================
# 探测
# ============================================================
def detect() -> Tuple[bool, Optional[Path]]:
    """探测 Obsidian 是否已安装。返回 (found, path)。"""
    plat = Platform.current()
    candidates = OBSIDIAN_PATHS.get(plat, [])
    for raw in candidates:
        p = _expand(raw)
        if not p:
            continue
        # 支持通配符（如 AppImage 版本号变化）
        if "*" in p.name:
            for match in sorted(p.parent.glob(p.name), reverse=True):
                if match.exists():
                    return True, match
        elif p.exists():
            return True, p
    # 额外：用 shutil.which 查 PATH
    which = shutil.which("obsidian") or shutil.which("Obsidian")
    if which:
        return True, Path(which)
    return False, None


def _expand(raw: str) -> Optional[Path]:
    """展开 ~ 和 %ENV%。"""
    if raw.startswith("~"):
        raw = os.path.expanduser(raw)
    if "%" in raw:
        # Windows 环境变量
        for k, v in os.environ.items():
            raw = raw.replace(f"%{k}%", v)
        if "%" in raw:
            return None  # 环境变量未定义
    return Path(raw)


# ============================================================
# 获取最新版本号
# ============================================================
def _get_latest_version() -> str:
    """从 GitHub API 获取最新版本号，失败回落。"""
    try:
        req = urllib.request.Request(
            OBSIDIAN_GITHUB_LATEST,
            headers={"User-Agent": "kb-kit-installer/1.0", "Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            # tag 形如 v1.4.16
            return tag.lstrip("v") or FALLBACK_VERSION
    except Exception:
        return FALLBACK_VERSION


# ============================================================
# 下载 + 安装
# ============================================================
def download_and_install() -> Tuple[bool, Optional[Path]]:
    """下载并静默安装 Obsidian。返回 (success, path)。"""
    if not check_network():
        print(f"❌ 网络不可达，无法自动下载 Obsidian。", file=sys.stderr)
        print(f"   请手动下载: {OBSIDIAN_DOWNLOAD_PAGE}", file=sys.stderr)
        return False, None

    plat = Platform.current()
    arch = Platform.arch()
    version = _get_latest_version()
    print(f"▶ 准备安装 Obsidian v{version}（{plat}/{arch}）……")

    if plat == "linux":
        return _install_linux(arch, version)
    if plat == "macos":
        return _install_macos(arch, version)
    if plat == "windows":
        return _install_windows(arch, version)
    print(f"❌ 不支持的平台: {plat}", file=sys.stderr)
    return False, None


def _install_linux(arch: str, version: str) -> Tuple[bool, Optional[Path]]:
    """Linux: 下载 AppImage → chmod +x → 移到 ~/.local/bin/obsidian。"""
    # Obsidian 官方 AppImage 命名: Obsidian-{version}.AppImage
    url = f"https://github.com/obsidianmd/obsidian-releases/releases/download/v{version}/Obsidian-{version}.AppImage"
    dest_dir = Path.home() / ".local" / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "obsidian"
    tmp = dest_dir / f"obsidian-{version}.AppImage"

    key = f"obsidian_linux_{arch}"
    expected = SHA256_HASHES.get(key)

    print(f"   下载: {url}")
    if not download(url, tmp, expected_sha256=expected):
        print(f"❌ 下载失败。请手动下载: {OBSIDIAN_DOWNLOAD_PAGE}", file=sys.stderr)
        return False, None

    try:
        tmp.chmod(0o755)
        if dest.exists():
            dest.unlink()
        tmp.rename(dest)
    except OSError as e:
        print(f"❌ 安装失败: {e}", file=sys.stderr)
        return False, None

    print(f"✅ Obsidian 已安装到 {dest}")
    return True, dest


def _install_macos(arch: str, version: str) -> Tuple[bool, Optional[Path]]:
    """macOS: 下载 .dmg → 挂载 → 复制 .app → 卸载。"""
    suffix = "arm64" if arch == "aarch64" else "x64"
    url = f"https://github.com/obsidianmd/obsidian-releases/releases/download/v{version}/Obsidian-{version}-{suffix}.dmg"
    tmp = Path("/tmp") / f"obsidian-{version}.dmg"
    dest = Path("/Applications/Obsidian.app")

    key = f"obsidian_macos_{arch}"
    expected = SHA256_HASHES.get(key)

    print(f"   下载: {url}")
    if not download(url, tmp, expected_sha256=expected):
        print(f"❌ 下载失败。请手动下载: {OBSIDIAN_DOWNLOAD_PAGE}", file=sys.stderr)
        return False, None

    try:
        # 挂载
        r = subprocess.run(["hdiutil", "attach", "-nobrowse", str(tmp)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"❌ 挂载 dmg 失败: {r.stderr}", file=sys.stderr)
            return False, None
        # 找挂载点
        mount_point = None
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                mount_point = parts[-1].strip()
        if not mount_point:
            print("❌ 无法确定挂载点", file=sys.stderr)
            return False, None

        src_app = Path(mount_point) / "Obsidian.app"
        if not src_app.exists():
            print(f"❌ dmg 中未找到 Obsidian.app", file=sys.stderr)
            subprocess.run(["hdiutil", "detach", mount_point], capture_output=True)
            return False, None

        # 复制
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src_app, dest, symlinks=True)
        subprocess.run(["hdiutil", "detach", mount_point], capture_output=True)
        tmp.unlink(missing_ok=True)
    except Exception as e:
        print(f"❌ 安装失败: {e}", file=sys.stderr)
        return False, None

    print(f"✅ Obsidian 已安装到 {dest}")
    return True, dest


def _install_windows(arch: str, version: str) -> Tuple[bool, Optional[Path]]:
    """Windows: 下载 .exe → 静默安装 /S。"""
    suffix = "arm64" if arch == "aarch64" else "x64"
    url = f"https://github.com/obsidianmd/obsidian-releases/releases/download/v{version}/Obsidian-{version}-{suffix}.exe"
    tmp = Path(os.environ.get("TEMP", ".")) / f"obsidian-{version}.exe"

    key = f"obsidian_windows_{arch}"
    expected = SHA256_HASHES.get(key)

    print(f"   下载: {url}")
    if not download(url, tmp, expected_sha256=expected):
        print(f"❌ 下载失败。请手动下载: {OBSIDIAN_DOWNLOAD_PAGE}", file=sys.stderr)
        return False, None

    try:
        r = subprocess.run([str(tmp), "/S"], timeout=120,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"❌ 静默安装失败（退出码 {r.returncode}）", file=sys.stderr)
            return False, None
        tmp.unlink(missing_ok=True)
    except Exception as e:
        print(f"❌ 安装失败: {e}", file=sys.stderr)
        return False, None

    # 重新探测
    found, path = detect()
    if found:
        print(f"✅ Obsidian 已安装到 {path}")
        return True, path
    print("⚠️ 安装完成但未探测到，请手动确认", file=sys.stderr)
    return False, None


# ============================================================
# 协议注册检查
# ============================================================
def ensure_protocol() -> bool:
    """检查 obsidian:// 协议是否注册。仅警告，不中断。"""
    plat = Platform.current()
    try:
        if plat == "linux":
            apps_dir = Path.home() / ".local" / "share" / "applications"
            if apps_dir.exists():
                for f in apps_dir.glob("*.desktop"):
                    if "obsidian" in f.read_text(errors="ignore").lower():
                        return True
            return False
        if plat == "macos":
            # macOS 安装 .app 时自动注册
            return Path("/Applications/Obsidian.app").exists()
        if plat == "windows":
            # 检查注册表
            r = subprocess.run(
                ["reg", "query", "HKCU\\Software\\Classes\\obsidian", "/v", "URL Protocol"],
                capture_output=True, text=True,
            )
            return r.returncode == 0
    except Exception:
        pass
    return False


# ============================================================
# 主入口
# ============================================================
def run() -> int:
    print("▶ 检查 Obsidian……")
    found, path = detect()
    if found:
        print(f"✅ Obsidian 已安装: {path}")
        if not ensure_protocol():
            print("⚠️ obsidian:// 协议未注册，自动打开功能可能不可用（不影响安装）")
        return 0

    print("  Obsidian 未安装，开始自动安装……")
    ok, path = download_and_install()
    if not ok:
        return 1

    if not ensure_protocol():
        print("⚠️ obsidian:// 协议未注册，自动打开功能可能不可用（不影响安装）")
    return 0


if __name__ == "__main__":
    sys.exit(run())
