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
        self._proc = subprocess.Popen(
            cmd,
            cwd=path or None,
            stdin=subprocess.DEVNULL,
            preexec_fn=_pdeathsig if kill else None,
            start_new_session=not kill,
        )
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
    try:
        return os.readlink("/proc/{}/exe".format(int(pid)))
    except OSError:
        return None


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
    try:
        from qtsymbols import QGuiApplication

        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda *_: wndmsgcb(0, 0, 0))
    except Exception:
        pass


def dispatchcloseevent():
    pass


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
