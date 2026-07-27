#Requires -Version 5.1
<#
.SYNOPSIS
  Discover the AMDS source and ingest a pilot extract into PostGIS.
#>
param([string]$Pilot = 'wellington', [string]$Distro = 'Ubuntu')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$wslRepo = (& wsl -d $Distro -- wslpath -a "$((Get-Location).Path)").Trim()

Write-Host "Step 1/3 - source discovery" -ForegroundColor Cyan
& wsl -d $Distro -- bash -lc "cd '$wslRepo/python' && ~/.venvs/nzcl/bin/python -m nzcl.discover"
if ($LASTEXITCODE -ne 0) { throw 'discovery failed' }

Write-Host "`nStep 2/3 - ingest '$Pilot'" -ForegroundColor Cyan
& wsl -d $Distro -- bash -lc "cd '$wslRepo/python' && ~/.venvs/nzcl/bin/python -m nzcl.ingest --pilot $Pilot --concurrency 8"
if ($LASTEXITCODE -ne 0) { throw 'ingest failed' }

Write-Host "`nStep 3/3 - quality report" -ForegroundColor Cyan
& wsl -d $Distro -- bash -lc "cd '$wslRepo/python' && ~/.venvs/nzcl/bin/python -m nzcl.qa"
exit $LASTEXITCODE
