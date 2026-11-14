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
    Invoke-Checked 'node' @((Join-Path $RepoRoot 'scripts\check_js.mjs'))
} else {
    Write-Host '(node not installed: skipped the JavaScript syntax check)'
}
