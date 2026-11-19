#!/bin/bash
# Build portable Linux native libraries (libNativeUtils.so, libCVUtils.so,
# libonnxruntime.so.1) for the AppImage inside a manylinux_2_28 container
# (AlmaLinux 8, glibc 2.28, gcc-toolset), with protobuf and OpenCV linked in
# statically. Unlike src/NativeImpl/scripts/linux/build_native.sh, the result
# does not depend on the build machine's distro libraries.
#
#   build_native_compat.sh [--workdir DIR] [--engine podman|docker] [--clean]
#                          [--onnxruntime DIR]
#
# --onnxruntime DIR: an extracted onnxruntime package, either the GitHub
# release onnxruntime-linux-x64-<ver>.tgz (include/, lib/) or the NuGet
# Microsoft.ML.OnnxRuntime .nupkg (build/native/include/, runtimes/), to use
# instead of downloading it. By
# default the copy a previous build_native.sh run left in
# src/NativeImpl/build/linux/_deps/onnxruntime-src is reused when present.
# DIR (default: <repo>/build/appimage) receives native/out/*.so; downloads and
# the static dependency builds are cached in DIR/native and reused.
# Needs podman (rootless, preferred) or docker, and network access.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../../.." && pwd)"          # src
REPO="$(cd "$SRC/.." && pwd)"
WORKDIR="$REPO/build/appimage"
ENGINE=""
CLEAN=0
ORT=""
IMAGE="${MANYLINUX_IMAGE:-quay.io/pypa/manylinux_2_28_x86_64}"

optval() { [ $# -ge 2 ] && [ -n "$2" ] || { echo "$1 needs a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
    case "$1" in
        --workdir) optval "$@"; WORKDIR="$2"; shift ;;
        --engine) optval "$@"; ENGINE="$2"; shift ;;
        --clean) CLEAN=1 ;;
        --onnxruntime) optval "$@"; ORT="$2"; shift ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -z "$ENGINE" ]; then
    if command -v podman >/dev/null; then ENGINE=podman
    elif command -v docker >/dev/null; then ENGINE=docker
    else echo "need podman or docker" >&2; exit 1
    fi
fi

W="$WORKDIR/native"
mkdir -p "$W"
W="$(cd "$W" && pwd)"
[ "$CLEAN" = 1 ] && rm -rf "$W/build" "$W/deps" "$W/out"

# CMake writes its output next to the sources (src/NativeImpl/builds), so the
# container works on a copy; LunaHook (Windows-only) is not needed.
mkdir -p "$W/src"
rsync -a --delete --exclude=/build --exclude=/builds --exclude=/LunaHook \
    "$SRC/NativeImpl/" "$W/src/"

RUNARGS=(--rm -v "$W:/work" -v "$HERE/native_in_container.sh:/native_in_container.sh:ro")
# onnxruntime: the official prebuilt package (glibc 2.27 baseline), wherever
# it comes from; mounted read-only, CMake's FetchContent then skips the download
HOST_ORT="$SRC/NativeImpl/build/linux/_deps/onnxruntime-src"
is_ort() {
    [ -f "$1/include/onnxruntime_cxx_api.h" ] || [ -f "$1/build/native/include/onnxruntime_cxx_api.h" ]
}
if [ -z "$ORT" ] && is_ort "$HOST_ORT"; then
    ORT="$HOST_ORT"
fi
if [ -n "$ORT" ]; then
    ORT="$(cd "$ORT" && pwd)"
    is_ort "$ORT" || { echo "not an onnxruntime package: $ORT" >&2; exit 1; }
    echo "using onnxruntime from $ORT"
    RUNARGS+=(-v "$ORT:/onnxruntime:ro" -e ORT_DIR=/onnxruntime)
fi
if [ "$ENGINE" = podman ]; then
    # files created in /work stay owned by the calling user
    RUNARGS+=(--userns=keep-id)
else
    RUNARGS+=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
fi
for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy; do
    [ -n "${!v:-}" ] && RUNARGS+=(-e "$v=${!v}")
done

"$ENGINE" run "${RUNARGS[@]}" "$IMAGE" bash /native_in_container.sh
echo "native libraries in $W/out"
