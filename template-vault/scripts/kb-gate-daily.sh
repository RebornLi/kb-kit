#!/usr/bin/env bash
# ============================================================
# kb-gate-daily.sh — 写入前质量门禁的每日扫描（由 OpenClaw 自动化调用）
#
# 干什么：跑 `kb gate check`，把 pass / quarantine / reject 三态分布与**具体条目**列出来。
#   设计取舍：reject **只用于不变量**（无可追溯来源的编译产物 / 缺必填 / 空正文或纯占位符）；
#   噪声一律 quarantine（入库但标记降权 + 进复核队列）—— 个人库里"误拒不可恢复、
#   误收可用降权挽回"，所以除不变量外一律不拒。
#
# 只有在**出现 reject** 时才在简报里显著提示（reject 是不变量被破坏，必须处理）；
#   quarantine 只报数量，避免每日噪声。
# ⚠️ 退出码始终为 0（同其它 cron 脚本约定）。
# ============================================================
set -uo pipefail

VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"
PY="${PYTHON:-python3}"
GATE_ENABLE="${GATE_ENABLE:-1}"
PIPE="$VAULT/pipeline"

echo "===== 质量门禁 $(date '+%F %H:%M') ====="
echo

if [ ! -x "$VAULT/kb" ]; then
  echo "【结论】❌ 库路径异常：$VAULT（缺 kb 启动器）"; exit 0
fi
if [ "$GATE_ENABLE" != "1" ]; then
  echo "【结论】⏸ 已关闭（GATE_ENABLE != 1）"; exit 0
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
timeout 900 "$PY" "$PIPE/gate.py" check --root "$VAULT" --json >"$TMP/g.json" 2>&1
RC=$?

if [ "$RC" -ne 0 ] || [ ! -s "$TMP/g.json" ]; then
  echo "  ❌ gate check 失败（rc=${RC}）"
  echo "【结论】❌ 质量门禁执行异常"
  echo "===== 报告结束 ====="
  exit 0
fi

"$PY" - "$TMP/g.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
c = d.get("counts") or {}
print(f"  三态分布：pass {c.get('pass', 0)} · quarantine {c.get('quarantine', 0)} · reject {c.get('reject', 0)}")
items = d.get("items") or []
rej = [x for x in items if x["verdict"] == "reject"]
qua = [x for x in items if x["verdict"] == "quarantine"]
if rej:
    print("\n  ❌ 触犯不变量的条目（必须处理）：")
    for x in rej[:10]:
        print(f"     {x['rel'][:58]}")
        for why in x["reasons"][:2]:
            print(f"        · {why}")
if qua:
    print("\n  ⚠️ 噪声嫌疑（已标记降权，进复核队列，**不算错误**）：")
    for x in qua[:6]:
        print(f"     {x['rel'][:58]} — {x['reasons'][0] if x['reasons'] else ''}")
print("\n【结论】" + ("❌ 有触犯不变量的条目" if rej else
                     ("⚠️ 有噪声嫌疑待复核" if qua else "✅ 全部通过")))
PYEOF

echo "===== 报告结束 ====="
exit 0
