#define AppName "Clip Watcher"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{3B3B09A9-5F63-47DA-9A1B-82C0BB23D3A6}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ankur Yadav
AppPublisherURL=https://github.com/itsankyz/clip-watcher
AppSupportURL=https://github.com/itsankyz/clip-watcher/issues
DefaultDirName={localappdata}\Programs\Clip Watcher
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
Uninstallable=yes
OutputDir=..\dist\installer
OutputBaseFilename=ClipWatcher-Setup
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "downloadmodel"; Description: "Download Whisper's base model now (~150 MB)"; GroupDescription: "Whisper captions:"; Flags: unchecked

[Files]
Source: "..\clip_watcher.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\clipwatcher_gui.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\setup.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\watch.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\watch-gui.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\download_model.py"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Clip Watcher\Clip Watcher"; Filename: "{app}\watch-gui.cmd"; WorkingDir: "{app}"
Name: "{autoprograms}\Clip Watcher\Command Line"; Filename: "{app}\watch.cmd"; WorkingDir: "{app}"
Name: "{autodesktop}\Clip Watcher"; Filename: "{app}\watch-gui.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  CommandLine: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  CommandLine := '/c ""' + ExpandConstant('{app}\setup.cmd') + '" /installer"';
  if not Exec(ExpandConstant('{cmd}'), CommandLine, ExpandConstant('{app}'),
       SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Could not start dependency setup. Run setup.cmd from the install folder to finish setup.',
      mbError, MB_OK);
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox('Dependency setup did not complete. Run setup.cmd from the install folder to retry.',
      mbError, MB_OK);
    Exit;
  end;

  if WizardIsTaskSelected('downloadmodel') then
  begin
    if not Exec(ExpandConstant('{app}\.venv\Scripts\python.exe'),
         '-c "import whisper; whisper.load_model(''base'')"',
         ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      MsgBox('Could not start the Whisper model download. It will download automatically when captions are first generated.',
        mbError, MB_OK)
    else if ResultCode <> 0 then
      MsgBox('The Whisper model download did not complete. It will download automatically when captions are first generated.',
        mbError, MB_OK);
  end;
end;
