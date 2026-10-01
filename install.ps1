<#
.SYNOPSIS
    Installs anime-sh on Windows, from nothing, without administrator rights.

.DESCRIPTION
    Run it and you have a working `anime` command. It installs Scoop if you do
    not have it, adds the two buckets involved, installs anime-sh and mpv, and
    puts `anime` on your PATH for this window as well as the next one.

    Scoop was chosen over the alternatives for one reason: it installs per-user.
    Nothing here needs an administrator prompt, nothing is written outside your
    own profile, and `scoop uninstall anime-sh` removes it completely.

    Every step checks before it acts, so running this twice is safe — the second
    run upgrades rather than reinstalling.

.PARAMETER DryRun
    Print what would happen and change nothing. Worth doing first if you landed
    here from a one-line command on the internet.

.PARAMETER WithFfmpeg
    Also install ffmpeg, which `anime download` needs. Skipped by default
    because it is a large download and most people never run that command.

.EXAMPLE
    irm https://raw.githubusercontent.com/Anime123450/anime-sh/master/install.ps1 | iex

.EXAMPLE
    .\install.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$WithFfmpeg
)

$ErrorActionPreference = 'Stop'

$Bucket = 'https://github.com/Anime123450/scoop-anime-sh'

function Write-Step  { param($m) Write-Host "  -> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "  ok  $m" -ForegroundColor Green }
function Write-Note  { param($m) Write-Host "      $m" -ForegroundColor DarkGray }
function Write-Fail  { param($m) Write-Host "  !!  $m" -ForegroundColor Red }

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# Whether *Scoop* has it, which is not the same question as whether `anime`
# runs. A uv or pipx install puts `anime` on PATH too, and asking the wrong
# question sent this script to `scoop update anime-sh` on a machine where Scoop
# had never installed it - which fails, and failed quietly.
function Test-ScoopApp {
    param([string]$Name)
    # `*>$null`, not `2>$null`: scoop prints "Could not find app path" on a
    # stream that stderr redirection alone does not catch, and a probe should
    # not print anything.
    & scoop prefix $Name *>$null
    return $LASTEXITCODE -eq 0
}

# scoop reports failure through its exit code, not by throwing, so
# $ErrorActionPreference='Stop' does not catch it. Without this check a failed
# install still reached the success message at the bottom.
function Invoke-Scoop {
    param([string[]]$ScoopArgs)
    & scoop @ScoopArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Fail ("scoop " + ($ScoopArgs -join ' ') + " failed (exit $LASTEXITCODE)")
        exit 1
    }
}

# Scoop's installer edits the *user* PATH, which this process will not see until
# it restarts. Re-reading both scopes is what makes `anime` work in the window
# you ran this from, instead of only in the next one you open.
function Sync-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Invoke-Step {
    param([string]$Describe, [scriptblock]$Action)
    Write-Step $Describe
    if ($DryRun) { Write-Note '(dry run - skipped)'; return }
    & $Action
}

Write-Host ''
Write-Host '  anime-sh' -ForegroundColor White
Write-Host '  ========' -ForegroundColor DarkGray
if ($DryRun) { Write-Host '  DRY RUN - nothing will be changed' -ForegroundColor Yellow }
Write-Host ''

# -- administrator check ----------------------------------------------------- #
# Scoop refuses to install from an elevated prompt, and the failure it gives is
# not obvious, so say it here instead.
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Fail 'This is an administrator PowerShell.'
    Write-Note 'Scoop installs per-user and refuses to run elevated.'
    Write-Note 'Close this window, open a normal PowerShell, and run it again.'
    exit 1
}

