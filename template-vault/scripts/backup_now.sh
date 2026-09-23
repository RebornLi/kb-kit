#!/usr/bin/env bash
# ============================================================
# backup_now.sh —— 手动全量快照（方案 v4.0 §2.2）
#   用法: scripts/backup_now.sh [vault_root] [daily|weekly]
#   产出: backups/<daily|weekly>/<date>.vault.zip  (+ .sha256)
#   真实 zip（排除 .git 与现有 backups/，避免递归打包）
# ============================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="${1:-$SELF_DIR/..}"
KIND="${2:-daily}"
[ "$KIND" = "weekly" ] && DST="$VAULT/backups/weekly" || DST="$VAULT/backups/daily"
mkdir -p "$DST"

BASE="$(date +%F)-vault.zip"
OUT="$DST/$BASE"

# 真实 zip 整仓快照（排除 .git 与 backups 自身）
python3 - "$VAULT" "$OUT" <<'PY'
import os, sys, zipfile
vault, out = sys.argv[1], sys.argv[2]
excl = {".git", "backups"}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if d not in excl]
        for f in fn:
            full = os.path.join(dp, f)
            if os.path.islink(full) and not os.path.exists(full):
                continue                     # 悬空软链（目标已删），跳过不中断整包
            try:
                z.write(full, os.path.relpath(full, vault))
            except (OSError, ValueError):
                continue                     # 个别文件读失败也跳过（best-effort）
PY

# 校验和（标准 `hash  文件名` 格式，可用 sha256sum -c 直接验；缺 sha256sum 则回退 openssl）
if command -v sha256sum >/dev/null 2>&1; then
  ( cd "$DST" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
else
  ( cd "$DST" && openssl dgst -sha256 "$(basename "$OUT")" | awk '{print $2"  "$(basename "$OUT")"}' > "$(basename "$OUT").sha256" )
fi

echo "[backup] $OUT  ($(du -h "$OUT" | cut -f1))  sha256=$(awk '{print $1}' "$OUT.sha256")"
