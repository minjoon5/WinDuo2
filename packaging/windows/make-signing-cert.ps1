<#
.SYNOPSIS
    Makes the certificate CI signs WinDuo2 releases with, and trusts it on
    this PC, once. After that every installed update keeps uiAccess.

.DESCRIPTION
    trust-self-signed.ps1 signs the installed exe on this PC, so it has to be
    run again after every update. This instead keeps one certificate for good:

      1. Creates a self-signed code signing certificate.
      2. Writes its private key, as a password-protected .pfx in base64, and
         the password into two text files, for the WINDOWS_CERT_PFX and
         WINDOWS_CERT_PASSWORD secrets of the GitHub repository. CI signs
         every WinDuo2-Setup.exe build with it from then on.
      3. Trusts its public half on this PC (Trusted Root and Trusted
         Publishers), and saves it as WinDuo2-signing.cer.
      4. Deletes the private key from this PC.

    Delete the two secret files once the secrets are saved on GitHub. Keep
    WinDuo2-signing.cer to trust the same certificate on another PC of yours:
        make-signing-cert.ps1 -Trust path\to\WinDuo2-signing.cer

    Run from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File make-signing-cert.ps1

    -Remove takes the certificate back out of this PC's stores.
#>
param(
    [string]$OutDir = (Join-Path ([Environment]::GetFolderPath("Desktop")) "WinDuo2-signing"),
    [string]$Trust,
    [switch]$Replace,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Subject = "CN=WinDuo2 release signing"
$Stores = "Root", "TrustedPublisher"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    throw "Run this from an elevated PowerShell (Run as administrator)."
}

function Get-Trusted {
    foreach ($store in $Stores) {
        Get-ChildItem "Cert:\LocalMachine\$store" | Where-Object Subject -eq $Subject
    }
}

function Add-Trusted([string]$Cer) {
    foreach ($store in $Stores) {
        Import-Certificate -FilePath $Cer -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
    }
}

if ($Remove) {
    Get-Trusted | Remove-Item
    Write-Host "Removed the WinDuo2 release signing certificate from this PC."
    return
}

if ($Trust) {
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Resolve-Path $Trust).Path)
    if ($cert.Subject -ne $Subject -or $cert.HasPrivateKey) {
        throw "$Trust is not the public WinDuo2-signing.cer this script writes."
    }
    Add-Trusted (Resolve-Path $Trust).Path
    Write-Host "This PC now trusts WinDuo2 builds signed with $($cert.Thumbprint)."
    return
}

if (Get-Trusted) {
    if (-not $Replace) {
        throw ("This PC already trusts a WinDuo2 release signing certificate. " +
            "-Replace makes a new one; builds signed with the old one stop starting.")
    }
    Get-Trusted | Remove-Item
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
    -CertStoreLocation Cert:\CurrentUser\My -KeyAlgorithm RSA -KeyLength 3072 `
    -HashAlgorithm SHA256 -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(10)

try {
    $bytes = [byte[]]::new(24)
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = [Convert]::ToBase64String($bytes)

    $pfx = Join-Path $OutDir "signing.pfx"
    Export-PfxCertificate -Cert $cert -FilePath $pfx `
        -Password (ConvertTo-SecureString $password -AsPlainText -Force) | Out-Null
    [Convert]::ToBase64String([IO.File]::ReadAllBytes($pfx)) |
        Set-Content (Join-Path $OutDir "WINDOWS_CERT_PFX.txt") -NoNewline
    $password | Set-Content (Join-Path $OutDir "WINDOWS_CERT_PASSWORD.txt") -NoNewline
    Remove-Item $pfx

    $cer = Join-Path $OutDir "WinDuo2-signing.cer"
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
    Add-Trusted $cer
} finally {
    # Only the GitHub secret holds the private key from here on.
    Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -DeleteKey
}

Write-Host ""
Write-Host "This PC now trusts WinDuo2 builds signed with $($cert.Thumbprint)."
Write-Host ""
Write-Host "Next, in the GitHub repository: Settings > Secrets and variables > Actions >"
Write-Host "New repository secret, twice:"
Write-Host "  WINDOWS_CERT_PFX       contents of $OutDir\WINDOWS_CERT_PFX.txt"
Write-Host "  WINDOWS_CERT_PASSWORD  contents of $OutDir\WINDOWS_CERT_PASSWORD.txt"
Write-Host ""
Write-Host "Then delete those two .txt files. Keep WinDuo2-signing.cer."
