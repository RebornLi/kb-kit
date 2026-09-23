@echo off
rem ============================================================
rem kb.cmd —— 知识库每日使用启动器（Windows）
rem   用法: kb <工具> [参数...]   例: kb rag index / kb query "怎么备份知识库"
rem   双击运行，或 PowerShell 里直接 kb xxx。
rem   --root 自动注入(=本文件所在 = vault 根)。
rem
rem 插件化版本：所有命令统一通过 kb_launcher.py 路由到插件注册中心。
rem ============================================================
setlocal enableextensions

set "VAULT=%~dp0"
set "VAULT=%VAULT:~0,-1}"

rem 选 python：优先 python，其次 py，再 python3
where python >nul 2>&1 && set "PY=python" || (where py >nul 2>&1 && set "PY=py" || (where python3 >nul 2>&1 && set "PY=python3" || goto :nopys))

rem backup 仍走 bash 脚本
if /i "%~1"=="backup" (
  call "%VAULT%\scripts\backup_now.sh" "%VAULT%" %2
  goto :eof
)

rem 所有其他命令统一走 kb_launcher.py
"%PY%" "%VAULT%\pipeline\kb_launcher.py" %* --root "%VAULT%"
goto :eof

:nopys
echo ❌ 未找到 Python。请先安装 Python 3.8+（装时勾选 Add to PATH 或加进 PATH），再重试。
goto :eof
