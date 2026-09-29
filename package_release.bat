@echo off
setlocal enabledelayedexpansion

REM package_release.bat - Builds a clean, shareable copy of this project
REM (e.g. for a public GitHub repo, or to hand someone a zip) by copying
REM everything into a temp staging folder, stripping local/generated/
REM sensitive files that must never be published, then zipping the
REM result. The actual project folder is never modified.
REM
REM Run this from the project root - the folder containing this file,
REM alongside server/, client/, discord_bot/, web/, docker/, docs/, etc.
REM
REM Uses PowerShell's Compress-Archive for the actual zipping - no extra
REM tools to install, works on any Windows 10/11 machine.

set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "PROJECT_NAME=Garage16-LMU"
set "STAGING_DIR=%TEMP%\%PROJECT_NAME%_release_staging"
set "OUTPUT_ZIP=%PROJECT_ROOT%\%PROJECT_NAME%_release.zip"

echo.
echo ==================================================
echo  Garage16-LMU Release Packager
echo ==================================================
echo.
echo Project root: %PROJECT_ROOT%
echo.

if exist "%STAGING_DIR%" (
    echo Removing old staging folder from a previous run...
    rmdir /s /q "%STAGING_DIR%" 2>nul
)
mkdir "%STAGING_DIR%"

echo Copying project into staging area (this may take a moment)...
REM /E = copy subfolders including empty ones. /XD excludes directories
REM anywhere in the tree; /XF excludes files by name/pattern anywhere in
REM the tree. Keep this list in sync with .gitignore.
robocopy "%PROJECT_ROOT%" "%STAGING_DIR%" /E ^
    /XD __pycache__ .pytest_cache .git .venv .venv-build venv env node_modules build dist dist-installer ^
        server_telemetry_storage lmu_garage_data .lmu_garage test-backups .vscode .idea __MACOSX secrets ^
    /XF *.db *.db-journal *.sqlite *.sqlite3 *.pyc *.pyo ^
        .env *.pem *.key client_config.json *.log ^
        test_*.csv test_*_log.txt Tests_Garage16.txt lmu_dump_*.csv smoketest_*.py ^
        *.sql.gz *.tar.gz *.zip .DS_Store Thumbs.db ^
    >nul

REM robocopy's exit codes 0-7 all mean success (different flavors of "some
REM files copied/skipped") - only 8+ is a real error.
if %ERRORLEVEL% GEQ 8 (
    echo.
    echo ERROR: robocopy failed with exit code %ERRORLEVEL%. Aborting.
    rmdir /s /q "%STAGING_DIR%" 2>nul
    exit /b 1
)

echo.
echo Safety check: scanning staging folder for anything that looks like a real secret...
set "FOUND_SECRET=0"

if exist "%STAGING_DIR%\.env" (
    echo   FOUND: .env file made it into staging - this should never happen.
    set "FOUND_SECRET=1"
)

if exist "%STAGING_DIR%\secrets" (
    echo   FOUND: secrets\ folder made it into staging - this should never happen.
    set "FOUND_SECRET=1"
)

REM Catch a real Fernet key or admin token accidentally left in a tracked
REM file (e.g. pasted into a stray notes.txt) - .env.example only ever
REM contains placeholders, so a real-looking secret anywhere is worth
REM stopping for.
findstr /s /r /c:"LMU_GARAGE_SECRET_KEY=.\{20,\}" "%STAGING_DIR%\*.py" "%STAGING_DIR%\*.md" "%STAGING_DIR%\*.txt" >nul 2>&1
if not errorlevel 1 (
    echo   FOUND: a real-looking LMU_GARAGE_SECRET_KEY value in a tracked file.
    set "FOUND_SECRET=1"
)

if "%FOUND_SECRET%"=="1" (
    echo.
    echo ABORTING - review the warning^(s^) above before packaging.
    echo The staging folder was left in place for inspection:
    echo   %STAGING_DIR%
    exit /b 1
)
echo   OK - nothing suspicious found.

echo.
if exist "%OUTPUT_ZIP%" (
    echo Removing previous release zip...
    del /q "%OUTPUT_ZIP%"
)
echo Creating zip: %OUTPUT_ZIP%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Compress-Archive -Path '%STAGING_DIR%\*' -DestinationPath '%OUTPUT_ZIP%' -CompressionLevel Optimal"

if not exist "%OUTPUT_ZIP%" (
    echo.
    echo ERROR: zip was not created. Staging folder left in place for inspection:
    echo   %STAGING_DIR%
    exit /b 1
)

echo.
echo Cleaning up staging folder...
rmdir /s /q "%STAGING_DIR%"

echo.
echo ==================================================
echo  Done.
echo  Release package: %OUTPUT_ZIP%
echo ==================================================
echo.
echo Before publishing anywhere public, double-check by hand:
echo   1. Unzip it somewhere and skim the file list once yourself.
echo   2. Confirm .env.example has ONLY placeholder values.
echo   3. Confirm README.md / CHANGELOG.md reflect the version you're releasing.
echo   4. Decide on a LICENSE file if you don't have one yet - an
echo      un-licensed public repo legally defaults to "all rights reserved",
echo      which surprises most people who intended otherwise.
echo.
pause
