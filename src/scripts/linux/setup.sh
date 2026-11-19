#!/bin/bash
# Set up LunaTranslator's Linux port to run from this source tree.
#
#   src/scripts/linux/setup.sh [options]
#
# Without options it only checks the system and prints what is missing, with
# the command that installs it (it never installs system packages itself).
#
#   --check               only check (the default when no action is given)
#   --venv [DIR]          create/update a Python venv (default: <repo>/.venv)
#                         with --system-site-packages, so distro PyQt6 is used
#                         when complete; missing Python packages come from pip
#   --pip-qt              with --venv: always take PyQt6/PyQt6-WebEngine from pip
#   --native              build the Linux native libraries
#                         (src/NativeImpl/scripts/linux/build_native.sh)
#   --ocr-model           with --native: also download the built-in OCR model
#   --winbins MODE        get the Windows-side binaries (src/files/LunaHook):
#                           build     compile them with msvc-wine
#                                     (build_winbins.sh; needs ~/opt/msvc)
#                           download  fetch a release archive ($LUNA_WINBINS_URL)
#                           auto      build if msvc-wine exists, else download
#   --install-desktop     install the launcher ~/.local/bin/lunatranslator, a
#                         menu entry and an icon for the current user
#   --uninstall-desktop   remove them again
#   --all                 --venv --native --winbins auto --install-desktop
#
# Environment:
#   LUNA_WINBINS_URL      archive with LunaHook{32,64}.dll, LunaHost{32,64}.dll,
#                         LunaBridge{32,64}.exe (top level or in LunaHook/);
#                         default: the latest release asset of the fork, see
#                         WINBINS_URL below. A "<url>.sha256" next to it is
#                         checked when present; LUNA_WINBINS_SHA256 forces a sum.
#   MSVC_WINE_DIR         msvc-wine installation (default ~/opt/msvc)
#   XDG_DATA_HOME, HOME   where --install-desktop writes
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../.." && pwd)"   # src
REPO="$(cd "$SRC/.." && pwd)"
NATIVE_SCRIPTS="$SRC/NativeImpl/scripts/linux"

# Release asset with the prebuilt Windows-side binaries. Publish it with:
#   tar -C src/files -czf lunatranslator-winbins.tar.gz LunaHook
#   sha256sum lunatranslator-winbins.tar.gz > lunatranslator-winbins.tar.gz.sha256
WINBINS_URL="${LUNA_WINBINS_URL:-https://github.com/Igrise12/LunaTranslatorLinux/releases/latest/download/lunatranslator-winbins.tar.gz}"
WINBINS_FILES="LunaHook32.dll LunaHook64.dll LunaHost32.dll LunaHost64.dll LunaBridge32.exe LunaBridge64.exe"
MSVC_WINE_DIR="${MSVC_WINE_DIR:-$HOME/opt/msvc}"

DO_VENV=0
VENV="$REPO/.venv"
PIP_QT=0
DO_NATIVE=0
OCR_MODEL=0
WINBINS=""
DO_DESKTOP=0
UNDO_DESKTOP=0

while [ $# -gt 0 ]; do
    case "$1" in
        --check) ;;
        --venv)
            DO_VENV=1
            if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then VENV="$2"; shift; fi
            ;;
        --pip-qt) PIP_QT=1 ;;
        --native) DO_NATIVE=1 ;;
        --ocr-model) OCR_MODEL=1 ;;
        --winbins)
            [ $# -gt 1 ] || { echo "--winbins needs build|download|auto" >&2; exit 2; }
            WINBINS="$2"; shift
            case "$WINBINS" in build|download|auto) ;; *) echo "bad --winbins mode: $WINBINS" >&2; exit 2 ;; esac
            ;;
        --install-desktop) DO_DESKTOP=1 ;;
        --uninstall-desktop) UNDO_DESKTOP=1 ;;
        --all) DO_VENV=1; DO_NATIVE=1; WINBINS=${WINBINS:-auto}; DO_DESKTOP=1 ;;
        -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
