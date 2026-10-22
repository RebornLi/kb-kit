@echo off
rem ============================================================
rem kb.cmd —— 知识库每日使用启动器（Windows）
rem   用法: kb <工具> [参数...]   例: kb rag index / kb query "怎么备份知识库"
rem   双击运行，或 PowerShell 里直接 kb xxx。
rem   默认库 = 本文件所在目录（经 KB_ROOT）；--root 可覆盖。
rem
rem 插件化版本：所有命令统一通过 kb_launcher.py 路由到插件注册中心。
rem ============================================================
setlocal enableextensions

set "VAULT=%~dp0"
set "VAULT=%VAULT:~0,-1%"

rem 选 python：优先 python，其次 py，再 python3
where python >nul 2>&1 && set "PY=python" || (where py >nul 2>&1 && set "PY=py" || (where python3 >nul 2>&1 && set "PY=python3" || goto :nopys))

rem 所有命令（含 backup）统一走 kb_launcher.py
rem backup 由 pipeline/backup.py 跨平台实现，不再依赖 bash
rem 目标库优先级：--root > KB_ROOT 环境变量 > 本文件所在目录
if not defined KB_ROOT set "KB_ROOT=%VAULT%"
"%PY%" "%VAULT%\pipeline\kb_launcher.py" %*
rem **必须显式透传退出码**：cmd 脚本末尾没有 `exit /b` 时，
rem 无论 kb_launcher 成功还是失败，`kb.cmd` 都返回 0。
rem Windows 侧的 cron / 任务计划 / 安装自检因此永远看不到失败 ——
rem 与 bash 侧 `kb-verify-cron.sh` 曾有的缺陷同类（P1-3/P3-31）。
exit /b %ERRORLEVEL%

:nopys
echo ❌ 未找到 Python。请先安装 Python 3.8+（装时勾选 Add to PATH 或加进 PATH），再重试。
rem 找不到解释器同样是失败，不能返回 0
exit /b 3
