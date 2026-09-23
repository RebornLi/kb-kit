@echo off
rem ============================================================
rem install.bat —— 一键搭建知识库（Windows 双击运行）
rem   输入目标目录(留空=当前目录下新建 kb-kit)；参数可选透传。
rem ============================================================
setlocal enabledelayedexpansion
set "SELF_DIR=%~dp0"
set "SELF_DIR=%SELF_DIR:~0,-1%"

rem ---- 1. 找 Python（where + 遍历常见路径）----
set "PY="
where python >nul 2>&1 && (for /f "delims=" %%i in ('where python') do (set "PY=%%i" & goto :foundpy))
where py >nul 2>&1 && (for /f "delims=" %%i in ('where py') do (set "PY=%%i" & goto :foundpy))
where python3 >nul 2>&1 && (for /f "delims=" %%i in ('where python3') do (set "PY=%%i" & goto :foundpy))

rem 遍历常见安装路径
for /f "delims=" %%i in ('dir /b /s "%LOCALAPPDATA%\Programs\Python\Python3*\python.exe" 2^>nul') do (set "PY=%%i" & goto :foundpy)
for /f "delims=" %%i in ('dir /b /s "%USERPROFILE%\AppData\Local\Programs\Python\Python3*\python.exe" 2^>nul') do (set "PY=%%i" & goto :foundpy)
for /f "delims=" %%i in ('dir /b /s "C:\Python3*\python.exe" 2^>nul') do (set "PY=%%i" & goto :foundpy)
for /f "delims=" %%i in ('dir /b /s "C:\Program Files\Python3*\python.exe" 2^>nul') do (set "PY=%%i" & goto :foundpy)
for /f "delims=" %%i in ('dir /b /s "C:\Program Files (x86)\Python3*\python.exe" 2^>nul') do (set "PY=%%i" & goto :foundpy)

echo ❌ 未找到 Python。请先安装 Python 3.8+：
echo    下载: https://www.python.org/downloads/
echo    安装时务必勾选 "Add Python to PATH"
pause
exit /b 3

:foundpy
rem ---- 2. 环境自检（precheck.py）----
if exist "%SELF_DIR%\scripts\precheck.py" (
    echo ▶ 环境检查……
    "%PY%" "%SELF_DIR%\scripts\precheck.py"
    if errorlevel 1 (
        echo ❌ 环境检查未通过，请按上方提示修复后重试。
        pause
        exit /b 3
    )
)

rem ---- 3. 解析参数：首个非 - 参数 = 目标目录（存在则跳过询问）；其余透传 flag ----
set "VAULT="
set "FLAGS="
:argloop
if "%~1"=="" goto :afterargs
set "ARG=%~1"
if "!ARG:~0,1!"=="-" (
  set "FLAGS=!FLAGS!!ARG! "
) else if not defined VAULT (
  set "VAULT=%~1"
) else (
  set "FLAGS=!FLAGS!!ARG! "
)
shift
goto :argloop
:afterargs
if not defined VAULT (
  set /p ANSWER="📂 目标知识库目录（留空 = 当前目录下新建 kb-kit）: "
  if "!ANSWER%"=="" set "ANSWER=kb-kit"
  set "VAULT=!ANSWER!"
)

echo.
echo 开始搭建……
"%PY%" "%SELF_DIR%\create_vault.py" --vault "%VAULT%"!FLAGS!
if %ERRORLEVEL% NEQ 0 ( echo 安装返回非 0 退出码: %ERRORLEVEL% )
echo.
pause
goto :eof
