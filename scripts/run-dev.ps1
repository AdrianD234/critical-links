#Requires -Version 5.1
<#
.SYNOPSIS
  Start the FastAPI service (in WSL) and the web app (on Windows).
.PARAMETER SnapshotId
  Snapshot to serve. Defaults to the most recent in the database.
#>
param([string]$SnapshotId = '', [string]$Distro = 'Ubuntu')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$wslRepo = (& wsl -d $Distro -- wslpath -a "$((Get-Location).Path)").Trim()

Write-Host 'Starting API in WSL (PostgreSQL + PostGIS + pgRouting)' -ForegroundColor Cyan
& wsl -d $Distro -u root -- env VENV=/home/$(& wsl -d $Distro -- whoami)/.venvs/nzcl bash "$wslRepo/scripts/wsl-run-api.sh" $SnapshotId
if ($LASTEXITCODE -ne 0) { throw 'API failed to start' }

Write-Host "`nStarting web app on http://localhost:5173" -ForegroundColor Cyan
& npm run dev --workspace apps/web
