@echo off
rem ============================================================
rem kb.cmd —— 知识库每日使用启动器（Windows）
rem   用法: kb <工具> [参数...]   例: kb rag index / kb query "怎么备份知识库"
rem   双击运行，或 PowerShell 里直接 kb xxx。
rem   --root 自动注入(=本文件所在 = vault 根)。
rem ============================================================
setlocal enableextensions

set "VAULT=%~dp0"
set "VAULT=%VAULT:~0,-1%"

rem 选 python：优先 python，其次 py，再 python3
where python >nul 2>&1 && set "PY=python" || (where py >nul 2>&1 && set "PY=py" || (where python3 >nul 2>&1 && set "PY=python3" || goto :nopys))

set "TOOL=%~1"
if "%TOOL%"=="" goto :help
shift

if /i "%TOOL%"=="query" (
  "%PY%" "%VAULT%\pipeline\rag.py" query %* --root "%VAULT%"
  goto :eof
)

if /i "%TOOL%"=="backup" (
  call "%VAULT%\scripts\backup_now.sh" "%VAULT%" %*
  goto :eof
)

if /i "%TOOL%"=="ingest" (
  if "%~1"=="" (
    "%PY%" "%VAULT%\pipeline\intake_triage.py" review --root "%VAULT%"
  ) else (
    "%PY%" "%VAULT%\pipeline\intake_triage.py" apply --move --root "%VAULT%"
  )
  goto :eof
)

if /i "%TOOL%"=="link" (
  if "%~1"=="" (
    "%PY%" "%VAULT%\pipeline\link_engine.py" suggestions --root "%VAULT%"
  ) else (
    "%PY%" "%VAULT%\pipeline\link_engine.py" apply --root "%VAULT%"
  )
  goto :eof
)

if /i "%TOOL%"=="recall" (
  if "%~1"=="" (
    "%PY%" "%VAULT%\pipeline\recall_schedule.py" deck --root "%VAULT%"
  ) else (
    "%PY%" "%VAULT%\pipeline\recall_schedule.py" mark --root "%VAULT%"
  )
  goto :eof
)

if /i "%TOOL%"=="clean" (
  if "%~1"=="" (
    "%PY%" "%VAULT%\pipeline\clean.py" dry-run --root "%VAULT%"
  ) else if "%~1"=="--apply" (
    "%PY%" "%VAULT%\pipeline\clean.py" apply --root "%VAULT%"
  ) else (
    "%PY%" "%VAULT%\pipeline\clean.py" "%~1" --root "%VAULT%"
  )
  goto :eof
)

if /i "%TOOL%"=="feedback" (
  if "%~1"=="hit" (
    shift
    "%PY%" "%VAULT%\pipeline\feedback_loop.py" hit %* --root "%VAULT%"
  ) else if "%~1"=="apply" (
    shift
    "%PY%" "%VAULT%\pipeline\feedback_loop.py" apply --root "%VAULT%" %*
  ) else (
    "%PY%" "%VAULT%\pipeline\feedback_loop.py" ingest --root "%VAULT%" %*
  )
  goto :eof
)

if /i "%TOOL%"=="healthcheck" (
  "%PY%" "%VAULT%\pipeline\kb-healthcheck.py" %*
  goto :eof
)

"%PY%" "%VAULT%\pipeline\%TOOL%.py" %* --root "%VAULT%"
goto :eof

:nopys
echo ❌ 未找到 Python。请先安装 Python 3.8+（装时勾选 Add to PATH 或加进 PATH），再重试。
goto :eof

:help
echo 用法: kb <工具> [参数...]
echo   kb rag index                 重建本地向量索引
echo   kb query "问题" [--answer]   自然语言检索（+ 最佳片段；--answer 需配 ORNITH_API_KEY/BASE_URL）
echo   kb ingest                    收件箱 triage（review）
echo   kb ingest move               按路由搬运收件箱（写操作）
echo   kb ingest trash              把收件箱进归档（写操作）
echo   kb feedback                  命中信号计数
echo   kb link                      孤岛补链建议
echo   kb link apply                应用补链（写操作）
echo   kb recall                    今日回忆 deck
echo   kb recall status              今日回忆排期状态
echo   kb healthcheck               健康巡检
echo   kb dashboard                 生成治理仪表盘
echo   kb clean --apply             清洗/分块
echo   kb validate                  frontmatter 校验
echo   kb backup [daily|weekly]     全量快照
goto :eof
