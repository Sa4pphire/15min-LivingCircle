@echo off
setlocal
cd /d "%~dp0"
set "EDITOR_ACTION=%~1"
if "%EDITOR_ACTION%"=="" set "EDITOR_ACTION=restart"
where pwsh >nul 2>nul
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %EDITOR_ACTION% -BackendPort 8001 -FrontendPort 5174
) else (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %EDITOR_ACTION% -BackendPort 8001 -FrontendPort 5174
)
if errorlevel 1 (
  echo Editor startup failed. If sources changed, run start-network-editor.bat restart.
  pause
  exit /b 1
)
if /I "%EDITOR_ACTION%"=="start" start "" "http://127.0.0.1:5174/?mode=editor"
if /I "%EDITOR_ACTION%"=="restart" start "" "http://127.0.0.1:5174/?mode=editor"
endlocal
