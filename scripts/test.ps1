#Requires -Version 5.1
<#
.SYNOPSIS
  Run the full test suite. Integration tests skip themselves if no snapshot exists.
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
& npx vitest run
exit $LASTEXITCODE
