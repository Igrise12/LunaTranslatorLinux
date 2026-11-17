#!/bin/bash
# Cross-compile the Windows-side binaries the Linux app needs inside Wine
# prefixes, with msvc-wine (real MSVC under Wine):
#   LunaHook{32,64}.dll   injected into the game
#   LunaHost{32,64}.dll   hook host, loaded by LunaBridge
#   LunaBridge{32,64}.exe Wine-side helper the Linux app talks to
# and deploy them to src/files/LunaHook.
#
#   build_winbins.sh [--clean] [x86] [x64]
#
# One-time toolchain setup (accepts the Visual Studio license):
#   git clone https://github.com/mstorsjo/msvc-wine ~/opt/msvc-wine
#   cd ~/opt/msvc-wine
#   ./vsdownload.py --accept-license --dest ~/opt/msvc --with-atl yes \
#       --architecture x86 x64 --host-arch x64
#   ./install.sh ~/opt/msvc
# (needs msitools for msiextract). Set MSVC_WINE_DIR to use another path.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NATIVE="$(cd "$HERE/../.." && pwd)"   # src/NativeImpl
SRC="$(cd "$NATIVE/.." && pwd)"       # src
HOOK="$NATIVE/LunaHook"
DEPLOY="$SRC/files/LunaHook"
export MSVC_WINE_DIR="${MSVC_WINE_DIR:-$HOME/opt/msvc}"

CLEAN=0
ARCHS=()
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        x86|x64) ARCHS+=("$arg") ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done
[ ${#ARCHS[@]} -eq 0 ] && ARCHS=(x86 x64)

for cmd in cmake ninja wine wineserver; do
    command -v "$cmd" >/dev/null || { echo "missing: $cmd" >&2; exit 1; }
done
[ -x "$MSVC_WINE_DIR/bin/x64/cl" ] || {
    echo "msvc-wine not found in $MSVC_WINE_DIR (see the header of this script)" >&2
    exit 1
}

# the compiler gets its own prefix; a persistent wineserver avoids paying
# its start-up cost for every compiler invocation
export WINEPREFIX="${MSVC_WINE_PREFIX:-$HOME/.cache/lunatranslator/msvc-wine-prefix}"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml="
mkdir -p "$WINEPREFIX"
# initialise the prefix before the first compiler run: a cl invoked while
# wineboot is still populating it fails (CMake then reports an unknown compiler)
wineboot -i >/dev/null 2>&1 || true
wineserver -w
wineserver -p
trap 'wineserver -k >/dev/null 2>&1 || true' EXIT

# LunaTmpFontLoader.dll (font replacement in Mono-backend Unity games) is
# C#; Mono's mcs accepts the same csc-style options. Optional.
FONTLOADER=(-DBUILD_TMPFONTLOADER=OFF)
MCS="$(command -v mcs || true)"
MSCORLIB=""
for d in /usr/lib/mono/4.5 /usr/lib/mono/4.8-api /usr/lib/mono/4.7.2-api; do
    [ -f "$d/mscorlib.dll" ] && { MSCORLIB="$d/mscorlib.dll"; break; }
done
if [ -n "$MCS" ] && [ -n "$MSCORLIB" ]; then
    FONTLOADER=(-DBUILD_TMPFONTLOADER=ON "-Dcsc=$MCS" "-Dmscorlib=$MSCORLIB")
else
    echo "note: mono (mcs) not found, skipping LunaTmpFontLoader.dll" >&2
fi

for arch in "${ARCHS[@]}"; do
    BUILD="$NATIVE/build/wine-$arch"
    [ "$CLEAN" = 1 ] && rm -rf "$BUILD"
    cmake -S "$HOOK" -B "$BUILD" -G Ninja \
        -DCMAKE_TOOLCHAIN_FILE="$NATIVE/cmake/msvc-wine.cmake" \
        -DMSVC_WINE_ARCH="$arch" \
        -DCMAKE_BUILD_TYPE=Release \
        -DWIN10ABOVE=ON -DBUILD_HOOK=ON -DBUILD_HOST=ON -DBUILD_BRIDGE=ON \
        "${FONTLOADER[@]}" >/dev/null
    cmake --build "$BUILD" -j "$(nproc)"
done

OUT="$HOOK/builds/Release_win10"
mkdir -p "$DEPLOY"
for f in LunaHook32.dll LunaHook64.dll LunaHost32.dll LunaHost64.dll LunaBridge32.exe LunaBridge64.exe LunaTmpFontLoader.dll; do
    if [ -f "$OUT/$f" ]; then
        cp -f "$OUT/$f" "$DEPLOY/"
        echo "deployed $f"
    fi
done
