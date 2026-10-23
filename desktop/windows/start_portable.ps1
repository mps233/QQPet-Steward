$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Config = Join-Path $Root "config.yaml"
$Example = Join-Path $Root "config.example.yaml"
if (-not (Test-Path $Config) -and (Test-Path $Example)) {
    Copy-Item $Example $Config
}

$Runs = Join-Path $Root "runs"
New-Item -ItemType Directory -Force -Path $Runs | Out-Null
$PidFile = Join-Path $Runs "dashboard.pid"
$Exe = Join-Path $Root "QQPetCopilot.exe"

if (-not (Test-Path $Exe)) {
    Write-Error "Missing dashboard executable: $Exe"
    exit 1
}

if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($OldPid -and (Get-Process -Id ([int]$OldPid) -ErrorAction SilentlyContinue)) {
        Write-Host "Dashboard already running (PID $OldPid)."
        exit 0
    }
}

$env:QQPET_DATA_DIR = $Root
$env:QQPET_PORTABLE = "1"
try {
    $Process = Start-Process -FilePath $Exe `
        -ArgumentList @("--port", "8787") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
} catch {
    Write-Error "Failed to start dashboard: $_"
    exit 1
}
Set-Content -Path $PidFile -Value $Process.Id -Encoding ASCII
Write-Host "Dashboard started (PID $($Process.Id)): http://127.0.0.1:8787"
Write-Host "Access code: $Runs\dashboard_access.token"
