"""Finding game executables on Linux, for the game manager's batch add.

* scan_folder(dir): every .exe below dir, minus installers, uninstallers,
  redistributables, crash handlers and the like;
* scan_steam(): Windows games installed in Steam libraries (every library
  listed in libraryfolders.vdf; titles from the appmanifest files);
* scan_prefixes(): programs installed in Wine prefixes (~/.wine, Lutris,
  Bottles, Heroic, umu, ...) and the executables configured in Lutris.

patch_common() replaces gui.gamemanager.common.addgamebatch: its menu
entry ("batch add") then offers a folder, the Steam libraries or the Wine
prefixes; Steam and prefix results are shown in a checklist first.
"""

import os
import re

# executables that are never the game
_JUNK_EXE = re.compile(
    r"^(unins\d*|uninst.*|uninstall.*|setup.*|.*installer.*|install|"
    r"vc_?redist.*|vcredist.*|dxsetup|dxwebsetup|directx.*|dotnet.*|ndp\d.*|"
    r"oalinst|physx.*|.*prereq.*|.*crashhandler.*|.*crash_?report.*|"
    r"crashpad_handler|.*errorreporter.*|.*bugreport.*|easyanticheat.*|"
    r"start_protected_game|battleye.*|be(service|launcher).*|.*updater.*|"
    r"update|notification_helper|.*subprocess.*|cefsharp.*|zfgamebrowser|"
    # helper/error/browser only as a separate word, so Helper.exe,
    # Terror.exe or "Browser Game.exe" stay
    r".*patcher.*|(.*[ _.-])?service(32|64)?|(.*[ _.-])helper(32|64)?|crashmailer.*|"
    r"(.*[ _.-])?error_?report(er)?(32|64)?|(.*[ _.-])?(cef|web)browser.*|"
    r".*anticheat.*|dxdiag|winedbg|explorer|regedit|notepad|cmd|7z.*|"
    r"unitycrashhandler(32|64)?)"
    r"\.exe$",
    re.I,
)
# folders that only hold redistributables / runtimes
_JUNK_DIR = re.compile(
    r"^(_?commonredist|redist|redistributables?|_?redist|__installer|installer|"
    r"directx|vcredist|dotnet|prerequisites?|support|easyanticheat|battleye|"
    r"monobleedingedge|jre|java|crashpad|thirdparty|errorreporting|\$recycle\.bin|"
    r"steamworks shared|\.wine|windows)$",
    re.I,
)
_STEAM_TOOLS = re.compile(r"^(proton|steamlinuxruntime|steamworks shared|steam controller configs)", re.I)
MAX_DEPTH = 4


def is_junk_exe(name):
    return bool(_JUNK_EXE.match(os.path.basename(name)))


def _walk_exes(root, max_depth=MAX_DEPTH):
    root = os.path.normpath(root)
    base = root.count(os.sep)
    for d, dirs, files in os.walk(root):
        depth = d.count(os.sep) - base
        dirs[:] = [x for x in dirs if not _JUNK_DIR.match(x) and not x.endswith("_Data")] if depth < max_depth else []
        for f in files:
            if f.lower().endswith(".exe") and not is_junk_exe(f):
                yield os.path.join(d, f)


def scan_folder(folder):
    """All plausible game executables below folder (no depth limit beyond
    MAX_DEPTH * 2, since the user chose this folder)."""
    return sorted(_walk_exes(folder, MAX_DEPTH * 2))


# ---- Steam -------------------------------------------------------------------

def steam_roots():
    home = os.path.expanduser("~")
    cands = [
        os.path.join(home, ".local/share/Steam"),
        os.path.join(home, ".steam/steam"),
        os.path.join(home, ".steam/root"),
        os.path.join(home, ".var/app/com.valvesoftware.Steam/.local/share/Steam"),
        os.path.join(home, "snap/steam/common/.local/share/Steam"),
    ]
    out = []
    for c in cands:
        if os.path.isdir(os.path.join(c, "steamapps")):
            r = os.path.realpath(c)
            if r not in out:
                out.append(r)
    return out


def _vdf_values(path, key):
    """All string values of `key` (any depth, case-insensitive), parsed with
    the launchers' VDF parser so escapes such as \\" are decoded."""
    from lunaplat.linux.launchers import parse_vdf

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = parse_vdf(f.read())
    except (OSError, ValueError):
        return []
    out = []
    low = key.lower()

    def walk(d):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v)
            elif k.lower() == low and isinstance(v, str):
                out.append(v)

    walk(tree)
    return out


