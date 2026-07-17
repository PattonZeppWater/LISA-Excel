@echo off
title LISA
cd /d "%~dp0LISA"

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
