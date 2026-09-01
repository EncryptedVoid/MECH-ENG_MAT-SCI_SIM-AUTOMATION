@echo off
REM ============================================================
REM  LAVA launcher — just double-click this file.
REM  It finds entrypoint.ps1 sitting next to it and runs it,
REM  bypassing the PowerShell script-blocking policy and asking
REM  for administrator rights automatically.
REM
REM  You do not need to open PowerShell yourself.
REM ============================================================

setlocal

REM Run the PowerShell script that lives in the SAME folder as this file.
set "SCRIPT=%~dp0entrypoint.ps1"

if not exist "%SCRIPT%" (
    echo.
    echo  Could not find entrypoint.ps1 next to this launcher.
    echo  Make sure "Start LAVA.bat" and "entrypoint.ps1" are in the same folder.
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting LAVA... a blue window will open.
echo  If Windows asks "Do you want to allow this app to make changes?",
echo  click YES.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"

endlocal