def steam_libraries():
    libs = []
    for root in steam_roots():
        cands = [root]
        for vdf in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
            cands += _vdf_values(os.path.join(root, vdf), "path")
        for c in cands:
            sa = os.path.join(c, "steamapps")
            if os.path.isdir(sa):
                r = os.path.realpath(c)
                if r not in libs:
                    libs.append(r)
    return libs


def scan_steam():
    """[(title, exe path)] of Windows games in all Steam libraries."""
    out = []
    for lib in steam_libraries():
        sa = os.path.join(lib, "steamapps")
        named = {}
        try:
            names = os.listdir(sa)
        except OSError:
            continue
        for n in names:
            if n.startswith("appmanifest_") and n.endswith(".acf"):
                p = os.path.join(sa, n)
                inst = _vdf_values(p, "installdir")
                title = _vdf_values(p, "name")
                if inst:
                    named[inst[0]] = title[0] if title else inst[0]
        common = os.path.join(sa, "common")
        try:
            dirs = sorted(os.listdir(common))
        except OSError:
            continue
        for d in dirs:
            if _STEAM_TOOLS.match(d):
                continue
            gdir = os.path.join(common, d)
            if not os.path.isdir(gdir):
                continue
            title = named.get(d, d)
            for exe in _walk_exes(gdir):
                out.append((title, exe))
    return out


# ---- Wine prefixes -----------------------------------------------------------

def _is_prefix(d):
    return os.path.isdir(os.path.join(d, "drive_c")) and os.path.isfile(os.path.join(d, "system.reg"))


def wine_prefixes():
    home = os.path.expanduser("~")
    cands = [os.path.join(home, ".wine")]
    if os.environ.get("WINEPREFIX"):
        cands.append(os.environ["WINEPREFIX"])
    parents = [
        os.path.join(home, ".local/share/wineprefixes"),
        os.path.join(home, ".local/share/bottles/bottles"),
        os.path.join(home, ".var/app/com.usebottles.bottles/data/bottles/bottles"),
        os.path.join(home, "Games"),
        os.path.join(home, "Games/Heroic/Prefixes"),
        os.path.join(home, "Games/Heroic/Prefixes/default"),
        os.path.join(home, "Games/umu"),
        os.path.join(home, ".local/share/lutris/prefixes"),
    ]
    for p in parents:
        try:
            for n in os.listdir(p):
                cands.append(os.path.join(p, n))
        except OSError:
            pass
    cands += _lutris_values("prefix")
    out = []
    for c in cands:
        c = os.path.expanduser(c)
        for d in (c, os.path.join(c, "pfx")):
            if _is_prefix(d):
                r = os.path.realpath(d)
                if r not in out:
                    out.append(r)
                break
    return out


def _lutris_files():
    """Game configs of every Lutris layout (0.5.18+ data dir, older config
    dir, Flatpak)."""
    from lunaplat.linux.launchers import lutris_config_dirs

    out = []
    for d in lutris_config_dirs():
        try:
            out += [os.path.join(d, n) for n in os.listdir(d) if n.endswith((".yml", ".yaml"))]
        except OSError:
            continue
    return out


def _lutris_values(key, path=None):
    out = []
    for p in [path] if path else _lutris_files():
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(r"\s*{}:\s*(.+?)\s*$".format(key), line)
                    if m:
                        out.append(m.group(1).strip("'\""))
        except OSError:
            pass
    return out


_PREFIX_SKIP = {"windows", "users", "programdata"}
_PROGRAMFILES_SKIP = re.compile(
    r"^(common files|internet explorer|windows .*|windowspowershell|microsoft\.net|"
    r"microsoft|msbuild|reference assemblies|steam|mono|gecko)$",
    re.I,
)


def scan_prefix(prefix):
    drive_c = os.path.join(prefix, "drive_c")
    out = []
    try:
        tops = os.listdir(drive_c)
    except OSError:
        return out
    for t in sorted(tops):
        full = os.path.join(drive_c, t)
        if t.lower() in _PREFIX_SKIP or not os.path.isdir(full) or os.path.islink(full):
            continue
        if t.lower().startswith("program files"):
            try:
                subs = sorted(os.listdir(full))
            except OSError:
                continue
            for s in subs:
                if _PROGRAMFILES_SKIP.match(s):
                    continue
                for exe in _walk_exes(os.path.join(full, s)):
                    out.append((s, exe))
        else:
            for exe in _walk_exes(full):
                out.append((t, exe))
    return out