case "$VENV" in /*) ;; *) VENV="$PWD/$VENV" ;; esac

if [ -t 1 ]; then B=$'\e[1m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'; else B=; R=; G=; Y=; N=; fi
say() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok() { printf '  %sok%s       %s\n' "$G" "$N" "$*"; }
miss() { printf '  %smissing%s  %s\n' "$R" "$N" "$*"; }
opt() { printf '  %soptional%s %s\n' "$Y" "$N" "$*"; }
die() { echo "${R}error:${N} $*" >&2; exit 1; }

# --- distribution ---------------------------------------------------------------
FAMILY=unknown
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    for id in ${ID:-} ${ID_LIKE:-}; do
        case "$id" in
            arch|archlinux|cachyos|manjaro|endeavouros|garuda) FAMILY=arch; break ;;
            debian|ubuntu|linuxmint|pop|elementary|zorin|neon) FAMILY=debian; break ;;
            fedora|rhel|centos|rocky|almalinux|nobara|ultramarine) FAMILY=fedora; break ;;
            opensuse*|suse|sles) FAMILY=suse; break ;;
        esac
    done
fi
case "$FAMILY" in
    arch) INSTALL="sudo pacman -S --needed" ;;
    debian) INSTALL="sudo apt install" ;;
    fedora) INSTALL="sudo dnf install" ;;
    suse) INSTALL="sudo zypper install" ;;
    *) INSTALL="" ;;
esac

# package names: pkg_<key>="arch|debian|fedora|suse" ("-" = not packaged)
pkg_cmake="cmake|cmake|cmake|cmake"
pkg_ninja="ninja|ninja-build|ninja-build|ninja"
pkg_cxx="gcc|build-essential|gcc-c++|gcc-c++"
pkg_git="git|git|git|git"
pkg_protoc="protobuf|protobuf-compiler|protobuf-compiler|protobuf-devel"
pkg_protodev="protobuf|libprotobuf-dev|protobuf-devel|protobuf-devel"
pkg_opencv="opencv|libopencv-dev|opencv-devel|opencv-devel"
pkg_curl="curl|curl|curl|curl"
pkg_python="python|python3 python3-venv|python3|python3"
pkg_pyqt6="python-pyqt6 python-pyqt6-webengine qt6-multimedia qt6-svg|python3-pyqt6 python3-pyqt6.qtwebengine python3-pyqt6.qtmultimedia python3-pyqt6.qtsvg|python3-pyqt6 python3-pyqt6-webengine|python3-PyQt6 python3-PyQt6-WebEngine"
pkg_xlib="python-xlib|python3-xlib|python3-xlib|python3-python-xlib"
pkg_pefile="python-pefile|python3-pefile|python3-pefile|python3-pefile"
pkg_fonttools="python-fonttools|python3-fonttools|python3-fonttools|python3-FontTools"
pkg_tinycss2="python-tinycss2|python3-tinycss2|python3-tinycss2|python3-tinycss2"
pkg_libcurl="curl|libcurl4|libcurl|libcurl4"
pkg_xcbcursor="xcb-util-cursor|libxcb-cursor0|xcb-util-cursor|libxcb-cursor0"
pkg_ffmpeg="ffmpeg|ffmpeg|ffmpeg-free|ffmpeg"
pkg_pacat="libpulse|pulseaudio-utils|pulseaudio-utils|pulseaudio-utils"
pkg_xdg="xdg-utils|xdg-utils|xdg-utils|xdg-utils"
pkg_wine="wine|wine|wine|wine"
pkg_wlclip="wl-clipboard|wl-clipboard|wl-clipboard|wl-clipboard"
pkg_grim="grim|grim|grim|grim"
pkg_espeak="espeak-ng|espeak-ng|espeak-ng|espeak-ng"
pkg_umu="-|-|-|-"
pkg_mecab="-|mecab-ipadic-utf8|mecab-ipadic|-"
pkg_tesseract="tesseract tesseract-data-jpn|tesseract-ocr tesseract-ocr-jpn|tesseract tesseract-langpack-jpn|tesseract-ocr tesseract-ocr-traineddata-jpn"

# Ubuntu 22.04 and older Debian releases have no PyQt6 packages
NO_DISTRO_QT=0
if [ "$FAMILY" = debian ] && command -v apt-cache >/dev/null 2>&1 && ! apt-cache show python3-pyqt6.qtwebengine >/dev/null 2>&1; then
    IFS='|' read -r _a _b _c _d <<<"$pkg_pyqt6"
    pkg_pyqt6="$_a|-|$_c|$_d"
    NO_DISTRO_QT=1
fi

pkgs_for() { # key -> package names for this distro (empty if unknown)
    local v="pkg_$1" i
    case "$FAMILY" in arch) i=1 ;; debian) i=2 ;; fedora) i=3 ;; suse) i=4 ;; *) return ;; esac
    v="$(printf '%s' "${!v}" | cut -d'|' -f"$i")"
    [ "$v" = "-" ] || printf '%s' "$v"
}

MISSING_REQ=()
MISSING_OPT=()
NOTES=()
need() { # key description
    local p
    p="$(pkgs_for "$1")"
    miss "$2${p:+  ($p)}"
    [ -n "$p" ] && MISSING_REQ+=($p)
}
want() { # key description
    local p
    p="$(pkgs_for "$1")"
    opt "$2${p:+  ($p)}"
    [ -n "$p" ] && MISSING_OPT+=($p)
}
have() { command -v "$1" >/dev/null 2>&1; }
any_exists() { # glob patterns...
    local f
    for f in "$@"; do [ -e "$f" ] && return 0; done
    return 1
}
have_lib() { # soname
    local cache
    cache="$( (ldconfig -p || /sbin/ldconfig -p) 2>/dev/null)"
    case "$cache" in *"$1 "*) return 0 ;; esac
    any_exists /usr/lib/"$1" /usr/lib64/"$1" /usr/lib/*-linux-gnu/"$1" /lib/*-linux-gnu/"$1"
}

# --- checks ------------------------------------------------------------------------------
check_build() {
    say "build dependencies (native libraries)"
    have cmake && ok cmake || need cmake cmake
    have ninja && ok ninja || need ninja ninja
    { have g++ || have c++; } && ok "C++ compiler" || need cxx "gcc/g++"
    have git && ok git || need git "git (CMake fetches sources)"
    have protoc && ok "protoc" || need protoc "protobuf compiler"
    if any_exists /usr/include/google/protobuf/message_lite.h /usr/local/include/google/protobuf/message_lite.h; then
        ok "protobuf headers"
    else
        need protodev "protobuf development files"
    fi
    if any_exists /usr/lib*/cmake/opencv[45]/OpenCVConfig.cmake /usr/lib/*/cmake/opencv[45]/OpenCVConfig.cmake \
        /usr/share/opencv[45]/OpenCVConfig.cmake /usr/local/lib*/cmake/opencv[45]/OpenCVConfig.cmake \
        || pkg-config --exists opencv4 2>/dev/null; then
        ok "OpenCV development files"
    else
        need opencv "OpenCV development files"
    fi
    have curl && ok curl || need curl curl
}

