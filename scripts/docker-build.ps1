<#
.SYNOPSIS
  Build the Docker image locally (Docker Desktop on Windows, or Docker on Linux with pwsh).
.DESCRIPTION
  Every build is stamped with a version: the build date (UTC, yyyy-mm-dd) and the git short
  hash ("-dirty" if tracked files have uncommitted changes), e.g. v2026-11-13 [a1b2c3d].
  It is shown in the web UI footer. The image is tagged NAME:dev and NAME:<date>-<hash>.
.EXAMPLE
  scripts\docker-build.ps1                      # -> openfan-service:dev
.EXAMPLE
  scripts\docker-build.ps1 -Tag myname:test -NoCache
.EXAMPLE
  scripts\docker-build.ps1 -MultiArch           # also check linux/amd64 + linux/arm64 builds (not pushed)
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Tag = 'openfan-service:dev',
    [switch]$MultiArch,
    [switch]$NoCache,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Remaining = @()
)
. (Join-Path $PSScriptRoot '_common.ps1')
$opt = Read-LongOptions $Remaining @{ tag = 'value'; multiarch = 'switch'; 'no-cache' = 'switch'; nocache = 'switch' } `
    -UnknownHint 'Options: -Tag NAME:TAG, -MultiArch, -NoCache.'
if ($opt.Values.ContainsKey('tag')) { $Tag = $opt.Values['tag'] }
if ($opt.Values.ContainsKey('multiarch')) { $MultiArch = $true }
if ($opt.Values.ContainsKey('no-cache') -or $opt.Values.ContainsKey('nocache')) { $NoCache = $true }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/' }
if (-not (Test-Native 'docker' @('info'))) { throw 'The Docker engine is not running. Start Docker Desktop and wait until it says "Engine running".' }

$extra = @()
if ($NoCache) { $extra += '--no-cache' }

$version = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
$gitHash = 'unknown'
if (Test-Native 'git' @('rev-parse', '--short', 'HEAD')) {
    $gitHash = (& git rev-parse --short HEAD).Trim()
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $dirty = & git status --porcelain --untracked-files=no 2>$null } finally { $ErrorActionPreference = $old }
    if ($dirty) { $gitHash = "$gitHash-dirty" }
}
$versionTag = "$(($Tag -split ':')[0]):$version-$gitHash"
$buildArgs = @('--build-arg', "OPENFAN_VERSION=$version", '--build-arg', "OPENFAN_GIT_HASH=$gitHash")

Write-Host "Building $Tag (version v$version [$gitHash]) ..."
Invoke-Checked 'docker' (@('build') + $extra + $buildArgs + @('-t', $Tag, '-t', $versionTag, '.'))
Write-Host "Built $Tag and $versionTag"

if ($MultiArch) {
    Write-Host 'Checking multi-arch build (linux/amd64, linux/arm64). Nothing is pushed.'
    if (-not (Test-Native 'docker' @('buildx', 'inspect', 'openfan-multiarch'))) { Invoke-Checked 'docker' @('buildx', 'create', '--name', 'openfan-multiarch', '--driver', 'docker-container') }
    Invoke-Checked 'docker' (@('buildx', 'build', '--builder', 'openfan-multiarch', '--platform', 'linux/amd64,linux/arm64') + $extra + $buildArgs + @('-t', $Tag, '.'))
    Write-Host 'Multi-arch build OK (result kept in the build cache only).'
}

Write-Host ''
Write-Host 'Run it with: scripts\docker-run.ps1 -Mode demo'
