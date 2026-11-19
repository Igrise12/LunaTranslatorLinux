#!/bin/bash
# Build the Linux native libraries (NativeUtils, CVUtils) and deploy them to
# src/files/DLL64, plus the runtime assets the Windows build normally ships.
#
#   build_native.sh [--clean] [--ocr-model]
#
# Dependencies (Arch): cmake ninja protobuf opencv
#              (Debian/Ubuntu): build-essential cmake ninja-build
#                               protobuf-compiler libprotobuf-dev libopencv-dev
# FetchContent downloads further sources from GitHub during configure; set
# http_proxy/https_proxy yourself if you need a proxy.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NATIVE="$(cd "$HERE/../.." && pwd)"   # src/NativeImpl
SRC="$(cd "$NATIVE/.." && pwd)"       # src
BUILD="$NATIVE/build/linux"
OUT="$NATIVE/builds/Release_x64"
DEPLOY="$SRC/files/DLL64"

CLEAN=0
OCR_MODEL=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        --ocr-model) OCR_MODEL=1 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

for cmd in cmake ninja protoc curl; do
    command -v "$cmd" >/dev/null || { echo "missing: $cmd" >&2; exit 1; }
done

if [ "$CLEAN" = 1 ]; then
    rm -rf "$BUILD"
fi

if [ ! -f "$BUILD/build.ninja" ]; then
    # BUILD_SHARED_LIBS=OFF in the cache: md4c's option() would otherwise
    # default it to ON on the first configure and link libmd4c*.so dynamically
    cmake -S "$NATIVE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF
fi
cmake --build "$BUILD" -j "$(nproc)"

mkdir -p "$DEPLOY"
# -a keeps the onnxruntime soname symlink
cp -a "$OUT"/libNativeUtils.so "$OUT"/libCVUtils.so "$OUT"/libonnxruntime.so* "$DEPLOY"/
echo "deployed native libraries to $DEPLOY"

# application icon (the Windows build embeds the same file into the exe)
ICON="$SRC/files/luna.ico"
if [ ! -s "$ICON" ]; then
    curl -fsSL -o "$ICON" https://image.lunatranslator.org/luna.ico \
        && echo "fetched $ICON" || echo "warning: could not fetch app icon" >&2
fi

# built-in local OCR model (same source and layout as
# src/scripts/build_lunatranslator.py downloadOCRModel); the app can also
# download it on demand from the OCR settings.
if [ "$OCR_MODEL" = 1 ]; then
    LINK="https://lunatranslator.org/r2/luna/ocr_models_v6/jazhchten.zip"
    MODELROOT="$SRC/files/ocrmodel"
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    curl -fL -o "$TMP/jazhchten.zip" "$LINK"
    SUM="$(md5sum "$TMP/jazhchten.zip" | cut -d' ' -f1)"
    mkdir -p "$MODELROOT/$SUM"
    (cd "$MODELROOT/$SUM" && python3 -m zipfile -e "$TMP/jazhchten.zip" .)
    echo "installed OCR model to $MODELROOT/$SUM"
fi
