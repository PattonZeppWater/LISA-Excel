# ensure-frontend.ps1 — make sure the LISA React UI is built (Frontend/frontend/dist).
#
# WHY: Frontend/frontend/dist and node_modules are gitignored (dist is generated,
# node_modules is huge/machine-specific), so a fresh `git clone` has NO built UI — LISA
# then serves HTTP 404. This script builds it, auto-provisioning a portable Node 20 if the
# machine has none, so a plain checkout becomes launchable with zero manual toolchain setup.
#
# Called by "SETUP - Run First.bat" (always) and "LAUNCH LISA.bat" (only if dist is missing).
# Safe to re-run: it skips work that's already done unless -Force is given.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File Build\ensure-frontend.ps1 [-Force]
[CmdletBinding()]
param(
  [string]$Root,       # repo root; defaults below to the parent of this script's Build\ folder
  [switch]$Force       # rebuild even if dist already exists
)

$ErrorActionPreference = "Stop"

# Resolve the repo root in the BODY (not a param default -- $PSScriptRoot is empty in a param
# default on Windows PowerShell 5.1). Root = parent of the Build\ folder this script lives in.
if (-not $Root) {
  $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
  $Root = Split-Path -Parent $scriptDir
}
# Portable Node target — MUST match the path the post-merge git hook looks for.
$NodeVer   = "20.18.1"
$NodeDir   = "C:\node20\node-v$NodeVer-win-x64"
$NodeUrl   = "https://nodejs.org/dist/v$NodeVer/node-v$NodeVer-win-x64.zip"
$FrontDir  = Join-Path $Root "Frontend\frontend"
$DistIndex = Join-Path $FrontDir "dist\index.html"

function Write-Step($m) { Write-Host "      $m" }

# This checkout may be a packaged bundle that ships a prebuilt dist and no frontend source.
# Nothing to build in that case — succeed quietly.
if (-not (Test-Path (Join-Path $FrontDir "package.json"))) {
  Write-Step "no frontend source here (packaged bundle) - skipping UI build."
  exit 0
}

if ((Test-Path $DistIndex) -and -not $Force) {
  Write-Step "LISA interface already built (dist present) - skipping."
  exit 0
}

# ── 1. Find npm: PATH → portable C:\node20 → download portable Node 20 ──────────────
$Npm = $null
if (Get-Command npm -ErrorAction SilentlyContinue) {
  $Npm = "npm"
  Write-Step "using Node/npm already on PATH."
} elseif (Test-Path (Join-Path $NodeDir "npm.cmd")) {
  $env:Path = "$NodeDir;$env:Path"
  $Npm = Join-Path $NodeDir "npm.cmd"
  Write-Step "using portable Node at $NodeDir."
} else {
  Write-Step "no Node found - downloading portable Node $NodeVer (no admin needed) ..."
  $zip = Join-Path $env:TEMP "node-v$NodeVer-win-x64.zip"
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $NodeUrl -OutFile $zip -UseBasicParsing
    if (-not (Test-Path "C:\node20")) { New-Item -ItemType Directory -Path "C:\node20" -Force | Out-Null }
    Write-Step "extracting Node to C:\node20 ..."
    Expand-Archive -Path $zip -DestinationPath "C:\node20" -Force
    Remove-Item $zip -ErrorAction SilentlyContinue
  } catch {
    Write-Host ""
    Write-Host "  ERROR: could not download/extract Node from $NodeUrl" -ForegroundColor Red
    Write-Host "  Check your internet/proxy, or install Node 20 LTS from https://nodejs.org ,"
    Write-Host "  then re-run.  (Or unzip node-v$NodeVer-win-x64 into C:\node20\ .)"
    exit 1
  }
  if (-not (Test-Path (Join-Path $NodeDir "npm.cmd"))) {
    Write-Host "  ERROR: Node did not extract to the expected path ($NodeDir)." -ForegroundColor Red
    exit 1
  }
  $env:Path = "$NodeDir;$env:Path"
  $Npm = Join-Path $NodeDir "npm.cmd"
  Write-Step "portable Node $NodeVer ready."
}

# ── 2. Install deps (only if node_modules is missing) then build ───────────────────
Push-Location $FrontDir
try {
  if (-not (Test-Path (Join-Path $FrontDir "node_modules"))) {
    Write-Step "installing frontend packages (npm install) - a few minutes ..."
    & $Npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)" }
  }
  Write-Step "building the LISA interface (npm run build) ..."
  & $Npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit $LASTEXITCODE)" }
} catch {
  Write-Host ""
  Write-Host "  ERROR building the frontend: $_" -ForegroundColor Red
  Pop-Location
  exit 1
} finally {
  if ((Get-Location).Path -eq $FrontDir) { Pop-Location }
}

if (-not (Test-Path $DistIndex)) {
  Write-Host "  ERROR: build finished but $DistIndex is missing." -ForegroundColor Red
  exit 1
}
Write-Step "LISA interface built OK (dist ready)."
exit 0
