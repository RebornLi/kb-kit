#!/bin/sh
# kb-kit 定期验收：跑 kb verify，只有**失败**才输出（成功静默），便于 cron 只在异常时提醒
#
# P1-3 修复（2026-10-22）——原实现有三个缺陷，都会让这个脚本**谎报全绿**：
#
#   ① `cd "$VAULT" && kb verify`：`kb` 的 --root 优先级是
#      `--root` > `KB_ROOT` > 脚本所在目录。只要环境里有 KB_ROOT
#      （本机就是全局设了的），目标库就被它劫持 —— 脚本验的是**另一个库**。
#      实测：删掉目标库的契约文件后，本脚本仍输出"五项全绿"。
#      修法：显式传 `--root "$VAULT"`，并清掉子环境的 KB_ROOT。
#
#   ② `kb` 未必在 PATH 里：cron 的 PATH 通常只有 /usr/bin:/bin，
#      而 `kb` 是安装器写到 vault 目录的。取不到就 command not found，
#      $?=127 被当成"验收失败"，可 $OUT 里没有 JSON，报告只剩半截错误。
#      修法：优先用 `$VAULT/kb`，其次 PATH 里的 `kb`，都没有则明确报错。
#
#   ③ 退出码语义混淆：`timeout` 超时返回 124，业务失败返回 1，
#      两者在报告里分不清"库坏了"还是"只是跑得久"。
#      修法：分开判定，超时单列一种结论（超时不等于库坏）。
#
# 用法：kb-verify-cron.sh [vault_root]
#   优先级：$1 > KB_ROOT > 脚本自身所在库（scripts/ 的上一级）
# ============================================================
set -u

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="${1:-${KB_ROOT:-$SELF_DIR/..}}"

# ①：目标库必须存在，否则后面全是无意义的错误
if [ ! -d "$VAULT" ]; then
  echo "===== KB 验收失败 $(date '+%F %H:%M') ====="
  echo "  ❌ 目标库目录不存在: $VAULT"
  echo "===== 报告结束 ====="
  exit 1
fi

# ②：定位 kb 启动器（库内优先，其次 PATH）
if [ -x "$VAULT/kb" ]; then
  KB="$VAULT/kb"
elif command -v kb >/dev/null 2>&1; then
  KB="kb"
else
  echo "===== KB 验收失败 $(date '+%F %H:%M') ====="
  echo "  ❌ 找不到 kb 启动器（$VAULT/kb 不存在，PATH 里也没有 kb）"
  echo "     修复：在库目录内跑一次安装器，或把库目录加入 PATH"
  echo "===== 报告结束 ====="
  exit 1
fi

# ①：清掉 KB_ROOT，避免它劫持 --root；并显式指定目标库
OUT="$(env -u KB_ROOT timeout 900 "$KB" verify --root "$VAULT" --json 2>&1)"
CODE=$?

if [ "$CODE" -eq 0 ]; then
  echo "OK $(date '+%F %H:%M') 五项全绿  (root=$VAULT)"
  exit 0
fi

echo "===== KB 验收失败 $(date '+%F %H:%M')  root=$VAULT ====="
# ③：超时与业务失败分开陈述
if [ "$CODE" -eq 124 ]; then
  echo "  ⏱️ 验收超时（900s）——这不代表库坏了，可能是索引太大或机器正忙"
  echo "     建议：单独跑一次 kb verify 看是慢还是卡死"
fi

# 逐条列出失败的检查项；解析不了就原文输出（截断）
echo "$OUT" | env -u KB_ROOT python3 -c "
import json,sys
raw=sys.stdin.read()
try:
    d=json.loads(raw)
    checks=d.get('checks') or []
    if not checks:
        print('  ⚠️ 输出里没有 checks 字段，原文前 600 字：')
        print(raw[:600])
    for c in checks:
        if not c.get('pass'):
            print(f\"  ❌ {c.get('name')}: {c.get('detail')}\")
except Exception:
    print('  ⚠️ 输出不是合法 JSON（可能是启动器崩溃），原文前 600 字：')
    print(raw[:600])
"
echo "===== 报告结束 ====="
exit 1
