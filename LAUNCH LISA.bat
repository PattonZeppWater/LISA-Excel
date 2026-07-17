@echo off
title LISA
cd /d "%~dp0LISA"
if not exist ".venv\Scripts\pythonw.exe" (
  echo LISA is not set up yet.  Run  "SETUP - Run First.bat"  first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app.py
