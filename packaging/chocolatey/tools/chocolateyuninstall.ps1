$ErrorActionPreference = 'Stop'

# The package is one downloaded executable in the tools directory. Chocolatey
# removes the package directory and the shim it generated, so there is nothing
# of ours left behind — but the download is not something chocolatey knows to
# clean up on its own, so remove it explicitly rather than leaving 21 MB in a
# directory the user thinks is gone.
$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$exe = Join-Path $toolsDir 'anime.exe'
if (Test-Path $exe) { Remove-Item $exe -Force }
