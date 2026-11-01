<#
.SYNOPSIS
    Signs the installed WinDuo2.exe with a certificate made on this PC, so
    Windows grants it uiAccess. For your own machine only.

.DESCRIPTION
    uiAccess needs the exe to be signed by a certificate the machine trusts.
    Without a purchased code signing certificate, this makes one here:

      1. Creates a self-signed code signing certificate.
      2. Signs C:\Program Files\WinDuo2\WinDuo2.exe with it.
      3. Trusts only its public half (Trusted Root and Trusted Publishers).
      4. Deletes the private key, so nothing else can ever be signed with it.

    Run it again after every update: the installer replaces the exe, and the
    new one is unsigned until you do. To avoid that, have CI sign every build
    with one certificate instead; see make-signing-cert.ps1. Each run makes a fresh certificate; old
    ones are removed first.

    Run from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\trust-self-signed.ps1"

    -Remove takes every certificate this script made back out of the stores.
#>
param(
    [string]$Exe = (Join-Path $env:ProgramFiles "WinDuo2\WinDuo2.exe"),
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Subject = "CN=WinDuo2 local uiAccess signing"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    throw "Run this from an elevated PowerShell (Run as administrator)."
}

if (-not $Remove -and (Test-Path $Exe)) {
    $existing = Get-AuthenticodeSignature -FilePath $Exe
    if ($existing.Status -eq "Valid" -and $existing.SignerCertificate.Subject -ne $Subject) {
        Write-Host "$Exe is already signed by a certificate this PC trusts. Nothing to do."
        return
    }
}

foreach ($store in "My", "Root", "TrustedPublisher") {
    Get-ChildItem "Cert:\LocalMachine\$store" |
        Where-Object Subject -eq $Subject |
        Remove-Item
}
if ($Remove) {
    Write-Host "Removed the WinDuo2 signing certificates."
    return
}

if (-not (Test-Path $Exe)) {
    throw "No WinDuo2.exe at $Exe. Install WinDuo2 with WinDuo2-Setup.exe first."
}

# A running copy holds the exe open, and signing rewrites it.
Get-Process WinDuo2 -ErrorAction SilentlyContinue | Stop-Process
Start-Sleep -Milliseconds 500

$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
    -CertStoreLocation Cert:\LocalMachine\My -KeyAlgorithm RSA -KeyLength 3072 `
    -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(5)

$public = Join-Path $env:TEMP "winduo2-signing.cer"
try {
    Export-Certificate -Cert $cert -FilePath $public | Out-Null
    Import-Certificate -FilePath $public -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    Import-Certificate -FilePath $public -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
} finally {
    Remove-Item $public -ErrorAction SilentlyContinue
}

$result = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert -HashAlgorithm SHA256
# The private key is not needed again; without it the trusted root cannot sign anything else.
Remove-Item "Cert:\LocalMachine\My\$($cert.Thumbprint)" -DeleteKey

if ($result.Status -ne "Valid") {
    throw "Signing failed: $($result.StatusMessage)"
}
Write-Host "Signed $Exe. Start WinDuo2 again from the Start menu."
