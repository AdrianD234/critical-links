#Requires -Version 5.1
<#
.SYNOPSIS
  Start the FastAPI service (in WSL) and the web app (on Windows).

.DESCRIPTION
  With no arguments the API picks its own snapshot, preferring a complete
  national one. Pass -SnapshotId to pin it — usually to work against the fast
  Wellington validation extract instead of the full national network.

  The script reports which snapshot the API actually chose, which is not always
  the one asked for: a missing snapshot or a fallback both change the answer,
  and a national tool quietly serving a regional extract is the failure worth
  making obvious.

.PARAMETER SnapshotId
  Pin the API to one snapshot. Overrides the default selection entirely.

.PARAMETER List
  Show the snapshots in the database and exit.

.PARAMETER ApiOnly
  Start the API without the web dev server.

.EXAMPLE
  .\scripts\run-dev.ps1
  National by default.

.EXAMPLE
  .\scripts\run-dev.ps1 -SnapshotId amds-wellington-2026-07-27-6ef785ad
  The Wellington validation snapshot.

.EXAMPLE
  .\scripts\run-dev.ps1 -List
#>
param(
    [string]$SnapshotId = '',
    [switch]$List,
    [switch]$ApiOnly,
    [int]$ApiPort = 8000,
    [string]$Distro = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$wslRepo = (& wsl -d $Distro -- wslpath -a "$((Get-Location).Path)").Trim()

if ($List) {
    Write-Host 'Snapshots in the database:' -ForegroundColor Cyan
    $sql = 'SELECT snapshot_id, coverage_kind, coverage_name, status, ' +
           'routable_link_count AS links, processing_version AS proc, ' +
           'retrieved_at_utc::date AS retrieved FROM network_snapshots ' +
           'ORDER BY retrieved_at_utc DESC'
    & wsl -d $Distro -- bash -lc "PGPASSWORD=nzcl_local_dev psql -h 127.0.0.1 -U nzcl -d nzcl -P pager=off -c `"$sql`""
    return
}

Write-Host 'Starting API in WSL (PostgreSQL + PostGIS + pgRouting)' -ForegroundColor Cyan
if ($SnapshotId) {
    Write-Host "  pinned to snapshot: $SnapshotId" -ForegroundColor Yellow
} else {
    Write-Host '  snapshot: default selection (prefers complete national)' -ForegroundColor Gray
}

& wsl -d $Distro -u root -- env VENV=/home/$(& wsl -d $Distro -- whoami)/.venvs/nzcl API_PORT=$ApiPort bash "$wslRepo/scripts/wsl-run-api.sh" $SnapshotId
if ($LASTEXITCODE -ne 0) { throw 'API failed to start' }

# Report what it actually chose, not what was requested.
try {
    $meta = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/v1/network/metadata" -TimeoutSec 30
    $cov = $meta.coverage
    Write-Host ''
    Write-Host "Active snapshot : $($meta.snapshotId)" -ForegroundColor Green
    if ($cov) {
        Write-Host "Coverage        : $($cov.kind) - $($cov.name)"
    }
    Write-Host "Graph           : $($meta.graph.links) links, $($meta.graph.arcs) arcs"
    if ($cov -and $cov.kind -ne 'national') {
        Write-Host 'WARNING: not national coverage. A replacement path cannot leave the extract.' -ForegroundColor Yellow
    }
    Write-Host ''
} catch {
    Write-Host "Could not read metadata: $_" -ForegroundColor Yellow
}

if ($ApiOnly) { return }

Write-Host "Starting web app on http://localhost:5173" -ForegroundColor Cyan
& npm run dev --workspace apps/web
