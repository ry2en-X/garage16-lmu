<#
Builds the friend-facing Garage16 client on WINDOWS (V0.8.6).
Run from the project root in PowerShell:

    .\packaging\build_windows.ps1 -ServerUrl "https://garage16.example.com"

Produces:
    dist\Garage16\                          the self-contained app folder (no Python needed to run it)
    dist-installer\Garage16-Client-Setup.exe   the installer (needs Inno Setup 6)
    dist-installer\Garage16-Portable.zip       fallback: unzip anywhere and run Garage16.exe

-ServerUrl is baked into the package (garage16_server.txt) so friends never
type an address. Omit it to ship a package that asks for one.
Requires: Python 3.11/3.12 (build machine only), and for the installer
Inno Setup 6 (https://jrsoftware.org/isinfo.php). See docs/CLIENT_BUILD.md.
#>
param([string]$ServerUrl = "")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-build\Scripts\python.exe -m pip install -r client\requirements.txt pyinstaller

& .\.venv-build\Scripts\python.exe packaging\sync_version.py

if ($ServerUrl) { $env:GARAGE16_SERVER_URL = $ServerUrl }
& .\.venv-build\Scripts\python.exe -m PyInstaller packaging\garage16.spec --noconfirm

# Smoke test: the built app must at least start and exit cleanly for
# --version. Start-Process -Wait is required: PowerShell does NOT wait for
# a windowed (GUI-subsystem) executable when it is simply invoked with &.
$smoke = Start-Process -FilePath .\dist\Garage16\Garage16.exe -ArgumentList "--version" -Wait -PassThru
if ($smoke.ExitCode -ne 0) { throw "Built Garage16.exe failed its --version smoke test (exit code $($smoke.ExitCode))" }

New-Item -ItemType Directory -Force -Path dist-installer | Out-Null
Compress-Archive -Path dist\Garage16 -DestinationPath dist-installer\Garage16-Portable.zip -Force

$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc packaging\garage16.iss
    Write-Host "Installer: dist-installer\Garage16-Client-Setup.exe"
} else {
    Write-Warning "Inno Setup 6 not found - skipped the installer. The portable zip was still built."
}
