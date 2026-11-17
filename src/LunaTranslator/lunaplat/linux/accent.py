"""System accent colour for the Fluent style (gui.fluent).

The Windows build reads the accent from the registry. Here it comes from the
xdg-desktop-portal Settings interface (org.freedesktop.appearance
accent-color, an (ddd) sRGB triple in 0..1; values outside that range mean
"not set"), read with dbus-python or, without it, gdbus. Desktops that do not
publish an accent fall back to the Qt palette's highlight colour.
"""

import re
import shutil
import subprocess

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SETTINGS_IFACE = "org.freedesktop.portal.Settings"
NAMESPACE = "org.freedesktop.appearance"
KEY = "accent-color"

_cache = []


def _valid(rgb):
    return (
        rgb is not None
        and len(rgb) == 3
        and all(0.0 <= float(c) <= 1.0 for c in rgb)
    )


def _read_dbus_python(bus_name, path):
    try:
        import dbus
    except ImportError:
        return None, False
    try:
        bus = dbus.SessionBus()
        obj = bus.get_object(bus_name, path, introspect=False)
        iface = dbus.Interface(obj, SETTINGS_IFACE)
        try:
            value = iface.ReadOne(NAMESPACE, KEY, timeout=2)
        except dbus.DBusException as e:
            name = e.get_dbus_name() or ""
            if name.endswith("UnknownMethod"):  # portal < v2: Read, variant in variant
                value = iface.Read(NAMESPACE, KEY, timeout=2)
            else:
                return None, True
        while isinstance(value, dbus.Struct) and len(value) == 1:
            value = value[0]
        return tuple(float(c) for c in value), True
    except Exception:
        return None, True


_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def parse_gdbus_output(text):
    """'(<(0.2, 0.4, 0.6)>,)' -> (0.2, 0.4, 0.6)"""
    m = re.search(r"\(\s*({0})\s*,\s*({0})\s*,\s*({0})\s*\)".format(_FLOAT), text)
    if not m:
        return None
    return tuple(float(x) for x in m.groups())


def _read_gdbus(bus_name, path):
    if not shutil.which("gdbus"):
        return None
    try:
        r = subprocess.run(
            [
                "gdbus", "call", "--session", "--timeout", "2",
                "--dest", bus_name, "--object-path", path,
                "--method", SETTINGS_IFACE + ".ReadOne", NAMESPACE, KEY,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return parse_gdbus_output(r.stdout.decode("utf-8", "replace"))


def portal_accent_rgb(bus_name=PORTAL_BUS, path=PORTAL_PATH):
    """(r, g, b) floats in 0..1 from the portal, or None."""
    rgb, tried = _read_dbus_python(bus_name, path)
    if rgb is None and not tried:
        rgb = _read_gdbus(bus_name, path)
    return rgb if _valid(rgb) else None


def accent_color():
    """QColor of the system accent; the platform palette's highlight when the
    desktop publishes none. Resolved once (the Fluent style later installs
    its own palette, which must not feed back into this). Never raises: it
    is called from queued event handlers."""
    from qtsymbols import QApplication, QColor

    try:
        if not _cache:
            app = QApplication.instance()
            if app is None:
                return QColor()
            rgb = portal_accent_rgb()
            if rgb is not None:
                _cache.append(QColor.fromRgbF(*rgb).name())
            else:
                from qtsymbols import QPalette

                _cache.append(app.palette().color(QPalette.ColorRole.Highlight).name())
        return QColor(_cache[0])
    except Exception:
        return QColor()


def portal_color_scheme(bus_name=PORTAL_BUS, path=PORTAL_PATH):
    """org.freedesktop.appearance color-scheme: 0 no preference, 1 dark,
    2 light; None when unavailable."""
    try:
        import dbus

        obj = dbus.SessionBus().get_object(bus_name, path, introspect=False)
        value = dbus.Interface(obj, SETTINGS_IFACE).ReadOne(
            NAMESPACE, "color-scheme", timeout=2
        )
        return int(value)
    except Exception:
        return None


def patch(mod):
    """gui.fluent"""
    mod.get_windows_accent_color = accent_color
