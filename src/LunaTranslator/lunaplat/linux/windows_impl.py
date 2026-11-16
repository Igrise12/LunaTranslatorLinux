"""Linux replacements for names in windows.py (applied after it imports).

windows.py binds Win32 APIs through the ctypes.windll stub, so every raw
binding exists and returns a zero value. This module overrides the helpers
the app depends on, mapping HWNDs to X11 window ids (see lunaplat.linux.x11).

Extended window styles are emulated per window: WS_EX_TOPMOST maps to
_NET_WM_STATE_ABOVE, WS_EX_TRANSPARENT to an empty input shape
(click-through) and WS_EX_NOACTIVATE to WM_HINTS input=False.
"""

import locale
import os
import subprocess
import threading

from lunaplat.linux import x11

_tls = threading.local()

# Win32 constants used here (same values as windows.py)
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x1
SWP_NOMOVE = 0x2
SWP_HIDEWINDOW = 0x80
SWP_SHOWWINDOW = 0x40
KEYEVENTF_KEYUP = 0x2
MOUSEEVENTF_LEFTDOWN = 0x2
MOUSEEVENTF_LEFTUP = 0x4
MOUSEEVENTF_RIGHTDOWN = 0x8
MOUSEEVENTF_RIGHTUP = 0x10
MOUSEEVENTF_MIDDLEDOWN = 0x20
MOUSEEVENTF_MIDDLEUP = 0x40

_exstyle = {}  # xid -> emulated WS_EX_* bits we manage
_exstyle_lock = threading.Lock()


def GetLastError():
    return getattr(_tls, "lasterror", 0)


def SetLastError(code):
    _tls.lasterror = code


