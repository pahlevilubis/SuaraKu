; Installer SuaraKu (Inno Setup 6).
; Dibangun otomatis di GitHub Actions:  ISCC /DAppVersion=1.0.5 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8C1E6B52-3D7A-4F0E-9B21-5A1F2C7D9E44}
AppName=SuaraKu
AppVersion={#AppVersion}
AppVerName=SuaraKu {#AppVersion}
AppPublisher=Pahlevi Lubis
AppComments=Edit video & suara ringan: konversi, rekam narasi, efek suara, suara AI, gabung video.
DefaultDirName={autopf}\SuaraKu
DefaultGroupName=SuaraKu
DisableProgramGroupPage=yes
; Tidak perlu hak admin: terpasang untuk user ini saja (bisa dipilih untuk semua user).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\Output
OutputBaseFilename=SuaraKu-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\SuaraKu.exe
UninstallDisplayName=SuaraKu
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\SuaraKu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Bersihkan file versi lama supaya tidak campur aduk saat update.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\SuaraKu"; Filename: "{app}\SuaraKu.exe"
Name: "{autodesktop}\SuaraKu"; Filename: "{app}\SuaraKu.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SuaraKu.exe"; Description: "{cm:LaunchProgram,SuaraKu}"; Flags: nowait postinstall skipifsilent
