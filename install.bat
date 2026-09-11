@echo off
rem ============================================================
rem install.bat —— 一键搭建知识库（Windows 双击运行）
rem   输入目标目录(留空=当前目录下新建 KB)；参数可选透传。
rem ============================================================
setlocal
set "SELF_DIR=%~dp0"

set /p ANSWER="📂 目标知识库目录（留空 = 当前目录下新建 KB）: "
if "%ANSWER%"=="" set "ANSWER=KB"

echo.
echo 开始搭建……
where python >nul 2>&1 && set "PY=python" || (where py >nul 2>&1 && set "PY=py" || (where python3 >nul 2>&1 && set "PY=python3" || set "PY=__NONE__"))
if "%PY%"=="__NONE__" goto :nopys
"%PY%" "%SELF_DIR%create_vault.py" --vault "%ANSWER%" %*
if %ERRORLEVEL% NEQ 0 ( echo 安装返回非 0 退出码: %ERRORLEVEL% )
echo.
pause
goto :eof
:nopys
echo ❌ 未找到 Python。请先安装 Python 3.8+（装时勾选 Add to PATH 或加进 PATH），再重试。
pause
exit /b 3
