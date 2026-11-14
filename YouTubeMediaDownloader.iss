; Inno Setup 6 installer definition
#define MyAppName "YouTube Media Downloader"
#define MyAppVersion "3.0.0"
#define MyExeName "YouTubeMediaDownloader.exe"

[Setup]
AppId={{8E9A2A77-4A5E-4B71-8E3A-DF9B30C9A6A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\YouTube Media Downloader
DefaultGroupName={#MyAppName}
OutputDir=installer
OutputBaseFilename=YouTubeMediaDownloader-Setup
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyExeName}
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\{#MyExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent
