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

`native/app/winduo2_uiaccess.manifest` asks for it, and CMake builds that variant of the exe (`native/build/app/uiaccess/WinDuo2.exe`) alongside the plain one. A CI step fails the release if the manifest lacks `uiAccess="true"`.

The exe is self-contained: nothing is unpacked to `%TEMP%` or loaded from beside it, which matters because requiring a location only administrators can change is the whole point.

The exe cannot be started on the CI runner, because it is not under Program Files there. The plain variant's `--selftest` covers the same code.

### 2. The signature

There are three ways to get a trusted signature. Each certificate below is a code-signing-only certificate, so it cannot be used for HTTPS.

#### A. Signed on the PC by the installer (anyone, the default)

Leave **"Sign WinDuo2 with a certificate made on this PC"** ticked on the installer's *Select Additional Tasks* page. It is ticked by default. After copying the files, the installer runs `trust-self-signed.ps1`, which does the following:

1. If `WinDuo2.exe` is already signed by a certificate this PC trusts, for example by method B, it stops there. Nothing is made or changed.
2. Otherwise, the first time, it makes a certificate for this PC. The certificate is self-signed, code signing only, and valid for 10 years.
   - Its public half goes into *Trusted Root* and *Trusted Publishers*.
   - Its private key stays in the machine's certificate store, marked non-exportable. Only administrators and SYSTEM can use it.
   - Later runs reuse this certificate.
3. It signs `WinDuo2.exe` with that certificate.

Inno Setup remembers the task, so every update installed over it is signed the same way. Nothing has to be redone, no GitHub account is needed, and nothing is bought. If signing fails, the installer says so. Details are in `C:\Program Files\WinDuo2\signing.log`. The uninstaller deletes the certificate, its key and its trust.

The script can also be run by hand from an administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\trust-self-signed.ps1"
```

`-Remove` deletes the certificate, its key and its trust.

The key is kept so that updates can be signed. Anyone able to use it is already an administrator of the PC, who could trust any certificate they liked, so keeping it gives no one new power. The key never leaves the PC.

#### B. One certificate, signed by CI (for whoever builds the releases)

This is for the person who builds releases from their own repository or fork and wants no signing key on their PCs. CI signs every build with one certificate, and each of that person's PCs trusts the certificate once. On those PCs, method A then finds the exe already signed and does nothing.

1. Install any `WinDuo2-Setup.exe` to get the scripts into `C:\Program Files\WinDuo2`.
2. From an administrator PowerShell, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\make-signing-cert.ps1"
   ```

   The script does four things:

   - It creates a self-signed code signing certificate.
   - It trusts the certificate's public half on this PC.
   - It writes `WINDOWS_CERT_PFX.txt`, `WINDOWS_CERT_PASSWORD.txt` and `WinDuo2-signing.cer` to a `WinDuo2-signing` folder on the desktop.
   - It deletes the private key from the PC.
3. In the GitHub repository, go to *Settings › Secrets and variables › Actions › New repository secret* and add two secrets:
   - `WINDOWS_CERT_PFX` with the contents of `WINDOWS_CERT_PFX.txt`
   - `WINDOWS_CERT_PASSWORD` with the contents of `WINDOWS_CERT_PASSWORD.txt`
4. **Delete the two `.txt` files.** Keep `WinDuo2-signing.cer`.
5. Build by pushing a `v*` or `Build_*` tag, or by running the workflow by hand. CI signs `WinDuo2.exe` and `WinDuo2-Setup.exe` and checks the result with `signtool verify`.

To trust the same certificate on another of your PCs, run this there:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\make-signing-cert.ps1" -Trust path\to\WinDuo2-signing.cer
```

Other options:

- `-Replace` makes a new certificate. Use it if the secret may have leaked, and then update both secrets.
- `-Remove` takes the certificate out of the PC.

Do not ask other people to trust your `.cer`. Anyone holding your key could then give programs uiAccess on their PCs. A build signed this way still works for other people: method A sees that their PC does not trust the signature and re-signs the exe with their own certificate.

#### C. A publicly trusted certificate

With a certificate that chains to a public root, no PC needs any of the above. Since June 2023, new OV and EV code signing certificates keep their private key in hardware (a token or an HSM), so they cannot be stored as a `.pfx` secret the way method B does. Cloud signing services such as Azure Trusted Signing are the practical route, and need the CI signing step replaced with their own action.

### 3. The secure location

`packaging/windows/WinDuo2.iss` is the Inno Setup script CI compiles into `WinDuo2-Setup.exe`. It:

- requires administrator rights and installs to `C:\Program Files\WinDuo2`.
- ships `make-signing-cert.ps1` and `trust-self-signed.ps1` next to the exe, and runs `trust-self-signed.ps1` after copying the files when the *Sign WinDuo2* task is ticked (method A).
- starts the app at the end through the shell (`shellexec`) as the signed-in user (`runasoriginaluser`), not as the elevated installer.
- closes a running copy so an update can replace its files.
- on uninstall, removes the sign-in task, the `Run` entry described below, and the method A certificate.

Do not move the folder or loosen its permissions. Anywhere else, Windows refuses to start the exe.

To build the installer by hand, from the repository root, after `native/build.ps1` and copying `native/build/app/uiaccess/WinDuo2.exe` to `dist\WinDuo2\`:

```bat
iscc packaging\windows\WinDuo2.iss
```

## Starting at sign-in

*Start with Windows* is on by default. When it is on, WinDuo2 registers a Task Scheduler task named **WinDuo2** that runs at the user's sign-in, without elevation (`winduo2/autostart.py`). If Windows refuses to register the task, it writes a `WinDuo2` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` instead. Turning the setting off removes both.

