; The installed, uiAccess build of WinDuo2.
;
; Windows only grants uiAccess to an executable that asks for it in its
; manifest, is signed by a certificate the machine trusts, and sits in a secure
; location such as Program Files. PyInstaller writes the manifest
; (--uac-uiaccess) and CI signs the exe when a certificate is configured; this
; installer supplies the third condition. uiAccess is what lets the overlay sit
; above Start, search and quick settings, so they are blurred instead of closed.
;
; Built by .github/workflows/release.yml from the --onedir output in
; dist\WinDuo2. By hand, from the repository root:
;   iscc /DAppVersion=1.0.0 packaging\windows\WinDuo2.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0B7C1E-3D52-4B7A-9E0B-2C1D8A5F4E21}
AppName=WinDuo2
AppVersion={#AppVersion}
AppPublisher=WinDuo2
AppPublisherURL=https://github.com/minjoon5/WinDuo2
DefaultDirName={autopf}\WinDuo2
DisableDirPage=yes
DefaultGroupName=WinDuo2
DisableProgramGroupPage=yes
; Program Files needs administrator rights, and is what uiAccess requires.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\..\dist
OutputBaseFilename=WinDuo2-Setup
SetupIconFile=..\..\winduo2\ui\icon.ico
UninstallDisplayIcon={app}\WinDuo2.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Lets an update replace the exe of a copy that is running in the tray.
CloseApplications=force
RestartApplications=no

[Files]
Source: "..\..\dist\WinDuo2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "trust-self-signed.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\WinDuo2"; Filename: "{app}\WinDuo2.exe"

[Run]
; shellexec: a uiAccess exe cannot be started with a plain CreateProcess.
; runasoriginaluser: the app must not inherit the installer's elevation.
Filename: "{app}\WinDuo2.exe"; Description: "Start WinDuo2"; Flags: nowait postinstall skipifsilent shellexec runasoriginaluser

[UninstallRun]
; The app registers its own sign-in start; leave nothing pointing at a removed exe.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN WinDuo2 /F"; Flags: runhidden; RunOnceId: "DeleteTask"
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v WinDuo2 /f"; Flags: runhidden; RunOnceId: "DeleteRunValue"
