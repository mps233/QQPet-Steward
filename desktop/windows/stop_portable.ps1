$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root "runs\dashboard.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "No dashboard PID file found; nothing to stop."
    exit 0
}

$DashboardPid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $DashboardPid) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Dashboard PID file was empty; removed it."
    exit 0
}

$Process = Get-Process -Id ([int]$DashboardPid) -ErrorAction SilentlyContinue
if (-not $Process) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Dashboard is not running; removed stale PID file."
    exit 0
}

Stop-Process -Id ([int]$DashboardPid)
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "Dashboard stopped (PID $DashboardPid)."
