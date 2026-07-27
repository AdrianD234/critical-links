#Requires -Version 5.1
<#
.SYNOPSIS
  Run both test suites: Python (pytest, against PostGIS) and TypeScript (vitest).
#>
param([string]$Distro = 'Ubuntu')
$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..')
$wslRepo = (& wsl -d $Distro -- wslpath -a "$((Get-Location).Path)").Trim()
$failed = $false

Write-Host '== Python (pytest) ==' -ForegroundColor Cyan
& wsl -d $Distro -- bash -lc "cd '$wslRepo/python' && ~/.venvs/nzcl/bin/python -m pytest tests/ -q"
if ($LASTEXITCODE -ne 0) { $failed = $true }

Write-Host "`n== TypeScript (vitest) ==" -ForegroundColor Cyan
& npx vitest run
if ($LASTEXITCODE -ne 0) { $failed = $true }

if ($failed) { Write-Host "`nFAILURES" -ForegroundColor Red; exit 1 }
Write-Host "`nAll suites passed" -ForegroundColor Green
