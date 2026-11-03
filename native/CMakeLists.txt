# WinDuo2.exe, and the core's test library for tests/test_core_parity.py.
#
#   cmake -S native -B native/build -A x64
#   cmake --build native/build --config Release
cmake_minimum_required(VERSION 3.20)
project(winduo2_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# The C runtime linked in, so the exe needs nothing installed beside Windows.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# The effect core's C test API, for tests/test_core_parity.py, which checks
# the C++ core against the Python implementation it was ported from.
add_library(winduo2_core_capi SHARED core/core.cpp core/core_capi.cpp)
if(MSVC)
  target_compile_options(winduo2_core_capi PRIVATE /W4 /WX /permissive- /utf-8)
  target_compile_definitions(winduo2_core_capi PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

# The app itself, all native: WinDuo2.exe, twice. "plain" is the portable
# exe; "uiaccess" asks for uiAccess in its manifest, for the installer, which
# puts it under Program Files where Windows grants it once it is signed.
set(APP_SOURCES
  app/main.cpp app/engine.cpp app/ui.cpp app/platform.cpp app/sensor.cpp
  core/core.cpp winduo2_native.cpp app/winduo2.rc)
foreach(variant IN ITEMS plain uiaccess)
  set(target WinDuo2_${variant})
  add_executable(${target} WIN32 ${APP_SOURCES})
  set_target_properties(${target} PROPERTIES
    # C++20 for C++/WinRT's coroutines; C++17 would pull in the removed /await ones.
    CXX_STANDARD 20
    OUTPUT_NAME WinDuo2
    RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_BINARY_DIR}/app/${variant}
    RUNTIME_OUTPUT_DIRECTORY_DEBUG ${CMAKE_BINARY_DIR}/app/${variant}/debug)
  target_compile_definitions(${target} PRIVATE
    UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN _WIN32_WINNT=0x0A00 WINVER=0x0A00
    _CRT_SECURE_NO_WARNINGS)
  if(variant STREQUAL "uiaccess")
    target_compile_definitions(${target} PRIVATE WD_UIACCESS)
  endif()
  target_link_libraries(${target} PRIVATE
    d3d11 dxgi dcomp d3dcompiler comctl32 winmm shell32 ole32 uuid advapi32 windowsapp
    dwmapi uxtheme)
  if(MSVC)
    target_compile_options(${target} PRIVATE
      /W4 /WX /permissive- /utf-8 /external:anglebrackets /external:W0 /bigobj)
    # The manifest comes from winduo2.rc; the linker must not add another.
    target_link_options(${target} PRIVATE /MANIFEST:NO)
  endif()
endforeach()
