# TokenAnalytics — one-line installer (Windows PowerShell).
#   irm https://raw.githubusercontent.com/2netgr/tokenanalytics/main/install.ps1 | iex
$ErrorActionPreference = "Stop"

$RepoUrl   = "https://github.com/2netgr/tokenanalytics.git"
$TargetDir = if ($env:TOKENANALYTICS_DIR) { $env:TOKENANALYTICS_DIR } elseif ($env:TOKENTELEMETRY_DIR) { $env:TOKENTELEMETRY_DIR } else { "tokenanalytics" }

function Need($cmd) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Error "ERROR: $cmd is required but not installed."
    exit 1
  }
}

Need git
Need node
Need npm
if (-not (Get-Command python -ErrorAction SilentlyContinue) -and
    -not (Get-Command python3 -ErrorAction SilentlyContinue)) {
  Write-Error "ERROR: python is required but not installed."
  exit 1
}

if (-not (Test-Path "./bin/cli.js")) {
  if (Test-Path $TargetDir) {
    Write-Host "-> using existing clone at $TargetDir"
  } else {
    Write-Host "-> cloning $RepoUrl -> $TargetDir"
    git clone --depth 1 $RepoUrl $TargetDir
  }
  Set-Location $TargetDir
}

node bin/cli.js
