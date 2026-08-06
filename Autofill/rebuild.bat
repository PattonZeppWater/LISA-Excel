@echo off
REM One-click rebuild of IDP_ControlPanel.exe.
REM   Double-click            -> rebuild to the last-used (or default dist\) location.
REM   rebuild.bat "D:\Folder"  -> rebuild into that folder and remember it.
REM   rebuild.bat "D:\a\x.exe" -> rebuild to that exact file and remember it.
cd /d "%~dp0"
python "%~dp0rebuild.py" %*
echo.
pause
