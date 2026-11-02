<#
.SYNOPSIS
    Signs the installed WinDuo2.exe with a certificate made on this PC, so
    Windows grants it uiAccess. Anyone can use it; no GitHub, no purchase.

.DESCRIPTION
    uiAccess needs the exe to be signed by a certificate the PC trusts. This
    makes such a certificate on this PC, once, and signs with it:

      1. If WinDuo2.exe is already signed by a certificate this PC trusts,
         it stops: nothing is needed.
      2. Otherwise it reuses this PC's WinDuo2 signing certificate, or makes
         one the first time: self-signed, code signing only, valid 10 years.
         Its public half goes in Trusted Root and Trusted Publishers. Its
         private key stays in the machine's certificate store, marked
         non-exportable, where only administrators and SYSTEM can use it.
      3. It signs WinDuo2.exe with it.

    The installer runs this after every install and update (the "Sign
    WinDuo2 on this PC" task), so updates keep uiAccess on their own. It can
    also be run by hand from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinDuo2\trust-self-signed.ps1"

    The key is kept so that updates can be signed. Anyone who can use it is
    already an administrator of this PC, who could trust any certificate
    they liked, so keeping it gives no one new power. It never leaves the PC.

    -Remove deletes the certificate, its key and its trust. The uninstaller
    runs it.
#>
param(
    [string]$Exe = (Join-Path $env:ProgramFiles "WinDuo2\WinDuo2.exe"),
    [string]$Log,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Subject = "CN=WinDuo2 local uiAccess signing"
$TrustStores = "Root", "TrustedPublisher"

function Get-Mine([string]$Store) {
    Get-ChildItem "Cert:\LocalMachine\$Store" | Where-Object Subject -eq $Subject
}

function Remove-All {
    foreach ($store in $TrustStores) { Get-Mine $store | Remove-Item }
    Get-Mine "My" | ForEach-Object { Remove-Item $_.PSPath -DeleteKey }
}

function Get-SigningCert {
    # A usable certificate has its key here, is trusted, and is not about to expire.
    $trusted = @(Get-Mine "Root" | ForEach-Object Thumbprint)
    Get-Mine "My" |
        Where-Object { $_.HasPrivateKey -and $trusted -contains $_.Thumbprint -and
            $_.NotAfter -gt (Get-Date).AddDays(30) } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1
}

function New-SigningCert {
    # Leftovers, such as those from an older version of this script that
    # deleted the key, are replaced rather than piled up.
    Remove-All
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
        -CertStoreLocation Cert:\LocalMachine\My -KeyAlgorithm RSA -KeyLength 3072 `
        -HashAlgorithm SHA256 -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(10)
    $public = Join-Path $env:TEMP "winduo2-signing-$([guid]::NewGuid()).cer"
    try {
        Export-Certificate -Cert $cert -FilePath $public | Out-Null
        foreach ($store in $TrustStores) {
            Import-Certificate -FilePath $public -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
        }
    } finally {
        Remove-Item $public -ErrorAction SilentlyContinue
    }
    Write-Host "Made a signing certificate for this PC: $($cert.Thumbprint)"
    return $cert
}

if ($Log) { Start-Transcript -Path $Log -Force | Out-Null }
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        throw "Run this from an elevated PowerShell (Run as administrator)."
    }

    if ($Remove) {
        Remove-All
        Write-Host "Removed this PC's WinDuo2 signing certificate."
        return
    }

    if (-not (Test-Path $Exe)) {
        throw "No WinDuo2.exe at $Exe. Install WinDuo2 with WinDuo2-Setup.exe first."
    }

    $existing = Get-AuthenticodeSignature -FilePath $Exe
    if ($existing.Status -eq "Valid") {
        Write-Host "$Exe is already signed by $($existing.SignerCertificate.Subject), which this PC trusts. Nothing to do."
        return
    }

    $cert = Get-SigningCert
    if (-not $cert) { $cert = New-SigningCert }

    # A running copy holds the exe open, and signing rewrites it.
    Get-Process WinDuo2 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq (Resolve-Path $Exe).Path } |
        Stop-Process -Force
    Start-Sleep -Milliseconds 500

    $result = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert -HashAlgorithm SHA256
    if ($result.Status -ne "Valid") {
        throw "Signing failed: $($result.StatusMessage)"
    }
    Write-Host "Signed $Exe for this PC."
} finally {
    if ($Log) { Stop-Transcript | Out-Null }
}
