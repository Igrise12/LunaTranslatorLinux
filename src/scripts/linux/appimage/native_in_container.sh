#!/bin/bash
# Runs INSIDE the manylinux_2_28 container started by build_native_compat.sh.
# Builds libNativeUtils.so and libCVUtils.so against glibc 2.28 with every
# third-party dependency linked statically, so the result runs on any distro
# the AppImage supports. Only libonnxruntime.so.1 (official prebuilt, glibc
# 2.27 baseline) stays a separate library, next to the modules ($ORIGIN rpath).
# gcc-toolset links the parts of its newer libstdc++ that the system one lacks
# statically, so libstdc++.so.6 of any distro since 2018 is enough.
# --exclude-libs hides the static dependencies' symbols (protobuf, OpenCV,
# mecab, ...) so they cannot clash with other libraries in the process.
#
# Layout under /work (a host folder mounted by the wrapper):
#   dl/        downloaded source archives (kept between runs)
#   deps/      static protobuf + OpenCV install prefix (kept between runs)
#   src/       copy of src/NativeImpl made by the wrapper
#   build/     CMake build folder of NativeImpl
#   out/       resulting .so files
set -euo pipefail

PROTOBUF_VERSION="${PROTOBUF_VERSION:-3.21.12}"
OPENCV_VERSION="${OPENCV_VERSION:-4.10.0}"
JOBS="${JOBS:-$(nproc)}"

W=/work
DL="$W/dl"
DEPS="$W/deps"
mkdir -p "$DL" "$DEPS" "$W/out"

fetch() { # url file
    [ -s "$DL/$2" ] && return 0
    curl -fL --retry 3 -o "$DL/$2.part" "$1"
    mv -f "$DL/$2.part" "$DL/$2"
}

# --- protobuf (lite runtime + protoc), static, PIC ---------------------------
# 3.21.x is the last release without the abseil dependency and the version the
# Windows build uses.
if [ ! -f "$DEPS/.protobuf-$PROTOBUF_VERSION" ]; then
    fetch "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOBUF_VERSION#3.}/protobuf-cpp-$PROTOBUF_VERSION.tar.gz" \
        "protobuf-cpp-$PROTOBUF_VERSION.tar.gz"
    rm -rf /tmp/pb && mkdir -p /tmp/pb
    tar -xzf "$DL/protobuf-cpp-$PROTOBUF_VERSION.tar.gz" -C /tmp/pb --strip-components=1
    cmake -S /tmp/pb -B /tmp/pb/_build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$DEPS" -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DCMAKE_CXX_FLAGS="-include cstdint" \
        -Dprotobuf_BUILD_SHARED_LIBS=OFF -Dprotobuf_BUILD_TESTS=OFF \
        -Dprotobuf_WITH_ZLIB=OFF
    cmake --build /tmp/pb/_build -j "$JOBS"
    cmake --install /tmp/pb/_build
    rm -rf /tmp/pb
    touch "$DEPS/.protobuf-$PROTOBUF_VERSION"
fi

# --- OpenCV core + imgproc, static, PIC, no optional backends -----------------
if [ ! -f "$DEPS/.opencv-$OPENCV_VERSION" ]; then
    fetch "https://github.com/opencv/opencv/archive/refs/tags/$OPENCV_VERSION.tar.gz" \
        "opencv-$OPENCV_VERSION.tar.gz"
    rm -rf /tmp/cv && mkdir -p /tmp/cv
    tar -xzf "$DL/opencv-$OPENCV_VERSION.tar.gz" -C /tmp/cv --strip-components=1
    cmake -S /tmp/cv -B /tmp/cv/_build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$DEPS" -DOPENCV_LIB_INSTALL_PATH=lib \
        -DOPENCV_3P_LIB_INSTALL_PATH=lib/opencv4/3rdparty \
        -DBUILD_SHARED_LIBS=OFF -DENABLE_PIC=ON \
        -DBUILD_LIST=core,imgproc \
        -DBUILD_ZLIB=ON -DWITH_IPP=OFF -DWITH_TBB=OFF -DWITH_OPENMP=OFF \
        -DWITH_LAPACK=OFF -DWITH_EIGEN=OFF -DWITH_OPENCL=OFF -DWITH_VA=OFF \
        -DWITH_VA_INTEL=OFF -DWITH_ITT=OFF -DWITH_PROTOBUF=OFF -DWITH_ADE=OFF \
        -DWITH_GTK=OFF -DWITH_QT=OFF -DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF \
        -DWITH_V4L=OFF -DWITH_1394=OFF -DWITH_CUDA=OFF -DWITH_OPENEXR=OFF \
        -DWITH_JPEG=OFF -DWITH_PNG=OFF -DWITH_TIFF=OFF -DWITH_WEBP=OFF \
        -DWITH_OPENJPEG=OFF -DWITH_JASPER=OFF -DWITH_IMGCODEC_HDR=OFF \
        -DCV_TRACE=OFF -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF \
        -DBUILD_EXAMPLES=OFF -DBUILD_opencv_apps=OFF -DBUILD_JAVA=OFF \
        -DBUILD_opencv_python3=OFF -DBUILD_DOCS=OFF \
        -DOPENCV_GENERATE_PKGCONFIG=OFF -DINSTALL_C_EXAMPLES=OFF
    cmake --build /tmp/cv/_build -j "$JOBS"
    cmake --install /tmp/cv/_build
    rm -rf /tmp/cv
    touch "$DEPS/.opencv-$OPENCV_VERSION"
fi

# --- NativeImpl ---------------------------------------------------------------
# NativeUtils links protobuf-lite by name and finds protoc on PATH; only the
# static archive exists in $DEPS, so it is linked in.
export PATH="$DEPS/bin:$PATH"
export CPLUS_INCLUDE_PATH="$DEPS/include"
export LIBRARY_PATH="$DEPS/lib"
cmake -S "$W/src" -B "$W/build" -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$DEPS" -DOpenCV_DIR="$DEPS/lib/cmake/opencv4" \
    -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    "-DFETCHCONTENT_SOURCE_DIR_ONNXRUNTIME=${ORT_DIR:-}" \
    -DCMAKE_SHARED_LINKER_FLAGS="-Wl,--exclude-libs,ALL" \
    -DCMAKE_MODULE_LINKER_FLAGS="-Wl,--exclude-libs,ALL"
cmake --build "$W/build" -j "$JOBS"

OUT="$W/src/builds/Release_x64"
rm -rf "$W/out" && mkdir -p "$W/out"
cp -a "$OUT"/libNativeUtils.so "$OUT"/libCVUtils.so "$OUT"/libonnxruntime.so* "$W/out/"
strip --strip-unneeded "$W/out/libNativeUtils.so" "$W/out/libCVUtils.so"

echo "== dependencies =="
for f in "$W/out"/libNativeUtils.so "$W/out"/libCVUtils.so; do
    echo "$f:"
    objdump -p "$f" | grep -E "NEEDED|RUNPATH"
    echo "  max GLIBC: $(objdump -T "$f" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)"
done
