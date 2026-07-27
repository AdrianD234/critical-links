#Requires -Version 5.1
<#
.SYNOPSIS
  Run QA on the most recent snapshot. The graph is built at load time, so there
  is no separate database load step.
.PARAMETER SnapshotId
  Snapshot to check. Defaults to the most recent.
#>
param([string]$SnapshotId)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if (-not $SnapshotId) {
  $dirs = Get-ChildItem 'data/processed' -Directory -ErrorAction SilentlyContinue | Sort-Object Name
  if (-not $dirs) { throw 'No snapshots found. Run .\scripts\download-pilot.ps1 first.' }
  $SnapshotId = $dirs[-1].Name
}
Write-Host "QA for $SnapshotId" -ForegroundColor Cyan
& npx tsx pipelines/validation/qa.ts $SnapshotId
exit $LASTEXITCODE
