#ifndef AppVersion
  #error AppVersion is required
#endif
[Setup]
AppId={{2454EE03-0D45-495C-945C-71C6E9872A23}
AppName=Anime Studio
AppVersion={#AppVersion}
AppPublisher=Anime Studio
DefaultDirName={localappdata}\Programs\Anime Studio
DefaultGroupName=Anime Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\release-assets
OutputBaseFilename=Anime-Studio-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\AnimeStudio.exe
WizardStyle=modern
[Files]
Source: "..\..\dist\AnimeStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Anime Studio"; Filename: "{app}\AnimeStudio.exe"
Name: "{autodesktop}\Anime Studio"; Filename: "{app}\AnimeStudio.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\AnimeStudio.exe"; Description: "Launch Anime Studio"; Flags: nowait postinstall skipifsilent
