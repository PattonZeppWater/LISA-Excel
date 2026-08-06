@echo off
title LISA
REM Work whether we're in a packaged bundle (app lives in a LISA\ subfolder) or in the
REM repo checkout (app.py is right next to this .bat). Pick whichever actually has app.py.
if exist "%~dp0LISA\app.py" (
  cd /d "%~dp0LISA"
) else (
  cd /d "%~dp0"
)

REM The virtual environment is machine-specific and is NOT shipped / committed. Check that it
REM both EXISTS and actually RUNS -- and that its real dependencies import. A .venv copied from
REM another PC is dead, and a half-installed one (e.g. missing pywin32) makes app.py crash on
REM `import pythoncom` with NO error window (pythonw has no console). Verify the actual imports
REM so we route to setup instead of "launching" into a silent crash.
if not exist ".venv\Scripts\pythonw.exe" goto needsetup
".venv\Scripts\python.exe" -c "import pythoncom, win32com, flask, flask_cors, webview" >nul 2>&1
if errorlevel 1 goto needsetup

REM Self-heal a missing interface. Frontend\frontend\dist is gitignored, so a fresh clone (or an
REM install from before the frontend was part of setup) has NO built UI and LISA would serve a
REM blank / HTTP 404 window. Build it now (auto-installs a portable Node if needed) rather than
REM launch broken. Only in a repo checkout with the helper present; a bundle ships dist.
if not exist "Frontend\frontend\dist\index.html" (
  if exist "Build\ensure-frontend.ps1" (
    echo Building the LISA interface for the first time -- this can take a few minutes ...
    powershell -NoProfile -ExecutionPolicy Bypass -File "Build\ensure-frontend.ps1"
  )
)
REM If the interface still isn't built (auto-build failed, e.g. no internet for Node), say so
REM clearly instead of launching into a blank / 404 window.
if not exist "Frontend\frontend\dist\index.html" (
  echo.
  echo   The LISA interface is not built yet, and the automatic build did not finish.
  echo   Please run  "SETUP - Run First.bat"  once ^(it builds the interface^), then launch again.
  echo.
  pause
  exit /b 1
)

REM Free port 5000 from a stale/zombie LISA (the most common "launch does nothing" cause).
REM If a HEALTHY LISA is already running, don't start a second copy.
if exist "Build\free-port.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "Build\free-port.ps1"
  if errorlevel 10 (
    echo.
    echo   LISA is already running -- look for its window on your taskbar.
    echo   If you can't find it, close it fully ^(or end pythonw.exe^) and launch again.
    echo.
    timeout /t 4 >nul
    exit /b 0
  )
)

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:needsetup
echo.
echo   LISA is not set up on THIS PC yet.
echo   Please run  "SETUP - Run First.bat"  first, then launch again.
echo.
pause
exit /b 1
