"""Linux replacements for names in NativeUtils.py (applied after it imports).

NativeUtils.py loads libNativeUtils.so through LenientCDLL, so the portable
exports (mecab, levenshtein, Markdown2Html, AES, glens, strnatcmp) are real
and every Windows-only export is a stub. Here we override the high-level
Python wrappers the app depends on.
"""

import ctypes
import fcntl
import os
import signal
import subprocess
import sys

from lunaplat.linux import clipboard as _clip
from lunaplat.linux import windows_impl

ERROR_ALREADY_EXISTS = 183

_PR_SET_PDEATHSIG = 1
try:
    _libc = ctypes.CDLL(None, use_errno=True)
    _prctl = _libc.prctl
except (OSError, AttributeError):
    _prctl = None


def _runtime_dir():
    d = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    d = os.path.join(d, "lunatranslator")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


# ---- named mutex / event ----------------------------------------------


class _LockFileMutex(object):
    """Named mutex emulated with an exclusive flock on a runtime file.
    Held until this object is garbage collected or the process exits."""

    def __init__(self, name):
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)
        self.path = os.path.join(_runtime_dir(), safe + ".lock")
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.existed = False
        except OSError:
            self.existed = True

    def __bool__(self):
        return True

    def __del__(self):
        try:
            os.close(self.fd)
        except Exception:
            pass


def SimpleCreateMutex(name):
    m = _LockFileMutex(name)
    windows_impl.SetLastError(ERROR_ALREADY_EXISTS if m.existed else 0)
    return m


class _NamedEvent(object):
    def __init__(self, name):
        self.name = name

    def __bool__(self):
        return True


def SimpleCreateEvent(name):
    windows_impl.SetLastError(0)
    return _NamedEvent(name)


# ---- processes --------------------------------------------------------


def _pdeathsig():
    if _prctl is not None:
        _prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


class AutoKillProcess(object):
    """Start a process (or adopt a pid) that dies with us when kill=True."""

    def __init__(self, commandorpid, path=None, hide=True, kill=True):
        self._kill = kill
        self._proc = None
        if isinstance(commandorpid, int):
            self.pid = commandorpid
            return
        if isinstance(commandorpid, str):
            import shlex

            cmd = shlex.split(commandorpid)
        else:
            cmd = list(commandorpid)
        def start():
            return subprocess.Popen(
                cmd,
                cwd=path or None,
                stdin=subprocess.DEVNULL,
                preexec_fn=_pdeathsig if kill else None,
                start_new_session=not kill,
            )

        if kill:
            # PR_SET_PDEATHSIG fires when the *thread* that forked the child
            # ends; callers often run in short-lived worker threads, so fork
            # from the long-lived spawner thread instead
            from lunaplat.linux.audio import _spawner

            self._proc = _spawner.run(start)
        else:
            self._proc = start()
        self.pid = self._proc.pid

    def setkill(self, kill):
        self._kill = kill

    def __del__(self):
        if self._kill and self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass


def IsProcessRunning(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def GetProcessFileName(pid):
    from lunaplat.linux import proc

    return proc.process_file_name(pid)


def ListProcess(exe=None):
    """{exe path: [pids]} of candidate game processes (Wine processes), or
    the pid list for one exe path."""
    from lunaplat.linux import proc

    groups = proc.list_process_groups()
    if exe is None:
        return groups
    target = os.path.realpath(exe)
    for path, pids in groups.items():
        if os.path.realpath(path) == target:
            return pids
    return []


def GetParentProcessID(pid):
    from lunaplat.linux import proc

    return proc.parent_pid(pid)


def Is64bit(pid):
    """Bitness of the program a process runs (PE header for Wine games)."""
    path = GetProcessFileName(pid)
    if path and path.lower().endswith(".exe"):
        try:
            import struct

            with open(path, "rb") as f:
                head = f.read(0x40)
                off = struct.unpack_from("<I", head, 0x3C)[0]
                f.seek(off + 4)
                machine = struct.unpack("<H", f.read(2))[0]
            return machine in (0x8664, 0xAA64)
        except Exception:
            pass
    return sys.maxsize > 2**32


# ---- windows -------------------------------------------------------------


def IsWindowViewable(hwnd):
    from lunaplat.linux import x11

    return x11.is_viewable(hwnd)


def GetProcessFirstWindow(pid):
    from lunaplat.linux import x11

    for xid in x11.client_list():
        if x11.window_pid(xid) == int(pid) and x11.is_viewable(xid):
            return xid
    return 0


def SetWindowInTaskbar(hwnd, show, ismain):
    from lunaplat.linux.qt import windowtypes

    windowtypes.remember_skip_taskbar(int(hwnd), not show)


def mouseselectwindow(callback):
    """Click a window to pick it: callback(pid, hwnd); right click or Escape
    cancels with callback(0, None). Runs on a worker thread."""
    import threading

    from lunaplat.linux.x11 import pick

    threading.Thread(target=pick.pick_window, args=(callback,), daemon=True).start()


def MouseMoveWindow(hwnd):
    from lunaplat.linux import windows_impl
    from lunaplat.linux.qtthread import run_in_gui

    w = windows_impl._own_widget(hwnd)
    if w is not None and w.windowHandle() is not None:
        run_in_gui(lambda: w.windowHandle().startSystemMove())


# ---- capture -------------------------------------------------------------


def GdiCropImage(x1, y1, x2, y2, hwnd=None, curr=-1):
    """(succ, encoded image) of a screen rectangle. Grabbing a single window's
    own contents (curr=0) is not possible on Linux/XWayland, so that attempt
    fails and callers fall back to the desktop (curr=1)."""
    from lunaplat.linux import capture

    if curr == 0:
        return False, None
    data = capture.grab_rect_coords(x1, y1, x2, y2)
    return (data is not None), data


def GdiGrabWindow(hwnd):
    from lunaplat.linux import capture, x11

    rect = x11.window_rect(hwnd) if hwnd else None
    if not rect:
        return None
    return capture.grab_rect_coords(rect[0], rect[1], rect[2] - 1, rect[3] - 1)


def find_sole_opaque_window(x1, y1, x2, y2, exclude_pid):
    return 0


def CreateSelectRangeWindow(parent, alpha, r, g, b, width, cb):
    from lunaplat.linux.qt.rangeselect import select_range

    select_range(parent, alpha, r, g, b, width, cb)


# ---- hotkeys -------------------------------------------------------------


def RegisterHotKey(_, cb):
    from lunaplat.linux.x11 import hotkeys

    mod, vk = _
    return hotkeys.register(mod, vk, cb)


def UnRegisterHotKey(uid):
    from lunaplat.linux.x11 import hotkeys

    hotkeys.unregister(uid)


def getcurrexe():
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return appimage
    return os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else sys.executable


def CheckMaybeUNCFile(v):
    return v


def OpenFileEx(path):
    os.startfile(path)


# ---- theme / screen ---------------------------------------------------


def IsDark():
    try:
        from qtsymbols import QGuiApplication, Qt

        app = QGuiApplication.instance()
        if app is not None:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
    except Exception:
        pass
    from lunaplat.linux import accent

    scheme = accent.portal_color_scheme()
    if scheme in (1, 2):
        return scheme == 1
    theme = os.environ.get("GTK_THEME", "").lower()
    return "dark" in theme


def GetDevicePixelRatioF(hwnd):
    try:
        from qtsymbols import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen else 1.0
    except Exception:
        return 1.0


def IsMultiDifferentDPI():
    try:
        from qtsymbols import QGuiApplication

        ratios = {s.devicePixelRatio() for s in QGuiApplication.screens()}
        return len(ratios) > 1
    except Exception:
        return False


def IsRunningOnWine():
    return False


# ---- global message listener -------------------------------------------


def globalmessagelistener(wineventcb, wndmsgcb):
    """Win32 version: WinEvent hook (foreground/destroy) + a message window.
    Here: clipboard changes (msg 3) and colour-scheme changes (msg 0).
    Foreground/destroy events come from the X11 backend (phase 1)."""
    from lunaplat.linux import qtthread

    qtthread._get_invoker()
    _clip.connect_listener(wndmsgcb)
    from lunaplat.linux import x11

    if x11.available():
        from lunaplat.linux.x11 import events

        events.start(wineventcb)
    try:
        from qtsymbols import QGuiApplication

        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda *_: wndmsgcb(0, 0, 0))
    except Exception:
        pass


def dispatchcloseevent():
    pass


# ---- selected text / web view menus ------------------------------------