A uiAccess exe needs one change here. Task Scheduler starts programs with a plain `CreateProcess`. For an exe whose manifest asks for uiAccess, that call fails with `ERROR_ELEVATION_REQUIRED` (Task Scheduler shows `0x800702E4`, *"The requested operation requires elevation"*). Starting it through the shell lets Windows grant uiAccess the same way it does for a double-click. So when WinDuo2 is running with uiAccess, it registers the task as:

```
C:\Windows\System32\rundll32.exe shell32.dll,ShellExec_RunDLL "C:\Program Files\WinDuo2\WinDuo2.exe" --autostart
```

The portable build registers the exe directly, as before.

WinDuo2 rewrites the task every time it starts. Settings live in `%APPDATA%\WinDuo2` and are shared by every build. So after switching from the portable exe to the installed one, start the installed one once and the task points at it. You do not have to delete the old task by hand.

To check where it points:

```bat
schtasks /Query /TN WinDuo2 /XML
```

For the uiAccess build, `<Command>` should be `rundll32.exe` and `<Arguments>` should contain `C:\Program Files\WinDuo2\WinDuo2.exe`.

## Switching from the portable exe

1. Quit the portable WinDuo2 from its tray icon and delete `WinDuo2.exe`. Running both would draw the effect twice, and whichever started last owns the sign-in task.
2. Install `WinDuo2-Setup.exe`, leaving *Sign WinDuo2 with a certificate made on this PC* ticked and *Start WinDuo2* ticked on the last page. Starting the installed copy also repoints the sign-in task.

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
| The installer says WinDuo2 could not be signed | See `C:\Program Files\WinDuo2\signing.log`. Running `trust-self-signed.ps1` by hand shows the same error. |
| *"A referral was returned from the server"* when starting it | The exe is not signed by a certificate this PC trusts (for example, it was installed with the *Sign WinDuo2* task unticked), or it is not under Program Files. Reinstall with the task ticked, or run `trust-self-signed.ps1`. |
| The log has no `with uiAccess` | It was started from somewhere other than the installed exe, such as a source install or the portable exe. |
| WinDuo2 does not start at sign-in, and the task's last result is `0x800702E4` | The task still points straight at the exe. Start WinDuo2 once from the Start menu so it rewrites the task. |
| The task points at a deleted `WinDuo2.exe` | It is left over from the portable build. Start the installed build once, or delete the task as shown above. |
| Flyouts still close | The process has no uiAccess. Check the log line. |

## Uninstalling

Uninstall WinDuo2 from *Settings › Apps*. This removes the sign-in task, the `Run` entry, and the method A certificate with its key.

If you used method B, take its certificate out first, because uninstalling also removes the script:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\make-signing-cert.ps1" -Remove
```

If anything is left over, open `certlm.msc` and delete *WinDuo2 local uiAccess signing* or *WinDuo2 release signing* from *Personal*, *Trusted Root Certification Authorities* and *Trusted Publishers*.

## Cautions

- **Method A keeps a key on the PC.** It is non-exportable and usable only by administrators and SYSTEM. Anyone at that level can already trust any certificate, so this grants no one new power, but it does mean any administrator-level program on that PC could sign things it trusts.
- **The private key is the trust.** With method B, the private key exists only in the GitHub secret. Anyone who gets it can build programs that receive uiAccess on every PC that trusts the certificate. Anyone with write access to the repository can change a workflow to read the secret. Workflows run for pull requests from forks do not receive secrets. If the key may have leaked, use `-Replace`.
- **Delete the secret files.** Remove `WINDOWS_CERT_PFX.txt` and `WINDOWS_CERT_PASSWORD.txt` as soon as the secrets are saved, especially if the desktop syncs to the cloud.
- **Only `WinDuo2.exe` is signed,** and it is the whole program: no DLLs ship beside it. Still, do not loosen the folder's permissions.
- **Only trust builds you made.** Do not run these scripts on a build from somewhere else. Doing so gives that code uiAccess.
- **Not on managed PCs.** On a work or school PC, group policy may manage the root store or the UIAccess policy. These steps may not work there and may break its rules.
- **Expiry.** Both certificates last 10 years. CI timestamps its signatures, so method B builds stay valid after the certificate expires. Method A signatures are not timestamped, and the script makes a new certificate once the old one is within 30 days of expiring, so the next install or update after that picks it up.

## Files

| File | Purpose |
|---|---|
| `winduo2/render/win32.py` | `has_ui_access()` reads `TokenUIAccess` from the process token. `close_shell_flyouts()` is the fallback for builds without it. |
| `winduo2/app.py` | Skips closing the flyouts when the process has uiAccess. |
| `winduo2/autostart.py` | Registers the sign-in task, through `ShellExec_RunDLL` for the uiAccess build. |
| `.github/workflows/release.yml` | Builds the portable and installer variants and signs the installer variant when the secrets are set. |
| `packaging/windows/WinDuo2.iss` | The Inno Setup script for `WinDuo2-Setup.exe`, including the *Sign WinDuo2* task. |
| `packaging/windows/trust-self-signed.ps1` | Method A, run by the installer. |
| `packaging/windows/make-signing-cert.ps1` | Method B. |