# -- 1. Scoop ---------------------------------------------------------------- #
Sync-Path
if (Test-Command 'scoop') {
    Write-Ok 'Scoop is already installed'
} else {
    Invoke-Step 'Installing Scoop' {
        # Windows blocks downloaded scripts by default. This is the per-user
        # setting Microsoft itself ships on Windows Server, and it still
        # requires anything downloaded to be signed.
        $policy = Get-ExecutionPolicy -Scope CurrentUser
        if ($policy -in @('Restricted', 'Undefined', 'AllSigned')) {
            Write-Note "Execution policy for your account is '$policy' - setting RemoteSigned"
            Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
        }
        Invoke-RestMethod -Uri 'https://get.scoop.sh' | Invoke-Expression
        Sync-Path
    }
    if (-not $DryRun -and -not (Test-Command 'scoop')) {
        Write-Fail 'Scoop installed but is still not on PATH.'
        Write-Note 'Open a new PowerShell window and run this script again.'
        exit 1
    }
}

# -- 2. buckets -------------------------------------------------------------- #
# mpv lives in `extras`, and a fresh Scoop only has `main`. anime-sh's manifest
# declares `extras/mpv` as a dependency, so without this bucket the install
# below fails on a dependency it cannot resolve.
$buckets = if ($DryRun -and -not (Test-Command 'scoop')) { '' } else { (scoop bucket list 6>$null | Out-String) }

if ($buckets -match '(?m)^\s*extras\b') {
    Write-Ok "Bucket 'extras' already added"
} else {
    Invoke-Step "Adding the 'extras' bucket (this is where mpv lives)" { Invoke-Scoop @('bucket', 'add', 'extras') }
}

if ($buckets -match '(?m)^\s*anime-sh\b') {
    Write-Ok "Bucket 'anime-sh' already added"
} else {
    Invoke-Step "Adding the anime-sh bucket" { Invoke-Scoop @('bucket', 'add', 'anime-sh', $Bucket) }
}

# -- 3. anime-sh (and mpv, as its dependency) -------------------------------- #
# If `anime` already runs but came from somewhere else - a uv or pipx install -
# say so. Installing it here as well leaves two copies on PATH, and which one
# wins is decided by PATH order rather than by anything you chose.
$scoopHasIt = if ($DryRun -and -not (Test-Command 'scoop')) { $false } else { Test-ScoopApp 'anime-sh' }

if (-not $scoopHasIt -and (Test-Command 'anime')) {
    $existing = (Get-Command 'anime').Source
    Write-Note "'anime' already exists at $existing, installed by something other than Scoop."
    Write-Note 'Installing it here too would put a second copy on your PATH.'
    Write-Note "If that one is a uv install, 'uv tool upgrade anime-sh' is the way to update it."
    Write-Host ''
}

if ($scoopHasIt) {
    Invoke-Step 'anime-sh is already installed by Scoop - checking for an update' {
        Invoke-Scoop @('update', 'anime-sh')
    }
} else {
    Invoke-Step 'Installing anime-sh and mpv' { Invoke-Scoop @('install', 'anime-sh') }
}

if ($WithFfmpeg) {
    if (Test-ScoopApp 'ffmpeg') {
        Write-Ok 'ffmpeg is already installed'
    } else {
        Invoke-Step 'Installing ffmpeg (for anime download)' { Invoke-Scoop @('install', 'ffmpeg') }
    }
}

# -- 4. PATH, in this window too --------------------------------------------- #
Sync-Path

if ($DryRun) {
    Write-Host ''
    Write-Host '  Dry run finished. Nothing was changed.' -ForegroundColor Yellow
    Write-Host ''
    exit 0
}

# -- 5. prove it actually works ---------------------------------------------- #
Write-Host ''
if (-not (Test-Command 'anime')) {
    Write-Fail 'Installed, but `anime` is still not on PATH in this window.'
    Write-Note 'Open a new PowerShell and run: anime doctor'
    exit 1
}

$version = (& anime --version 2>&1 | Out-String).Trim()
Write-Ok "$version"
Write-Host ''
Write-Host '  Checking the environment:' -ForegroundColor White
& anime doctor
Write-Host ''
Write-Host '  Start it with: ' -NoNewline -ForegroundColor White
Write-Host 'anime' -ForegroundColor Cyan
Write-Host ''
