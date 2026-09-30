#!/bin/sh
# kb-kit 定期验收：跑 kb verify，只有**失败**才输出（成功静默），便于 cron 只在异常时提醒
VAULT="${1:-/home/mushan/kb-kit-pure}"
OUT="$(cd "$VAULT" && timeout 900 kb verify --json 2>&1)"
CODE=$?
if [ "$CODE" -ne 0 ]; then
  echo "===== KB 验收失败 $(date '+%F %H:%M') ====="
  echo "$OUT" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    for c in d['checks']:
        if not c['pass']: print(f\"  ❌ {c['name']}: {c['detail']}\")
except Exception:
    print(sys.stdin.read()[:800])
"
  echo "===== 报告结束 ====="
  exit 1
fi
echo "OK $(date '+%F %H:%M') 五项全绿"
