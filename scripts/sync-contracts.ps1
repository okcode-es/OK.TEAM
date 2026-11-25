<#
.SYNOPSIS
  Sync OpenAPI contract from FastAPI backend and generate frontend TypeScript types.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "uv executable not found in PATH or $fallback"
}

$UvExe = Resolve-Uv

Write-Host "[1/2] Exporting contracts/openapi.json from FastAPI..." -ForegroundColor Cyan
Push-Location (Join-Path $RepoRoot "backend")
try {
    & $UvExe run python (Join-Path $RepoRoot "scripts\export_openapi.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "[2/2] Generating frontend/src/api/generated/schema.d.ts..." -ForegroundColor Cyan
Push-Location (Join-Path $RepoRoot "frontend")
try {
    npm run generate:api
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "Contract sync complete." -ForegroundColor Green