def scan_prefixes():
    """[(title, exe path)] of programs in Wine prefixes and Lutris games."""
    out = []
    seen = set()
    for pfx in wine_prefixes():
        for title, exe in scan_prefix(pfx):
            if exe not in seen:
                seen.add(exe)
                out.append((title, exe))
    for yml in _lutris_files():
        for exe in _lutris_values("exe", yml):
            exe = os.path.expanduser(exe)
            if not os.path.isabs(exe):
                pfx = (_lutris_values("prefix", yml) or [""])[0]
                exe = os.path.join(os.path.expanduser(pfx), exe)
            if exe.lower().endswith(".exe") and os.path.isfile(exe) and exe not in seen:
                seen.add(exe)
                name = os.path.splitext(os.path.basename(yml))[0]
                out.append((re.sub(r"-\d+$", "", name), exe))
    return out


# ---- game manager integration ------------------------------------------------

def _pick(parent, title, items):
    """Checklist of (title, path); returns the chosen paths."""
    from qtsymbols import (
        QDialog,
        QDialogButtonBox,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        Qt,
        QVBoxLayout,
    )
    from myutils.config import _TR

    dlg = QDialog(parent)
    dlg.setWindowTitle(_TR(title))
    dlg.resize(760, 520)
    lay = QVBoxLayout(dlg)
    flt = QLineEdit(dlg)
    flt.setPlaceholderText(_TR("Filter"))
    lay.addWidget(flt)
    lst = QListWidget(dlg)
    lay.addWidget(lst)
    for name, path in items:
        it = QListWidgetItem("{}  —  {}".format(name, path))
        it.setToolTip(path)
        it.setData(Qt.ItemDataRole.UserRole, path)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Unchecked)
        lst.addItem(it)

    def refilter(text):
        text = text.lower()
        for i in range(lst.count()):
            it = lst.item(i)
            it.setHidden(bool(text) and text not in it.text().lower())

    flt.textChanged.connect(refilter)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
    allbtn = QPushButton(_TR("Select shown"), dlg)
    buttons.addButton(allbtn, QDialogButtonBox.ButtonRole.ActionRole)

    def select_shown():
        for i in range(lst.count()):
            it = lst.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.CheckState.Checked)

    allbtn.clicked.connect(select_shown)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if not dlg.exec():
        return []
    out = []
    for i in range(lst.count()):
        it = lst.item(i)
        if it.checkState() == Qt.CheckState.Checked:
            out.append(it.data(Qt.ItemDataRole.UserRole))
    return out


def _busy(fn):
    from qtsymbols import QApplication, QCursor, Qt

    QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    try:
        return fn()
    finally:
        QApplication.restoreOverrideCursor()


def patch_common(mod):
    def addgamebatch(callback, targetlist):
        from qtsymbols import QApplication, QCursor, QFileDialog, QMenu
        from myutils.config import _TR

        parent = QApplication.activeWindow()
        menu = QMenu(parent)
        a_dir = menu.addAction(_TR("Choose a folder..."))
        a_steam = menu.addAction(_TR("Games in Steam libraries..."))
        a_wine = menu.addAction(_TR("Programs in Wine prefixes..."))
        action = menu.exec(QCursor.pos())
        if action is None:
            return
        if action == a_dir:
            res = QFileDialog.getExistingDirectory(options=QFileDialog.Option.DontResolveSymlinks)
            if not res:
                return
            paths = _busy(lambda: scan_folder(res))
        else:
            steam = action == a_steam
            items = _busy(scan_steam if steam else scan_prefixes)
            if not items:
                from qtsymbols import QMessageBox

                QMessageBox.information(
                    parent,
                    _TR("Batch add"),
                    _TR("No Steam libraries with Windows games found.")
                    if steam
                    else _TR("No programs found in Wine prefixes."),
                )
                return
            paths = _pick(
                parent,
                "Games in Steam libraries" if steam else "Programs in Wine prefixes",
                items,
            )
        if paths:
            mod.addgamebatch_x(callback, targetlist, paths)

    mod.addgamebatch = addgamebatch
