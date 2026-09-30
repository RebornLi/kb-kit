#!/usr/bin/env bash
# ============================================================
# kb-daily-supervisor.sh — KB 每日「维护 + 监督 + 汇报」
# 由 OpenClaw 的 02:00 自动化调用（openclaw automations）。
#
# 它做三件事：
#   ① 维护：跑完整成长流水线 scripts/growth_cron.sh
#           （rag索引 → 摄入triage → feedback → recall → 补链 → dashboard
#             → agent记忆写入侧 → 记忆同步；单步隔离，带坏不中断）
#   ② 巡检：跑 scripts/healthcheck_cron.sh，抽出真正要看的数字
#           （死链 / 骨架笔记 / 标签越界），忽略 draft 状态之类的信息项
#   ③ 监督：实测 KB「是否真的能用」——kb query 能否召回 + 嵌入服务是否在线
#           （检索依赖 vllm 的 :8081 嵌入服务，这是最容易静默挂掉的一环）
#
# 然后输出一段可直接投递到微信的简报。
#
# ⚠️ 退出码**始终为 0**：确保 OpenClaw 一定把简报投递出去。
#    问题不会靠退出码表达，而是在正文里以 ❌/⚠️ 明确标注
#    （若返回非 0，OpenClaw 可能判定任务失败而跳过投递，那就又变成静默了）。
#
# 用法: kb-daily-supervisor.sh [vault_root]
# 环境变量: KB_ROOT / EMBED_URL / QUERY_TIMEOUT / GROWTH_TIMEOUT
# ============================================================
set -uo pipefail

VAULT="${1:-${KB_ROOT:-/home/mushan/kb-kit-pure}}"
EMBED_URL="${EMBED_URL:-http://127.0.0.1:8081/health}"
QUERY_TIMEOUT="${QUERY_TIMEOUT:-30}"
GROWTH_TIMEOUT="${GROWTH_TIMEOUT:-900}"
HC_TIMEOUT="${HC_TIMEOUT:-300}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VERDICT="ok"          # ok | warn | fail
PROBLEMS=()

note_problem() {      # note_problem <级别> <描述>
  local lvl="$1"; shift
  PROBLEMS+=("$lvl $*")
  if [ "$lvl" = "❌" ]; then VERDICT="fail"
  elif [ "$lvl" = "⚠️" ] && [ "$VERDICT" = "ok" ]; then VERDICT="warn"; fi
}

echo "===== KB 日报 $(date '+%F %H:%M') ====="
echo

# ---------- 前置：库是否存在 ----------
if [ ! -d "$VAULT" ] || [ ! -f "$VAULT/kb" ]; then
  note_problem "❌" "库路径异常：$VAULT（缺 kb 启动器）"
  echo "【结论】❌ 库路径异常，未执行任何维护"
  printf '%s\n' "${PROBLEMS[@]}"
  exit 0
fi
NOTE_CNT="$(find "$VAULT" -name '*.md' -not -path '*/.git/*' 2>/dev/null | wc -l | tr -d ' ')"
echo "库: $VAULT · 笔记 $NOTE_CNT 篇"
echo

# ---------- ① 维护流水线 ----------
echo "【① 日常维护】"
if [ -x "$VAULT/scripts/growth_cron.sh" ]; then
  timeout "$GROWTH_TIMEOUT" "$VAULT/scripts/growth_cron.sh" "$VAULT" >"$TMP/growth.out" 2>&1
  GROWTH_RC=$?
  # growth_cron.sh 用 tee 同时写 stdout，这里抽它自己的摘要行
  SUMMARY="$(grep -a '节奏汇总' "$TMP/growth.out" | tail -1)"
  OK_N="$(printf '%s' "$SUMMARY" | sed -n 's/.*: \([0-9]*\) 步成功.*/\1/p')"
  FAIL_N="$(printf '%s' "$SUMMARY" | sed -n 's/.*\/ \([0-9]*\) 步失败.*/\1/p')"
  if [ "$GROWTH_RC" -eq 124 ]; then
    echo "  ❌ 维护超时（>${GROWTH_TIMEOUT}s）"
    note_problem "❌" "维护流水线超时"
  elif [ "${FAIL_N:-0}" != "0" ] && [ -n "${FAIL_N:-}" ]; then
    echo "  步骤: ${OK_N:-?} 成功 / ${FAIL_N} 失败"
    grep -a '  ❌ ' "$TMP/growth.out" | sed 's/^/    /' | head -6
    note_problem "❌" "维护有 ${FAIL_N} 步失败"
  else
    echo "  ✅ 全部步骤成功（${OK_N:-?} 步）"
  fi
else
  echo "  ❌ 缺少 scripts/growth_cron.sh"
  note_problem "❌" "缺少 growth_cron.sh"
fi
echo

