# =============================================================
# Stop script: AI Job Assistant (main + Web console)
# Usage: powershell -File tools\scripts\stop_all.ps1
# =============================================================
$ErrorActionPreference = "SilentlyContinue"

Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq "python.exe" -and ($_.CommandLine -match "job_assistant\.main" -or $_.CommandLine -match "job_assistant\.web\.server") } |
  ForEach-Object {
    $name = if ($_.CommandLine -match "web\.server") { "Web console" } else { "Main" }
    Stop-Process -Id $_.ProcessId -Force
    Write-Output "Stopped $name PID $($_.ProcessId)"
  }

Write-Output "All stopped"
