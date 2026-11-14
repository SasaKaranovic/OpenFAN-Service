<#
.SYNOPSIS
  Run OpenFAN Service from source (no Docker).
.DESCRIPTION
  The mode (-Demo / -Serial / -Rest) only seeds a NEW config folder. Once
  config.yaml exists, change controllers in the web UI or use -Fresh.
.EXAMPLE
  scripts\run.ps1 -Demo
.EXAMPLE
  scripts\run.ps1 -Serial auto          # real OpenFAN over USB, auto-detected
.EXAMPLE
  scripts\run.ps1 -Serial COM3
.EXAMPLE
  scripts\run.ps1 -Rest http://192.168.1.10:3000
.EXAMPLE
  scripts\run.ps1 --rest http://192.168.1.10:3000 --port 5341     # Linux-style options work too
.NOTES
  This runs the service directly with Python, NOT in Docker, so Docker options such as
  "-v host:container" do not apply: sensor sources read host paths directly, e.g. an
  "Unraid disks" source with path  <repo>\dev-config\unraid\disks.ini.
  To run in Docker (and mount folders), use scripts\docker-run.ps1.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$Demo,
    [string]$Serial = '',
    [string]$Rest = '',
    [string]$Config = '',
    [int]$Port = 5340,
    [switch]$Fresh,
    [switch]$DebugLog,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Remaining = @()
)
. (Join-Path $PSScriptRoot '_common.ps1')

$opt = Read-LongOptions $Remaining @{ demo = 'switch'; serial = 'value'; rest = 'value'; config = 'value';
                                      port = 'value'; fresh = 'switch'; debug = 'switch'; debuglog = 'switch' } `
    -UnknownHint ('run.ps1 starts the service from source with Python, not in Docker, so Docker options like -v do not ' +
                  'apply here. Point sensor sources at host paths directly (e.g. dev-config\unraid\disks.ini), or use ' +
                  'scripts\docker-run.ps1 to run in Docker. Options: -Demo, -Serial PORT, -Rest URL, -Config DIR, -Port N, -Fresh, -DebugLog.')
$v = $opt.Values
if ($v.ContainsKey('demo')) { $Demo = $true }
if ($v.ContainsKey('serial')) { $Serial = $v['serial'] }
if ($v.ContainsKey('rest')) { $Rest = $v['rest'] }
if ($v.ContainsKey('config')) { $Config = $v['config'] }
if ($v.ContainsKey('port')) { $Port = [int]$v['port'] }
if ($v.ContainsKey('fresh')) { $Fresh = $true }
if ($v.ContainsKey('debug') -or $v.ContainsKey('debuglog')) { $DebugLog = $true }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid port '$Port'." }

$py = Require-Venv

$mode = 'demo'
if ($Serial) { $mode = 'serial' }
if ($Rest) { $mode = 'rest' }
if ($Demo) { $mode = 'demo' }
if (-not $Config) { $Config = Join-Path 'dev-config' $mode }

if ($Fresh -and (Test-Path $Config)) {
    Write-Host "Removing $Config (-Fresh)"
    Remove-Item -Recurse -Force $Config
}
New-Item -ItemType Directory -Force -Path $Config | Out-Null

$names = 'OPENFAN_CONFIG_DIR', 'OPENFAN_PORT', 'OPENFAN_LOG_LEVEL', 'OPENFAN_DEMO', 'OPENFAN_SERIAL_PORT', 'OPENFAN_CONTROLLER_URL'
$saved = @{}
foreach ($n in $names) { $saved[$n] = [Environment]::GetEnvironmentVariable($n, 'Process') }
try {
    foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $null, 'Process') }
    $env:OPENFAN_CONFIG_DIR = $Config
    $env:OPENFAN_PORT = "$Port"
    $env:OPENFAN_LOG_LEVEL = 'info'
    if ($DebugLog) { $env:OPENFAN_LOG_LEVEL = 'debug' }
    switch ($mode) {
        'demo' { $env:OPENFAN_DEMO = '1' }
        'serial' { $env:OPENFAN_SERIAL_PORT = $Serial }
        'rest' { $env:OPENFAN_CONTROLLER_URL = $Rest }
    }
    if (Test-Path (Join-Path $Config 'config.yaml')) {
        Write-Host "Using existing config in $Config (mode switches only apply to a new config; add -Fresh to reset)"
    }
    Write-Host "OpenFAN Service: http://localhost:$Port   (config: $Config, Ctrl+C to stop)"
    $code = Invoke-Native $py @('-m', 'openfan_service')
    if ($code -ne 0) { Write-Warning "The service exited with code $code" }
} finally {
    foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $saved[$n], 'Process') }
}
