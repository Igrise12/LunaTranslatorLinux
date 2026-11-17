# Cross-compile the Windows parts (LunaHook, LunaHost, LunaBridge) on Linux
# with msvc-wine (https://github.com/mstorsjo/msvc-wine): the real MSVC
# cl/link/rc run under Wine, so SEH, MS inline asm, ATL and concrt behave as
# on Windows.
#
#   cmake -DCMAKE_TOOLCHAIN_FILE=.../msvc-wine.cmake -DMSVC_WINE_ARCH=x64|x86 ...
#
# MSVC_WINE_DIR (env or cache) points at the msvc-wine install (default
# ~/opt/msvc).
set(CMAKE_SYSTEM_NAME Windows)

if(NOT DEFINED MSVC_WINE_ARCH)
    set(MSVC_WINE_ARCH x64)
endif()
if(MSVC_WINE_ARCH STREQUAL "x86")
    set(CMAKE_SYSTEM_PROCESSOR X86)
else()
    set(CMAKE_SYSTEM_PROCESSOR AMD64)
endif()

if(NOT DEFINED MSVC_WINE_DIR)
    if(DEFINED ENV{MSVC_WINE_DIR})
        set(MSVC_WINE_DIR $ENV{MSVC_WINE_DIR})
    else()
        set(MSVC_WINE_DIR $ENV{HOME}/opt/msvc)
    endif()
endif()

set(_bin ${MSVC_WINE_DIR}/bin/${MSVC_WINE_ARCH})
set(CMAKE_C_COMPILER ${_bin}/cl CACHE FILEPATH "")
set(CMAKE_CXX_COMPILER ${_bin}/cl CACHE FILEPATH "")
set(CMAKE_RC_COMPILER ${_bin}/rc CACHE FILEPATH "")
set(CMAKE_MT ${_bin}/mt CACHE FILEPATH "")
set(CMAKE_LINKER ${_bin}/link CACHE FILEPATH "")
set(CMAKE_AR ${_bin}/lib CACHE FILEPATH "")

# keep host tools out of the target search paths
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
