@echo off
setlocal enabledelayedexpansion
title LISA Setup
REM Work in a packaged bundle (app in a LISA\ subfolder) OR the repo checkout (app.py
REM next to this .bat) -- set up the venv wherever app.py + requirements.txt actually live.
if exist "%~dp0LISA\app.py" (
  cd /d "%~dp0LISA"
) else (
  cd /d "%~dp0"
)
echo ============================================================
echo    LISA Setup  -  run this ONCE  (needs internet)
echo ============================================================
echo.

REM --- 0. refuse to run while LISA is open --------------------
REM A running LISA holds the .venv files LOCKED; the rebuild below would fail 'Access is
REM denied' partway and CORRUPT the venv. Bail early with a clear message instead.
if exist "Build\lisa-running.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "Build\lisa-running.ps1"
  if not errorlevel 1 (
    echo.
    echo   LISA appears to be RUNNING. Close the LISA window first ^(and make sure no
    echo   pythonw.exe is left in Task Manager^), then run this setup again.
    echo.
    pause
    exit /b 1
  )
)

REM --- 1. look for an already-installed Python 3.10-3.12 -------
echo [1/5] Looking for Python 3.10-3.12 ...
set "PYEXE="
for %%V in (3.12 3.11 3.10) do (
  if not defined PYEXE (
    py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V"
  )
)
if not defined PYEXE (
  for /f "delims=" %%P in ('python -c "import sys;print('%%d.%%d'%%sys.version_info[:2])" 2^>nul') do set "PYVER=%%P"
  if defined PYVER (
    echo !PYVER!| findstr /r "^3\.1[012]$" >nul && set "PYEXE=python"
  )
)

REM --- 2. if none found, download + silently install Python 3.12 ---
if not defined PYEXE (
  echo       No compatible Python found - downloading Python 3.12 ...
  set "PYINST=%TEMP%\lisa_python312_setup.exe"
  set "PYURL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
  del "!PYINST!" >nul 2>&1
  curl -L -s -o "!PYINST!" "!PYURL!"
  if not exist "!PYINST!" (
    echo       ^(curl unavailable - trying PowerShell^) ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try{[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;Invoke-WebRequest -Uri '!PYURL!' -OutFile '!PYINST!'}catch{exit 1}"
  )
  if not exist "!PYINST!" (
    echo.
    echo   ERROR: could not download Python. Check your internet / proxy,
    echo   or install Python 3.12 manually from https://www.python.org/downloads/
    echo   ^(tick "Add python.exe to PATH"^) then run this file again.
    pause
    exit /b 1
  )
  echo       Installing Python 3.12 ^(silent, no admin needed^) ...
  "!PYINST!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
  del "!PYINST!" >nul 2>&1
  REM PATH isn't refreshed in this window, so point at the just-installed python.exe directly
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  ) else if exist "%ProgramFiles%\Python312\python.exe" (
    set "PYEXE=%ProgramFiles%\Python312\python.exe"
  )
)

if not defined PYEXE (
  echo.
  echo   ERROR: Python still not available after install.
  echo   Please RESTART your PC and run this file again.
  pause
  exit /b 1
)
echo       Using: !PYEXE!
echo.

REM --- 3. create the virtual environment ----------------------
echo [2/5] Creating the virtual environment ...
REM Remove any existing .venv first -- if one was copied in from another PC it is
REM dead (its config points at the original machine), so always build fresh here.
if exist ".venv" rmdir /s /q ".venv"
echo !PYEXE!| findstr "\\" >nul
if errorlevel 1 (
  !PYEXE! -m venv .venv
) else (
  "!PYEXE!" -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo   ERROR: could not create the environment ^(need Python 3.12^).
  pause
  exit /b 1
)

REM --- 4. install dependencies --------------------------------
echo [3/5] Installing dependencies ^(a few minutes^) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" ".venv\Scripts\pywin32_postinstall.py" -install >nul 2>&1

REM --- enable the auto-sync git hook (repo checkout only; a bundle has no .git) ---
REM After this, a `git pull` auto-rebuilds the venv (if requirements.txt changed) and the
REM frontend (if its source changed), so a pulled checkout is never left running old code.
git rev-parse --is-inside-work-tree >nul 2>&1
if not errorlevel 1 (
  git config core.hooksPath .githooks >nul 2>&1
  echo       Git auto-sync hook enabled ^(pull rebuilds venv/frontend as needed^).
)

REM --- 5. build the React UI (Frontend\frontend\dist) ---------
REM dist/ and node_modules/ are gitignored, so a fresh clone has NO built interface and
REM LISA would serve a blank / HTTP 404 window. ensure-frontend.ps1 builds it, downloading
REM a portable Node 20 automatically if this PC has none. This is THE step that makes a
REM plain "git clone" launchable. ^(A packaged bundle ships dist and the script no-ops.^)
echo [4/5] Building the LISA interface ^(first time downloads Node; a few minutes^) ...
if exist "Build\ensure-frontend.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "Build\ensure-frontend.ps1"
  if errorlevel 1 (
    echo.
    echo   WARNING: the LISA interface did not build, so LISA may show a blank/404 window.
    echo   Fix your internet/proxy or install Node 20 from https://nodejs.org , then re-run
    echo   this setup ^(or just run:  Build\ensure-frontend.ps1^).
    echo.
  )
) else (
  echo       ^(no Build\ensure-frontend.ps1 - packaged bundle already includes the interface^)
)

echo [5/5] Done.
echo.
echo ============================================================
echo    Setup complete!  Now double-click  "LAUNCH LISA.bat"
echo ============================================================
pause
