Inno Setup script for ATE Runner (frontend + backend + embedded Python runtime).
Build with:
    ISCC.exe /DAppVersion=1.0.250913 /DPayloadDir=<dist>\payload\ATERunner /DOutDir=<dist> ate_runner.iss
or simply run packaging\build_package.ps1 (it fills the defines in automatically).

NOTE: this file is UTF-8 WITH BOM (written by the build tooling) so Inno Setup 6
reads the Chinese strings correctly on any Windows code page.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\..\ATE_DIST\payload\ATERunner"
#endif
#ifndef OutDir
  #define OutDir "..\..\ATE_DIST"
#endif

#define AppName "ATE Runner"
#define AppPublisher "ATE"
#define AppExeName "ATE_Launcher.exe"
#define DefaultDirName "C:\ATERunner"

[Setup]
AppId={{7C1F5E3A-9B24-4A61-9E57-2F0C8B7A31D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={#DefaultDirName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
OutputDir={#OutDir}
OutputBaseFilename=ATE_Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; 数据目录（logs / workspace / ate.db）都在安装目录内，卸载时按用户选择保留或删除
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce
Name: "autostart"; Description: "开机自动启动（工控机常用）"; GroupDescription: "附加任务:"; Flags: unchecked
Name: "firewall"; Description: "放行 8000 端口（允许车间其他电脑访问界面）"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 整包部署：app\（后端） web\（前端） runtime\（嵌入式 Python）
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; 数据（logs / workspace / ate.db / testresource）都存在安装目录内，
; 因此整个 {app} 需要给普通用户写权限，否则非管理员运行时无法保存数据。
Name: "{app}"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\workspace"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ATERunner"; ValueData: """{app}\{#AppExeName}"" --no-browser"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; 放行端口（需要管理员；失败不阻断安装）
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""ATE Runner (8000)"" dir=in action=allow protocol=TCP localport=8000"; Flags: runhidden; Tasks: firewall; Check: IsAdminInstall
Filename: "{app}\{#AppExeName}"; Description: "立即启动 ATE Runner"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""ATE Runner (8000)"""; Flags: runhidden

[Code]
function IsAdminInstall(): Boolean;
begin
  Result := IsAdmin;
end;

{ 卸载时询问是否一并删除测试数据（logs / workspace / ate.db） }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Res: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Res := MsgBox('是否同时删除测试数据？' + #13#10 + #13#10 +
                  '选择"否"将保留 logs\、workspace\、ate.db（历史测试记录、硬件自检报告、TPS 运行目录）。',
                  mbConfirmation, MB_YESNO);
    if Res = IDYES then
    begin
      DelTree(ExpandConstant('{app}\logs'), True, True, True);
      DelTree(ExpandConstant('{app}\workspace'), True, True, True);
      DeleteFile(ExpandConstant('{app}\ate.db'));
    end;
  end;
end;
