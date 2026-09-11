# ============================================================
# install.ps1 —— 一键搭建知识库（Windows PowerShell）
#   用法: .\install.ps1 [-NoDemo] [-Force] [-NoGit]
# ============================================================
[Console]::Out.Write("📂 目标知识库目录（留空 = 当前目录下新建 KB）: ")
$answer = [Console]::ReadLine()
$scriptRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($answer)) { $dest = Join-Path $scriptRoot "KB" }
else { $dest = if ([System.IO.Path]::IsPathRooted($answer)) { $answer } else { Join-Path $scriptRoot $answer } }

Write-Host "▶ 开始搭建: $dest" -ForegroundColor Cyan
$py = $null
foreach ($c in @('python','py','python3')) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) {
  Write-Host "❌ 未找到 Python。请先安装 Python 3.8+（装时勾选 Add to PATH 或加进 PATH），再重试。" -ForegroundColor Red
  Read-Host "回车退出"
  exit 3
}
& $py (Join-Path $scriptRoot "create_vault.py") --vault $dest $args
if ($LASTEXITCODE -ne 0) { Write-Host "安装返回非 0 退出码: $LASTEXITCODE" -ForegroundColor Red; exit $LASTEXITCODE }
Read-Host "回车退出"
