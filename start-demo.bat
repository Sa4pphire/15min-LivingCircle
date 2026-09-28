@echo off
setlocal
if not "%~1"=="" goto command_line

:menu
echo.
echo Living Circle Demo Services
echo [1] Start    [2] Stop    [3] Restart    [4] Status    [5] Exit
choice /c 12345 /n /m "Select: "
if errorlevel 5 exit /b 0
if errorlevel 4 set "DEMO_ACTION=status"
if errorlevel 4 goto selected
if errorlevel 3 set "DEMO_ACTION=restart"
if errorlevel 3 goto selected
if errorlevel 2 set "DEMO_ACTION=stop"
if errorlevel 2 goto selected
set "DEMO_ACTION=start"

:selected
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" -Action "%DEMO_ACTION%"
echo.
pause
goto menu

:command_line
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1" %*
exit /b %errorlevel%