PY=""
pick_python() {
    if [ -x "$VENV/bin/python3" ]; then PY="$VENV/bin/python3"; else PY="$(command -v python3 || true)"; fi
}

# module -> pip name, checked with the chosen interpreter
PYMODS="PyQt6.QtWidgets:PyQt6 PyQt6.QtWebEngineWidgets:PyQt6-WebEngine PyQt6.QtMultimedia:PyQt6 PyQt6.QtDBus:PyQt6 PyQt6.QtSvg:PyQt6 Xlib:python-xlib pefile:pefile fontTools:fonttools tinycss2:tinycss2"
missing_pymods() { # prints missing "module:pipname" entries
    "$PY" - "${1:-list}" $PYMODS <<'EOF' 2>/dev/null
import importlib, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
explain = sys.argv[1] == "explain"
for item in sys.argv[2:]:
    mod = item.split(":")[0]
    try:
        importlib.import_module(mod)
    except Exception as e:
        print("{}  ({}: {})".format(mod, type(e).__name__, e) if explain else item)
EOF
}

check_runtime() {
    say "runtime dependencies"
    pick_python
    if [ -z "$PY" ]; then
        need python "python3 >= 3.10"
        need pyqt6 "PyQt6 with QtWebEngine, QtMultimedia, QtDBus, QtSvg"
        [ "$NO_DISTRO_QT" = 1 ] && NOTES+=("this distribution has no PyQt6 packages: install python3-venv and libxcb-cursor0, then run $0 --venv --pip-qt")
        need xlib "python-xlib"
        need pefile "pefile"
        need fonttools "fontTools"
        need tinycss2 "tinycss2"
    elif "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        ok "python $("$PY" -c 'import platform; print(platform.python_version())') ($PY)"
        local m mod key
        local miss_list
        miss_list="$(missing_pymods)"
        for m in $PYMODS; do
            mod="${m%%:*}"
            if printf '%s\n' "$miss_list" | grep -qx "$m"; then
                case "$mod" in
                    PyQt6.*) key=pyqt6 ;;
                    Xlib) key=xlib ;;
                    pefile) key=pefile ;;
                    fontTools) key=fonttools ;;
                    tinycss2) key=tinycss2 ;;
                esac
                need "$key" "python module $mod"
            else
                ok "python module $mod"
            fi
        done
        if [ "$NO_DISTRO_QT" = 1 ] && printf '%s\n' "$miss_list" | grep -q '^PyQt6'; then
            NOTES+=("this distribution has no PyQt6 packages: install python3-venv and libxcb-cursor0, then run $0 --venv --pip-qt")
        elif [ -n "$miss_list" ]; then
            NOTES+=("Python packages can also come from pip: $0 --venv (add --pip-qt for PyQt6 from pip)")
        fi
    else
        need python "python3 >= 3.10 (found $("$PY" -V 2>&1))"
        [ "$FAMILY" = suse ] && NOTES+=("openSUSE Leap: install python311 and run this script with it on PATH as python3, or use the AppImage")
    fi
    have_lib libcurl.so.4 && ok "libcurl" || need libcurl "libcurl (network requests)"
    have_lib libxcb-cursor.so.0 && ok "libxcb-cursor (Qt xcb platform plugin)" || need xcbcursor "libxcb-cursor (Qt xcb platform plugin)"
    { have ffmpeg && have ffprobe; } && ok "ffmpeg/ffprobe" || need ffmpeg "ffmpeg + ffprobe (audio)"
    if have pacat || have pw-cat; then ok "pacat or pw-cat (audio playback)"; else need pacat "pacat or pw-cat (audio playback)"; fi
    have parec && ok "parec (audio recording)" || want pacat "parec (recording, speech recognition input)"
    have xdg-open && ok "xdg-open" || need xdg "xdg-utils"
    have wine && ok "wine ($(wine --version 2>/dev/null))" || need wine "wine (text hooking of Windows games)"
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
        have wl-copy && ok "wl-clipboard" || need wlclip "wl-clipboard (Wayland clipboard)"
        case "${XDG_CURRENT_DESKTOP:-}" in
            *GNOME*|*KDE*) have grim && ok "grim" || opt "grim (only used on wlroots compositors; the screenshot portal is used here)" ;;
            *) have grim && ok "grim (screenshots on wlroots compositors)" || need grim "grim (screenshots on wlroots compositors)" ;;
        esac
    else
        have wl-copy && ok "wl-clipboard" || want wlclip "wl-clipboard (only on Wayland)"
        have grim && ok "grim" || want grim "grim (only on wlroots Wayland compositors)"
    fi
    say "optional"
    have espeak-ng && ok "espeak-ng (default offline TTS)" || want espeak "espeak-ng (offline text to speech)"
    have umu-run && ok "umu-run (Proton outside Steam)" || {
        want umu "umu-run (start games with Proton outside Steam)"
        [ -z "$(pkgs_for umu)" ] && NOTES+=("umu-run: https://github.com/Open-Wine-Components/umu-launcher/releases (Arch: AUR umu-launcher)")
    }
    # the MeCab library is built into libNativeUtils.so; only a dictionary is needed
    if any_exists /usr/lib/mecab/dic/* /usr/lib64/mecab/dic/* /usr/share/mecab/dic/* /var/lib/mecab/dic/*; then
        ok "MeCab dictionary"
    else
        want mecab "MeCab dictionary (Japanese word segmentation; a UniDic folder can also be chosen in the app)"
        [ "$FAMILY" = arch ] && NOTES+=("MeCab dictionaries on Arch are in the AUR (mecab-ipadic)")
    fi
    have tesseract && ok "tesseract (OCR engine)" || want tesseract "tesseract (alternative OCR engine)"
}

check_project() {
    say "project files"
    if [ -f "$SRC/files/DLL64/libNativeUtils.so" ]; then
        ok "native libraries (src/files/DLL64)"
    else
        miss "native libraries: run $0 --native"
        NOTES+=("build the native libraries: $0 --native")
    fi
    local f m=""
    for f in $WINBINS_FILES; do [ -f "$SRC/files/LunaHook/$f" ] || m="$m $f"; done
    if [ -z "$m" ]; then
        ok "Windows-side hook binaries (src/files/LunaHook)"
    else
        miss "Windows-side hook binaries:$m"
        NOTES+=("get the hook binaries: $0 --winbins auto  (build with msvc-wine if $MSVC_WINE_DIR exists, else download)")
    fi
    if [ -x "$VENV/bin/python3" ]; then ok "venv $VENV"; fi
}

report() {
    # unique package names, required first; extras exclude required ones
    local -A seen=()
    local req=() extra=() x
    for x in "${MISSING_REQ[@]}"; do [ -n "${seen[$x]:-}" ] || { seen[$x]=1; req+=("$x"); }; done
    for x in "${MISSING_OPT[@]}"; do [ -n "${seen[$x]:-}" ] || { seen[$x]=1; extra+=("$x"); }; done
    say "summary"
    if [ ${#req[@]} -eq 0 ]; then
        echo "  all required system packages are present"
    else
        echo "  install the missing packages with:"
        if [ -n "$INSTALL" ]; then
            echo "    $INSTALL ${req[*]}"
        else
            echo "    (unknown distribution: install the items marked missing above)"
        fi
    fi
    if [ ${#extra[@]} -gt 0 ] && [ -n "$INSTALL" ]; then
        echo "  optional extras:"
        echo "    $INSTALL ${extra[*]}"
    fi
    case "$FAMILY" in
        debian) echo "  (Debian/Ubuntu: wine needs 'sudo dpkg --add-architecture i386' for 32-bit games; ffmpeg/grim/wl-clipboard are in universe on Ubuntu)" ;;
        fedora) echo "  (Fedora: ffmpeg-free lacks some codecs; RPM Fusion's ffmpeg also works)" ;;
    esac
    local n
    for n in "${NOTES[@]}"; do echo "  - $n"; done
}

# --- actions --------------------------------------------------------------------------------
make_venv() {
    say "venv $VENV"
    local base
    base="$(command -v python3)" || die "python3 not found"
    "$base" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "python3 >= 3.10 required"
    if [ ! -x "$VENV/bin/python3" ]; then
        "$base" -m venv --system-site-packages "$VENV" || die "cannot create the venv (Debian/Ubuntu: install python3-venv)"
    fi
    PY="$VENV/bin/python3"
    "$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade >/dev/null || die "pip unavailable in the venv"
    local miss_list pipnames=() m
    miss_list="$(missing_pymods)"
    if [ "$PIP_QT" = 1 ] || printf '%s\n' "$miss_list" | grep -q '^PyQt6'; then
        # the distro PyQt6 is incomplete: take the whole Qt stack from pip
        # (mixing a pip PyQt6-WebEngine with a distro PyQt6 does not work).
        # --ignore-installed: otherwise pip counts the distro's PyQt6-sip,
        # visible through --system-site-packages, as already satisfied
        local qt=(PyQt6 PyQt6-sip PyQt6-Qt6 PyQt6-WebEngine PyQt6-WebEngine-Qt6)
        echo "pip install --ignore-installed ${qt[*]}"
        "$PY" -m pip install --disable-pip-version-check --upgrade --ignore-installed "${qt[@]}" ||
            die "pip install failed"
    fi
    for m in $miss_list; do
        case "$m" in PyQt6*) ;; *) pipnames+=("${m#*:}") ;; esac
    done
    if [ ${#pipnames[@]} -gt 0 ]; then
        echo "pip install ${pipnames[*]}"
        "$PY" -m pip install --disable-pip-version-check --upgrade "${pipnames[@]}" || die "pip install failed"
    elif [ "$PIP_QT" = 0 ]; then
        echo "all Python packages come from the system"
    fi
    miss_list="$(missing_pymods)"
    if [ -n "$miss_list" ]; then
        echo "these modules do not import in the venv:" >&2
        missing_pymods explain | sed 's/^/  /' >&2
        die "a module failing with a missing lib*.so needs that system library (install it, then run $0 --venv again)"
    fi
}

WB_TMP=""
trap '[ -n "$WB_TMP" ] && rm -rf "$WB_TMP"' EXIT

# download_winbins: fetch, verify and install the archive; on failure prints
# why and returns 1 (installs nothing unless every required file is present)
download_winbins() {
    err() { echo "error: $*" >&2; return 1; }
    have curl || { err "curl needed"; return 1; }
    WB_TMP="$(mktemp -d)"
    local tmp="$WB_TMP"
    echo "downloading $WINBINS_URL"
    curl -fL --retry 3 -o "$tmp/winbins.tar.gz" "$WINBINS_URL" || {
        err "download failed. Set LUNA_WINBINS_URL to an archive of src/files/LunaHook, or build with msvc-wine (--winbins build)"
        return 1
    }
    local want="${LUNA_WINBINS_SHA256:-}"
    if [ -z "$want" ] && curl -fsL -o "$tmp/sum" "$WINBINS_URL.sha256" 2>/dev/null; then
        want="$(cut -d' ' -f1 < "$tmp/sum")"
    fi
    if [ -n "$want" ]; then
        [ "$(sha256sum "$tmp/winbins.tar.gz" | cut -d' ' -f1)" = "$want" ] || { err "checksum mismatch for $WINBINS_URL"; return 1; }
        echo "checksum ok"
    else
        echo "warning: no checksum published for the archive" >&2
    fi
    mkdir -p "$tmp/x"
    case "$WINBINS_URL" in
        *.zip) (cd "$tmp/x" && python3 -m zipfile -e "$tmp/winbins.tar.gz" .) ;;
        *) tar -xf "$tmp/winbins.tar.gz" -C "$tmp/x" ;;
    esac || { err "cannot unpack the archive"; return 1; }
    local f p found=()
    for f in $WINBINS_FILES LunaTmpFontLoader.dll; do
        p="$(find "$tmp/x" -type f -name "$f" | head -1)"
        if [ -n "$p" ]; then
            found+=("$p")
        elif [ "$f" != LunaTmpFontLoader.dll ]; then
            err "$f not in the archive"
            return 1
        fi
    done
    mkdir -p "$SRC/files/LunaHook"
    for p in "${found[@]}"; do
        install -m 644 "$p" "$SRC/files/LunaHook/${p##*/}" && echo "installed ${p##*/}"
    done
    rm -rf "$tmp"
    WB_TMP=""
}

