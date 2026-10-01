@echo off
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MouseClickCounter /f
echo.
pause
