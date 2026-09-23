# ============================================================
# install.ps1 —— 一键搭建知识库（Windows PowerShell）
#   用法: .\install.ps1 [-NoDemo] [-Force] [-NoGit]
# ============================================================
$scriptRoot = $PSScriptRoot

# ---- 1. 找 Python ----
$py = $null
foreach ($c in @('python','py','python3')) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
# 遍历常见安装路径
if (-not $py) {
  $searchPaths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python3*\python.exe",
    "C:\Python3*\python.exe",
    "C:\Program Files\Python3*\python.exe",
    "C:\Program Files (x86)\Python3*\python.exe"
  )
  foreach ($sp in $searchPaths) {
    $found = Get-ChildItem -Path $sp -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $py = $found.FullName; break }
  }
}
if (-not $py) {
  Write-Host "❌ 未找到 Python。请先安装 Python 3.8+：" -ForegroundColor Red
  Write-Host "   下载: https://www.python.org/downloads/" -ForegroundColor Yellow
  Write-Host "   安装时务必勾选 'Add Python to PATH'" -ForegroundColor Yellow
  Read-Host "回车退出"
  exit 3
}

# ---- 2. 环境自检（precheck.py）----
$precheck = Join-Path $scriptRoot "scripts\precheck.py"
if (Test-Path $precheck) {
  Write-Host "▶ 环境检查……" -ForegroundColor Cyan
  & $py $precheck
  if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 环境检查未通过，请按上方提示修复后重试。" -ForegroundColor Red
    Read-Host "回车退出"
    exit 3
  }
}

# ---- 3. 解析参数：首个非 - 参数 = 目标目录（存在则跳过询问）；其余透传 flag ----
$dest = $null
$flags = @()
foreach ($a in $args) {
  if ($a -like '-') { $flags += $a; continue }
  if ($null -eq $dest) { $dest = $a } else { $flags += $a }
}
if ($null -eq $dest) {
  # 未给位置参数 → 交互询问
  [Console]::Out.Write("📂 目标知识库目录（留空 = 当前目录下新建 kb-kit）: ")
  $answer = [Console]::ReadLine()
  $dest = if ([string]::IsNullOrWhiteSpace($answer)) { Join-Path $scriptRoot "kb-kit" }
          else { if ([System.IO.Path]::IsPathRooted($answer)) { $answer } else { Join-Path $scriptRoot $answer } }
}

Write-Host "▶ 开始搭建: $dest" -ForegroundColor Cyan

# ---- 4. 调 create_vault.py（仅透传 flag，避免与 --vault 重复）----
& $py (Join-Path $scriptRoot "create_vault.py") --vault $dest $flags
if ($LASTEXITCODE -ne 0) { Write-Host "安装返回非 0 退出码: $LASTEXITCODE" -ForegroundColor Red; exit $LASTEXITCODE }
Read-Host "回车退出"
