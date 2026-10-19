; Inno Setup script for MapMaker – compiled by build_installer.bat
; Installs for the current user (no administrator rights needed) into
; %LOCALAPPDATA%\Programs\MapMaker, adds Start-menu (and optional desktop) shortcuts
; and opens .mapmaker map files with MapMaker. Maps are saved in Documents\MapMaker\Maps.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#if AppVersion == ""
  #undef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{B1F6A0C4-7E2D-4C59-9E3B-2D8F5A61C7E0}
AppName=MapMaker
AppVersion={#AppVersion}
AppPublisher=MapMaker
DefaultDirName={localappdata}\Programs\MapMaker
DefaultGroupName=MapMaker
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MapMaker-Setup-{#AppVersion}
SetupIconFile=MapMaker.ico
UninstallDisplayIcon={app}\MapMaker.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "assoc"; Description: "Open MapMaker maps (.mapmaker) with MapMaker"; GroupDescription: "Files:"

[Files]
Source: "..\dist\MapMaker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MapMaker"; Filename: "{app}\MapMaker.exe"
Name: "{group}\MapMaker self-test"; Filename: "{app}\MapMaker.exe"; Parameters: "--selftest"
Name: "{group}\Uninstall MapMaker"; Filename: "{uninstallexe}"
Name: "{userdesktop}\MapMaker"; Filename: "{app}\MapMaker.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.mapmaker"; ValueType: string; ValueName: ""; ValueData: "MapMaker.Map"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKCU; Subkey: "Software\Classes\MapMaker.Map"; ValueType: string; ValueName: ""; ValueData: "MapMaker map"; Flags: uninsdeletekey; Tasks: assoc
Root: HKCU; Subkey: "Software\Classes\MapMaker.Map\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\MapMaker.exe,0"; Tasks: assoc
Root: HKCU; Subkey: "Software\Classes\MapMaker.Map\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MapMaker.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\MapMaker.exe"; Description: "Start MapMaker"; Flags: nowait postinstall skipifsilent