def GetLocale():
    """(language, country), e.g. ("zh", "CN"), like GetLocaleInfoW."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val and val not in ("C", "POSIX"):
            break
    else:
        try:
            val = locale.getlocale()[0] or ""
        except Exception:
            val = ""
    val = val.split(".")[0].split("@")[0]
    lang, _, country = val.partition("_")
    return lang or "en", country or ""


def PathFileExists(path):
    return os.path.exists(path)


def ShellExecute(hwnd, op, file, params, _dir, bShow):
    """Win32 ShellExecute: returns > 32 on success."""
    if op == "runas":
        # no UAC on Linux; the caller falls back to its non-elevated path
        return 5  # SE_ERR_ACCESSDENIED
    if not file:
        return 2
    from lunaplat.linux import child_env

    try:
        if params:
            import shlex

            cmd = [file] + shlex.split(params)
            subprocess.Popen(
                cmd, cwd=_dir or None, env=child_env(), start_new_session=True
            )
        else:
            subprocess.Popen(
                ["xdg-open", file],
                cwd=_dir or None,
                env=child_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return 42
    except OSError:
        return 2


def IsUserAnAdmin():
    return os.geteuid() == 0


# ---- own Qt windows ----------------------------------------------------


def _own_widget(xid):
    """Top-level QWidget of this process with that X11 window id."""
    try:
        from qtsymbols import QApplication

        from lunaplat.linux.qt.windowtypes import native_id

        for w in QApplication.topLevelWidgets():
            if native_id(w) == int(xid):
                return w
    except Exception:
        pass
    return None


def _in_gui(fn):
    from lunaplat.linux.qtthread import run_in_gui

    return run_in_gui(fn)


# ---- window queries ----------------------------------------------------


def GetForegroundWindow():
    return x11.active_window()


def GetWindowThreadProcessId(hwnd):
    if not hwnd:
        return 0
    if _own_widget(hwnd) is not None:
        return os.getpid()
    return x11.window_pid(hwnd)


def GetWindowRect(hwnd):
    if not hwnd:
        return None
    return x11.window_rect(hwnd)


def GetWindowText(hwnd):
    return x11.window_title(hwnd) if hwnd else ""


def GetClassName(hwnd):
    return x11.window_class(hwnd)[1] if hwnd else ""


def GetAncestor(hwnd):
    return hwnd


def IsZoomed(hwnd):
    return x11.is_maximized(hwnd) if hwnd else False


def MonitorFromWindow(hwnd, dwFlags=2):
    rect = x11.window_rect(hwnd) if hwnd else None
    if not rect:
        return 1

    def _get():
        from qtsymbols import QGuiApplication, QPoint

        c = QPoint((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        s = QGuiApplication.screenAt(c)
        screens = QGuiApplication.screens()
        return (screens.index(s) + 1) if s in screens else 1

    return _in_gui(_get) or 1


def FindWindow(cls, title):
    return 0


def FindWindowEx(parent, after, cls, title):
    return 0


def WindowFromPoint(point):
    return x11.client_at_pointer()


# ---- window state ------------------------------------------------------


def _is_topmost(xid):
    with _exstyle_lock:
        bits = _exstyle.get(xid)
    if bits is not None and (bits & 0x80000000):
        return bool(bits & WS_EX_TOPMOST)
    w = _own_widget(xid)
    if w is not None:
        from qtsymbols import Qt

        return bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    return x11.atom("_NET_WM_STATE_ABOVE") in x11.window_state(xid)


def _set_topmost(xid, on):
    with _exstyle_lock:
        bits = _exstyle.get(xid, 0) | 0x80000000  # "topmost known"
        bits = (bits | WS_EX_TOPMOST) if on else (bits & ~WS_EX_TOPMOST)
        _exstyle[xid] = bits
    x11.set_state(xid, "_NET_WM_STATE_ABOVE", on)


def GetWindowLong(hwnd, index):
    if index != GWL_EXSTYLE or not hwnd:
        return 0
    xid = int(hwnd)
    with _exstyle_lock:
        bits = _exstyle.get(xid, 0)
    style = bits & (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
    if _is_topmost(xid):
        style |= WS_EX_TOPMOST
    return style


def SetWindowLong(hwnd, index, value):
    if index != GWL_EXSTYLE or not hwnd:
        return 0
    xid = int(hwnd)
    old = GetWindowLong(xid, index)
    changed = old ^ value
    if changed & WS_EX_TRANSPARENT:
        x11.set_input_passthrough(xid, bool(value & WS_EX_TRANSPARENT))
    if changed & WS_EX_NOACTIVATE:
        x11.set_accepts_focus(xid, not (value & WS_EX_NOACTIVATE))
    with _exstyle_lock:
        keep = _exstyle.get(xid, 0) & ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        _exstyle[xid] = keep | (value & (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE))
    if changed & WS_EX_TOPMOST:
        _set_topmost(xid, bool(value & WS_EX_TOPMOST))
    return old


def SetWindowPos(hwnd, InsertAfter, X, Y, cx, cy, uFlags):
    if not hwnd:
        return False
    xid = int(hwnd)
    if InsertAfter == HWND_TOPMOST:
        if not _is_topmost(xid):
            _set_topmost(xid, True)
        else:
            x11.set_state(xid, "_NET_WM_STATE_ABOVE", True)
    elif InsertAfter == HWND_NOTOPMOST:
        if _is_topmost(xid):
            _set_topmost(xid, False)
    move = not (uFlags & SWP_NOMOVE)
    size = not (uFlags & SWP_NOSIZE)
    if move or size:
        w = _own_widget(xid)
        if w is not None:

            def _apply():
                if move:
                    w.move(X, Y)
                if size:
                    w.resize(cx, cy)

            _in_gui(_apply)
        else:
            x11.configure(
                xid,
                X if move else None,
                Y if move else None,
                cx if size else None,
                cy if size else None,
            )
    return True


def MoveWindow(hwnd, X, Y, w, h, bRepaint=True):
    return SetWindowPos(hwnd, 0, X, Y, w, h, 0x4)


def SetForegroundWindow(hwnd):
    if not hwnd:
        return False
    w = _own_widget(hwnd)
    if w is not None:

        def _act():
            w.raise_()
            w.activateWindow()

        _in_gui(_act)
        return True
    _activate_foreign(int(hwnd))
    return True


def _activate_foreign(xid):
    """Focus another application's window. Hyprland ignores X11 activation
    requests unless misc:focus_on_activate is set, so ask it directly."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        pid = x11.window_pid(xid)
        if pid:
            try:
                subprocess.run(
                    ["hyprctl", "dispatch", "focuswindow", "pid:{}".format(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
                return
            except (OSError, subprocess.TimeoutExpired):
                pass
    x11.activate(xid)


def BringWindowToTop(hwnd):
    return SetForegroundWindow(hwnd)


def ShowWindow(hwnd, cmd):
    w = _own_widget(hwnd) if hwnd else None
    if w is None:
        return False

    def _do():
        if cmd == 0:
            w.hide()
        elif cmd in (2, 6, 7, 11):
            w.showMinimized()
        elif cmd == 3:
            w.showMaximized()
        else:
            w.showNormal() if (w.isMinimized() or w.isMaximized()) else w.show()

    _in_gui(_do)
    return True


# ---- pointer / keyboard ------------------------------------------------


def GetCursorPos():
    import windows as _w

    x, y, _ = x11.pointer()
    return _w.POINT(x, y)


def SetCursorPos(x, y):
    x11.warp_pointer(x, y)
    return True


def _qt_down(vk):
    """Key/button state as Qt sees it (reliable while one of our windows is
    active, which is when the app's modifier checks matter)."""
    try:
        from qtsymbols import QGuiApplication, Qt

        if QGuiApplication.instance() is None:
            return False
        mods = QGuiApplication.queryKeyboardModifiers()
        km = Qt.KeyboardModifier
        if vk in (0x10, 0xA0, 0xA1):
            return bool(mods & km.ShiftModifier)
        if vk in (0x11, 0xA2, 0xA3):
            return bool(mods & km.ControlModifier)
        if vk in (0x12, 0xA4, 0xA5):
            return bool(mods & km.AltModifier)
        if vk in (0x5B, 0x5C):
            return bool(mods & km.MetaModifier)
        buttons = QGuiApplication.mouseButtons()
        mb = Qt.MouseButton
        if vk == 0x01:
            return bool(buttons & mb.LeftButton)
        if vk == 0x02:
            return bool(buttons & mb.RightButton)
        if vk == 0x04:
            return bool(buttons & mb.MiddleButton)
    except Exception:
        pass
    return False


def _x11_down(vk):
    if not x11.available():
        return False
    from lunaplat.linux.x11 import keymap

    mask_bit = keymap.VK_BUTTON_MASK.get(vk)
    if mask_bit is not None:
        return bool(x11.pointer()[2] & mask_bit)
    keymap_bytes = x11.keymap_state()
    if not keymap_bytes:
        return False
    syms = [keymap.vk_to_keysym(vk)]
    for name in keymap.VK_ALSO.get(vk, ()):
        from Xlib import XK

        syms.append(XK.string_to_keysym(name))
    for sym in syms:
        code = x11.keysym_to_keycode(sym)
        if code and keymap_bytes[code // 8] & (1 << (code % 8)):
            return True
    return False


def _vk_down(vk):
    vk = int(vk) & 0xFF
    return _x11_down(vk) or _qt_down(vk)


def GetKeyState(vk):
    # high bit set (negative SHORT) when the key is down
    return -32768 if _vk_down(vk) else 0


def GetAsyncKeyState(vk):
    return -32768 if _vk_down(vk) else 0


def _use_uinput():
    # Xwayland drops XTest input on compositors without EIS support
    return bool(os.environ.get("WAYLAND_DISPLAY")) or not x11.available()


def keybd_event(bVk, bScan, dwFlags, _=None):
    press = not (dwFlags & KEYEVENTF_KEYUP)
    if _use_uinput():
        from lunaplat.linux import uinput

        if uinput.key(bVk, press):
            return
    from lunaplat.linux.x11 import keymap

    code = x11.keysym_to_keycode(keymap.vk_to_keysym(bVk))
    x11.fake_key(code, press)


def mouse_event(dwFlags, dx, dy, dwData, dwExtraInfo=None):
    if _use_uinput():
        from lunaplat.linux import uinput

        fake = uinput.button
    else:
        fake = x11.fake_button
    for flag, button, press in (
        (MOUSEEVENTF_LEFTDOWN, 1, True),
        (MOUSEEVENTF_LEFTUP, 1, False),
        (MOUSEEVENTF_RIGHTDOWN, 3, True),
        (MOUSEEVENTF_RIGHTUP, 3, False),
        (MOUSEEVENTF_MIDDLEDOWN, 2, True),
        (MOUSEEVENTF_MIDDLEUP, 2, False),
    ):
        if dwFlags & flag:
            fake(button, press)


def patch(mod):
    mod.GetLastError = GetLastError
    mod.SetLastError = SetLastError
    mod.GetLocale = GetLocale
    mod.PathFileExists = PathFileExists
    mod.ShellExecute = ShellExecute
    mod.IsUserAnAdmin = IsUserAnAdmin
    # input works with or without X11 (uinput on Wayland, Qt key state)
    for name in ("GetKeyState", "GetAsyncKeyState", "keybd_event", "mouse_event"):
        setattr(mod, name, globals()[name])
    if not x11.available():
        return
    for name in (
        "GetForegroundWindow",
        "GetWindowThreadProcessId",
        "GetWindowRect",
        "GetWindowText",
        "GetClassName",
        "GetAncestor",
        "IsZoomed",
        "MonitorFromWindow",
        "FindWindow",
        "FindWindowEx",
        "WindowFromPoint",
        "GetWindowLong",
        "SetWindowLong",
        "SetWindowPos",
        "MoveWindow",
        "SetForegroundWindow",
        "BringWindowToTop",
        "ShowWindow",
        "GetCursorPos",
        "SetCursorPos",
    ):
        setattr(mod, name, globals()[name])
