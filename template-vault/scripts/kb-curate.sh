#!/usr/bin/env bash
# ============================================================
# kb-curate.sh — 知识结晶「空闲批处理」（由 OpenClaw 自动化定时调用）
#
# 干什么：把库里还带「日志味」的笔记，用空闲的本地 Agent（ornith1.5-35b）
#         炼成「结论 → 依据 → 操作 → 坑与边界」的可调用正典；代码守三关
#         （接地核对 / 信息守恒 / 引用可达），写回前 git checkpoint，原文保号入
#         raw/_curated/，可整体回滚。
#
# 三重闸门（任一不满足就静默跳过，绝不抢资源、绝不阻塞其它流水线）：
#   1) 开关：CURATE_ENABLE=1（默认关；本脚本由自动化注入）
#   2) 密钥：ORNITH_API_KEY 存在（curate 内部仍会探端点，不可达则自动降级只出 plan）
#   3) 空闲：1 分钟负载 < CURATE_MAX_LOAD（默认 4.0），忙时让路
# 另有 L4 宪法闸门：若 lc rung 被真人连续判错而冻结，curate 自动退化为「只出提案」。
#
# 单轮有界：CURATE_LIMIT（默认 12 篇）/ CURATE_SECONDS（默认 3600s），到时停止，
#           剩余留给下一轮 —— 空闲预算被切成小片，长期自然收敛。
#
# ⚠️ 退出码**始终为 0**（与 kb-daily-supervisor.sh 同约定）：让 OpenClaw 一定把
#    简报投递出去；问题在正文里以 ❌/⚠️ 标注，而不是靠退出码。
#
# 用法: kb-curate.sh [vault_root]
# 环境变量: KB_ROOT / CURATE_ENABLE / CURATE_LIMIT / CURATE_SECONDS /
#           CURATE_MAX_LOAD / ORNITH_API_KEY / ORNITH_BASE_URL / ORNITH_CHAT_MODEL
# ============================================================
set -uo pipefail

VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"
PY="${PYTHON:-python3}"
CURATE_ENABLE="${CURATE_ENABLE:-1}"
CURATE_LIMIT="${CURATE_LIMIT:-12}"
CURATE_SECONDS="${CURATE_SECONDS:-3600}"
CURATE_MAX_LOAD="${CURATE_MAX_LOAD:-4.0}"
PIPE="$VAULT/pipeline"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
VERDICT="ok"; PROBLEMS=()
note_problem() { local lvl="$1"; shift; PROBLEMS+=("$lvl $*")
  if [ "$lvl" = "❌" ]; then VERDICT="fail"
  elif [ "$lvl" = "⚠️" ] && [ "$VERDICT" = "ok" ]; then VERDICT="warn"; fi; }

echo "===== 知识结晶 $(date '+%F %H:%M') ====="
echo

if [ ! -x "$VAULT/kb" ]; then
  echo "【结论】❌ 库路径异常：$VAULT（缺 kb 启动器）"; exit 0
fi

# ---- 闸门 ----
LOAD1="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
if [ "$CURATE_ENABLE" != "1" ]; then
  echo "【结论】⏸ 结晶已关闭（CURATE_ENABLE != 1）"; exit 0
fi
if [ -z "${ORNITH_API_KEY:-}" ]; then
  echo "【结论】⏸ 跳过：未设 ORNITH_API_KEY（本地模型不可用）"
  echo "   → 只读清单：cd $VAULT && $PY $PIPE/curate.py plan --limit 5"
  exit 0
fi
if ! awk -v a="$LOAD1" -v b="$CURATE_MAX_LOAD" 'BEGIN{exit !(a < b)}'; then
  echo "【结论】⏸ 让路：1 分钟负载 ${LOAD1} ≥ ${CURATE_MAX_LOAD}"; exit 0
fi

# ---- 结晶前：进度快照（用于算本轮增量）----
before="$("$PY" "$PIPE/curate.py" report --root "$VAULT" --json 2>/dev/null \
          | "$PY" -c 'import json,sys; print((json.load(sys.stdin) or {}).get("canon",0))' 2>/dev/null || echo 0)"

echo "【运行】上限 ${CURATE_LIMIT} 篇 / ${CURATE_SECONDS}s（负载 ${LOAD1}）"
timeout $((CURATE_SECONDS + 300)) "$PY" "$PIPE/curate.py" run --root "$VAULT" \
  --apply --limit "$CURATE_LIMIT" --max-seconds "$CURATE_SECONDS" >"$TMP/curate.out" 2>&1
RC=$?
tail -6 "$TMP/curate.out" | sed 's/^/  /'

WROTE="$(grep -ac 'canon-written' "$TMP/curate.out" 2>/dev/null || echo 0)"
FAILS="$(grep -acE '⚠️|error' "$TMP/curate.out" 2>/dev/null || echo 0)"
if [ "$RC" -eq 124 ]; then
  echo "  ⚠️ 结晶超时（>${CURATE_SECONDS}s），已处理的部分照常落盘"
  note_problem "⚠️" "结晶超时，剩余留给下一轮"
elif [ "$RC" -ne 0 ]; then
  echo "  ❌ 结晶退出码 ${RC}"
  note_problem "❌" "kb curate run 退出码 ${RC}"
fi

# ---- 结晶后：重建索引，让新正典立即可检索 ----
if [ "$WROTE" -gt 0 ]; then
  timeout 300 "$PY" "$PIPE/rag.py" index --root "$VAULT" >"$TMP/rag.out" 2>&1
  echo "  🗂 $(tail -1 "$TMP/rag.out" | sed 's/^ *//')"
fi

# ---- 报告 ----
after="$("$PY" "$PIPE/curate.py" report --root "$VAULT" --json 2>/dev/null \
         | "$PY" -c 'import json,sys; print((json.load(sys.stdin) or {}).get("canon",0))' 2>/dev/null || echo 0)"
"$PY" "$PIPE/curate.py" report --root "$VAULT" 2>/dev/null | sed -n '2,7p' | sed 's/^/  /'
echo
echo "  本轮新增正典：$(( ${after:-0} - ${before:-0} )) 篇（累计 ${after:-0}）"
echo "  一键回原文：kb raw show \"<source_ref>\"（正典 frontmatter 里）"
echo "  待人工裁决：kb curate review（低置信提案，L4 闸门）"

case "$VERDICT" in
  ok)   echo "【结论】✅ 结晶正常" ;;
  warn) echo "【结论】⚠️ 有需要关注项（见下）" ;;
  fail) echo "【结论】❌ 存在问题（见下）" ;;
esac
if [ "${#PROBLEMS[@]}" -gt 0 ]; then printf '%s\n' "${PROBLEMS[@]}" | sed 's/^/  /'; fi
echo "===== 报告结束 ====="
exit 0
