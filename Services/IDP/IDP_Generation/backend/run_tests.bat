@echo off
REM Double-click to run the IDP test harness.
REM cd to this file's folder (the backend dir) so pytest finds pytest.ini.
cd /d "%~dp0"
"C:\Users\patton.zepp\AppData\Local\AIC\LISA\.venv\Scripts\python.exe" -m pytest
echo.
echo ----------------------------------------------------------------
echo Done. (Level-3 AutoCAD test runs only if AutoCAD is open; else it skips.)
pause
