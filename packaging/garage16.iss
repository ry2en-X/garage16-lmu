; Inno Setup script for Garage16-Client-Setup.exe (V0.8.6).
; Compile with Inno Setup 6 (ISCC.exe) AFTER `pyinstaller packaging/garage16.spec`
; has produced dist\Garage16\ — see packaging\build_windows.ps1 and
; docs/CLIENT_BUILD.md. The version comes from packaging\version.iss,
; generated from client/uploader/uploader.py's CLIENT_VERSION by
; packaging\sync_version.py, so the installer can't advertise a different
; version than the app inside it.

#include "version.iss"

[Setup]
; Fixed AppId: installing a newer Garage16-Client-Setup.exe over an
; existing install UPDATES it in place (same app, same folder) instead of
; installing a second copy. That's the whole friend-side update story.
AppId={{6E2B0C3A-9F41-4D7B-8C55-2A7D1B9E4F10}
AppName=Garage16
AppVersion={#AppVersion}
AppPublisher=Garage16
; Per-user install: no administrator rights, no UAC prompt for a friend
; on a normal Windows account.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Garage16
DefaultGroupName=Garage16
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=Garage16-Client-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Garage16.exe
; If Garage16 is running while an update is installed, close it first
; rather than failing on locked files (and restart it afterwards).
CloseApplications=yes
RestartApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startupentry"; Description: "Start Garage16 automatically when I log in to Windows"; GroupDescription: "Convenience:"; Flags: unchecked

[Files]
Source: "..\dist\Garage16\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\Garage16"; Filename: "{app}\Garage16.exe"
Name: "{autodesktop}\Garage16"; Filename: "{app}\Garage16.exe"; Tasks: desktopicon
Name: "{userstartup}\Garage16"; Filename: "{app}\Garage16.exe"; Tasks: startupentry

[Run]
Filename: "{app}\Garage16.exe"; Description: "Start Garage16 now"; Flags: postinstall nowait skipifsilent

; Deliberately NO [UninstallDelete] for user data: the friend's saved
; login (%USERPROFILE%\.lmu_garage) and recorded laps (%LOCALAPPDATA%\Garage16)
; live OUTSIDE the install folder, so uninstalling or updating never
; touches them.
