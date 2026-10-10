@echo off
setlocal
if not "%~1"=="" goto command_line

echo Starting Living Circle and Network Editor...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" -Action restart
if errorlevel 1 (
  echo.
  echo Startup failed. See the error and log paths above.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:5173/"
exit /b 0

:command_line
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
exit /b %errorlevel%
