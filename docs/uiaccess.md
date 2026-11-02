# The uiAccess build

`WinDuo2-Setup.exe` installs a build of WinDuo2 that runs with *uiAccess*. This page covers what that buys, what Windows demands before it grants it, how to meet each demand, how starting at sign-in works with it, and what to check when it does not work.

The portable `WinDuo2.exe` needs none of this. Use it if you do not mind Start and quick settings closing when the effect starts.

## What it changes

Windows layers windows in z-order *bands*. An ordinary app's always-on-top window is in the topmost band. Start, search, the notification centre and quick settings are in bands above that, so no ordinary window can cover them. The portable build therefore closes them (it sends Escape, only when one of them is in front) as the effect starts.

A process that has uiAccess puts its topmost windows in the `ZBID_UIACCESS` band, which is above those flyouts. This is the band on-screen keyboards and screen magnifiers use. The overlay then covers them, and because Desktop Duplication captures the screen after composition, they are already in the picture the overlay blurs. WinDuo2 checks its own token at runtime (`win32.has_ui_access()`) and stops closing the flyouts when it has uiAccess.

What uiAccess does not change:

| | Portable `WinDuo2.exe` | Installed with uiAccess |
|---|---|---|
| Start, search, notification centre, quick settings | Closed when the effect starts | Stay open and are blurred |
| Mouse pointer | Hidden for the effect | Hidden for the effect |
| Lock screen, UAC prompts | Not covered | Not covered |

The pointer is drawn on a hardware plane after composition, outside any z-order band. The lock screen and UAC prompts run on a separate secure desktop that no app outside it can draw on, uiAccess or not.

## What Windows requires

Windows grants uiAccess only when all three of these hold. If any one is missing, it does not quietly run the app without uiAccess. It refuses to start it at all, usually with *"A referral was returned from the server"*.

1. **The manifest asks for it.** The exe's embedded manifest says `uiAccess="true"`.
2. **It is signed by a certificate the PC trusts.** The Authenticode signature has to chain to a certificate in the machine's *Trusted Root Certification Authorities* store.
3. **It is installed in a secure location.** That means a folder only administrators can write to, such as `C:\Program Files`. This applies while the group policy *User Account Control: Only elevate UIAccess applications that are installed in secure locations* is enabled, which is the default. Leave it enabled.

### 1. The manifest

PyInstaller writes it with `--uac-uiaccess`. The `installer` variant in `.github/workflows/release.yml` builds with `--onedir --uac-uiaccess`, and a CI step fails the build if the manifest lacks `uiAccess="true"`.

It is a folder build, not `--onefile`. A one-file build unpacks its code into `%TEMP%`, which any program running as the user can write to. That would defeat the point of requiring a location only administrators can change.

The exe cannot be started on the CI runner, because it is not under Program Files there. The portable variant's `--version` check covers the same bundle importing cleanly.

### 2. The signature

There are three ways to get a trusted signature. Each certificate below is a code-signing-only certificate, so it cannot be used for HTTPS.

#### A. One certificate, signed by CI (recommended for your own PCs)

You do this once. Every later build CI makes is signed with the same certificate, so updates keep working with nothing to redo.

1. Install any `WinDuo2-Setup.exe`, even an unsigned one, to get the scripts into `C:\Program Files\WinDuo2`.
2. From an administrator PowerShell, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\make-signing-cert.ps1"
   ```

   The script does four things:

   - It creates a self-signed code signing certificate (RSA 3072, valid 10 years).
   - It trusts the certificate's public half on this PC, in *Trusted Root* and *Trusted Publishers*.
   - It writes three files to a `WinDuo2-signing` folder on the desktop: `WINDOWS_CERT_PFX.txt`, `WINDOWS_CERT_PASSWORD.txt` and `WinDuo2-signing.cer`.
   - It deletes the private key from the PC.
3. In the GitHub repository, go to *Settings › Secrets and variables › Actions › New repository secret* and add two secrets:
   - `WINDOWS_CERT_PFX` with the contents of `WINDOWS_CERT_PFX.txt`
   - `WINDOWS_CERT_PASSWORD` with the contents of `WINDOWS_CERT_PASSWORD.txt`
4. **Delete the two `.txt` files.** Keep `WinDuo2-signing.cer`.
5. Build a release by pushing a `v*` or `Build_*` tag, or by running the workflow by hand. CI signs `WinDuo2.exe` and `WinDuo2-Setup.exe` and checks the result with `signtool verify`.
6. Install that `WinDuo2-Setup.exe`. Updates built the same way install over it and keep uiAccess.

To use the same certificate on another PC of yours, run this there:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\make-signing-cert.ps1" -Trust path\to\WinDuo2-signing.cer
```

