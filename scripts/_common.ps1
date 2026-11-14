# Shared helpers for the PowerShell scripts. Works in Windows PowerShell 5.1 and PowerShell 7+.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Get-VenvPython {
    $win = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $nix = Join-Path $RepoRoot '.venv/bin/python'
    if (Test-Path $win) { return $win }
    if (Test-Path $nix) { return $nix }
    return $null
}

function Require-Venv {
    $py = Get-VenvPython
    if (-not $py) { throw 'No .venv yet. Run scripts\setup.ps1 first.' }
    return $py
}

# Native tools (docker, pip, git...) often write progress to stderr. Windows PowerShell 5.1 turns
# that into errors when $ErrorActionPreference is 'Stop', so native calls run with 'Continue'
# and are judged by their exit code instead.

function Invoke-Checked {
    # Run a native command, show its output, and stop if it fails.
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host; $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
    if ($code -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $code" }
}

function Test-Native {
    # Run a native command quietly; return $true if it exits with 0.
    param([string]$Exe, [string[]]$Arguments)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments *> $null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
    finally { $ErrorActionPreference = $old }
}

function Invoke-Native {
    # Run a native command in the foreground (output straight to the console); return its exit code.
    param([string]$Exe, [string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments | Out-Host; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
}

function Read-LongOptions {
    # Lets the PowerShell scripts also accept the Linux-style spelling of their options
    # ("--port 5341", "--port=5341", "--fresh"), so the same command works in both shells.
    # Spec: hashtable of option name -> 'switch' or 'value'.
    # With -AllowPassThrough, everything from "--" or the first unknown token on is returned in
    # .Pass (PowerShell itself removes a bare "--" before the script sees the arguments).
    param([string[]]$Tokens, [hashtable]$Spec, [switch]$AllowPassThrough, [string]$UnknownHint = '')
    $values = @{}
    $pass = @()
    if (-not $Tokens) { $Tokens = @() }
    for ($i = 0; $i -lt $Tokens.Count; $i++) {
        $t = $Tokens[$i]
        if ($t -eq '--') {
            if ($i + 1 -lt $Tokens.Count) { $pass = @($Tokens[($i + 1)..($Tokens.Count - 1)]) }
            break
        }
        $key = $null; $inline = $null; $hasInline = $false
        if ($t -match '^--?([A-Za-z][A-Za-z0-9-]*)(=(.*))?$') {
            $key = $Matches[1].ToLower()
            if ($Matches.ContainsKey(2)) { $hasInline = $true; $inline = $Matches[3] }
        }
        if ($key -and $Spec.ContainsKey($key)) {
            if ($Spec[$key] -eq 'switch') {
                $values[$key] = $true
            } elseif ($hasInline) {
                $values[$key] = $inline
            } elseif ($i + 1 -lt $Tokens.Count) {
                $i++
                $values[$key] = $Tokens[$i]
            } else {
                throw "Option --$key needs a value."
            }
            continue
        }
        if ($AllowPassThrough) {
            $pass = @($Tokens[$i..($Tokens.Count - 1)])
            break
        }
        throw "Unknown option or argument '$t'. $UnknownHint"
    }
    return @{ Values = $values; Pass = $pass }
}

function Wait-Healthy {
    param([int]$Port, [int]$Seconds = 60)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Write-Host -NoNewline '.'
        Start-Sleep -Seconds 2
    }
    return $false
}
