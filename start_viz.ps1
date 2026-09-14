$ErrorActionPreference="Stop"
Set-Location "."
$env:PYTHONPATH="src"
$env:PYTHONWARNINGS="ignore"
Write-Host "启动 AlphaForge 仪表盘..." -ForegroundColor Cyan
Start-Process "http://127.0.0.1:8050"
python -m alphaforge viz