Other options:

- `-Replace` makes a new certificate. Use it if the secret may have leaked, and then update both GitHub secrets. Builds signed with the old certificate stop starting.
- `-Remove` takes the certificate out of this PC's stores.

#### B. Sign the installed copy on the PC (no GitHub setup)

From an administrator PowerShell, after **every** install or update:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\trust-self-signed.ps1"
```

The script does four things:

- It stops a running WinDuo2, because signing rewrites the exe.
- It makes a fresh certificate and signs `WinDuo2.exe` with it.
- It trusts only the certificate's public half.
- It deletes the private key.

An update replaces the exe with an unsigned one, so the installed app will not start until you run the script again. If the exe is already signed by a certificate the PC trusts, for example by method A, the script does nothing. `-Remove` takes its certificate out again.

#### C. A publicly trusted certificate (to distribute to other people)

Methods A and B only make *your* PCs trust the signature. For other people's PCs, the certificate has to chain to a public root. Since June 2023, new OV and EV code signing certificates keep their private key in hardware (a token or an HSM), so they cannot be stored as a `.pfx` secret the way method A does. Cloud signing services such as Azure Trusted Signing are the practical route, and need the CI signing step replaced with their own action.

### 3. The secure location

`packaging/windows/WinDuo2.iss` is the Inno Setup script CI compiles into `WinDuo2-Setup.exe`. It:

- requires administrator rights and installs to `C:\Program Files\WinDuo2`.
- ships `make-signing-cert.ps1` and `trust-self-signed.ps1` next to the exe.
- starts the app at the end through the shell (`shellexec`) as the signed-in user (`runasoriginaluser`), not as the elevated installer.
- closes a running copy so an update can replace its files.
- on uninstall, removes the sign-in task and the `Run` entry described below.

Do not move the folder or loosen its permissions. Anywhere else, Windows refuses to start the exe.

To build the installer by hand, from the repository root, after the `--onedir` PyInstaller build:

```bat
iscc /DAppVersion=1.0.0 packaging\windows\WinDuo2.iss
```

## Starting at sign-in

*Start with Windows* is on by default. When it is on, WinDuo2 registers a Task Scheduler task named **WinDuo2** that runs at the user's sign-in, without elevation (`winduo2/autostart.py`). If Windows refuses to register the task, it writes a `WinDuo2` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` instead. Turning the setting off removes both.

A uiAccess exe needs one change here. Task Scheduler starts programs with a plain `CreateProcess`. For an exe whose manifest asks for uiAccess, that call fails with `ERROR_ELEVATION_REQUIRED` (Task Scheduler shows `0x800702E4`, *"The requested operation requires elevation"*). Starting it through the shell lets Windows grant uiAccess the same way it does for a double-click. So when WinDuo2 is running with uiAccess, it registers the task as:

```
C:\Windows\System32\rundll32.exe shell32.dll,ShellExec_RunDLL "C:\Program Files\WinDuo2\WinDuo2.exe" --autostart
```

The portable build and a source install register the exe (or `pythonw -m winduo2`) directly, as before.

WinDuo2 rewrites the task every time it starts. Settings live in `%APPDATA%\WinDuo2` and are shared by every build. So after switching from the portable exe to the installed one, start the installed one once and the task points at it. You do not have to delete the old task by hand.

To check where it points:

```bat
schtasks /Query /TN WinDuo2 /XML
```

For the uiAccess build, `<Command>` should be `rundll32.exe` and `<Arguments>` should contain `C:\Program Files\WinDuo2\WinDuo2.exe`.

## Switching from the portable exe

