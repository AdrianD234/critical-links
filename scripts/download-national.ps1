#Requires -Version 5.1
<#
.SYNOPSIS
  Ingest the full national network (~272,000 vehicle-routable links).
.DESCRIPTION
  Downloads roughly 140 batches from the NZTA service. Expect 15-40 minutes
  depending on connection. Raw data is gitignored.
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$env:NODE_OPTIONS = '--max-old-space-size=10240'
Write-Host 'Ingesting the national AMDS network. This takes a while.' -ForegroundColor Cyan
& npx tsx pipelines/ingestion/ingest.ts --national --concurrency 8
exit $LASTEXITCODE
