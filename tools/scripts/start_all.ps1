# =============================================================
# Start script: AI Job Assistant (main + Web console)
# Logs go to data/logs/ (main.log / main_err.log / web.log / web_err.log)
# Usage: powershell -File tools\scripts\start_all.ps1
# =============================================================
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # tools/scripts -> project root
$py   = "C:\Users\26440\anaconda3\python.exe"
$logs = Join-Path $root "data\logs"

if (-not (Test-Path $py)) { Write-Error "Python not found: $py"; exit 1 }
New-Item -ItemType Directory -Force $logs | Out-Null

# Main program (NapCat listener + full pipeline)
Start-Process -FilePath $py -ArgumentList @("-m", "job_assistant.main") `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $logs "main.log") `
  -RedirectStandardError  (Join-Path $logs "main_err.log") `
  -WindowStyle Hidden -PassThru | ForEach-Object { Write-Output "Main started PID $($_.Id)" }

# Web console (job dashboard, port 8099)
Start-Process -FilePath $py -ArgumentList @("-m", "job_assistant.web.server", "--port", "8099") `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $logs "web.log") `
  -RedirectStandardError  (Join-Path $logs "web_err.log") `
  -WindowStyle Hidden -PassThru | ForEach-Object { Write-Output "Web started PID $($_.Id)  http://127.0.0.1:8099" }

Write-Output "Log dir: $logs"
