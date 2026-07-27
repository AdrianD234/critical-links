#Requires -Version 5.1
<#
.SYNOPSIS
  One-time setup: check the toolchain, install dependencies, create .env.
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

Write-Host 'NZ Critical Links - bootstrap' -ForegroundColor Cyan

$node = (& node --version) 2>$null
if (-not $node) { throw 'Node.js is required. Install Node 20.11 or newer from https://nodejs.org/' }
$major = [int](($node -replace '^v','') -split '\.')[0]
if ($major -lt 20) { throw "Node $node found; 20.11 or newer is required." }
Write-Host "  node $node" -ForegroundColor Green

Write-Host '  installing dependencies (this may take a minute)...'
& npm install --silent
if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }

if (-not (Test-Path '.env')) {
  Copy-Item '.env.example' '.env'
  Write-Host '  created .env from .env.example' -ForegroundColor Yellow
  Write-Host '  Add a free LINZ Basemaps key (VITE_LINZ_API_KEY) for the map background.' -ForegroundColor Yellow
  Write-Host '  Register at https://basemaps.linz.govt.nz/ - the app runs without one.' -ForegroundColor Yellow
} else {
  Write-Host '  .env already present, left untouched'
}

New-Item -ItemType Directory -Force -Path 'data' | Out-Null

Write-Host ''
Write-Host 'Bootstrap complete. Next:' -ForegroundColor Cyan
Write-Host '  .\scripts\download-pilot.ps1     # discover the source and ingest Wellington'
Write-Host '  .\scripts\run-dev.ps1            # start the API and web app'
