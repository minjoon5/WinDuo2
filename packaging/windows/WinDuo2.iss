; The installed, uiAccess build of WinDuo2.
;
; Windows only grants uiAccess to an executable that asks for it in its
; manifest, is signed by a certificate the machine trusts, and sits in a secure
; location such as Program Files. The uiAccess build's manifest asks for it
; (native/app/winduo2_uiaccess.manifest); the "selfsign" task below signs it on the PC with a
; certificate made there, unless CI already signed it with one the PC trusts;
; and this installer puts it in Program Files. uiAccess is what lets the overlay sit
; above Start, search and quick settings, so they are blurred instead of closed.
;
; Built by .github/workflows/release.yml from the native uiAccess exe staged
; in dist\WinDuo2. By hand, from the repository root:
;   iscc packaging\windows\WinDuo2.iss
;
; There is no version number. AppVerName stands in for the AppVersion Inno
; Setup would otherwise require, and installing over an earlier install
; replaces it whatever was there before.

[Setup]
AppId={{6F0B7C1E-3D52-4B7A-9E0B-2C1D8A5F4E21}
AppName=WinDuo2
AppVerName=WinDuo2
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
SetupIconFile=..\..\native\app\icon.ico
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
Source: "make-signing-cert.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
; Remembered across updates (UsePreviousTasks), so each update is signed too.
Name: "selfsign"; Description: "Sign WinDuo2 with a certificate made on this PC, so it can blur Start and quick settings (uiAccess). Skipped when this build is already signed for this PC."

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
; This PC's signing certificate, its key and its trust go with the app.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\trust-self-signed.ps1"" -Remove"; Flags: runhidden; RunOnceId: "RemoveSigningCert"

[UninstallDelete]
Type: files; Name: "{app}\signing.log"

[Code]
// Signs the exe after the files are in place and before the finished page
// offers to start it, and says so when that fails rather than leaving an app
// that will not start without explanation.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
  LogPath: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('selfsign') then
  begin
    LogPath := ExpandConstant('{app}\signing.log');
    Params := '-NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\trust-self-signed.ps1') + '" -Exe "' +
      ExpandConstant('{app}\WinDuo2.exe') + '" -Log "' + LogPath + '"';
    WizardForm.StatusLabel.Caption := 'Signing WinDuo2 for this PC...';
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      SuppressibleMsgBox('WinDuo2 could not be signed for this PC, so Windows will not ' +
        'start it. Details are in ' + LogPath + '.', mbError, MB_OK, IDOK);
  end;
end;
