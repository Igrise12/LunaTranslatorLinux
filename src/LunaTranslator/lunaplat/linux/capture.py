"""Screen capture for OCR and screenshots.

Under (rootless) XWayland, X11 GetImage on the root window or on a client
window returns black, so the backend depends on the session:

* Wayland + wlroots compositor (Hyprland, Sway, ...): `grim`, fast and
  non-interactive; X11 coordinates equal Wayland layout coordinates.
* other Wayland compositors: xdg-desktop-portal Screenshot (whole screen,
  then cropped); slower, may ask for permission once.
* X11 session: QScreen.grabWindow on the root window.

Images are returned as encoded bytes (PPM/PNG), which is what the Win32
NativeUtils helpers returned (callers load them with QPixmap.loadFromData).
"""

import os
import shutil
import subprocess

from qtsymbols import QBuffer, QByteArray, QGuiApplication, QImage, QPoint, QRect

from lunaplat.linux.qtthread import run_in_gui


def _wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY"))


_grim_ok = [None]


def _grim_usable():
    if _grim_ok[0] is None:
        _grim_ok[0] = _wayland() and shutil.which("grim") is not None
    return _grim_ok[0]


def _grim(rect):
    args = ["grim", "-s", "1", "-t", "ppm"]
    if rect is not None:
        args += ["-g", "{},{} {}x{}".format(rect.x(), rect.y(), rect.width(), rect.height())]
    args.append("-")
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout:
        if b"compositor doesn't support" in r.stderr or b"wlr" in r.stderr:
            _grim_ok[0] = False
        return None
    return r.stdout


def _encode(img):
    if img is None or img.isNull():
        return None
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PPM")
    buf.close()
    return bytes(ba)


def virtual_geometry():
    def _get():
        rect = QRect()
        for s in QGuiApplication.screens():
            rect = rect.united(s.geometry())
        return rect

    return run_in_gui(_get, default=QRect())


def _qt_grab(rect):
    def _get():
        if rect is None:
            r = QRect()
            for s in QGuiApplication.screens():
                r = r.united(s.geometry())
        else:
            r = rect
        screen = QGuiApplication.screenAt(r.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return None
        # grabWindow(0) grabs the X root window; coordinates are relative to
        # the screen's origin
        g = screen.geometry()
        pix = screen.grabWindow(0, r.x() - g.x(), r.y() - g.y(), r.width(), r.height())
        return _encode(pix.toImage())

    return run_in_gui(_get)


def _portal_grab(rect):
    from lunaplat.linux.portal import screenshot

    path = screenshot.take()
    if not path:
        return None

    def _crop():
        img = QImage(path)
        try:
            os.remove(path)
        except OSError:
            pass
        if img.isNull():
            return None
        if rect is not None:
            img = img.copy(rect)
        return _encode(img)

    return run_in_gui(_crop)


def grab(rect=None):
    """Encoded image of a screen region (QRect in desktop coordinates), or of
    the whole desktop when rect is None. None on failure."""
    if rect is not None and (rect.width() <= 0 or rect.height() <= 0):
        return None
    if _wayland():
        if _grim_usable():
            data = _grim(rect)
            if data:
                return data
        if os.environ.get("LUNA_NO_PORTAL") != "1":
            data = _portal_grab(rect)
            if data:
                return data
        return None
    return _qt_grab(rect)


def grab_rect_coords(x1, y1, x2, y2):
    return grab(QRect(QPoint(x1, y1), QPoint(x2, y2)))
