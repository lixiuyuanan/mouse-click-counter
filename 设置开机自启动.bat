@echo off
cd /d "%~dp0"
set "APP="
for %%F in ("%~dp0*.exe") do set "APP=%%~fF"
if defined APP (
  reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MouseClickCounter /t REG_SZ /d "\"%APP%\"" /f
) else (
  python "mouse_counter.py" --set-startup 2>nul
  if errorlevel 1 py "mouse_counter.py" --set-startup
)
echo.
pause