get_winbins() {
    local mode="$1"
    local soft=0
    if [ "$mode" = auto ]; then
        soft=1
        if [ -x "$MSVC_WINE_DIR/bin/x64/cl" ]; then mode=build; else mode=download; fi
    fi
    say "Windows-side binaries ($mode)"
    if [ "$mode" = build ]; then
        "$NATIVE_SCRIPTS/build_winbins.sh" && return 0
        [ "$soft" = 1 ] || die "build_winbins.sh failed"
        echo "warning: build_winbins.sh failed; continuing without the Windows-side binaries" >&2
        return 0
    fi
    download_winbins && return 0
    [ "$soft" = 1 ] || exit 1
    echo "warning: continuing without the Windows-side binaries (text hooking will not work until they are installed)" >&2
}

BIN_DIR="$HOME/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
LAUNCHER="$BIN_DIR/lunatranslator"
# same ID as the AppImage's entry and the app's desktopFileName
APP_ID="io.github.Igrise12.LunaTranslatorLinux"
DESKTOP="$DATA_DIR/applications/$APP_ID.desktop"
ICON="$DATA_DIR/icons/hicolor/256x256/apps/$APP_ID.png"
# names used before
OLD_FILES=("$DATA_DIR/applications/lunatranslator.desktop" "$DATA_DIR/icons/hicolor/256x256/apps/lunatranslator.png")

