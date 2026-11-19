"""Remove the parts of the PyQt6 wheels LunaTranslator does not use.

    python prune_qt.py <site-packages>

The PyQt6-Qt6 wheel ships every Qt add-on (Quick 3D, Designer, Bluetooth,
...). This keeps the PyQt6 modules the app imports, the plugins it can need
and every library they reach through DT_NEEDED (read with readelf), and
deletes the rest. Libraries Qt loads by name at run time are all plugins,
which are kept per folder below.
"""

import os
import re
import shutil
import subprocess
import sys

# PyQt6 extension modules imported by the app (and by PyQt6 itself for them)
KEEP_MODULES = {
    "QtCore", "QtGui", "QtWidgets", "QtDBus", "QtMultimedia", "QtNetwork",
    "QtSvg", "QtSvgWidgets", "QtWebEngineCore", "QtWebEngineWidgets",
    "QtWebChannel", "QtPrintSupport", "QtOpenGL", "QtOpenGLWidgets",
    "QtQuick", "QtQml", "QtQuickWidgets", "QtPositioning",
}
# plugin folders that are never used by a widgets/WebEngine desktop app
DROP_PLUGINS = {
    "assetimporters", "geometryloaders", "help", "position", "qmllint",
    "qmlls", "renderers", "renderplugins", "sceneparsers", "scxmldatamodel",
    "sensors", "sqldrivers", "webview", "texttospeech", "designer",
    "canbus", "qmltooling",
}


def needed(path):
    try:
        out = subprocess.run(
            ["readelf", "-d", path], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        sys.exit("readelf (binutils) is required")
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out)


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def main(site):
    pyqt = os.path.join(site, "PyQt6")
    qt = os.path.join(pyqt, "Qt6")
    libdir = os.path.join(qt, "lib")
    before = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(pyqt)
        for f in fs
        if not os.path.islink(os.path.join(dp, f))
    )

    roots = []
    for name in os.listdir(pyqt):
        m = re.match(r"(Qt\w+)\.abi3\.so$", name)
        if not m:
            continue
        if m.group(1) in KEEP_MODULES:
            roots.append(os.path.join(pyqt, name))
        else:
            os.remove(os.path.join(pyqt, name))
            for extra in (m.group(1) + ".pyi",):
                p = os.path.join(pyqt, extra)
                if os.path.exists(p):
                    os.remove(p)
    roots += [os.path.join(pyqt, n) for n in os.listdir(pyqt) if n.startswith("sip.")]

    plugins = os.path.join(qt, "plugins")
    for d in sorted(os.listdir(plugins)):
        full = os.path.join(plugins, d)
        if d in DROP_PLUGINS:
            shutil.rmtree(full)
            continue
        for dp, _, fs in os.walk(full):
            roots += [os.path.join(dp, f) for f in fs if is_elf(os.path.join(dp, f))]
    libexec = os.path.join(qt, "libexec")
    if os.path.isdir(libexec):
        roots += [
            os.path.join(libexec, f)
            for f in os.listdir(libexec)
            if is_elf(os.path.join(libexec, f))
        ]

    present = set(os.listdir(libdir))
    keep = set()
    todo = list(roots)
    while todo:
        for lib in needed(todo.pop()):
            if lib in present and lib not in keep:
                keep.add(lib)
                todo.append(os.path.join(libdir, lib))
    # bundled helpers for the kept libraries (xcb-util & co.) are reached the
    # same way; keep their unversioned/real-name aliases too
    for name in sorted(present):
        base = re.sub(r"(\.so\.\d+).*$", r"\1", name)
        if name in keep or base in keep:
            continue
        p = os.path.join(libdir, name)
        if os.path.isdir(p) and not os.path.islink(p):
            # Qt*.framework style folders do not exist on Linux; leave others
            continue
        os.remove(p)

    after = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(pyqt)
        for f in fs
        if not os.path.islink(os.path.join(dp, f))
    )
    print(
        "PyQt6: {:.0f} MB -> {:.0f} MB, {} libraries kept".format(
            before / 1e6, after / 1e6, len(keep)
        )
    )


if __name__ == "__main__":
    main(sys.argv[1])
