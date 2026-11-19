@echo off
setlocal
cd /d "%~dp0"

set "INSTALLER_MODE="
if /i "%~1"=="/installer" set "INSTALLER_MODE=1"
set "PYTHON_INSTALL_ATTEMPTED="

:find_python
set "PYTHON_LAUNCHER="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"
)
if not defined PYTHON_LAUNCHER (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
  )
)
if not defined PYTHON_LAUNCHER goto offer_python

if not exist ".venv\Scripts\python.exe" (
  echo Creating the project Python environment...
  %PYTHON_LAUNCHER% -m venv .venv
  if errorlevel 1 goto venv_failed
)

echo Installing project requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto requirements_failed

where ffmpeg >nul 2>nul
if errorlevel 1 goto offer_ffmpeg
where ffprobe >nul 2>nul
if errorlevel 1 goto offer_ffmpeg

echo.
echo Setup complete. Start the GUI with watch-gui.cmd or the CLI with watch.cmd.
if not defined INSTALLER_MODE pause
exit /b 0

:offer_ffmpeg
echo.
echo FFmpeg and FFprobe are required to create clips.
where winget >nul 2>nul
if errorlevel 1 goto ffmpeg_manual
choice /C YN /M "Install Gyan.FFmpeg with Winget now?"
if errorlevel 2 goto ffmpeg_manual
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto ffmpeg_manual
where ffmpeg >nul 2>nul
if errorlevel 1 goto ffmpeg_restart
where ffprobe >nul 2>nul
if errorlevel 1 goto ffmpeg_restart
echo.
echo Setup complete. Start the GUI with watch-gui.cmd or the CLI with watch.cmd.
if not defined INSTALLER_MODE pause
exit /b 0

:ffmpeg_restart
echo Winget installed FFmpeg, but this window cannot see it on PATH yet.
echo Close this window, open a new one, and run setup.cmd again.
if not defined INSTALLER_MODE pause
exit /b 1

:ffmpeg_manual
echo FFmpeg was not installed. Run this command in a terminal:
echo winget install --id Gyan.FFmpeg -e
echo Then reopen the terminal and run setup.cmd again.
if not defined INSTALLER_MODE pause
exit /b 1

:offer_python
where winget >nul 2>nul
if errorlevel 1 goto python_manual
if defined PYTHON_INSTALL_ATTEMPTED goto python_manual
echo Python 3.9 or newer was not found.
choice /C YN /M "Install Python 3.12 with Winget now?"
if errorlevel 2 goto python_manual
set "PYTHON_INSTALL_ATTEMPTED=1"
winget install --id Python.Python.3.12 -e --scope user --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto python_manual
goto find_python

:python_manual
echo Python could not be installed automatically.
echo Install Python 3.9 or newer, then run setup.cmd again.
if not defined INSTALLER_MODE pause
exit /b 1

:venv_failed
echo Could not create .venv. Check that Python includes the venv module.
if not defined INSTALLER_MODE pause
exit /b 1

:requirements_failed
echo Dependency installation failed. Check the pip output above and try again.
if not defined INSTALLER_MODE pause
exit /b 1
