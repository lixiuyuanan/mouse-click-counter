@echo off
cd /d "%~dp0"
set "APP="
for %%F in ("%~dp0*.exe") do set "APP=%%~fF"
if defined APP (
  start "" "%APP%"
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "mouse_counter.py"
  exit /b
)
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw "mouse_counter.py"
  exit /b
)
start "" python "mouse_counter.py"
