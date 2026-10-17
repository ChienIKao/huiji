; 會記 Huiji 安裝程式（Inno Setup 6）
; 由 build.ps1 呼叫：ISCC /DAppVersion=0.1.0 installer\huiji.iss
; 安裝在使用者自己的資料夾，不需要系統管理員權限

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B2267E53-BFEF-40F8-8589-99E7AEEC9388}
AppName=會記 Huiji
AppVersion={#AppVersion}
AppVerName=會記 Huiji {#AppVersion}
AppPublisher=ChienIKao
AppPublisherURL=https://github.com/ChienIKao/huiji
DefaultDirName={localappdata}\Programs\Huiji
DefaultGroupName=會記 Huiji
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Huiji-Setup-v{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Huiji.exe
UninstallDisplayName=會記 Huiji
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "zh_tw"; MessagesFile: "ChineseTraditional.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他選項："

[Files]
Source: "..\dist\Huiji\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 升級時清掉舊版的程式庫，避免殘留不相容的檔案（會議資料不在這裡，不受影響）
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\會記 Huiji"; Filename: "{app}\Huiji.exe"
Name: "{group}\解除安裝 會記 Huiji"; Filename: "{uninstallexe}"
Name: "{autodesktop}\會記 Huiji"; Filename: "{app}\Huiji.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Huiji.exe"; Description: "啟動 會記 Huiji"; Flags: nowait postinstall skipifsilent
