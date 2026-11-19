#!/bin/bash
# Build a self-contained x86_64 AppImage of the Linux port.
#
#   build_appimage.sh [options]
#     --workdir DIR      scratch folder (default: <repo>/build/appimage); keeps
#                        downloads and the native dependency builds between runs
#     --output FILE      default: <repo>/dist/LunaTranslator-<version>-x86_64.AppImage
#     --native MODE      where libNativeUtils.so/libCVUtils.so come from:
#                          compat (default) build them in a manylinux_2_28
#                                  container (build_native_compat.sh)
#                          local   take src/files/DLL64 as is (NOT portable:
#                                  linked against this machine's libraries)
#                          DIR     take them from DIR
#     --engine NAME      podman (default if installed) or docker
#     --onnxruntime DIR  extracted onnxruntime package (release .tgz or NuGet) for
#                        --native compat (see build_native_compat.sh)
#     --python-url URL   python-appimage base image (default: Python 3.13,
#                        manylinux_2_28)
#     --no-xcb-libs      do not bundle the xcb-util libraries Qt's xcb plugin
#                        needs (libxcb-cursor0 & co., often not installed)
#     --ocr-model        download the built-in OCR model first if missing
#     --update-info STR  embed AppImage update information (e.g.
#                        "gh-releases-zsync|Igrise12|LunaTranslatorLinux|latest|LunaTranslator-*x86_64.AppImage.zsync")
#
# Inputs from the source tree:
#   src/LunaTranslator, src/files/{lang,static,Locale,linux}, src/version.txt;
#   src/files/LunaHook: the Windows-side binaries (LunaHook*.dll, LunaHost*.dll,
#     LunaBridge*.exe). They are gitignored build outputs: build them with
#     src/NativeImpl/scripts/linux/build_winbins.sh or fetch a release with
#     src/scripts/linux/setup.sh --winbins download;
#   src/files/luna.ico (fetched if missing), src/files/ocrmodel (if present).
# Needs: bash, curl, rsync, readelf (binutils), podman/docker (for --native compat and the xcb
# libraries), network access. Python and PyQt6 come from downloads, not from
# the build machine.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../../.." && pwd)"   # src
REPO="$(cd "$SRC/.." && pwd)"

WORKDIR="$REPO/build/appimage"
OUTPUT=""
NATIVE=compat
ENGINE=""
# python-appimage replaces the builds in its rolling "python3.13" release, so
# the newest cp313 manylinux_2_28 build is looked up (GitHub API), with this
# one as the fallback; both downloads are checked against the SHA-256 digest
# the API reports
PYTHON_URL="${PYTHON_APPIMAGE_URL:-}"
PYTHON_FALLBACK_URL="https://github.com/niess/python-appimage/releases/download/python3.13/python3.13.17-cp313-cp313-manylinux_2_28_x86_64.AppImage"
APPIMAGETOOL_URL="${APPIMAGETOOL_URL:-}"
XCB_IMAGE="${XCB_IMAGE:-docker.io/library/ubuntu:22.04}"
XCB_LIBS=1
OCR_MODEL=0
UPDATE_INFO=""
ORT_ARGS=()
APP_ID="io.github.Igrise12.LunaTranslatorLinux"

