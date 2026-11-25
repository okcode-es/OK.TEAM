<#
.SYNOPSIS
  Zero-Token local quality gate for okTeam (Backend + Frontend).
#>
[CmdletBinding()]
param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Scope = "all"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "uv executable not found in PATH or $fallback"
}

function Invoke-Step {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    Write-Host "`n>>> $Label" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Label (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "PASS: $Label" -ForegroundColor Green
}

if ($Scope -in @("all", "backend")) {
    $UvExe = Resolve-Uv
    Push-Location (Join-Path $RepoRoot "backend")
    try {
        Invoke-Step "Backend: Domain Purity AST Check" {
            & $UvExe run python (Join-Path $RepoRoot "scripts\check_domain_purity.py")
        }
        Invoke-Step "Backend: Ruff Lint" {
            & $UvExe run ruff check .
        }
        Invoke-Step "Backend: Mypy Type Check" {
            & $UvExe run mypy app
        }
        Invoke-Step "Backend: Pytest Suite" {
            & $UvExe run pytest
        }
    } finally {
        Pop-Location
    }
}

if ($Scope -in @("all", "frontend")) {
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        Invoke-Step "Frontend: TypeScript Check (tsc --noEmit)" {
            npm run typecheck
        }
        Invoke-Step "Frontend: ESLint" {
            npm run lint
        }
    } finally {
        Pop-Location
    }
}

Write-Host "`nALL QUALITY GATES PASSED ($Scope)." -ForegroundColor Green
