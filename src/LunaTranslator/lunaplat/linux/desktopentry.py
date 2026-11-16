"""Desktop integration: URL protocol handler and launcher shortcuts.

Windows registers lunatranslator:// in HKCU and creates .lnk shortcuts.
Here both become freedesktop .desktop entries under
$XDG_DATA_HOME/applications; files are only rewritten when they change.
"""

import os
import re
import subprocess
import sys

SCHEME = "lunatranslator"
URL_ENTRY = "lunatranslator-url.desktop"


def _apps_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "applications")
    os.makedirs(d, exist_ok=True)
    return d


def self_command():
    """argv that starts this app again."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage]
    main = os.path.abspath(os.path.join("LunaTranslator", "main.py"))
    return [sys.executable, main]


def _quote(arg):
    # Desktop Entry spec: quote args containing reserved characters
    if re.search(r'[\s"\'\\><~|&;$*?#()`]', arg) or not arg:
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("%", "%%")


def _exec_line(args, extra=""):
    line = " ".join(_quote(a) for a in args)
    return line + (" " + extra if extra else "")


def _write_if_changed(path, content):
    try:
        with open(path, "r", encoding="utf8") as f:
            if f.read() == content:
                return False
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        f.write(content)
    os.replace(tmp, path)
    return True


def _run_quiet(cmd):
    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def CreateUrlProtocol(_exe=None):
    apps = _apps_dir()
    path = os.path.join(apps, URL_ENTRY)
    icon = os.path.abspath("files/luna.ico")
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=LunaTranslator\n"
        "Exec={}\n"
        "Icon={}\n"
        "NoDisplay=true\n"
        "Terminal=false\n"
        "MimeType=x-scheme-handler/{};\n"
    ).format(
        _exec_line(self_command() + ["--URLProtocol"], "%u"),
        icon if os.path.exists(icon) else "",
        SCHEME,
    )
    if _write_if_changed(path, content):
        _run_quiet(["xdg-mime", "default", URL_ENTRY, "x-scheme-handler/" + SCHEME])
        _run_quiet(["update-desktop-database", apps])


def _png_icon(icon_path):
    """Convert the caller's .ico to a .png next to it (.desktop wants png)."""
    if not icon_path or not os.path.exists(icon_path):
        return ""
    try:
        from qtsymbols import QImage

        img = QImage(icon_path)
        if img.isNull():
            return icon_path
        png = os.path.splitext(icon_path)[0] + ".png"
        img.save(png, "PNG")
        return os.path.abspath(png)
    except Exception:
        return icon_path


def CreateShortcut(name, exe, args, icon):
    """Launcher entry that starts this app with args (e.g. "--Exec <uid>")."""
    import shlex

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "game"
    path = os.path.join(_apps_dir(), "lunatranslator-{}.desktop".format(safe))
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name={}\n"
        "Exec={}\n"
        "Icon={}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
    ).format(
        name.replace("\n", " "),
        _exec_line(self_command() + shlex.split(args or "")),
        _png_icon(icon),
    )
    _write_if_changed(path, content)
    return path