optval() { [ $# -ge 2 ] && [ -n "$2" ] || { echo "$1 needs a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
    case "$1" in
        --workdir) optval "$@"; WORKDIR="$2"; shift ;;
        --output) optval "$@"; OUTPUT="$2"; shift ;;
        --native) optval "$@"; NATIVE="$2"; shift ;;
        --engine) optval "$@"; ENGINE="$2"; shift ;;
        --onnxruntime) optval "$@"; ORT_ARGS=(--onnxruntime "$2"); shift ;;
        --python-url) optval "$@"; PYTHON_URL="$2"; shift ;;
        --no-xcb-libs) XCB_LIBS=0 ;;
        --ocr-model) OCR_MODEL=1 ;;
        --update-info) optval "$@"; UPDATE_INFO="$2"; shift ;;
        -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say() { printf '\n== %s\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

for cmd in curl rsync readelf; do
    command -v "$cmd" >/dev/null || die "missing: $cmd"
done
if [ -z "$ENGINE" ]; then
    if command -v podman >/dev/null; then ENGINE=podman
    elif command -v docker >/dev/null; then ENGINE=docker
    fi
fi

START=$(date +%s)
VERSION="$(tr -d '[:space:]' < "$SRC/version.txt")"
[ -n "$OUTPUT" ] || OUTPUT="$REPO/dist/LunaTranslator-$VERSION-x86_64.AppImage"
mkdir -p "$WORKDIR" "$(dirname "$OUTPUT")"
WORKDIR="$(cd "$WORKDIR" && pwd)"
DL="$WORKDIR/dl"
APPDIR="$WORKDIR/AppDir"
mkdir -p "$DL"

# --- Windows-side binaries ------------------------------------------------------
HOOK="$SRC/files/LunaHook"
for f in LunaHook32.dll LunaHook64.dll LunaHost32.dll LunaHost64.dll LunaBridge32.exe LunaBridge64.exe; do
    [ -f "$HOOK/$f" ] || die "missing $HOOK/$f
  build it:  src/NativeImpl/scripts/linux/build_winbins.sh
  or fetch:  src/scripts/linux/setup.sh --winbins download"
done

# --- native libraries -------------------------------------------------------------
say "native libraries ($NATIVE)"
case "$NATIVE" in
    compat)
        [ -n "$ENGINE" ] || die "--native compat needs podman or docker (or use --native local|DIR)"
        "$HERE/build_native_compat.sh" --workdir "$WORKDIR" --engine "$ENGINE" "${ORT_ARGS[@]}"
        NATIVE_DIR="$WORKDIR/native/out"
        ;;
    local)
        NATIVE_DIR="$SRC/files/DLL64"
        echo "warning: using src/files/DLL64; the AppImage will only run on systems with the same libraries" >&2
        ;;
    *) NATIVE_DIR="$(cd "$NATIVE" && pwd)" ;;
esac
for f in libNativeUtils.so libCVUtils.so libonnxruntime.so.1; do
    [ -e "$NATIVE_DIR/$f" ] || die "missing $NATIVE_DIR/$f"
done

# --- downloads ----------------------------------------------------------------------
gh_asset() { # repo tag name-regex -> "url sha256" of the newest matching asset
    curl -fsSL --retry 3 ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} \
        "https://api.github.com/repos/$1/releases/tags/$2" 2>/dev/null |
        python3 -c '
import json, re, sys
try:
    assets = json.load(sys.stdin)["assets"]
except Exception:
    sys.exit(1)
key = lambda a: [int(x) for x in re.findall(r"\d+", a["name"])]
m = sorted((a for a in assets if re.fullmatch(sys.argv[1], a["name"])), key=key)
if not m:
    sys.exit(1)
print(m[-1]["browser_download_url"], (m[-1].get("digest") or "").replace("sha256:", ""))
' "$3"
}
fetch() { # url file [sha256]
    if [ -s "$DL/$2" ]; then
        [ -z "${3:-}" ] && return 0
        [ "$(sha256sum "$DL/$2" | cut -d' ' -f1)" = "$3" ] && return 0
    fi
    echo "downloading $1"
    curl -fL --retry 3 -o "$DL/$2.part" "$1" || die "download failed: $1"
    if [ -n "${3:-}" ]; then
        [ "$(sha256sum "$DL/$2.part" | cut -d' ' -f1)" = "$3" ] || die "checksum mismatch: $1"
    fi
    mv -f "$DL/$2.part" "$DL/$2"
}
PYTHON_SUM=""
if [ -z "$PYTHON_URL" ]; then
    if r="$(gh_asset niess/python-appimage python3.13 'python3\.13\.[0-9]+-cp313-cp313-manylinux_2_28_x86_64\.AppImage')"; then
        read -r PYTHON_URL PYTHON_SUM <<<"$r"
    else
        echo "warning: GitHub API unavailable, using $PYTHON_FALLBACK_URL" >&2
        PYTHON_URL="$PYTHON_FALLBACK_URL"
    fi
fi
TOOL_SUM=""
if [ -z "$APPIMAGETOOL_URL" ]; then
    APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    if r="$(gh_asset AppImage/appimagetool continuous 'appimagetool-x86_64\.AppImage')"; then
        read -r APPIMAGETOOL_URL TOOL_SUM <<<"$r"
    fi
fi
fetch "$PYTHON_URL" "$(basename "$PYTHON_URL")" "$PYTHON_SUM"
fetch "$APPIMAGETOOL_URL" appimagetool-x86_64.AppImage "$TOOL_SUM"
chmod +x "$DL/$(basename "$PYTHON_URL")" "$DL/appimagetool-x86_64.AppImage"

ICON="$SRC/files/luna.ico"
if [ ! -s "$ICON" ]; then
    curl -fsSL -o "$ICON" https://image.lunatranslator.org/luna.ico || die "cannot fetch $ICON"
