#!/usr/bin/env bash
# ============================================================
# install.sh —— 一键搭建知识库（Linux / macOS）
#   用法:
#     ./install.sh                 # 交互：询问目标目录
#     ./install.sh "我的知识库"     # 直接指定目录
#     ./install.sh kb-kit --no-git --no-demo    # 目录 + create_vault 参数
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

# ---- 1. 环境自检（precheck.py）----
# 先找一个能用的 Python（precheck 自己也会找，但需要 python 来跑 precheck）
PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
elif command -v py >/dev/null 2>&1; then PY=py
fi

if [ -z "$PY" ]; then
  echo "❌ 未找到 Python。请先安装 Python 3.8+：" >&2
  echo "   下载: https://www.python.org/downloads/" >&2
  echo "   Ubuntu/Debian: sudo apt-get install python3" >&2
  echo "   macOS (装 brew 后): brew install python3" >&2
  exit 3
fi

# 运行 precheck（环境自检 + 自动补齐 Obsidian）
if [ -f "$SELF_DIR/scripts/precheck.py" ]; then
  echo "▶ 环境检查……"
  if ! "$PY" "$SELF_DIR/scripts/precheck.py"; then
    echo "❌ 环境检查未通过，请按上方提示修复后重试。" >&2
    exit 3
  fi
fi

# ---- 2. 询问目标目录 ----
if [ -z "$VAULT" ]; then
  read -rp "📂 目标知识库目录（留空 = 当前目录下新建 kb-kit）: " VAULT
  [ -z "${VAULT:-}" ] && VAULT="kb-kit"
fi

echo "▶ 开始搭建: $VAULT"

# ---- 3. 调 create_vault.py ----
if [ "${#EXTRA[@]}" -gt 0 ]; then
  exec "$PY" "$SELF_DIR/create_vault.py" --vault "$VAULT" "${EXTRA[@]}"
else
  exec "$PY" "$SELF_DIR/create_vault.py" --vault "$VAULT"
fi
