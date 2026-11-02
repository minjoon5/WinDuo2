# The native Direct3D 11 renderer, as winduo2_native.dll.
#
#   cmake -S native -B native/build -A x64
#   cmake --build native/build --config Release
#
# then copy native/build/Release/winduo2_native.dll to winduo2/render/, where
# winduo2/render/native.py looks for it. CI and the release workflow do this.
cmake_minimum_required(VERSION 3.20)
project(winduo2_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# The C runtime linked in, so the DLL needs nothing installed beside Windows.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(winduo2_native SHARED winduo2_native.cpp)
target_link_libraries(winduo2_native PRIVATE d3d11 dxgi dcomp d3dcompiler user32)
if(MSVC)
  target_compile_options(winduo2_native PRIVATE /W4 /WX /permissive- /utf-8)
  target_compile_definitions(winduo2_native PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
