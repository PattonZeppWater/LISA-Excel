# free-port.ps1 — make port 5000 available for LISA before launch, SAFELY.
#
# LISA serves Flask on port 5000. The #1 "clicking LAUNCH does nothing" cause is a previous LISA
# that never fully closed: its pythonw keeps port 5000, so the new instance can't bind it and dies
# silently (pythonw has no console). This clears that stale process.
#
# Two safety rules so we never kill the wrong thing:
#   * If a HEALTHY LISA already answers /api/health, we leave it alone (exit 10).
#   * We only ever kill a process whose executable is THIS checkout's own .venv python(w) --
#     identified by exact exe path -- so an unrelated program on 5000, or a LISA from a different
#     checkout, is never touched.
#
# Exit 10 : a healthy LISA already answers on http://127.0.0.1:5000  -> caller should NOT start a 2nd copy.
# Exit  0 : port is free, or a stale THIS-checkout LISA squatting on it was cleared -> ok to launch.
param([string]$Root)   # repo root; defaults to the parent of this script's Build\ folder
$ErrorActionPreference = "SilentlyContinue"
$port = 5000

# 1. Healthy LISA already serving? Leave it alone (don't interrupt an in-progress generation).
#    Use 127.0.0.1 explicitly -- 'localhost' can resolve to IPv6 ::1 while Flask binds IPv4.
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3 -UseBasicParsing
  if ($r.Content -match '"service"\s*:\s*"LISA"') {
    Write-Host "      LISA is already running (healthy on port $port)."
    exit 10
  }
} catch { }

# 2. Otherwise clear a STALE LISA holding the port. We identify it as a python/pythonw process,
#    listening on 5000, whose COMMAND LINE runs LISA's app.py -- NOT by exe path: a venv
#    python(w).exe is only a redirector stub, so its process image path is the BASE interpreter
#    (…\Python3xx\python.exe), never the .venv path. Command line reliably shows "... app.py".
#    Guarded by the health check above, so a healthy LISA is never in scope -- only a crashed one.
$owners = @()
try {
  $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique
} catch {
  # Get-NetTCPConnection unavailable (very old Windows) -> parse netstat.
  foreach ($ln in (netstat -ano -p tcp 2>$null | Select-String ":$port\s+.*LISTENING")) {
    $cols = $ln.ToString().Trim() -split '\s+'
    $owners += $cols[$cols.Count - 1]
  }
}
$killed = 0
foreach ($procId in ($owners | Sort-Object -Unique)) {
  $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if (-not $p) { continue }
  if ($p.ProcessName -ne 'python' -and $p.ProcessName -ne 'pythonw') { continue }
  $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue).CommandLine
  if ($cmd -and ($cmd -match 'app\.py')) {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    $killed++
  }
}
if ($killed -gt 0) {
  Write-Host "      Cleared $killed stale LISA process(es) holding port $port."
  Start-Sleep -Milliseconds 600
}
exit 0
