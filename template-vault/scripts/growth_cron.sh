#!/usr/bin/env bash
# ============================================================
# growth_cron.sh —— 成长引擎定时节拍（成长系统自动维护）
#   跑: ② feedback ingest(命中计数) → ④ recall deck(回忆) →
#       ③ 补链建议(只读,写 link_suggestions.md) → dashboard(治理面) →
#       ⑦ 记忆写入侧(agent 记忆落 KB) → ⑥ 记忆同步(memory/ → KB)
#   每步独立隔离:某引擎带坏只记日志并继续,绝不因单步崩而中断整条流水线。
#   尤其⑦⑥是 agent 记忆落库的写侧,不能被前序任意单步的报错跳过(否则一周静默断粮)。
#   dashboard 有变化才 commit 且只 add 该文件——绝不 sweep Obsidian 运行时态。
#   末尾输出本轮 成功/失败 汇总;全失败时退出非 0,便于 cron 邮件察觉。
#   用法: scripts/growth_cron.sh <vault_root>
#   环境变量(可选覆盖):
#     PYTHON        Python 解释器(默认 python3)
#     AGENT_ROOT    agent 工作区根(含 SOUL/USER/MEMORY 等核心文件)
#     AGENT_MEMORY  agent 每日记忆目录(含 *.md 日报)
#   不写死任何用户私人路径;vault_root 必填,agent 源缺省时跳过记忆写入侧。
# ============================================================
set -uo pipefail          # 不设 -e:单步失败不得中断流水线(靠 step() 捕获判定)
VAULT="${1:-${KB_ROOT:-}}"
if [ -z "$VAULT" ]; then
  echo "❌ 用法: $0 <vault_root>  (或设 KB_ROOT 环境变量)" >&2
  exit 2
fi
PIPE="$VAULT/pipeline"
PYTHON="${PYTHON:-python3}"
# 记忆写入侧源：agent 实时记忆流落点(由部署方通过环境变量提供;缺省则跳过)
AGENT_ROOT="${AGENT_ROOT:-}"
AGENT_MEMORY="${AGENT_MEMORY:-}"
LOGDIR="$VAULT/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/growth-$(date +%F).log"
SUCCESS=0; FAIL=0
log(){ echo "[growth $(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
# 隔离单步:参数即完整命令。先捕获真实退出码,再决定成功/失败;带坏不中断。
step(){
  local label="$1"; shift
  log "▶ ${label}"
  "$@" 2>&1 | tee -a "$LOG" | tail -n 6
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -eq 0 ]; then
    SUCCESS=$((SUCCESS+1)); log "  ✅ ${label}"
  else
    FAIL=$((FAIL+1)); log "  ❌ ${label}(退出码 ${rc})"
  fi
}

log "⑥ rag index（重建向量索引，供 feedback/recall/memory_sync 检索）"
step "rag index" "${PYTHON}" "${PIPE}/rag.py" index --root "${VAULT}"

log "① intake review（收件箱 triage 报告，只读不写，写回 $VAULT/intake_triage.md）"
step "intake review" "${PYTHON}" "${PIPE}/intake_triage.py" review --root "${VAULT}"

log "② feedback ingest（命中计数，写回 $VAULT/feedback_hits.md）"
step "feedback ingest" "${PYTHON}" "${PIPE}/feedback_loop.py" ingest --root "${VAULT}"

log "④ recall deck（今日回忆 deck，写回 $VAULT/recall_deck.md）"
step "recall deck" "${PYTHON}" "${PIPE}/recall_schedule.py" deck --root "${VAULT}"

log "③ 补链建议（只读；写回由人工 kb link apply，避免无人监管自动改笔记）"
step "link suggestions" "${PYTHON}" "${PIPE}/link_engine.py" suggestions --root "${VAULT}"

log "⑤③ dashboard（刷新治理面，写回 70-知识治理 Governance/_INDEX.md）"
step "dashboard" "${PYTHON}" "${PIPE}/dashboard.py" --root "${VAULT}"

# 只刷新仪表盘治理面：有变化才 commit，且只 add 该文件——绝不 sweep Obsidian 运行时态
IDX="70-知识治理 Governance/_INDEX.md"
if git -C "${VAULT}" add -- "${IDX}" 2>>"${LOG}"; then
  if git -C "${VAULT}" diff --cached --quiet; then
    log "  仪表盘无变化"
  else
    git -C "${VAULT}" commit -q -m "chore: cron 刷新仪表盘" >>"${LOG}" 2>&1 \
      && log "  ✅ 仪表盘已刷新 committed" \
      || log "  ⚠️ 仪表盘有变更但未提交(疑似 git 身份缺失或权限不足)"
  fi
fi

