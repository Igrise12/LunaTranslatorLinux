"""Click-to-pick a window (NativeUtils.mouseselectwindow).

Grabs the pointer with a crosshair on a private connection and waits for a
click: left button picks the client window under the pointer, right button
or Escape cancels. Under XWayland an active grab only succeeds while the
pointer is over an X11 window; if it fails we fall back to polling the
button state (also X11-only, which covers Wine game windows).
"""

import time

from Xlib import X, Xcursorfont

from lunaplat.linux import x11

_TIMEOUT = 30.0


def _client_of(d, win):
    wm_state = d.intern_atom("WM_STATE")
    depth = 0
    while win and depth < 16:
        try:
            if win.get_full_property(wm_state, X.AnyPropertyType) is not None:
                return int(win.id)
            nxt = win.query_pointer().child
        except Exception:
            return int(win.id) if win else 0
        if not nxt:
            return int(win.id)
        win = nxt
        depth += 1
    return int(win.id) if win else 0


def _result(xid):
    if not xid:
        return 0, None
    return x11.window_pid(xid), xid


def _grab_pick(d):
    root = d.screen().root
    font = d.open_font("cursor")
    cursor = font.create_glyph_cursor(
        font, Xcursorfont.crosshair, Xcursorfont.crosshair + 1,
        (0, 0, 0), (0xFFFF, 0xFFFF, 0xFFFF),
    )
    status = root.grab_pointer(
        False, X.ButtonPressMask, X.GrabModeAsync, X.GrabModeAsync,
        X.NONE, cursor, X.CurrentTime,
    )
    if status != X.GrabSuccess:
        return None
    root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
    escape = d.keysym_to_keycode(0xFF1B)
    deadline = time.monotonic() + _TIMEOUT
    try:
        while time.monotonic() < deadline:
            if not d.pending_events():
                time.sleep(0.02)
                continue
            ev = d.next_event()
            if ev.type == X.ButtonPress:
                if ev.detail == 1:
                    return _result(_client_of(d, ev.child) if ev.child else 0)
                return 0, None
            if ev.type == X.KeyPress and ev.detail == escape:
                return 0, None
        return 0, None
    finally:
        d.ungrab_pointer(X.CurrentTime)
        d.ungrab_keyboard(X.CurrentTime)
        d.flush()


def _poll_pick():
    deadline = time.monotonic() + _TIMEOUT
    # wait for any held button to be released first
    while x11.pointer()[2] & (X.Button1Mask | X.Button3Mask):
        if time.monotonic() > deadline:
            return 0, None
        time.sleep(0.02)
    while time.monotonic() < deadline:
        mask = x11.pointer()[2]
        if mask & X.Button3Mask:
            return 0, None
        if mask & X.Button1Mask:
            return _result(x11.client_at_pointer())
        time.sleep(0.02)
    return 0, None


def pick_window(callback):
    d = x11.new_connection()
    res = None
    if d is not None:
        try:
            res = _grab_pick(d)
        except Exception:
            res = None
        finally:
            try:
                d.close()
            except Exception:
                pass
    if res is None:
        res = _poll_pick()
    pid, xid = res
    callback(pid, xid)
