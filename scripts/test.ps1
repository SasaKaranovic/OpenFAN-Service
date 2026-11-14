<#
.SYNOPSIS
  Run the unit tests and a syntax check of the web UI JavaScript.
.EXAMPLE
  scripts\test.ps1
.EXAMPLE
  scripts\test.ps1 -k engine      # extra arguments go to pytest
#>
. (Join-Path $PSScriptRoot '_common.ps1')
$py = Require-Venv

Invoke-Checked $py (@('-m', 'pytest') + $args)

if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host 'Checking web UI JavaScript syntax...'
    Get-ChildItem -Recurse -Filter *.js (Join-Path $RepoRoot 'openfan_service\web') | ForEach-Object {
        Invoke-Checked 'node' @('--check', $_.FullName)
    }
    Write-Host 'JavaScript OK'
} else {
    Write-Host '(node not installed: skipped the JavaScript syntax check)'
}
