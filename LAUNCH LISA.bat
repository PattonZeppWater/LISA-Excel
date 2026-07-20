@echo off
title LISA
REM Work whether we're in a packaged bundle (app lives in a LISA\ subfolder) or in the
REM repo checkout (app.py is right next to this .bat). Pick whichever actually has app.py.
if exist "%~dp0LISA\app.py" (
  cd /d "%~dp0LISA"
) else (
  cd /d "%~dp0"
)

REM The virtual environment is machine-specific and is NOT shipped in the zip.
REM Check that it both EXISTS and actually RUNS -- a .venv copied from another PC
REM is dead (its config points at the original PC's Python), so we must verify it
REM works, not just that the files are present, or LISA would fail to launch silently.
if not exist ".venv\Scripts\pythonw.exe" goto needsetup
".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 goto needsetup

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:needsetup
echo.
echo   LISA is not set up on THIS PC yet.
echo   Please run  "SETUP - Run First.bat"  first, then launch again.
echo.
pause
exit /b 1
