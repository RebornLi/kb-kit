#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backup.py —— 全量快照（跨平台，零依赖）。

原 scripts/backup_now.sh 只能跑在 bash 上，Windows 的 kb.cmd 无法调用。
本模块用纯标准库实现同一产物契约，供 `kb backup` 在所有平台统一调用：

  产出: backups/<daily|weekly>/<YYYY-MM-DD>-vault.zip  (+ .sha256)
  契约: 真实 zip（排除 .git 与现有 backups/，避免递归打包）；
        .sha256 为标准 `hash  文件名` 两空格格式，dashboard/drill 可直接
        `sha256sum -c` 校验。

用法:
  python3 pipeline/backup.py [--root R] [--kind daily|weekly]
  python3 pipeline/backup.py [--root R] [daily|weekly]
"""
import argparse
import hashlib
import os
import sys
import zipfile
from datetime import date
from pathlib import Path

# 与 backup_now.sh 保持一致：任意层级的 .git / backups 目录都不入包
EXCLUDE_DIRS = {".git", "backups"}
KINDS = ("daily", "weekly")


def make_backup(vault, kind="daily"):
    """生成一份全量快照，返回退出码。"""
    if kind not in KINDS:
        print(f"❌ 未知快照类型: {kind}（可选: {', '.join(KINDS)}）", file=sys.stderr)
        return 2

    vault = Path(vault).resolve()
    if not vault.is_dir():
        print(f"❌ vault 目录不存在: {vault}", file=sys.stderr)
        return 2

    dst = vault / "backups" / kind
    dst.mkdir(parents=True, exist_ok=True)
    base = f"{date.today():%Y-%m-%d}-vault.zip"
    out = dst / base

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dn, fn in os.walk(vault):
            dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
            for f in fn:
                full = Path(dp) / f
                # 悬空软链（目标已删）跳过，不中断整包
                if full.is_symlink() and not full.exists():
                    continue
                try:
                    z.write(full, os.path.relpath(full, vault))
                except (OSError, ValueError):
                    continue  # 个别文件读失败也跳过（best-effort）

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    # 标准 `hash  文件名`（两空格）格式，sha256sum -c 可直接校验
    (dst / f"{base}.sha256").write_text(f"{sha}  {base}\n", encoding="utf-8")

    size = out.stat().st_size
    print(f"[backup] {out}  ({size} bytes)  sha256={sha}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="全量快照（zip + sha256，跨平台）")
    ap.add_argument("--root",
                    default=os.environ.get("KB_ROOT") or str(Path(__file__).resolve().parents[1]),
                    help="vault 根目录（默认取 KB_ROOT 或脚本上一级）")
    ap.add_argument("--kind", dest="kind_opt", choices=KINDS, default=None,
                    help="快照类型（daily/weekly）")
    ap.add_argument("kind", nargs="?", choices=KINDS, default=None,
                    help="快照类型（位置参数，兼容旧 backup_now.sh 用法）")
    args = ap.parse_args()
    kind = args.kind_opt or args.kind or "daily"
    return make_backup(args.root, kind)


if __name__ == "__main__":
    sys.exit(main())
