$ErrorActionPreference = 'Stop'

# The binary is fetched from the GitHub release rather than embedded in the
# package. It is the same file winget and scoop point at, so there is exactly
# one artifact per release for every channel to verify against — and the
# checksum below is generated from that artifact by scripts/make_manifests.py,
# never typed.
$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

$packageArgs = @{
  packageName    = 'anime-sh'
  url64bit       = 'https://github.com/Anime123450/anime-sh/releases/download/v0.0.0/anime-sh-0.0.0-windows-x64.exe'
  checksum64     = '0000000000000000000000000000000000000000000000000000000000000000'
  checksumType64 = 'sha256'
  # Named `anime.exe` on disk on purpose: chocolatey shims every .exe in the
  # tools directory under its own filename, and `anime` is the command the
  # README, the docs and every other channel tell people to run.
  fileFullPath   = Join-Path $toolsDir 'anime.exe'
}

Get-ChocolateyWebFile @packageArgs
