@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto setup_required
".venv\Scripts\python.exe" -c "import customtkinter" >nul 2>nul
if errorlevel 1 goto setup_required
if not exist ".venv\Scripts\pythonw.exe" (
  echo The virtual environment has no pythonw.exe. Run setup.cmd again.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "clipwatcher_gui.py"
exit /b 0

:setup_required
echo The app environment or GUI dependencies are missing.
echo Run setup.cmd first, then try watch-gui.cmd again.
pause
exit /b 1
