; Inno Setup script: a Windows installer for the PyInstaller build (dist\openswx).
; Build:  .venv\Scripts\python packaging\build.py   (the app, the launchers, then ISCC on this script)
; Output: dist\openswx-setup-<version>.exe
; Installs per user. The install folder ({userpf}\openswx by default) holds only two small launchers
; (packaging\launcher.cs, compiled by build.py into build-new\launcher); the PyInstaller runtime (its exe and
; _internal) goes to %LOCALAPPDATA%\openswx\runtime, which the launchers find through
; HKCU\Software\openswx\RuntimeDir. Adds Start menu / optional desktop shortcuts and the
; same file associations as `openswx-cli register-file-types` (.oswprt / .oswasm open in openswx; it is
; offered in "Open with" for SOLIDWORKS and neutral CAD files). Uninstall removes all of it.

#define AppName "openswx"
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
#define SourceDir "..\dist\openswx"
#define LauncherDir "..\build-new\launcher"
#define RuntimeDir "{localappdata}\openswx\runtime"

[Setup]
AppId={{8C5E1E42-3F7B-4C1A-9B8E-0A5D2F7C61B3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=openswx contributors
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
; per user: the runtime lives in this user's AppData
PrivilegesRequired=lowest
UninstallFilesDir={#RuntimeDir}\uninstall
ChangesAssociations=yes
OutputDir=..\dist
OutputBaseFilename=openswx-setup-{#AppVersion}
SetupIconFile=openswx.ico
UninstallDisplayIcon={app}\openswx.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assoc"; Description: "Open .oswprt and .oswasm files with openswx"; GroupDescription: "File associations:"

[InstallDelete]
; an upgrade replaces the runtime as a whole (no stale modules from the previous version)
Type: filesandordirs; Name: "{#RuntimeDir}\_internal"

[Files]
Source: "{#LauncherDir}\openswx.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#LauncherDir}\openswx-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\NOTICE"; DestDir: "{app}"; DestName: "NOTICE.txt"; Flags: ignoreversion
Source: "{#SourceDir}\*"; DestDir: "{#RuntimeDir}"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
Type: filesandordirs; Name: "{#RuntimeDir}"

[Icons]
Name: "{group}\openswx"; Filename: "{app}\openswx.exe"
Name: "{group}\Uninstall openswx"; Filename: "{uninstallexe}"
Name: "{autodesktop}\openswx"; Filename: "{app}\openswx.exe"; Tasks: desktopicon

[Registry]
; where the launchers find the runtime
Root: HKCU; Subkey: "Software\openswx"; ValueType: string; ValueName: "RuntimeDir"; ValueData: "{#RuntimeDir}"; Flags: uninsdeletekey
; openswx's own formats
Root: HKA; Subkey: "Software\Classes\.oswprt"; ValueType: string; ValueName: ""; ValueData: "openswx.Part"; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Part"; ValueType: string; ValueName: ""; ValueData: "openswx Part"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Part\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"",0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Part\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"" ""%1"""; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.oswasm"; ValueType: string; ValueName: ""; ValueData: "openswx.Assembly"; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Assembly"; ValueType: string; ValueName: ""; ValueData: "openswx Assembly"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Assembly\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"",0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Assembly\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"" ""%1"""; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.oswdrw"; ValueType: string; ValueName: ""; ValueData: "openswx.Drawing"; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Drawing"; ValueType: string; ValueName: ""; ValueData: "openswx Drawing"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Drawing\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"",0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\openswx.Drawing\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"" ""%1"""; Tasks: assoc
; "Open with" for SOLIDWORKS and neutral CAD files (their default program is left alone)
Root: HKA; Subkey: "Software\Classes\openswx.File"; ValueType: string; ValueName: ""; ValueData: "CAD file (openswx)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\openswx.File\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"",0"
Root: HKA; Subkey: "Software\Classes\openswx.File\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.sldprt\OpenWithProgids"; ValueType: string; ValueName: "openswx.File"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.sldasm\OpenWithProgids"; ValueType: string; ValueName: "openswx.File"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.step\OpenWithProgids"; ValueType: string; ValueName: "openswx.File"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.stp\OpenWithProgids"; ValueType: string; ValueName: "openswx.File"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.x_t\OpenWithProgids"; ValueType: string; ValueName: "openswx.File"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\.x_b\OpenWithProgids"; ValueType: string; ValueName: "openswx.File"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\Applications\openswx.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "openswx"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\openswx.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\openswx.exe"" ""%1"""

[Run]
Filename: "{app}\openswx.exe"; Description: "{cm:LaunchProgram,openswx}"; Flags: nowait postinstall skipifsilent
