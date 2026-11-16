"""Global hotkeys with XGrabKey, replacing NativeUtils.SysRegisterHotKey.

Grabs are made on the root window of a private connection owned by one
thread; registration requests are queued to it. Lock modifiers (Caps Lock,
Num Lock) are ignored by grabbing every combination.

Under XWayland a root grab only fires while an X11 window has keyboard
focus, which covers the main case (the Wine game is focused).
"""

import itertools
import os
import queue
import select
import threading

from Xlib import X, error

from lunaplat.linux import x11
from lunaplat.linux.x11 import keymap


class _HotkeyThread(object):
    def __init__(self):
        self.d = x11.new_connection()
        self.cmds = queue.Queue()
        self.rfd, self.wfd = os.pipe()
        self.bindings = {}  # uid -> (keycode, mask, callback)
        self.lookup = {}  # (keycode, mask) -> uid
        self.next_uid = 1
        self.ignored = []
        if self.d is not None:
            self._compute_ignored()
            threading.Thread(target=self._run, name="x11-hotkeys", daemon=True).start()

    def _compute_ignored(self):
        numlock = 0
        try:
            mm = self.d.get_modifier_mapping()
            nl = self.d.keysym_to_keycode(0xFF7F)  # Num_Lock
            for i, codes in enumerate(mm):
                if nl and nl in codes:
                    numlock = 1 << i
        except Exception:
            numlock = X.Mod2Mask
        locks = [X.LockMask, numlock or X.Mod2Mask]
        combos = set()
        for n in range(len(locks) + 1):
            for c in itertools.combinations(locks, n):
                m = 0
                for b in c:
                    m |= b
                combos.add(m)
        self.ignored = sorted(combos)
        self.ignore_mask = 0
        for b in locks:
            self.ignore_mask |= b

    def _call(self, fn):
        done = threading.Event()
        box = []
        self.cmds.put((fn, box, done))
        os.write(self.wfd, b"x")
        done.wait(5)
        return box[0] if box else 0

    def register(self, mods, vk, callback):
        if self.d is None:
            return 0
        return self._call(lambda: self._register(mods, vk, callback))

    def unregister(self, uid):
        if self.d is None:
            return
        self._call(lambda: self._unregister(uid))

    def _register(self, mods, vk, callback):
        keysym = keymap.vk_to_keysym(vk)
        if not keysym:
            return 0
        keycode = self.d.keysym_to_keycode(keysym)
        if not keycode:
            return 0
        mask = keymap.mods_to_mask(mods)
        if (keycode, mask) in self.lookup:
            return 0  # already taken, like RegisterHotKey failing
        root = self.d.screen().root
        failed = []

        def onerror(*args):
            failed.append(args)

        for extra in self.ignored:
            root.grab_key(
                keycode, mask | extra, True, X.GrabModeAsync, X.GrabModeAsync,
                onerror=onerror,
            )
        self.d.sync()
        if failed:
            for extra in self.ignored:
                root.ungrab_key(keycode, mask | extra)
            self.d.sync()
            return 0
        uid = self.next_uid
        self.next_uid += 1
        self.bindings[uid] = (keycode, mask, callback)
        self.lookup[(keycode, mask)] = uid
        return uid

    def _unregister(self, uid):
        b = self.bindings.pop(uid, None)
        if not b:
            return 0
        keycode, mask, _ = b
        self.lookup.pop((keycode, mask), None)
        root = self.d.screen().root
        for extra in self.ignored:
            root.ungrab_key(keycode, mask | extra)
        self.d.sync()
        return 1

    def _run(self):
        d = self.d
        fd = d.fileno()
        while True:
            if not d.pending_events():
                try:
                    r, _, _ = select.select([fd, self.rfd], [], [], 1.0)
                except (OSError, ValueError):
                    break
                if self.rfd in r:
                    os.read(self.rfd, 64)
            while not self.cmds.empty():
                fn, box, done = self.cmds.get()
                try:
                    box.append(fn())
                except Exception:
                    box.append(0)
                done.set()
            while d.pending_events():
                ev = d.next_event()
                if ev.type != X.KeyPress:
                    continue
                key = (ev.detail, ev.state & ~self.ignore_mask & 0xFF)
                uid = self.lookup.get(key)
                if uid is None:
                    continue
                cb = self.bindings[uid][2]
                try:
                    cb()
                except Exception:
                    pass


_thread = None
_lock = threading.Lock()


def _get():
    global _thread
    with _lock:
        if _thread is None:
            _thread = _HotkeyThread()
    return _thread


def register(mods, vk, callback):
    return _get().register(mods, vk, callback)


def unregister(uid):
    _get().unregister(uid)
