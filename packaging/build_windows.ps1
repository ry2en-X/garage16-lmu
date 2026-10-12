<#
Builds the friend-facing Garage16 client on WINDOWS (V0.8.6; two editions since V0.9.2).
Run from the project root in PowerShell:

    .\packaging\build_windows.ps1                              # the plain client
    .\packaging\build_windows.ps1 -Edition analysis            # client + analysis app
    .\packaging\build_windows.ps1 -ServerUrl "https://garage16.de" -Edition analysis

Produces (per edition):
    dist\Garage16\                                   the self-contained app folder (no Python needed to run it)
    standard:
      dist-installer\Garage16-Client-Setup.exe            the installer (needs Inno Setup 6)
      dist-installer\Garage16-Portable.zip                fallback: unzip anywhere and run Garage16.exe
    analysis ("Client + analysis app"):
      dist-installer\Garage16-Client-Analysis-Setup.exe
      dist-installer\Garage16-Analysis-Portable.zip

Both editions install as the SAME app (same AppId, same folder): installing one over the
other switches the edition and keeps the friend's login and laps.

-ServerUrl is baked into the package (garage16_server.txt) so friends never
type an address. It defaults to https://garage16.de; pass -ServerUrl "" to
ship a package that asks for one instead.
Requires: Python 3.11/3.12 (build machine only), and for the installer
Inno Setup 6 (https://jrsoftware.org/isinfo.php). See docs/CLIENT_BUILD.md.
#>
param(
    [string]$ServerUrl = "https://garage16.de",
    [ValidateSet("standard", "analysis")][string]$Edition = "standard"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# A fresh build environment per edition: the plain client must never pick up the
# analysis window's packages from a previous analysis build.
$venv = ".venv-build-$Edition"
python -m venv $venv
& ".\$venv\Scripts\python.exe" -m pip install --upgrade pip
$requirements = if ($Edition -eq "analysis") { "client\requirements-analysis.txt" } else { "client\requirements.txt" }
& ".\$venv\Scripts\python.exe" -m pip install -r $requirements pyinstaller

& ".\$venv\Scripts\python.exe" packaging\sync_version.py

if ($ServerUrl) { $env:GARAGE16_SERVER_URL = $ServerUrl }
$env:GARAGE16_EDITION = $Edition
& ".\$venv\Scripts\python.exe" -m PyInstaller packaging\garage16.spec --noconfirm

# Smoke tests: the built app must at least start and exit cleanly for --version,
# and the analysis edition must be able to load its window engine (pythonnet +
# WebView2 assemblies) — a broken bundle never ships. Start-Process -Wait is
# required: PowerShell does NOT wait for a windowed (GUI-subsystem) executable
# when it is simply invoked with &.
$smoke = Start-Process -FilePath .\dist\Garage16\Garage16.exe -ArgumentList "--version" -Wait -PassThru
if ($smoke.ExitCode -ne 0) { throw "Built Garage16.exe failed its --version smoke test (exit code $($smoke.ExitCode))" }
$selftest = Start-Process -FilePath .\dist\Garage16\Garage16.exe -ArgumentList "--analysis-selftest" -Wait -PassThru
if ($selftest.ExitCode -ne 0) { throw "Built Garage16.exe failed its --analysis-selftest (exit code $($selftest.ExitCode))" }

$portable = if ($Edition -eq "analysis") { "Garage16-Analysis-Portable.zip" } else { "Garage16-Portable.zip" }
New-Item -ItemType Directory -Force -Path dist-installer | Out-Null
Compress-Archive -Path dist\Garage16 -DestinationPath "dist-installer\$portable" -Force

$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc "/DEdition=$Edition" packaging\garage16.iss
    $setup = if ($Edition -eq "analysis") { "Garage16-Client-Analysis-Setup.exe" } else { "Garage16-Client-Setup.exe" }
    Write-Host "Installer: dist-installer\$setup"
} else {
    Write-Warning "Inno Setup 6 not found - skipped the installer. The portable zip was still built."
}
