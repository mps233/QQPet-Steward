@echo off
setlocal
cd /d "%~dp0"

if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy /Y "config.example.yaml" "config.yaml" >nul
    )
)
if not exist "runs" mkdir "runs"

set "QQPET_DATA_DIR=%CD%"
set "QQPET_PORTABLE=1"

rem Start the dashboard, then leave the window open so users can see the URL
rem and access-code location.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_portable.ps1"
if errorlevel 1 (
    pause
    exit /b %errorlevel%
)
echo.
echo Dashboard: http://127.0.0.1:8787
echo Access code: %CD%\runs\dashboard_access.token
pause
endlocal
