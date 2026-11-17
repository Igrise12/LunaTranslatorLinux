"""Clipboard backend replacing NativeUtils.ClipBoard and its listener.

Interface matches NativeUtils._ClipBoard: .text (get/set), .setText(),
.files, .image (encoded image bytes, get/set). Safe to use from any thread.

Two backends:

* Wayland session with wl-clipboard: the Wayland clipboard is the
  authoritative one, and an XWayland client (this app runs as one by
  default) only sees it after one of its windows gains focus. So text goes
  through wl-paste / wl-copy, and changes are watched with
  `wl-paste --watch`. That needs the wlr data-control protocol (wlroots
  compositors such as Hyprland and Sway, and KDE); elsewhere, e.g. GNOME,
  we fall back to Qt.
* everything else: QClipboard (X11 selections).
"""

import os
import shutil
import subprocess
import threading
from ctypes import addressof, create_unicode_buffer
from urllib.parse import unquote, urlparse

from qtsymbols import QApplication, QBuffer, QByteArray, QImage

from lunaplat.linux.qtthread import run_in_gui

_RS = b"\x1e"  # record separator between watch events


def _cb():
    return QApplication.clipboard()


def _wayland_tools():
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False
    return bool(shutil.which("wl-paste") and shutil.which("wl-copy"))


class _WlClipboard(object):
    """wl-clipboard helpers. Every call is a short subprocess."""

    TEXT_TYPE = "text/plain;charset=utf-8"

    @staticmethod
    def get_text():
        try:
            r = subprocess.run(
                ["wl-paste", "--no-newline", "--type", "text"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return ""
        return r.stdout.decode("utf-8", "replace")

    @staticmethod
    def set_text(text):
        try:
            subprocess.run(
                ["wl-copy", "--type", _WlClipboard.TEXT_TYPE],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False


class LinuxClipBoard(object):
    def __init__(self):
        # text we set ourselves; the change notification it triggers is
        # reported to listeners as "ismy" so they can ignore it
        self._own_text = None
        self._own_lock = threading.Lock()
        self.wayland = _wayland_tools()

    def _mark_own(self, t):
        with self._own_lock:
            self._own_text = t

    def take_own_flag(self, text):
        with self._own_lock:
            own = self._own_text is not None and self._own_text == text
            if own:
                self._own_text = None
            return own

    @property
    def text(self):
        if self.wayland:
            t = _WlClipboard.get_text()
            if t is not None:
                return t

        def _get():
            cb = _cb()
            return cb.text() if cb is not None else ""

        return run_in_gui(_get, default="") or ""

    @text.setter
    def text(self, t):
        t = "" if t is None else str(t)
        self._mark_own(t)
        if self.wayland and _WlClipboard.set_text(t):
            return

        def _set():
            cb = _cb()
            if cb is not None:
                cb.setText(t)

        run_in_gui(_set)

    def setText(self, t):
        self.text = t

    @property
    def files(self):
        def _get():
            cb = _cb()
            md = cb.mimeData() if cb is not None else None
            if md is None:
                return []
            out = []
            if md.hasUrls():
                for url in md.urls():
                    if url.isLocalFile():
                        out.append(url.toLocalFile())
            if not out and md.hasText():
                for line in md.text().splitlines():
                    if line.startswith("file://"):
                        out.append(unquote(urlparse(line).path))
            return out

        return run_in_gui(_get, default=[]) or []

    @property
    def image(self):
        def _get():
            cb = _cb()
            if cb is None:
                return None
            img = cb.image()
            if img is None or img.isNull():
                return None
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            buf.close()
            return bytes(ba)

        return run_in_gui(_get)

    @image.setter
    def image(self, bytes_):
        def _set():
            cb = _cb()
            img = QImage.fromData(bytes_)
            if cb is not None and not img.isNull():
                cb.setImage(img)

        run_in_gui(_set)


ClipBoard = LinuxClipBoard()

# ---- change listener ----------------------------------------------------

_state = {"listening": False, "wndmsgcb": None, "connected": False}
_keepalive = []


def ClipBoardListenerStart():
    _state["listening"] = True
    return True


def ClipBoardListenerStop():
    _state["listening"] = False


def _dispatch(text):
    """GUI thread: forward a clipboard change like the Win32 listener did:
    msg 3, wparam = ismy, lparam = pointer to the text (valid during call)."""
    cb = _state["wndmsgcb"]
    if cb is None or not _state["listening"] or not text:
        ClipBoard.take_own_flag(text)
        return
    ismy = ClipBoard.take_own_flag(text)
    buf = create_unicode_buffer(text)
    _keepalive[:] = [buf]
    cb(3, int(ismy), addressof(buf))


def _on_qt_changed():
    clip = _cb()
    if clip is not None:
        _dispatch(clip.text())


class _WlWatcher(object):
    """Runs `wl-paste --watch` and turns each change into a _dispatch()."""

    def __init__(self):
        self.proc = None
        self.started = 0.0
        self.initial = None

    def start(self):
        import time

        # wl-paste --watch reports the current selection once at start, but
        # only when there is one; remember it so that report can be ignored
        self.initial = _WlClipboard.get_text()
        self.started = time.monotonic()
        # forked by the long-lived spawner thread with PR_SET_PDEATHSIG, so
        # the watcher dies with the app (which ends with os._exit, so no
        # cleanup hook runs)
        from lunaplat.linux.audio import _pdeathsig, _spawner

        def start():
            return subprocess.Popen(
                [
                    "wl-paste",
                    "--no-newline",
                    "--type",
                    "text",
                    "--watch",
                    "sh",
                    "-c",
                    "cat; printf '\\036'",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                preexec_fn=_pdeathsig,
            )

        try:
            self.proc = _spawner.run(start)
        except OSError:
            return False
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def _read(self):
        import time

        buf = b""
        first = True
        while True:
            chunk = self.proc.stdout.read1(65536)
            if not chunk:
                break
            buf += chunk
            while _RS in buf:
                rec, buf = buf.split(_RS, 1)
                text = rec.decode("utf-8", "replace")
                if first:
                    first = False
                    if (
                        time.monotonic() - self.started < 1.5
                        and text == (self.initial or "")
                    ):
                        continue  # the start-up report, not a change
                run_in_gui(lambda t=text: _dispatch(t), timeout=10)
        err = b""
        try:
            err = self.proc.stderr.read()
        except Exception:
            pass
        if err:
            # e.g. compositor without data-control: use Qt instead
            run_in_gui(_connect_qt)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


_watcher = _WlWatcher()


def _connect_qt():
    if _state["connected"] == "qt":
        return
    ClipBoard.wayland = False
    clip = _cb()
    if clip is None:
        return
    clip.dataChanged.connect(_on_qt_changed)
    _state["connected"] = "qt"


def connect_listener(wndmsgcb):
    """Called from globalmessagelistener on the GUI thread."""
    _state["wndmsgcb"] = wndmsgcb
    if _state["connected"]:
        return
    if ClipBoard.wayland and _watcher.start():
        _state["connected"] = "wayland"
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_watcher.stop)
        return
    _connect_qt()