def _wl_primary():
    """Wayland primary selection through wl-paste; None if unavailable."""
    import shutil

    if not (os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste")):
        return None
    try:
        r = subprocess.run(
            ["wl-paste", "--primary", "--no-newline", "--type", "text"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return ""  # "Nothing is copied"
    return r.stdout.decode("utf-8", "replace")


def _qt_primary():
    """X11 PRIMARY (we run on xcb/XWayland) through Qt; None if the platform
    has no selection clipboard."""

    def _get():
        from PyQt6.QtGui import QClipboard
        from qtsymbols import QApplication

        cb = QApplication.clipboard()
        if cb is None or not cb.supportsSelection():
            return None
        return cb.text(QClipboard.Mode.Selection)

    from lunaplat.linux.qtthread import run_in_gui

    return run_in_gui(_get, timeout=3)


def GetSelectedText():
    """Text selected in the focused application, i.e. the PRIMARY selection
    (Win32 build: UI Automation). Wayland sessions keep separate PRIMARY
    selections for native Wayland and for X11 (XWayland, e.g. Wine games)
    clients, so the Wayland one is read with wl-paste and, when empty, the
    X11 one through Qt. Returns None when neither can be read, "" when
    nothing is selected."""
    # an X11 window (Wine game, XWayland app) has the focus: its PRIMARY is
    # the current one; the Wayland PRIMARY may hold a stale selection
    x11_focused = False
    try:
        from lunaplat.linux import x11

        x11_focused = bool(x11.available() and x11.active_window())
    except Exception:
        pass
    if x11_focused:
        x = _qt_primary()
        if x:
            return x
    wl = _wl_primary()
    if wl:
        return wl
    x = _qt_primary()
    if x is None:
        return wl
    return x


class MenuItem(object):
    """Context-menu entry for the web views (rendered by
    lunaplat.linux.qt.webengine as a QMenu instead of a native menu)."""

    def __init__(
        self,
        issep=False,
        checkable=False,
        checked=False,
        clicked=None,
        text="",
        translate=True,
    ):
        self.issep = issep
        self.checkable = checkable
        self.checked = checked
        self.clicked = clicked
        self.rawtext = text
        if translate and text:
            from myutils.config import _TR

            text = _TR(text)
        self.text = text
        self.sub = []

    def appendSub(self, sub):
        self.sub.append(sub)


def patch(mod):
    mod.ClipBoard = _clip.ClipBoard
    mod.ClipBoardListenerStart = _clip.ClipBoardListenerStart
    mod.ClipBoardListenerStop = _clip.ClipBoardListenerStop
    mod.globalmessagelistener = globalmessagelistener
    mod.dispatchcloseevent = dispatchcloseevent
    mod.SimpleCreateMutex = SimpleCreateMutex
    mod.SimpleCreateEvent = SimpleCreateEvent
    mod.AutoKillProcess = AutoKillProcess
    mod.IsProcessRunning = IsProcessRunning
    mod.GetProcessFileName = GetProcessFileName
    mod.getcurrexe = getcurrexe
    mod.CheckMaybeUNCFile = CheckMaybeUNCFile
    mod.OpenFileEx = OpenFileEx
    mod.IsDark = IsDark
    mod.GetDevicePixelRatioF = GetDevicePixelRatioF
    mod.IsMultiDifferentDPI = IsMultiDifferentDPI
    mod.IsRunningOnWine = IsRunningOnWine
    from lunaplat.linux import proc as _proc

    mod.SuspendResumeProcess = _proc.suspend_resume
    mod.GetProcessListenPort = _proc.listen_port
    mod.ListProcess = ListProcess
    mod.GetParentProcessID = GetParentProcessID
    mod.Is64bit = Is64bit
    mod.GdiCropImage = GdiCropImage
    mod.GdiGrabWindow = GdiGrabWindow
    mod.find_sole_opaque_window = find_sole_opaque_window
    mod.CreateSelectRangeWindow = CreateSelectRangeWindow
    mod.RegisterHotKey = RegisterHotKey
    mod.UnRegisterHotKey = UnRegisterHotKey
    mod.SetWindowInTaskbar = SetWindowInTaskbar
    mod.GetSelectedText = GetSelectedText
    mod.MenuItem = MenuItem
    from lunaplat.linux import audiomute, desktopentry

    mod.StartMonitorVolume = audiomute.StartMonitorVolume
    mod.MonitorPidVolume = audiomute.MonitorPidVolume
    mod.SetCurrProcessMute = audiomute.SetCurrProcessMute
    mod.CreateUrlProtocol = desktopentry.CreateUrlProtocol
    mod.CreateShortcut = desktopentry.CreateShortcut
    from lunaplat.linux import x11

    if x11.available():
        mod.IsWindowViewable = IsWindowViewable
        mod.GetProcessFirstWindow = GetProcessFirstWindow
        mod.mouseselectwindow = mouseselectwindow
        mod.MouseMoveWindow = MouseMoveWindow
