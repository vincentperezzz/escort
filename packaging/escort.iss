#define AppName "Escort"
#define AppVersion "0.1.0"
#define AppPublisher "Escort"
#define DistDir "..\dist\Escort"
; The Electron overlay is packaged by `npm run package` in overlay/, not by PyInstaller.
#define OverlayDir "..\overlay\out\EscortOverlay-win32-x64"

#if !FileExists(AddBackslash(SourcePath) + OverlayDir + "\EscortOverlay.exe")
  #error Missing overlay\out\EscortOverlay-win32-x64\EscortOverlay.exe. Run "npm ci" and "npm run package" in overlay\ before compiling the installer.
#endif

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
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\overlay"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#OverlayDir}\*"; DestDir: "{app}\overlay"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Escort"; Filename: "{app}\Escort.exe"
Name: "{autodesktop}\Escort"; Filename: "{app}\Escort.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Escort.exe"; Description: "Launch Escort"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Escort.exe"; Flags: runhidden; RunOnceId: "StopEscort"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM EscortOverlay.exe"; Flags: runhidden; RunOnceId: "StopEscortOverlay"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure RunHidden(const Cmd, Params: String);
var
  Code: Integer;
begin
  Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Key: String;
begin
  if CurUninstallStep <> usPostUninstall then Exit;
  Key := 'Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store';
  RegDeleteValue(HKCU, Key, ExpandConstant('{app}\Escort.exe'));
  RegDeleteValue(HKCU, Key, ExpandConstant('{app}\unins000.exe'));
  if UninstallSilent then Exit;
  if MsgBox('Also delete your Escort data (settings, meeting data and saved sign-ins)?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(ExpandConstant('{userappdata}\Escort'), True, True, True);
    RunHidden(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -Command "cmdkey /list | ForEach-Object { if ($_ -match ''Target:\s*(?:LegacyGeneric:target=)?(\S*escort\S*)'') { cmdkey /delete:$Matches[1] } }"');
  end;
end;
