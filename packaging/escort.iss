#define AppName "Escort"
#define AppVersion "0.2.0"
#define AppPublisher "Escort"
#define DistDir "..\dist\Escort"

[Setup]
AppId={{8F3A1C2E-6B74-4D1A-9E55-0E5C04700001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Escort
DefaultGroupName=Escort
DisableProgramGroupPage=no
OutputDir=Output
OutputBaseFilename=EscortSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=escort.ico
UninstallDisplayIcon={app}\Escort.exe
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Escort"; Filename: "{app}\Escort.exe"
Name: "{autodesktop}\Escort"; Filename: "{app}\Escort.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Escort.exe"; Description: "Launch Escort"; Flags: nowait postinstall skipifsilent
