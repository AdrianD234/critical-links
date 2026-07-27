#Requires -Version 5.1
<#
.SYNOPSIS
  Provision the Python + PostGIS + pgRouting stack inside WSL.
.DESCRIPTION
  Installs PostgreSQL 16, PostGIS and pgRouting via apt, creates the nzcl
  database and role, then builds a Python virtual environment and installs the
  nzcl package. Idempotent - safe to re-run.

  No Docker is required. WSL Ubuntu carries the whole stack in apt.
#>
param([string]$Distro = 'Ubuntu')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$wslRepo = (& wsl -d $Distro -- wslpath -a "$((Get-Location).Path)").Trim()
Write-Host "repo in WSL: $wslRepo" -ForegroundColor Cyan

Write-Host "`nStep 1/2 - database" -ForegroundColor Cyan
& wsl -d $Distro -u root -- bash "$wslRepo/scripts/wsl-setup-db.sh"
if ($LASTEXITCODE -ne 0) { throw 'database provisioning failed' }

Write-Host "`nStep 2/2 - Python environment" -ForegroundColor Cyan
& wsl -d $Distro -- bash -lc "python3 -m venv ~/.venvs/nzcl && ~/.venvs/nzcl/bin/pip install -q --upgrade pip && cd '$wslRepo/python' && ~/.venvs/nzcl/bin/pip install -q -e '.[dev]' && ~/.venvs/nzcl/bin/python -c 'from nzcl.db import migrate, server_versions; print(\"migrations:\", migrate()); [print(f\"  {k:12} {v}\") for k,v in server_versions().items()]'"
if ($LASTEXITCODE -ne 0) { throw 'python environment setup failed' }

Write-Host "`nDone. Next:" -ForegroundColor Cyan
Write-Host '  .\scripts\ingest-pilot.ps1     # discover + ingest Wellington into PostGIS'
Write-Host '  .\scripts\run-dev.ps1          # start the API and web app'
