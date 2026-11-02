# Builds winduo2_native.dll and puts it where winduo2/render/native.py looks.
# Needs Visual Studio's C++ tools and CMake, both on GitHub's Windows runners.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
cmake -S "$root/native" -B "$root/native/build" -A x64
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }
cmake --build "$root/native/build" --config Release
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }
Copy-Item "$root/native/build/Release/winduo2_native.dll" "$root/winduo2/render/" -Force
Write-Host "built winduo2/render/winduo2_native.dll"
