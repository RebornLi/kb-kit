#!/usr/bin/env python3
# ============================================================
# _download_utils.py —— 下载 / SHA256 校验 / 平台检测共享工具
#
# 供 precheck.py 和 setup_obsidian.py 复用，仅用 Python 标准库。
# ============================================================
import hashlib
import platform
import shutil
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import NamedTuple, Optional


# ============================================================
# 平台检测
# ============================================================
class Platform:
    """平台检测封装（不写死私人路径）。"""

    @staticmethod
    def current() -> str:
        """返回 'linux' | 'macos' | 'windows'。"""
        if sys.platform == "win32":
            return "windows"
        if sys.platform == "darwin":
            return "macos"
        return "linux"

    @staticmethod
    def arch() -> str:
        """返回 'x86_64' | 'aarch64' | 'arm64'。"""
        m = platform.machine().lower()
        if m in ("x86_64", "amd64"):
            return "x86_64"
        if m in ("aarch64", "arm64"):
            return "aarch64"
        return m or "x86_64"


# ============================================================
# 下载元数据
# ============================================================
class DownloadMeta(NamedTuple):
    url: str
    dest: Path
    expected_sha256: Optional[str]
    official_page: str


# SHA256 哈希常量（发布前填入实际值；None 表示跳过校验）
# 键格式: "<product>_<platform>_<arch>"
SHA256_HASHES: dict[str, Optional[str]] = {
    "python_windows_x86_64": None,
    "python_windows_aarch64": None,
    "obsidian_linux_x86_64": None,
    "obsidian_linux_aarch64": None,
    "obsidian_macos_x86_64": None,
    "obsidian_macos_aarch64": None,
    "obsidian_windows_x86_64": None,
    "obsidian_windows_aarch64": None,
}


# ============================================================
# 网络连通性
# ============================================================
def check_network() -> bool:
    """检测网络是否可用（3 秒超时，不抛异常）。"""
    try:
        req = urllib.request.Request("https://www.python.org", method="HEAD")
        with urllib.request.urlopen(req, timeout=3) as _:
            return True
    except Exception:
        return False


# ============================================================
# SHA256 校验
# ============================================================
def verify_sha256(filepath: Path, expected: str) -> bool:
    """流式计算文件 SHA256 并与期望值比较。"""
    if not filepath.exists():
        return False
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest().lower() == expected.lower()


# ============================================================
# 文件下载
# ============================================================
def download(url: str, dest: Path, expected_sha256: Optional[str] = None,
             show_progress: bool = True) -> bool:
    """
    下载文件到 dest，可选校验 SHA256。
    返回 True 成功（含校验通过），False 失败。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kb-kit-installer/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if show_progress and total > 0:
                        pct = done * 100 // total
                        sys.stdout.write(f"\r   下载中: {done // 1024}KB / {total // 1024}KB ({pct}%)")
                        sys.stdout.flush()
            if show_progress and total > 0:
                sys.stdout.write("\n")
                sys.stdout.flush()
    except urllib.error.URLError as e:
        print(f"❌ 下载失败: {e}", file=sys.stderr)
        _safe_unlink(dest)
        return False
    except OSError as e:
        print(f"❌ 写文件失败: {e}", file=sys.stderr)
        _safe_unlink(dest)
        return False

    if expected_sha256:
        if not verify_sha256(dest, expected_sha256):
            print(f"❌ SHA256 校验失败: {dest}", file=sys.stderr)
            _safe_unlink(dest)
            return False
        if show_progress:
            print(f"   ✅ SHA256 校验通过")

    return True


def _safe_unlink(p: Path) -> None:
    try:
        Path(p).unlink()
    except OSError:
        pass


# ============================================================
# 自检
# ============================================================
if __name__ == "__main__":
    print(f"平台: {Platform.current()} / {Platform.arch()}")
    print(f"网络: {'可用' if check_network() else '不可用'}")
