#!/usr/bin/env bash
# ============================================================
# healthcheck_cron.sh —— SOP-3 健康巡检定时（死链/骨架/标签越界/空目录/…）
#   跑: kb-healthcheck.py all → logs/health-DATE.log
#   kb-healthcheck.py 有问题 exit 1；本脚本记录退出码到日志但不 abort
#   （cron 语境下 abort 会发邮件噪声，巡检问题留日志供 dashboard 汇总）
#   用法: scripts/healthcheck_cron.sh <vault_root>
#   (或设 KB_ROOT 环境变量)
# ============================================================
set -uo pipefail
VAULT="${1:-${KB_ROOT:-}}"
if [ -z "$VAULT" ]; then
  echo "❌ 用法: $0 <vault_root>  (或设 KB_ROOT 环境变量)" >&2
  exit 2
fi
HC="$VAULT/pipeline/kb-healthcheck.py"
LOGDIR="$VAULT/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/health-$(date +%F).log"
python3 "$HC" all > "$LOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
  echo "[health $(date +%H:%M:%S)] ⚠ 巡检发现问题(退出码 $RC) · 输出见 $LOG" >> "$LOG"
fi
echo "[health $(date +%H:%M:%S)] 巡检完成(退出码 $RC) · 输出见 $LOG"
tail -4 "$LOG"
