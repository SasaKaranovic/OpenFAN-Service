<#
.SYNOPSIS
  Run the locally built image (see scripts\docker-build.ps1).
.DESCRIPTION
  Modes:
    demo  simulated controller and sensors (default)
    rest  OpenFAN Controller REST API. Default URL http://host.docker.internal:3000, i.e. a
          controller app or scripts\fake_openfan_rest.py running on this PC.
    usb   real controller over USB. On Windows the device must first be attached to
          WSL 2 with usbipd-win (see docs\DEVELOPMENT.md); then use -Device /dev/ttyACM0.
.EXAMPLE
  scripts\docker-run.ps1 -Mode demo
.EXAMPLE
  scripts\docker-run.ps1 -Mode rest -Url http://192.168.1.10:3000
.EXAMPLE
  scripts\docker-run.ps1 -Mode usb -Device /dev/ttyACM0
.EXAMPLE
  scripts\docker-run.ps1 -Stop
.EXAMPLE
  # Extra "docker run" arguments: use -DockerArgs, or put them after "--".
  # Relative host paths in -v/--volume are made absolute.
  scripts\docker-run.ps1 -Mode demo -DockerArgs '-v', '.\dev-config\unraid:/unraid:ro'
  scripts\docker-run.ps1 -Mode demo -- -v .\dev-config\unraid:/unraid:ro -e OPENFAN_LOG_LEVEL=debug
.EXAMPLE
  scripts\docker-run.ps1 --mode rest --url http://192.168.1.10:3000 --port 5341 -- -v .\dev-config\unraid:/unraid:ro
  # Linux-style options work too
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Mode = 'demo',
    [string]$Device = '/dev/ttyACM0',
    [string]$Url = 'http://host.docker.internal:3000',
    [string]$Tag = 'openfan-service:dev',
    [string]$Name = 'openfan-service-dev',
    [int]$Port = 5340,
    [string]$Config = '',
    [string]$Unraid = '',
    [switch]$Fresh,
    [switch]$Foreground,
    [switch]$Stop,
    [string[]]$DockerArgs = @(),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Remaining = @()
)
. (Join-Path $PSScriptRoot '_common.ps1')

# Linux-style options (--mode demo, --port 5341, --fresh ...). The first token that is not one of
# them, and everything after it, is passed to "docker run".
$opt = Read-LongOptions $Remaining @{ mode = 'value'; device = 'value'; url = 'value'; tag = 'value'; name = 'value';
                                      port = 'value'; config = 'value'; unraid = 'value'; fresh = 'switch';
                                      foreground = 'switch'; stop = 'switch' } -AllowPassThrough
$v = $opt.Values
if ($v.ContainsKey('mode')) { $Mode = $v['mode'] }
if ($v.ContainsKey('device')) { $Device = $v['device'] }
if ($v.ContainsKey('url')) { $Url = $v['url'] }
if ($v.ContainsKey('tag')) { $Tag = $v['tag'] }
if ($v.ContainsKey('name')) { $Name = $v['name'] }
if ($v.ContainsKey('port')) { $Port = [int]$v['port'] }
if ($v.ContainsKey('config')) { $Config = $v['config'] }
if ($v.ContainsKey('unraid')) { $Unraid = $v['unraid'] }
if ($v.ContainsKey('fresh')) { $Fresh = $true }
if ($v.ContainsKey('foreground')) { $Foreground = $true }
if ($v.ContainsKey('stop')) { $Stop = $true }
if (@('demo', 'usb', 'rest') -notcontains $Mode) { throw "Mode must be demo, usb or rest (got '$Mode')." }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid port '$Port'." }

Test-Native 'docker' @('rm', '-f', $Name) | Out-Null
if ($Stop) { Write-Host "Stopped $Name"; return }

if (-not (Test-Native 'docker' @('image', 'inspect', $Tag))) { throw "Image $Tag not found. Run scripts\docker-build.ps1 first." }

if (-not $Config) { $Config = Join-Path 'dev-config' "docker-$Mode" }
if ($Fresh -and (Test-Path $Config)) { Remove-Item -Recurse -Force $Config }
New-Item -ItemType Directory -Force -Path $Config | Out-Null
$configAbs = (Resolve-Path $Config).Path

Write-Host "Mode: $Mode   Port: $Port   Config: $Config"
$runArgs = @('--name', $Name, '-p', "${Port}:5340", '-v', "${configAbs}:/config",
             '-e', 'PUID=1000', '-e', 'PGID=1000', '-e', 'TZ=Etc/UTC',
             '--add-host=host.docker.internal:host-gateway')
switch ($Mode) {
    'demo' { $runArgs += @('-e', 'OPENFAN_DEMO=1') }
    'usb' { $runArgs += @('--device', "${Device}:/dev/ttyACM0", '-e', 'OPENFAN_SERIAL_PORT=/dev/ttyACM0') }
    'rest' { $runArgs += @('-e', "OPENFAN_CONTROLLER_URL=$Url") }
}
if ($Unraid) { $runArgs += @('-v', "$((Resolve-Path $Unraid).Path):/unraid:ro") }

# Extra "docker run" arguments (-DockerArgs and anything after "--"). Docker needs absolute host
# paths for bind mounts, so relative sources like ".\x:/y" are resolved against the repo folder.
function ConvertTo-AbsVolume([string]$Spec) {
    if ($Spec -match '^(\.[^:]*):(.+)$') {
        New-Item -ItemType Directory -Force -Path $Matches[1] | Out-Null
        return "$((Resolve-Path $Matches[1]).Path):$($Matches[2])"
    }
    return $Spec
}
$pass = @($DockerArgs) + @($opt.Pass | Where-Object { $_ -ne '--' })
for ($i = 0; $i -lt $pass.Count; $i++) {
    $a = $pass[$i]
    if (($a -eq '-v' -or $a -eq '--volume') -and $i + 1 -lt $pass.Count) {
        $runArgs += @($a, (ConvertTo-AbsVolume $pass[$i + 1])); $i++
    } elseif ($a -like '--volume=*') {
        $runArgs += "--volume=$(ConvertTo-AbsVolume $a.Substring(9))"
    } else {
        $runArgs += $a
    }
}

if ($Foreground) {
    Write-Host "Starting $Name in the foreground: http://localhost:$Port"
    Invoke-Native 'docker' (@('run', '--rm') + $runArgs + @($Tag)) | Out-Null
    return
}

Invoke-Checked 'docker' (@('run', '-d', '--restart', 'unless-stopped') + $runArgs + @($Tag)) | Out-Null
Write-Host -NoNewline "Started $Name, waiting for it to become healthy"
if (Wait-Healthy -Port $Port -Seconds 60) {
    Write-Host ''
    Write-Host "Ready: http://localhost:$Port   (config: $Config)"
    Write-Host "Logs:  docker logs -f $Name     Stop: scripts\docker-run.ps1 -Stop"
} else {
    Write-Host ''
    Write-Warning 'The service did not answer within 60 s. Last log lines:'
    Invoke-Native 'docker' @('logs', '--tail', '40', $Name) | Out-Null
    exit 1
}
