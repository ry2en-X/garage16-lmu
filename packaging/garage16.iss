; Inno Setup script for Garage16-Client-Setup.exe (V0.8.6).
; Compile with Inno Setup 6 (ISCC.exe) AFTER `pyinstaller packaging/garage16.spec`
; has produced dist\Garage16\ — see packaging\build_windows.ps1 and
; docs/CLIENT_BUILD.md. The version comes from packaging\version.iss,
; generated from client/uploader/uploader.py's CLIENT_VERSION by
; packaging\sync_version.py, so the installer can't advertise a different
; version than the app inside it.

#include "version.iss"

; V0.9.2: two editions from the same app (ISCC /DEdition=analysis ...):
;   standard  -> Garage16-Client-Setup.exe           the plain client
;   analysis  -> Garage16-Client-Analysis-Setup.exe  client + analysis app window
; Same AppId for both: installing one over the other SWITCHES the edition in place
; (same folder; login and laps live outside it and stay).
#ifndef Edition
  #define Edition "standard"
#endif
#if Edition == "analysis"
  #define SetupName "Garage16-Client-Analysis-Setup"
  #define EditionLabel "Client + analysis app"
#else
  #define SetupName "Garage16-Client-Setup"
  #define EditionLabel "Client"
#endif

[Setup]
; Fixed AppId: installing a newer Garage16-Client-Setup.exe over an
; existing install UPDATES it in place (same app, same folder) instead of
; installing a second copy. That's the whole friend-side update story.
AppId={{6E2B0C3A-9F41-4D7B-8C55-2A7D1B9E4F10}
AppName=Garage16
AppVersion={#AppVersion}
AppVerName=Garage16 {#AppVersion} ({#EditionLabel})
AppPublisher=Garage16
; Per-user install: no administrator rights, no UAC prompt for a friend
; on a normal Windows account.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Garage16
DefaultGroupName=Garage16
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename={#SetupName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Garage16.exe
; V0.9.4: the Garage16 icon (an angular "16") for the installer itself
SetupIconFile=garage16.ico
; If Garage16 is running while an update is installed, close it first
; rather than failing on locked files (and restart it afterwards).
CloseApplications=yes
RestartApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
; V1.0.2: autostart is a choice in two steps — whether Garage16 starts with Windows at all, and
; if so whether its window opens or it starts minimized to the taskbar (Garage16.exe --minimized).
; The two sub-options are radio buttons; "open the window" is the default once autostart is ticked.
Name: "startupentry"; Description: "Start Garage16 automatically when I log in to Windows"; GroupDescription: "Autostart:"; Flags: unchecked
Name: "startupentry\window"; Description: "Open the Garage16 window"; Flags: exclusive
Name: "startupentry\minimized"; Description: "Start minimized (only in the taskbar)"; Flags: exclusive unchecked

[InstallDelete]
; The app's own program files only (PyInstaller's _internal folder), so that switching
; from "Client + analysis app" back to "Client" leaves none of the analysis window's
; files behind. Never anything outside {app}: the login (%USERPROFILE%\.garage16)
; and recorded laps (%LOCALAPPDATA%\Garage16) are not in here.
Type: filesandordirs; Name: "{app}\_internal"
; V1.0.2: the autostart shortcut is made fresh from this install's choice — without this, an update
; with autostart switched off (or switched from window to minimized) would leave the old one behind.
Type: files; Name: "{userstartup}\Garage16.lnk"

[Files]
Source: "..\dist\Garage16\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\Garage16"; Filename: "{app}\Garage16.exe"
Name: "{autodesktop}\Garage16"; Filename: "{app}\Garage16.exe"; Tasks: desktopicon
Name: "{userstartup}\Garage16"; Filename: "{app}\Garage16.exe"; Tasks: startupentry\window
Name: "{userstartup}\Garage16"; Filename: "{app}\Garage16.exe"; Parameters: "--minimized"; Tasks: startupentry\minimized

[Run]
Filename: "{app}\Garage16.exe"; Description: "Start Garage16 now"; Flags: postinstall nowait skipifsilent

; Deliberately NO [UninstallDelete] for user data: the friend's saved
; login (%USERPROFILE%\.garage16) and recorded laps (%LOCALAPPDATA%\Garage16)
; live OUTSIDE the install folder, so uninstalling or updating never
; touches them.