shq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

install_desktop() {
    say "desktop integration"
    mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP")" "$(dirname "$ICON")" || die "cannot create folders"
    cat > "$LAUNCHER.tmp" <<EOF
#!/bin/sh
# LunaTranslator launcher, written by $SRC/scripts/linux/setup.sh
SRC=$(shq "$SRC")
VENV=$(shq "$VENV")
if [ -x "\$VENV/bin/python3" ]; then PY="\$VENV/bin/python3"; else PY=python3; fi
exec "\$PY" -B "\$SRC/LunaTranslator/main.py" "\$@"
EOF
    chmod 755 "$LAUNCHER.tmp" && mv "$LAUNCHER.tmp" "$LAUNCHER"
    echo "launcher  $LAUNCHER"

    local ico="$SRC/files/luna.ico"
    if [ ! -s "$ico" ]; then
        curl -fsSL -o "$ico" https://image.lunatranslator.org/luna.ico || echo "warning: could not fetch the icon" >&2
    fi
    if [ -s "$ico" ]; then
        python3 - "$ico" "$ICON" <<'EOF' && echo "icon      $ICON"
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
    fi
    # Exec with an absolute path: ~/.local/bin is not on every desktop's PATH
    local exec_path="$LAUNCHER"
    case "$exec_path" in *[[:space:]\"\'\\\$\`]*) exec_path="\"$(printf '%s' "$exec_path" | sed 's/[\\"`$]/\\&/g')\"" ;; esac
    sed -e "s|^Exec=.*|Exec=$exec_path|" \
        "$SRC/files/linux/$APP_ID.desktop" > "$DESKTOP.tmp" &&
        mv "$DESKTOP.tmp" "$DESKTOP"
    echo "menu      $DESKTOP"
    have update-desktop-database && update-desktop-database "$(dirname "$DESKTOP")" >/dev/null 2>&1
    case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "note: $BIN_DIR is not on PATH; the menu entry works anyway" ;; esac
    return 0
}

uninstall_desktop() {
    say "removing desktop integration"
    local f
    for f in "$LAUNCHER" "$DESKTOP" "$ICON" "${OLD_FILES[@]}"; do
        [ -e "$f" ] && rm -f "$f" && echo "removed $f"
    done
    have update-desktop-database && update-desktop-database "$(dirname "$DESKTOP")" >/dev/null 2>&1
    return 0
}

# --- main ------------------------------------------------------------------------------------
echo "${B}LunaTranslator Linux setup${N}  (source: $SRC, distribution: ${PRETTY_NAME:-unknown}, family: $FAMILY)"

[ "$UNDO_DESKTOP" = 1 ] && uninstall_desktop
[ "$DO_VENV" = 1 ] && make_venv
if [ "$DO_NATIVE" = 1 ]; then
    say "native libraries"
    args=()
    [ "$OCR_MODEL" = 1 ] && args+=(--ocr-model)
    "$NATIVE_SCRIPTS/build_native.sh" "${args[@]}" || die "build_native.sh failed (check the build dependencies below)"
fi
[ -n "$WINBINS" ] && get_winbins "$WINBINS"
[ "$DO_DESKTOP" = 1 ] && install_desktop

check_build
check_runtime
check_project
report
[ ${#MISSING_REQ[@]} -eq 0 ]
