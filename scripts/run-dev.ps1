#Requires -Version 5.1
<#
.SYNOPSIS
  Start the API and the web app together.
.PARAMETER SnapshotId
  Snapshot to serve. Defaults to the most recent.
#>
param([string]$SnapshotId)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if (-not $SnapshotId) {
  $dirs = Get-ChildItem 'data/processed' -Directory -ErrorAction SilentlyContinue | Sort-Object Name
  if (-not $dirs) { throw 'No snapshots found. Run .\scripts\download-pilot.ps1 first.' }
  $SnapshotId = $dirs[-1].Name
}

Write-Host "Starting API with snapshot $SnapshotId" -ForegroundColor Cyan
$env:SNAPSHOT_ID = $SnapshotId
$api = Start-Process -PassThru -NoNewWindow npx -ArgumentList 'tsx','apps/api/src/server.ts'
Start-Sleep -Seconds 8

Write-Host 'Starting web app on http://localhost:5173' -ForegroundColor Cyan
try {
  & npm run dev --workspace apps/web
} finally {
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
}