1. Quit the portable WinDuo2 from its tray icon and delete `WinDuo2.exe`. Running both would draw the effect twice, and whichever started last owns the sign-in task.
2. Install `WinDuo2-Setup.exe`. If the build is unsigned, untick *Start WinDuo2* on the last page, because it cannot start yet.
3. Get a trusted signature with method A or B above.
4. Start WinDuo2 from the Start menu. This also repoints the sign-in task.

If you will not install the uiAccess build for a while, remove the leftover task and `Run` entry, which point at the deleted exe:

```bat
schtasks /Delete /TN WinDuo2 /F
```

```bat
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WinDuo2 /f
```

## Checking it works

1. **Signature.** Run this in PowerShell:

   ```powershell
   Get-AuthenticodeSignature "C:\Program Files\WinDuo2\WinDuo2.exe" | Format-List Status, SignerCertificate
   ```

   `Status` should be `Valid`.
2. **uiAccess.** `%APPDATA%\WinDuo2\winduo2.log` should have a line like `WinDuo2 1.0.0 ready ..., with uiAccess`. If the line has no `with uiAccess`, the process did not get it.
3. **The effect.** Open Start or quick settings and close the lid partway. They should blur with the rest of the screen instead of closing.
4. **Sign-in.** Sign out and back in. WinDuo2 should appear in the notification area.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| *"A referral was returned from the server"* when starting it | The exe is not signed by a trusted certificate (for example, after an update with method B), or it is not under Program Files. |
| The log has no `with uiAccess` | It was started from somewhere other than the installed exe, such as a source install or the portable exe. |
| WinDuo2 does not start at sign-in, and the task's last result is `0x800702E4` | The task still points straight at the exe. Start WinDuo2 once from the Start menu so it rewrites the task. |
| The task points at a deleted `WinDuo2.exe` | It is left over from the portable build. Start the installed build once, or delete the task as shown above. |
| Flyouts still close | The process has no uiAccess. Check the log line. |

## Uninstalling

1. Take the certificate out first. Uninstalling also removes the scripts.

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\make-signing-cert.ps1" -Remove
   ```

   For method B, run `trust-self-signed.ps1 -Remove` instead.
2. Uninstall WinDuo2 from *Settings › Apps*. This removes the sign-in task and the `Run` entry too.

If you uninstalled first, open `certlm.msc` and delete *WinDuo2 release signing* or *WinDuo2 local uiAccess signing* from *Trusted Root Certification Authorities* and *Trusted Publishers*.

## Cautions

- **The private key is the trust.** With method A, the private key exists only in the GitHub secret. Anyone who gets it can build programs that receive uiAccess on every PC that trusts the certificate. Anyone with write access to the repository can change a workflow to read the secret. Workflows run for pull requests from forks do not receive secrets. If the key may have leaked, use `-Replace`.
- **Delete the secret files.** Remove `WINDOWS_CERT_PFX.txt` and `WINDOWS_CERT_PASSWORD.txt` as soon as the secrets are saved, especially if the desktop syncs to the cloud.
- **Only `WinDuo2.exe` is signed.** The DLLs in `_internal` are not. They rely on Program Files being writable only by administrators, so do not loosen the folder's permissions.
- **Only trust builds you made.** Do not run these scripts on a build from somewhere else. Doing so gives that code uiAccess.
- **Not on managed PCs.** On a work or school PC, group policy may manage the root store or the UIAccess policy. These steps may not work there and may break its rules.
- **Expiry.** The method A certificate lasts 10 years. CI timestamps its signatures, so builds stay valid after it expires. The method B certificate lasts 5 years and is not timestamped, so run the script again when it expires.

## Files

| File | Purpose |
|---|---|
| `winduo2/render/win32.py` | `has_ui_access()` reads `TokenUIAccess` from the process token. `close_shell_flyouts()` is the fallback for builds without it. |
| `winduo2/app.py` | Skips closing the flyouts when the process has uiAccess. |
| `winduo2/autostart.py` | Registers the sign-in task, through `ShellExec_RunDLL` for the uiAccess build. |
| `.github/workflows/release.yml` | Builds the portable and installer variants and signs the installer variant when the secrets are set. |
| `packaging/windows/WinDuo2.iss` | The Inno Setup script for `WinDuo2-Setup.exe`. |
| `packaging/windows/make-signing-cert.ps1` | Method A. |
| `packaging/windows/trust-self-signed.ps1` | Method B. |
