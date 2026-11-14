<#
.SYNOPSIS
  Create .venv and install the service + development dependencies.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.EXAMPLE
  scripts\setup.ps1 -Python "C:\Python313\python.exe"
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Python = '',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Remaining = @()
)
. (Join-Path $PSScriptRoot '_common.ps1')
$opt = Read-LongOptions $Remaining @{ python = 'value' } -UnknownHint 'Options: -Python PATH.'
if ($opt.Values.ContainsKey('python')) { $Python = $opt.Values['python'] }

# Find a Python 3.11+ interpreter: explicit -Python, then the 'py' launcher, then python/python3.
$candidates = @()
if ($Python) { $candidates += ,@($Python) }
$candidates += ,@('py', '-3')
$candidates += ,@('python')
$candidates += ,@('python3')

$found = $null
foreach ($c in $candidates) {
    $exe = $c[0]
    $pre = @()
    if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
    if (Test-Native $exe ($pre + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'))) {
        $found = $c; break
    }
}
if (-not $found) {
    throw 'Python 3.11 or newer not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run this again.'
}
$exe = $found[0]
$pre = @()
if ($found.Count -gt 1) { $pre = $found[1..($found.Count - 1)] }

if (-not (Get-VenvPython)) {
    Write-Host "Creating virtual environment in .venv"
    Invoke-Checked $exe ($pre + @('-m', 'venv', '.venv'))
}
$py = Require-Venv
Invoke-Checked $py @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet')
Invoke-Checked $py @('-m', 'pip', 'install', '-r', 'requirements-dev.txt')

Write-Host ''
Write-Host 'Done. Next:'
Write-Host '  scripts\run.ps1 -Demo     # start with simulated hardware, open http://localhost:5340'
Write-Host '  scripts\test.ps1          # run the tests'
