# Builds the native app (native/build/app/{plain,uiaccess}/WinDuo2.exe), the
# renderer DLL the Python app uses (copied to winduo2/render/), and the core
# test library tests/test_core_parity.py loads.
# Needs Visual Studio's C++ tools and CMake, both on GitHub's Windows runners.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
cmake -S "$root/native" -B "$root/native/build" -A x64
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }
cmake --build "$root/native/build" --config Release
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }
Copy-Item "$root/native/build/Release/winduo2_native.dll" "$root/winduo2/render/" -Force
Write-Host "built winduo2/render/winduo2_native.dll"
Write-Host "built native/build/app/plain/WinDuo2.exe and native/build/app/uiaccess/WinDuo2.exe"
