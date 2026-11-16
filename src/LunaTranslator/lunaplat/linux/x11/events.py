"""Foreground / window-destroy notifications, replacing the Win32 WinEvent
hook (EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_DESTROY) that
NativeUtils.globalmessagelistener installs.

Watches root-window PropertyNotify for _NET_ACTIVE_WINDOW and
_NET_CLIENT_LIST on a private connection; callbacks run on the GUI thread.
"""

import select
import threading

from Xlib import X

from lunaplat.linux import x11
from lunaplat.linux.qtthread import post_to_gui

EVENT_SYSTEM_FOREGROUND = 3
EVENT_OBJECT_DESTROY = 0x8001
OBJID_WINDOW = 0


class _Watcher(object):
    def __init__(self, callback):
        self.callback = callback
        self.d = x11.new_connection()
        self.stop = False

    def start(self):
        if self.d is None:
            return False
        threading.Thread(target=self._run, name="x11-events", daemon=True).start()
        return True

    def _clients(self, root, a_list):
        try:
            p = root.get_full_property(a_list, X.AnyPropertyType)
            return set(int(_) for _ in p.value) if p is not None else set()
        except Exception:
            return set()

    def _run(self):
        d = self.d
        root = d.screen().root
        a_active = d.intern_atom("_NET_ACTIVE_WINDOW")
        a_list = d.intern_atom("_NET_CLIENT_LIST")
        root.change_attributes(event_mask=X.PropertyChangeMask)
        d.flush()
        known = self._clients(root, a_list)
        fd = d.fileno()
        while not self.stop:
            if not d.pending_events():
                try:
                    select.select([fd], [], [], 1.0)
                except (OSError, ValueError):
                    break
            while d.pending_events():
                ev = d.next_event()
                if ev.type != X.PropertyNotify:
                    continue
                if ev.atom == a_active:
                    try:
                        p = root.get_full_property(a_active, X.AnyPropertyType)
                        xid = int(p.value[0]) if p is not None and len(p.value) else 0
                    except Exception:
                        xid = 0
                    self._emit(EVENT_SYSTEM_FOREGROUND, xid)
                elif ev.atom == a_list:
                    now = self._clients(root, a_list)
                    for gone in known - now:
                        self._emit(EVENT_OBJECT_DESTROY, gone)
                    known = now

    def _emit(self, event, xid):
        cb = self.callback

        def _call():
            try:
                cb(event, xid, OBJID_WINDOW)
            except Exception:
                pass

        post_to_gui(_call)


_watcher = None


def start(wineventcb):
    global _watcher
    if _watcher is not None:
        _watcher.callback = wineventcb
        return True
    w = _Watcher(wineventcb)
    if w.start():
        _watcher = w
        return True
    return False
