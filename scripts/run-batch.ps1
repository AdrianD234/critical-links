#Requires -Version 5.1
<#
.SYNOPSIS
  Compute detours for every eligible link, then export to CSV and XLSX.
.PARAMETER SnapshotId
  Snapshot to process. Defaults to the most recent.
.PARAMETER Resume
  Continue a previous run instead of starting over.
#>
param([string]$SnapshotId, [switch]$Resume)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if (-not $SnapshotId) {
  $dirs = Get-ChildItem 'data/processed' -Directory -ErrorAction SilentlyContinue | Sort-Object Name
  if (-not $dirs) { throw 'No snapshots found.' }
  $SnapshotId = $dirs[-1].Name
}

$env:NODE_OPTIONS = '--max-old-space-size=10240'
$batchArgs = @('tsx','pipelines/detours/compute-all.ts','--snapshot',$SnapshotId)
if ($Resume) { $batchArgs += '--resume' }

Write-Host "Batch detours for $SnapshotId" -ForegroundColor Cyan
& npx @batchArgs
if ($LASTEXITCODE -ne 0) { throw 'batch failed' }

Write-Host ''
Write-Host 'Exporting to CSV and XLSX' -ForegroundColor Cyan
& npx tsx pipelines/detours/export.ts --snapshot $SnapshotId
exit $LASTEXITCODE
