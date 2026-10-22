#!/usr/bin/env bash
# ============================================================
# kb-consolidate-nightly.sh — 睡眠时整理（由 OpenClaw 自动化在凌晨空闲时调用）
#
# 干什么：空闲期做四件"重活"，**只产提议、不改任何笔记**：
#   ① 近重复（bottom-k 候选 + 精确 Jaccard 复核，区分整体重复 / 片段重复）
#   ② claim 槽位对齐（同槽位且时间相交=矛盾；时间不相交=时序更新，不该去调和）
#   ③ 陈旧的高 importance 笔记
#   ④ 质量门禁分布（pass / quarantine / reject）
#
# 为什么放凌晨：睡眠时计算研究（arXiv:2504.13171）把记忆管理从**查询路径**搬到**空闲路径**，
#   同等精度省约 5× test-time 算力；这也是本库"只读主流程 + 有写入权的整理流程"的落点。
#
# 安全：整理**从不写笔记**（脚本也只会写 .kb/state/ 下的提议与报告）。
#   合并/删除永远交人工或 `kb ladder adjudicate`，因为不可逆。
#
# 闸门：LADDER_CONSOLIDATE_ENABLE=1（默认 1）· 1 分钟负载 < CONSOLIDATE_MAX_LOAD
# ⚠️ 退出码**始终为 0**（与其它 cron 脚本同约定），让简报一定投递出去。
# ============================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="${1:-${KB_ROOT:-$SELF_DIR/..}}"
PY="${PYTHON:-python3}"
CONSOLIDATE_ENABLE="${CONSOLIDATE_ENABLE:-1}"
CONSOLIDATE_MAX_LOAD="${CONSOLIDATE_MAX_LOAD:-4.0}"
CONSOLIDATE_SECONDS="${CONSOLIDATE_SECONDS:-1800}"
PIPE="$VAULT/pipeline"

echo "===== 睡眠时整理 $(date '+%F %H:%M') ====="
echo

if [ ! -x "$VAULT/kb" ]; then
  echo "【结论】❌ 库路径异常：$VAULT（缺 kb 启动器）"; exit 0
fi

LOAD1="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
if [ "$CONSOLIDATE_ENABLE" != "1" ]; then
  echo "【结论】⏸ 已关闭（CONSOLIDATE_ENABLE != 1）"; exit 0
fi
if ! awk -v a="$LOAD1" -v b="$CONSOLIDATE_MAX_LOAD" 'BEGIN{exit !(a < b)}'; then
  echo "【结论】⏸ 让路：1 分钟负载 ${LOAD1} ≥ ${CONSOLIDATE_MAX_LOAD}"; exit 0
fi

VERDICT="ok"; PROBLEMS=()
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

timeout "$CONSOLIDATE_SECONDS" "$PY" "$PIPE/consolidate.py" run --root "$VAULT" \
  >"$TMP/c.out" 2>&1
RC=$?
grep -vE '^\s*$' "$TMP/c.out" | sed 's/^/  /' | head -14

if [ "$RC" -eq 124 ]; then
  echo "  ⚠️ 超出 ${CONSOLIDATE_SECONDS}s 预算，已算出的部分照常落盘"
  VERDICT="warn"; PROBLEMS+=("⚠️ 整理超时，剩余留给下一轮")
elif [ "$RC" -ne 0 ]; then
  echo "  ❌ consolidate 退出码 ${RC}"
  VERDICT="fail"; PROBLEMS+=("❌ kb consolidate 退出码 ${RC}")
fi

echo
echo "【待人工/阶梯处理】"
"$PY" - "$VAULT" <<'PYEOF' 2>/dev/null || echo "  （提议文件暂不可读）"
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / ".kb" / "state" / "kb_consolidate_proposals.json"
d = json.loads(p.read_text(encoding="utf-8"))
n = {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in d.items()}
print(f"  整体重复 {n.get('duplicates', 0)} 对 · 片段重复 {n.get('sections', 0)} 对")
print(f"  矛盾 {n.get('conflicts', 0)} 组 · 时序更新 {n.get('temporal_updates', 0)} 组（后者**不是**矛盾）")
print(f"  陈旧高重要 {n.get('stale', 0)} 篇 · 闸门 {n.get('gate', {})}")
if n.get("duplicates") or n.get("conflicts"):
    print("  → 需人审：kb consolidate status · 或交 `kb ladder adjudicate`")
else:
    print("  → 无需要处理的重复/矛盾")
PYEOF

echo
case "$VERDICT" in
  ok)   echo "【结论】✅ 整理正常（未改任何笔记）" ;;
  warn) echo "【结论】⚠️ 有需要关注项（见下）" ;;
  fail) echo "【结论】❌ 存在问题（见下）" ;;
esac
if [ "${#PROBLEMS[@]}" -gt 0 ]; then printf '%s\n' "${PROBLEMS[@]}" | sed 's/^/  /'; fi
echo "===== 报告结束 ====="
exit 0
