; SourceCraft - Inno Setup 安装脚本
; Application: SourceCraft

#define MyAppName "SourceCraft"
#define MyAppVersion "1.1.142"
#define MyAppPublisher "SourceCraft"
#define MyAppURL "https://sourcecraft.com"
#define MyAppExeName "SourceCraft.exe"
#define MyAppId "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 默认安装到当前用户目录，避免 Program Files 权限导致更新/写入失败
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
UsePreviousAppDir=no
UsePreviousGroup=no
AllowNoIcons=yes

DisableDirPage=no
DisableProgramGroupPage=no

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=installer
OutputBaseFilename=SourceCraft_Installer_v{#MyAppVersion}
SetupIconFile=app_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
DiskSpanning=yes
DiskSliceSize=max

WizardStyle=modern
WizardSizePercent=120

PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

CloseApplications=yes
RestartApplications=no

VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[InstallDelete]
; 安装前强制清理核心目录，避免历史版本文件残留导致行为不一致
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\release_manifest.json"
; 防止覆盖安装时残留旧运行时数据
Type: filesandordirs; Name: "{app}\browser_data"
Type: filesandordirs; Name: "{app}\playwright_data"
Type: filesandordirs; Name: "{app}\cookies"
Type: files; Name: "{app}\browser_debug.log"
Type: files; Name: "{app}\download_history.json"
Type: files; Name: "{app}\browser_history.json"
Type: files; Name: "{app}\browser_bookmarks.json"

[Files]
Source: "dist\SourceCraft\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; 卸载时移除用户级运行缓存（不删除用户下载文件）
Type: filesandordirs; Name: "{localappdata}\SourceCraft"
