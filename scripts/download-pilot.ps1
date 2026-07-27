#Requires -Version 5.1
<#
.SYNOPSIS
  Discover the AMDS source and ingest the Wellington pilot extract.
.PARAMETER Pilot
  Pilot preset name. 'wellington' (default) or 'auckland'.
#>
param([string]$Pilot = 'wellington')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

Write-Host 'Step 1/2 - source discovery' -ForegroundColor Cyan
& npx tsx pipelines/discovery/discover.ts
if ($LASTEXITCODE -ne 0) { throw 'discovery failed' }

Write-Host ''
Write-Host "Step 2/2 - ingesting pilot '$Pilot'" -ForegroundColor Cyan
& npx tsx pipelines/ingestion/ingest.ts --pilot $Pilot
if ($LASTEXITCODE -ne 0) { throw 'ingest failed' }

Write-Host ''
Write-Host 'Done. Next: .\scripts\load-pilot.ps1' -ForegroundColor Cyan