# ⑦ 记忆写入侧：agent 实时记忆流(SOUL/USER/MEMORY/AGENTS/TOOLS + 每日记忆)→ 知识库
#   4 步: mirror-core(核心文件→reference/) → mirror(日报→KB memory/，喂 ⑥ 晋升) →
#   extract(经验教训/偏好→结构化笔记) → sync(带 kb_target 的日报按 agent 路由写入)
#   四步都只 git add 被改/新建的 KB 文件(绝不 git add -A sweep Obsidian),
#   状态记 .memory_ingest_state.json 不入库(与 feedback/recall/state 同约定)。
log "⑦ 记忆写入侧（agent memory 同步进 KB）"
if [ -n "${AGENT_MEMORY}" ] && [ -d "${AGENT_MEMORY}" ]; then
  step "mirror-core" "${PYTHON}" "${PIPE}/memory_ingest.py" mirror-core --root "${VAULT}" --agent-root "${AGENT_ROOT}"
  step "mirror"      "${PYTHON}" "${PIPE}/memory_ingest.py" mirror   --root "${VAULT}" --agent-src "${AGENT_MEMORY}"
  step "extract"     "${PYTHON}" "${PIPE}/memory_ingest.py" extract  --root "${VAULT}" --agent-root "${AGENT_ROOT}"
  step "sync"        "${PYTHON}" "${PIPE}/memory_ingest.py" sync     --root "${VAULT}" --agent-src "${AGENT_MEMORY}"
else
  log "⑦ 记忆写入侧跳过（未设 AGENT_MEMORY 或路径不存在）"
fi

# ⑦b 注册表驱动记忆摄取：读 kb-agent.json，对所有已启用 agent 自动摄取记忆
#   这是问题三的核心：定时获取各 agent 的记忆文件，处理成知识库内容
#   按 agent 类型分发：md → memory_ingest.py / json → memory_ingest_json.py /
#                       sqlite → memory_ingest_sqlite.py / project-context → mirror_project_context
#   先 dry-run 预览再实际摄取，确保安全
log "⑦b 注册表驱动记忆摄取（kb-agent.json → 各 agent adapter → KB）"
AGENT_JSON="${VAULT}/kb-agent.json"
if [ -f "${AGENT_JSON}" ]; then
  # 用 agent_registry.py ingest 按注册表调度（先 dry-run 再实际摄取）
  step "agent ingest dry-run" "${PYTHON}" "${PIPE}/agent_registry.py" ingest --root "${VAULT}" --dry-run
  step "agent ingest" "${PYTHON}" "${PIPE}/agent_registry.py" ingest --root "${VAULT}"
else
  log "⑦b 跳过（kb-agent.json 不存在，未注册任何 agent）"
fi

# ⑥ 记忆同步：memory/*.md 过「五问」的沉淀灌进知识库(只加新建笔记、幂等、
#   状态记 .memory_sync_state.json 不入库；无源时静默 0 晋升)
log "⑥ 记忆同步（memory/ → 知识库）"
step "memory promote" "${PYTHON}" "${PIPE}/memory_sync.py" promote --root "${VAULT}" --apply

# ⑧ 知识结晶：空闲时才跑，用本地 Agent 把「日志/碎片」炼成「可调用正典」。
#   三重闸门（任一不满足即跳过，绝不抢资源、绝不阻塞流水线）：
#     1) 开关：CURATE_ENABLE=1（默认关；确认效果后再开）
#     2) 密钥：ORNITH_API_KEY 存在（curate 内部仍会探端点，不可达则降级只出 plan）
#     3) 空闲：1 分钟负载 < CURATE_MAX_LOAD（默认 4.0），忙时让路
#   单轮有界：CURATE_SECONDS（默认 1800s）+ CURATE_LIMIT（默认 8 篇），
#   到时停止，剩余留给下一轮 —— 空闲预算被切成小片，永远不抢推理资源。
CURATE_ENABLE="${CURATE_ENABLE:-0}"
CURATE_LIMIT="${CURATE_LIMIT:-8}"
CURATE_SECONDS="${CURATE_SECONDS:-1800}"
CURATE_MAX_LOAD="${CURATE_MAX_LOAD:-4.0}"
LOAD1="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
if [ "${CURATE_ENABLE}" != "1" ]; then
  log "⑧ 知识结晶跳过（CURATE_ENABLE != 1）"
elif [ -z "${ORNITH_API_KEY:-}" ]; then
  log "⑧ 知识结晶跳过（未设 ORNITH_API_KEY，本地模型不可用）"
elif ! awk -v a="${LOAD1}" -v b="${CURATE_MAX_LOAD}" 'BEGIN{exit !(a < b)}'; then
  log "⑧ 知识结晶跳过（负载 ${LOAD1} ≥ ${CURATE_MAX_LOAD}，让路）"
else
  log "⑧ 知识结晶（负载 ${LOAD1}；上限 ${CURATE_LIMIT} 篇 / ${CURATE_SECONDS}s）"
  step "curate run" "${PYTHON}" "${PIPE}/curate.py" run --root "${VAULT}" \
       --apply --limit "${CURATE_LIMIT}" --max-seconds "${CURATE_SECONDS}"
  # 结晶改写了正文 → 重建索引，让新的正典立即可检索
  step "rag index (post-curate)" "${PYTHON}" "${PIPE}/rag.py" index --root "${VAULT}"
fi

# 日志 Retention:清理 30 天前的 logs(已 gitignore,仅占盘)
log "日志 Retention（清理 30 天前 logs）"
find "$LOGDIR" -name '*.log' -type f -mtime +30 -delete 2>/dev/null || true

log "-------------------------------------------"
log "📊 节奏汇总: ${SUCCESS} 步成功 / ${FAIL} 步失败 · 日志: ${LOG}"
# 有失败则退出非 0,让 cron 邮件/监控察觉;不影响已落地的各步结果。
[ "${FAIL}" -eq 0 ]