# ---------- ② 健康巡检 ----------
echo "【② 健康巡检】"
if [ -x "$VAULT/scripts/healthcheck_cron.sh" ]; then
  timeout "$HC_TIMEOUT" "$VAULT/scripts/healthcheck_cron.sh" "$VAULT" >"$TMP/hc.out" 2>&1
  HC_LOG="$VAULT/logs/health-$(date +%F).log"
  if [ -f "$HC_LOG" ]; then
    # healthcheck 的原始格式（不能用 's/.*: //' 贪婪截取，会把
    # "46 个 | 全部自由 tag: 129 种" 截成 "129 种"）：
    #   🔗 死链检测: 3/1167 死链 (0.3%)     ← 分子=死链数, 分母=总链接数
    #   🦴 骨架笔记: 0 个(...)
    #   🏷  标签越界(内容层): 46 个 | 全部自由 tag: 129 种
    dead_line="$(grep -a '死链检测' "$HC_LOG" | head -1)"
    dnum="$(printf '%s' "$dead_line" | sed -n 's#.*检测:[[:space:]]*\([0-9]*\)/.*#\1#p')"
    dtot="$(printf '%s' "$dead_line" | sed -n 's#.*检测:[[:space:]]*[0-9]*/\([0-9]*\)[[:space:]]*死链.*#\1#p')"
    echo "  🔗 死链: ${dnum:-?}/${dtot:-?}"

    skel_line="$(grep -a '骨架笔记' "$HC_LOG" | head -1)"
    snum="$(printf '%s' "$skel_line" | sed -n 's#.*骨架笔记:[[:space:]]*\([0-9]*\)[[:space:]]*个.*#\1#p')"
    echo "  🦴 骨架: ${snum:-?} 个"

    tag_line="$(grep -a '标签越界' "$HC_LOG" | head -1)"
    tnum="$(printf '%s' "$tag_line" | sed -n 's#.*标签越界[^:]*:[[:space:]]*\([0-9]*\)[[:space:]]*个.*#\1#p')"
    talls="$(printf '%s' "$tag_line" | sed -n 's#.*全部自由 tag:[[:space:]]*\([0-9]*\)[[:space:]]*种.*#\1#p')"
    echo "  🏷  标签越界: ${tnum:-?} 个（自由 tag 共 ${talls:-?} 种）"

    # 死链：healthcheck 自身阈值 <2%，这里再加一条绝对量告警
    if [ -n "$dnum" ] && [ "$dnum" -gt 20 ]; then
      note_problem "⚠️" "死链 ${dnum} 条偏多"
    fi
    if [ -n "$tnum" ] && [ "$tnum" -gt 0 ]; then
      note_problem "⚠️" "标签越界 ${tnum} 个待归入白名单"
    fi
  else
    echo "  ⚠️ 未生成巡检日志（$HC_LOG）"
    note_problem "⚠️" "巡检未产出日志"
  fi
else
  echo "  ⚠️ 缺少 scripts/healthcheck_cron.sh"
  note_problem "⚠️" "缺少 healthcheck_cron.sh"
fi
echo

# ---------- ③ 可用性自检（KB 是否真能用） ----------
echo "【③ 可用性自检】"
# 3a 嵌入服务（检索的硬依赖）
# 嵌入服务探测：**重试 3 次**。
#   为什么：2026-10-01 02:00 那次日报报「嵌入服务不可达 → KB 检索已失效」并推到微信，
#   但服务其实健康（03:00 实测 /health=200）。根因是单次 curl -m 5 撞上服务重启/首请求
#   加载窗口 → 一次超时就被判成致命故障。**单次探测不配下"检索已失效"这种结论。**
emb_code=000
for _try in 1 2 3; do
  emb_code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$EMBED_URL" 2>/dev/null || echo 000)"
  [ "$emb_code" = "200" ] && break
  sleep 3
done
if [ "$emb_code" = "200" ]; then
  echo "  ✅ 嵌入服务在线 (:8081)"
else
  echo "  ❌ 嵌入服务不可达 (:8081 HTTP $emb_code) → 检索已失效"
  note_problem "❌" "嵌入服务 :8081 不可达（KB 检索已失效）"
fi

# 3b 实跑一次检索（真正验证「KB 是否运行」）
qout="$(timeout "$QUERY_TIMEOUT" bash -lc "cd '$VAULT' && kb query '知识库' 2>&1" 2>/dev/null || true)"
if printf '%s' "$qout" | grep -qaE '命中|score|\.md'; then
  qn="$(printf '%s' "$qout" | grep -acE '\.md' || echo '?')"
  echo "  ✅ 检索可用（返回约 ${qn} 条）"
else
  echo "  ❌ 检索无有效返回"
  printf '%s' "$qout" | head -3 | sed 's/^/     /'
  note_problem "❌" "kb query 无有效返回"
fi

# 3c 索引新鲜度
if [ -f "$VAULT/vector index/df_idf.json" ]; then
  age_h=$(( ( $(date +%s) - $(stat -c %Y "$VAULT/vector index/df_idf.json") ) / 3600 ))
  if [ "$age_h" -gt 48 ]; then
    echo "  ⚠️ 向量索引已 ${age_h}h 未更新"
    note_problem "⚠️" "向量索引 ${age_h}h 未更新"
  else
    echo "  ✅ 向量索引新鲜（${age_h}h 前）"
  fi
else
  echo "  ⚠️ 未找到向量索引"
  note_problem "⚠️" "缺向量索引"
fi
echo

# ---------- 结论 ----------
case "$VERDICT" in
  ok)   echo "【结论】✅ 一切正常" ;;
  warn) echo "【结论】⚠️ 有需要关注项（见下）" ;;
  fail) echo "【结论】❌ 存在问题（见下）" ;;
esac
if [ "${#PROBLEMS[@]}" -gt 0 ]; then
  printf '%s\n' "${PROBLEMS[@]}" | sed 's/^/  /'
fi
echo "===== 报告结束 ====="

exit 0
