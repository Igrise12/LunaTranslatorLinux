"""Synthetic keyboard/mouse input on Wayland sessions.

Xwayland (23.1+) routes XTest through libei and drops it when the
compositor offers no EIS/RemoteDesktop support (e.g. Hyprland), so key
presses for the game ("auto advance", "hold Ctrl to skip") go through:

1. a uinput virtual device (/dev/uinput must be writable, usually via the
   `input` group); works on every compositor and reaches the focused
   window, including XWayland/Wine games;
2. `wtype` (wlroots virtual-keyboard protocol) for keys, if installed.

Only Windows VK codes that the app actually sends are mapped.
"""

import fcntl
import os
import shutil
import struct
import subprocess
import threading
import time

_EV_SYN = 0x00
_EV_KEY = 0x01
_SYN_REPORT = 0
_UI_SET_EVBIT = 0x40045564
_UI_SET_KEYBIT = 0x40045565
_UI_DEV_SETUP = 0x405C5503
_UI_DEV_CREATE = 0x5501
_UI_DEV_DESTROY = 0x5502
_BUS_VIRTUAL = 0x06

BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112

_LETTERS = "QWERTYUIOP ASDFGHJKL  ZXCVBNM"
_VK_TO_EVDEV = {}
for _row, _start in ((_LETTERS[0:10], 16), (_LETTERS[11:20], 30), (_LETTERS[22:29], 44)):
    for _i, _ch in enumerate(_row):
        if _ch != " ":
            _VK_TO_EVDEV[ord(_ch)] = _start + _i
for _i in range(1, 10):
    _VK_TO_EVDEV[0x30 + _i] = 1 + _i  # KEY_1..KEY_9 = 2..10
_VK_TO_EVDEV[0x30] = 11  # KEY_0
for _i in range(10):
    _VK_TO_EVDEV[0x70 + _i] = 59 + _i  # F1..F10
for _i in range(12):
    _VK_TO_EVDEV[0x7C + _i] = 183 + _i  # F13..F24
_VK_TO_EVDEV.update(
    {
        0x7A: 87, 0x7B: 88,  # F11, F12
        0x08: 14, 0x09: 15, 0x0D: 28, 0x1B: 1, 0x20: 57,
        0x10: 42, 0xA0: 42, 0xA1: 54,  # shift
        0x11: 29, 0xA2: 29, 0xA3: 97,  # ctrl
        0x12: 56, 0xA4: 56, 0xA5: 100,  # alt
        0x5B: 125, 0x5C: 126, 0x5D: 127,
        0x14: 58, 0x90: 69, 0x91: 70, 0x13: 119, 0x2C: 99,
        0x21: 104, 0x22: 109, 0x23: 107, 0x24: 102,
        0x25: 105, 0x26: 103, 0x27: 106, 0x28: 108,
        0x2D: 110, 0x2E: 111,
        0xBA: 39, 0xBB: 13, 0xBC: 51, 0xBD: 12, 0xBE: 52, 0xBF: 53,
        0xC0: 41, 0xDB: 26, 0xDC: 43, 0xDD: 27, 0xDE: 40,
        0x60: 82, 0x61: 79, 0x62: 80, 0x63: 81, 0x64: 75,
        0x65: 76, 0x66: 77, 0x67: 71, 0x68: 72, 0x69: 73,
        0x6A: 55, 0x6B: 78, 0x6D: 74, 0x6E: 83, 0x6F: 98,
    }
)


class _Device(object):
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, _UI_SET_EVBIT, _EV_KEY)
        for code in set(_VK_TO_EVDEV.values()) | {BTN_LEFT, BTN_RIGHT, BTN_MIDDLE}:
            fcntl.ioctl(self.fd, _UI_SET_KEYBIT, code)
        name = b"LunaTranslator virtual input"
        setup = struct.pack("<HHHH80sI", _BUS_VIRTUAL, 0x1234, 0x5678, 1, name, 0)
        fcntl.ioctl(self.fd, _UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, _UI_DEV_CREATE)
        # give the compositor time to add the new device before first use
        time.sleep(0.3)

    def _emit(self, etype, code, value):
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1e6)
        os.write(self.fd, struct.pack("llHHi", sec, usec, etype, code, value))

    def key(self, code, press):
        self._emit(_EV_KEY, code, 1 if press else 0)
        self._emit(_EV_SYN, _SYN_REPORT, 0)

    def close(self):
        try:
            fcntl.ioctl(self.fd, _UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass


_dev = None
_dev_failed = False
_lock = threading.Lock()


def _device():
    global _dev, _dev_failed
    with _lock:
        if _dev is None and not _dev_failed:
            if not os.access("/dev/uinput", os.W_OK):
                _dev_failed = True
            else:
                try:
                    _dev = _Device()
                    import atexit

                    atexit.register(_dev.close)
                except OSError:
                    _dev_failed = True
        return _dev


def _wtype_key(vk, press):
    from lunaplat.linux.x11 import keymap

    sym = keymap.vk_to_keysym(vk)
    if not sym or not shutil.which("wtype"):
        return False
    from Xlib import XK

    name = XK.keysym_to_string(sym) or ""
    # keysym_to_string gives the character for printable keys; wtype wants
    # keysym names for both
    for k, v in XK.__dict__.items():
        if k.startswith("XK_") and v == sym:
            name = k[3:]
            break
    if not name:
        return False
    try:
        subprocess.run(
            ["wtype", "-P" if press else "-p", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def available():
    return _device() is not None or shutil.which("wtype") is not None


def key(vk, press):
    code = _VK_TO_EVDEV.get(int(vk) & 0xFF)
    dev = _device()
    if dev is not None and code:
        with _lock:
            dev.key(code, press)
        return True
    return _wtype_key(vk, press)


def button(btn, press):
    """btn: 1 left, 2 middle, 3 right (X11 numbering)."""
    code = {1: BTN_LEFT, 2: BTN_MIDDLE, 3: BTN_RIGHT}.get(btn)
    dev = _device()
    if dev is None or code is None:
        return False
    with _lock:
        dev.key(code, press)
    return True
