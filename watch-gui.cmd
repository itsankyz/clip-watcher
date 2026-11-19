@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "clipwatcher_gui.py"
  exit /b 0
)
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
  start "" pythonw "clipwatcher_gui.py"
  exit /b 0
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  start "" python "clipwatcher_gui.py"
  exit /b 0
)
echo Python was not found. Install Python or create .venv first.
pause
