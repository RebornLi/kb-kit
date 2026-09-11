#!/usr/bin/env bash
# ============================================================
# install.sh —— 一键搭建知识库（Linux / macOS）
#   用法:
#     ./install.sh                 # 交互：询问目标目录
#     ./install.sh "我的知识库"     # 直接指定目录
#     ./install.sh KB --no-git --no-demo    # 目录 + create_vault 参数
#   若解压后 install.sh 没有执行权限，先 chmod +x install.sh
# ============================================================
set -euo pipefail
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

# 首个 non-flag 参数 = 目标目录；其余透传给 create_vault.py
VAULT=""
EXTRA=()
for a in "$@"; do
  if [ -z "$VAULT" ] && [ "${a:0:1}" != "-" ]; then VAULT="$a";
  else EXTRA+=("$a"); fi
done
if [ -z "$VAULT" ]; then
  read -rp "📂 目标知识库目录（留空 = 当前目录下新建 KB）: " VAULT
  [ -z "${VAULT:-}" ] && VAULT="KB"
fi

echo "▶ 开始搭建: $VAULT"
# Python：优先 python3，其次 python，再 py（与其它脚本一致）
PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
elif command -v py >/dev/null 2>&1; then PY=py
fi
if [ -z "$PY" ]; then
  echo "❌ 未找到 Python。请先安装 Python 3.8+（装时勾选 Add to PATH 或加进 PATH），再重试。" >&2
  exit 3
fi
if [ "${#EXTRA[@]}" -gt 0 ]; then
  exec "$PY" "$SELF_DIR/create_vault.py" --vault "$VAULT" "${EXTRA[@]}"
else
  exec "$PY" "$SELF_DIR/create_vault.py" --vault "$VAULT"
fi
