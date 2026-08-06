# lisa-running.ps1 — is a LISA instance currently running?
#
# A running LISA is "pythonw app.py" (or "python app.py"). While it runs, it holds the venv's
# .pyd/.dll files LOCKED, so a `pip install` or a venv rebuild hits "Access is denied" mid-way
# and CORRUPTS the venv (missing pip / pywin32 / everything) -- which then makes LISA refuse to
# launch. The post-merge hook and SETUP call this first and refuse to touch the venv while LISA
# is open, telling the user to close it instead.
#
# Exit 0 : a LISA process IS running (do NOT touch the venv).
# Exit 1 : none running (safe to install / rebuild).
$ErrorActionPreference = "SilentlyContinue"
$running = @(
  Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'app\.py' }
)
if ($running.Count -gt 0) { exit 0 } else { exit 1 }
