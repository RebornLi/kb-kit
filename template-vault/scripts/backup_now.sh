#!/usr/bin/env bash
# ============================================================
# backup_now.sh —— 全量快照（兼容包装）
#
# 真正的实现已迁到跨平台的 pipeline/backup.py（Windows 的 kb.cmd 也能用）。
# 本脚本保留为兼容入口，供 cron / 旧文档 / 手动调用：
#     scripts/backup_now.sh [vault_root] [daily|weekly]
# 产物同前：backups/<daily|weekly>/<date>-vault.zip (+ .sha256)
# ============================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="${1:-$SELF_DIR/..}"
KIND="${2:-daily}"

PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
elif command -v py >/dev/null 2>&1; then PY=py
fi
if [ -z "$PY" ]; then
  echo "❌ 未找到 Python，无法备份。" >&2; exit 3
fi

exec "$PY" "$VAULT/pipeline/backup.py" --root "$VAULT" --kind "$KIND"
