#Requires -Version 5.1
<#
.SYNOPSIS
  Run QA over the national snapshot.
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$dirs = Get-ChildItem 'data/processed' -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -like '*national*' } | Sort-Object Name
if (-not $dirs) { throw 'No national snapshot found. Run .\scripts\download-national.ps1 first.' }
$env:NODE_OPTIONS = '--max-old-space-size=10240'
& npx tsx pipelines/validation/qa.ts $dirs[-1].Name
exit $LASTEXITCODE