fi
if [ "$OCR_MODEL" = 1 ] && [ ! -d "$SRC/files/ocrmodel" ]; then
    # same source and layout as build_native.sh --ocr-model
    fetch "https://lunatranslator.org/r2/luna/ocr_models_v6/jazhchten.zip" jazhchten.zip
    SUM="$(md5sum "$DL/jazhchten.zip" | cut -d' ' -f1)"
    mkdir -p "$SRC/files/ocrmodel/$SUM"
    unzip -q -o "$DL/jazhchten.zip" -d "$SRC/files/ocrmodel/$SUM" \
        || python3 -m zipfile -e "$DL/jazhchten.zip" "$SRC/files/ocrmodel/$SUM"
fi

# --- Python base ----------------------------------------------------------------------
say "Python base"
rm -rf "$APPDIR" "$WORKDIR/squashfs-root"
(cd "$WORKDIR" && "$DL/$(basename "$PYTHON_URL")" --appimage-extract >/dev/null)
mv "$WORKDIR/squashfs-root" "$APPDIR"
PYDIR="$(cd "$APPDIR/opt" && ls -d python3.* | head -1)"
ln -sfn "$PYDIR" "$APPDIR/opt/python3"
PY="$APPDIR/opt/python3/bin/${PYDIR}"
[ -x "$PY" ] || die "no Python in $APPDIR/opt/$PYDIR"
# AppRun starts opt/python3/bin/python3
ln -sfn "$PYDIR" "$APPDIR/opt/python3/bin/python3"
PYLIB="$APPDIR/opt/$PYDIR/lib/$PYDIR"
# drop python-appimage's own launcher, desktop entry and Tcl/Tk (the X11
# libraries Tk ships must not end up next to Qt's)
rm -f "$APPDIR/AppRun" "$APPDIR"/*.desktop "$APPDIR"/python.png "$APPDIR/.DirIcon"
rm -rf "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo" \
    "$APPDIR/usr/share/icons" "$APPDIR/usr/share/tcltk" \
    "$APPDIR"/usr/lib/libtcl* "$APPDIR"/usr/lib/libtk* \
    "$APPDIR"/usr/lib/libXft* "$APPDIR"/usr/lib/libXrender* \
    "$PYLIB/tkinter" "$PYLIB/idlelib" "$PYLIB/turtledemo" "$PYLIB/test" \
    "$PYLIB"/lib-dynload/_tkinter*

# --- Python packages -------------------------------------------------------------------
say "Python packages"
SSL_CERT_FILE="$APPDIR/opt/_internal/certs.pem" "$PY" -E -s -m pip install \
    --disable-pip-version-check --no-cache-dir --no-warn-script-location \
    --only-binary=:all: \
    -r "$SRC/scripts/linux/requirements.txt" -c "$HERE/constraints.txt"
SITE="$PYLIB/site-packages"
QT="$SITE/PyQt6/Qt6"
[ -d "$QT/lib" ] || die "PyQt6 wheel layout changed: no $QT/lib"
# type stubs, .sip files, Qt Designer/QML bits the app does not use
rm -rf "$SITE/PyQt6/bindings" "$QT/qml" "$QT/qsci"
find "$SITE/PyQt6" -name '*.pyi' -delete
rm -rf "$SITE"/pip "$SITE"/pip-*.dist-info "$APPDIR/opt/$PYDIR/bin"/pip* "$APPDIR/usr/bin"/pip*

# --- xcb-util libraries for Qt's xcb plugin --------------------------------------------
# Qt >= 6.5 needs libxcb-cursor0, which many desktops lack; with the other
# xcb-util libraries it goes next to the Qt libraries, where the plugin's
# $ORIGIN rpath finds them.
if [ "$XCB_LIBS" = 1 ]; then
    say "xcb-util libraries"
    [ -n "$ENGINE" ] || die "bundling the xcb-util libraries needs podman or docker (or use --no-xcb-libs)"
    XCB_OUT="$WORKDIR/xcb-libs"
    if [ ! -f "$XCB_OUT/.done" ]; then
        rm -rf "$XCB_OUT" && mkdir -p "$XCB_OUT"
        "$ENGINE" run --rm -v "$XCB_OUT:/out" "$XCB_IMAGE" bash -euc '
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            cd /tmp
            apt-get download -qq libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
                libxcb-keysyms1 libxcb-render-util0 libxcb-util1
            for d in *.deb; do dpkg-deb -x "$d" x; done
            cp -a x/usr/lib/x86_64-linux-gnu/*.so.* /out/
            for d in *.deb; do dpkg-deb -f "$d" Package Version; done > /out/VERSIONS
            chown -R "$(stat -c %u:%g /out)" /out'
        touch "$XCB_OUT/.done"
    fi
    cp -a "$XCB_OUT"/*.so.* "$QT/lib/"
fi

# Qt add-ons the app does not use (Quick 3D, Designer, Bluetooth, ...)
"$PY" -I "$HERE/prune_qt.py" "$SITE"

# --- the application --------------------------------------------------------------------
say "application"
APP="$APPDIR/opt/lunatranslator"
mkdir -p "$APP/files/DLL64" "$APP/files/LunaHook"
rsync -a --exclude=__pycache__ --exclude='*.pyc' "$SRC/LunaTranslator/" "$APP/LunaTranslator/"
cp "$SRC/version.txt" "$APP/"
for d in lang static Locale linux ocrmodel; do
    [ -d "$SRC/files/$d" ] && rsync -a "$SRC/files/$d" "$APP/files/"
done
cp "$ICON" "$APP/files/luna.ico"
cp -a "$NATIVE_DIR"/libNativeUtils.so "$NATIVE_DIR"/libCVUtils.so "$NATIVE_DIR"/libonnxruntime.so* "$APP/files/DLL64/"
for f in LunaHook32.dll LunaHook64.dll LunaHost32.dll LunaHost64.dll LunaBridge32.exe LunaBridge64.exe LunaTmpFontLoader.dll; do
    [ -f "$HOOK/$f" ] && cp "$HOOK/$f" "$APP/files/LunaHook/"
done
# AppRun refreshes its writable copies of LunaHook/ etc. when this changes
GITREV="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "$VERSION $GITREV $(date -u +%Y%m%dT%H%M%SZ)" > "$APP/files/.build-id"

# byte-code next to the sources: the image is read-only, so without it every
# start would compile everything again
"$PY" -E -s -m compileall -q -j 0 "$APP/LunaTranslator" "$SITE" >/dev/null || true

# --- AppImage metadata ------------------------------------------------------------------
say "metadata"
install -m 755 "$HERE/AppRun" "$APPDIR/AppRun"
ICONDIR="$APPDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$ICONDIR" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"
# the 256px image inside luna.ico is a PNG
"$PY" -E -s - "$ICON" "$ICONDIR/$APP_ID.png" <<'EOF'
import struct, sys
data = open(sys.argv[1], "rb").read()
best = None
for i in range(struct.unpack("<H", data[4:6])[0]):
    e = data[6 + 16 * i : 22 + 16 * i]
    size, off = struct.unpack("<II", e[8:16])
    w = e[0] or 256
    if data[off : off + 4] == b"\x89PNG" and (best is None or w > best[0]):
        best = (w, data[off : off + size])
if best is None:
    sys.exit("no PNG image in " + sys.argv[1])
open(sys.argv[2], "wb").write(best[1])
EOF
cp "$SRC/files/linux/$APP_ID.desktop" "$APPDIR/usr/share/applications/"
# the release entry follows version.txt ("Bump version" commits only touch that)
sed -E "s|<release version=\"[^\"]*\" date=\"[^\"]*\"|<release version=\"$VERSION\" date=\"$(date -u +%F)\"|" \
    "$SRC/files/linux/$APP_ID.metainfo.xml" > "$APPDIR/usr/share/metainfo/$APP_ID.metainfo.xml"
ln -sf "usr/share/applications/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
ln -sf "usr/share/icons/hicolor/256x256/apps/$APP_ID.png" "$APPDIR/$APP_ID.png"
ln -sf "$APP_ID.png" "$APPDIR/.DirIcon"

# --- pack -------------------------------------------------------------------------------
say "appimagetool"
TOOLARGS=()
[ -n "$UPDATE_INFO" ] && TOOLARGS+=(-u "$UPDATE_INFO")
rm -f "$OUTPUT"
# python-appimage extracts with mode 0700; the image is used by other users
chmod -R a+rX,go-w "$APPDIR"
# APPIMAGE_EXTRACT_AND_RUN: works without FUSE (containers, CI)
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$DL/appimagetool-x86_64.AppImage" \
    --no-appstream "${TOOLARGS[@]}" "$APPDIR" "$OUTPUT"

say "done in $(( $(date +%s) - START ))s"
ls -la "$OUTPUT"
du -sh "$APPDIR"
