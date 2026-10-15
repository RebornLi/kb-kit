#!/usr/bin/env bash
# ============================================================
# kb-ladder-daily.sh — L0–L5 反馈阶梯的每日产出（由 OpenClaw 自动化定时调用）
#
# 干什么：把库里**已经在积累的使用信号**加工成**待人工裁决的提案**。
#   为什么需要它：L0–L5 六个模块合计 6743 行（占 pipeline 的 25.9%），
#   在 2026-10-16 之前既没有 CLI 入口、也没有被任何调度调用 ——
#   生产库实测这条阶梯的全部状态/账本文件都不存在，即 **26% 的代码、0% 的动作**。
#   而同期 feedback_state.json 已积累 2039 个查询会话 / 1407 条命中记录，
#   且 2039 次查询只有 382 个不同问题（5.3× 重复），最高频的问题问了 73 次 ——
#   L1 缺口检测的前提信号充足到溢出，却零提案。本脚本就是把这条断点接上。
#
# 安全边界：**只产出提案，不修改任何笔记、不改变检索行为**。
#   落地的 `kb ladder apply --level L1 --id N` 必须人工确认 —— 有实测依据：
#   L1 契约正确时黄金集 rec@10 98.3%→100%，但契约指向无关笔记时 top1 90.0%→76.7%、
#   MRR 0.950→0.883。坏契约代价远大于"少一条好契约"，所以自动化停在"产出提案"。
#
# 闸门（任一不满足就静默跳过）：
#   1) 开关：LADDER_ENABLE=1（默认 1 —— 因为本脚本只读+产提案，无写入风险）
#   2) 空闲：1 分钟负载 < LADDER_MAX_LOAD（默认 4.0），忙时让路
#
# ⚠️ 退出码**始终为 0**（与 kb-daily-supervisor.sh / kb-curate.sh 同约定）：
#    让 OpenClaw 一定把简报投递出去；问题在正文里以 ❌/⚠️ 标注。
#
# 用法: kb-ladder-daily.sh [vault_root]
# ============================================================
set -uo pipefail

VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"
PY="${PYTHON:-python3}"
LADDER_ENABLE="${LADDER_ENABLE:-1}"
LADDER_MAX_LOAD="${LADDER_MAX_LOAD:-4.0}"
LADDER_SECONDS="${LADDER_SECONDS:-600}"
LADDER_ADJUDICATE="${LADDER_ADJUDICATE:-1}"        # 1=每日自动裁决（LLM+实测双闸）
LADDER_ADJUDGE_MAX="${LADDER_ADJUDGE_MAX:-20}"      # 单级单次落地上限
LADDER_ADJUDGE_SECONDS="${LADDER_ADJUDGE_SECONDS:-3600}"
PIPE="$VAULT/pipeline"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "===== 反馈阶梯 $(date '+%F %H:%M') ====="
echo

if [ ! -x "$VAULT/kb" ]; then
  echo "【结论】❌ 库路径异常：$VAULT（缺 kb 启动器）"; exit 0
fi

LOAD1="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
if [ "$LADDER_ENABLE" != "1" ]; then
  echo "【结论】⏸ 阶梯已关闭（LADDER_ENABLE != 1）"; exit 0
fi
if ! awk -v a="$LOAD1" -v b="$LADDER_MAX_LOAD" 'BEGIN{exit !(a < b)}'; then
  echo "【结论】⏸ 让路：1 分钟负载 ${LOAD1} ≥ ${LADDER_MAX_LOAD}"; exit 0
fi

# ---- 产出提案（只写提案文件，不碰笔记）----
echo "【运行】propose（负载 ${LOAD1}）"
timeout "$LADDER_SECONDS" "$PY" "$PIPE/ladder.py" propose --root "$VAULT" >"$TMP/propose.out" 2>&1
RC=$?
sed 's/^/  /' "$TMP/propose.out" | head -12

VERDICT="ok"; PROBLEMS=()
if [ "$RC" -eq 124 ]; then
  echo "  ⚠️ 超出 ${LADDER_SECONDS}s 预算，已产出的提案照常落盘"
  VERDICT="warn"; PROBLEMS+=("⚠️ 阶梯超时，剩余留给下一轮")
elif [ "$RC" -ne 0 ]; then
  echo "  ❌ ladder propose 退出码 ${RC}"
  VERDICT="fail"; PROBLEMS+=("❌ ladder propose 退出码 ${RC}")
fi
if grep -q '❌' "$TMP/propose.out" 2>/dev/null; then
  VERDICT="warn"; PROBLEMS+=("⚠️ 有级别产出失败（见上）")
fi

# ---- 本地模型自主裁决（LLM 语义 + 实测双闸）----
echo
echo "【自主裁决】"
if [ "${LADDER_ADJUDICATE:-1}" != "1" ]; then
  echo "  ⏸ 已关闭（LADDER_ADJUDICATE != 1）"
else
  timeout "$LADDER_ADJUDGE_SECONDS" "$PY" "$PIPE/adjudicate.py" run \
    --root "$VAULT" --level L1 --level L2 --level L3 \
    --max "${LADDER_ADJUDGE_MAX:-20}" >"$TMP/adj.out" 2>&1
  ARC=$?
  if [ "$ARC" -eq 124 ]; then
    echo "  ⚠️ 裁决超时（>${LADDER_ADJUDGE_SECONDS}s），已裁决的部分照常落盘"
    VERDICT="warn"; PROBLEMS+=("⚠️ 自主裁决超时")
  fi
  grep -E '^\s+(✅|❌) id=|→ 批准|裁判调用|⚠️|ℹ️' "$TMP/adj.out" 2>/dev/null | sed 's/^/  /' | tail -30
  if grep -q '裁判模型不可用' "$TMP/adj.out" 2>/dev/null; then
    echo "  ⚠️ 裁判模型不可用：本轮**未裁决**（不会无证据放行）"
    VERDICT="warn"; PROBLEMS+=("⚠️ 裁判模型不可用，本轮未裁决")
  fi
fi

# ---- 状态汇总 ----
echo
echo "【阶梯状态】"
timeout 300 "$PY" "$PIPE/ladder.py" status --root "$VAULT" 2>/dev/null \
  | sed -n '/L0/,/待人工裁决/p' | grep -v '^        ' | sed 's/^/  /'

echo
echo "【待人工裁决】"
PENDING="$(timeout 300 "$PY" "$PIPE/ladder.py" status --root "$VAULT" --json 2>/dev/null \
  | "$PY" -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print(0); raise SystemExit
def n(st):
    v = st.get("count", st.get("docket_count", st.get("pending_proposals"))) or st.get("n_pending") or 0
    return len(v) if isinstance(v, list) else int(v or 0)
print(sum(n(s) for s in d.values()))
' 2>/dev/null || echo 0)"
if [ "${PENDING:-0}" -gt 0 ]; then
  echo "  ${PENDING} 条提案在队列 —— **人工确认后才落地**（自动化边界就在这里）："
  echo "    看详情：kb ladder status"
  echo "    落地：  kb ladder apply --level L1 --id N"
else
  echo "  队列为空（信号不足或已全部裁决）"
fi

echo
case "$VERDICT" in
  ok)   echo "【结论】✅ 阶梯正常产出" ;;
  warn) echo "【结论】⚠️ 有需要关注项（见下）" ;;
  fail) echo "【结论】❌ 存在问题（见下）" ;;
esac
if [ "${#PROBLEMS[@]}" -gt 0 ]; then printf '%s\n' "${PROBLEMS[@]}" | sed 's/^/  /'; fi
echo "===== 报告结束 ====="
exit 0
