"""X11 / XWayland window backend (python-xlib).

All window handles ("hwnd") on Linux are X11 window ids: our own Qt windows
(int(widget.winId()) under the xcb platform) and Wine game windows, which
are X11 clients too (also under XWayland).

One shared Display connection, serialised by a lock, serves request/reply
calls from any thread. Event loops (foreground tracking, hotkeys) use their
own connections.
"""

import os
import threading

try:
    from Xlib import X, Xatom, display, error
    from Xlib.protocol import event as xevent

    _HAVE_XLIB = True
except ImportError:  # python-xlib missing
    _HAVE_XLIB = False

_lock = threading.RLock()
_disp = None
_disp_failed = False
_atoms = {}


def available():
    return _get() is not None


def qt_uses_xcb():
    """Whether Qt will run on the xcb platform (so our windows are X11
    windows), mirroring Qt's choice from QT_QPA_PLATFORM."""
    plat = os.environ.get("QT_QPA_PLATFORM", "")
    first = plat.split(";")[0].strip()
    if first == "xcb":
        return True
    if first.startswith("wayland"):
        # falls back to the next entry (xcb) without a Wayland display
        return not os.environ.get("WAYLAND_DISPLAY") and "xcb" in plat
    if first:
        return False  # offscreen, eglfs, ...
    return not os.environ.get("WAYLAND_DISPLAY")


def _get():
    global _disp, _disp_failed
    if _disp is not None or _disp_failed:
        return _disp
    with _lock:
        if _disp is None and not _disp_failed:
            if not _HAVE_XLIB or not os.environ.get("DISPLAY"):
                _disp_failed = True
                return None
            if not qt_uses_xcb():
                # our own windows are not X11 windows in that case
                _disp_failed = True
                return None
            try:
                _disp = display.Display()
            except Exception:
                _disp_failed = True
    return _disp


def new_connection():
    """A separate Display for an event-loop thread."""
    if not _HAVE_XLIB or not os.environ.get("DISPLAY"):
        return None
    try:
        return display.Display()
    except Exception:
        return None


def atom(name, d=None):
    d = d or _get()
    a = _atoms.get(name)
    if a is None:
        with _lock:
            a = d.intern_atom(name)
        _atoms[name] = a
    return a


def _win(xid):
    return _get().create_resource_object("window", int(xid))


def _prop(xid, name, ptype=X.AnyPropertyType if _HAVE_XLIB else 0):
    d = _get()
    if d is None or not xid:
        return None
    with _lock:
        try:
            p = _win(xid).get_full_property(atom(name), ptype)
        except (error.BadWindow, error.BadMatch, error.BadValue):
            return None
        except Exception:
            return None
    return p.value if p is not None else None


def root():
    d = _get()
    return d.screen().root if d else None


# ---- queries ------------------------------------------------------------


def active_window():
    v = _prop(root().id, "_NET_ACTIVE_WINDOW") if available() else None
    return int(v[0]) if v is not None and len(v) else 0


def client_list():
    v = _prop(root().id, "_NET_CLIENT_LIST") if available() else None
    return [int(_) for _ in v] if v is not None else []


def window_pid(xid):
    v = _prop(xid, "_NET_WM_PID")
    return int(v[0]) if v is not None and len(v) else 0


def window_title(xid):
    v = _prop(xid, "_NET_WM_NAME")
    if v is None:
        v = _prop(xid, "WM_NAME")
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def window_class(xid):
    v = _prop(xid, "WM_CLASS")
    if not v:
        return ("", "")
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    parts = v.split("\0")
    return (parts[0], parts[1] if len(parts) > 1 else "")


def window_state(xid):
    v = _prop(xid, "_NET_WM_STATE")
    return set(int(_) for _ in v) if v is not None else set()


def window_rect(xid):
    """(left, top, right, bottom) in root coordinates, or None."""
    d = _get()
    if d is None or not xid:
        return None
    with _lock:
        try:
            w = _win(xid)
            g = w.get_geometry()
            t = w.translate_coords(d.screen().root, 0, 0)
        except Exception:
            return None
    x, y = -t.x, -t.y
    return (x, y, x + g.width, y + g.height)


def is_viewable(xid):
    d = _get()
    if d is None or not xid:
        return False
    with _lock:
        try:
            attrs = _win(xid).get_attributes()
        except Exception:
            return False
    if attrs.map_state != X.IsViewable:
        return False
    return atom("_NET_WM_STATE_HIDDEN") not in window_state(xid)


def is_iconic(xid):
    return atom("_NET_WM_STATE_HIDDEN") in window_state(xid)


def is_maximized(xid):
    st = window_state(xid)
    return (
        atom("_NET_WM_STATE_MAXIMIZED_VERT") in st
        and atom("_NET_WM_STATE_MAXIMIZED_HORZ") in st
    )


def pointer():
    """(x, y, button/modifier mask) of the pointer in root coordinates."""
    d = _get()
    if d is None:
        return (0, 0, 0)
    with _lock:
        try:
            p = d.screen().root.query_pointer()
        except Exception:
            return (0, 0, 0)
    return (p.root_x, p.root_y, p.mask)


def client_at_pointer():
    """Top-level client window (the one with WM_STATE) under the pointer."""
    d = _get()
    if d is None:
        return 0
    wm_state = atom("WM_STATE")
    with _lock:
        try:
            w = d.screen().root
            child = w.query_pointer().child
            # descend until a window carrying WM_STATE
            depth = 0
            while child and depth < 16:
                if child.get_full_property(wm_state, X.AnyPropertyType) is not None:
                    return int(child.id)
                nxt = child.query_pointer().child
                if not nxt:
                    break
                child = nxt
                depth += 1
            return int(child.id) if child else 0
        except Exception:
            return 0


def keymap_state():
    d = _get()
    if d is None:
        return b""
    with _lock:
        try:
            return bytes(d.query_keymap())
        except Exception:
            return b""


def keysym_to_keycode(keysym):
    d = _get()
    if d is None or not keysym:
        return 0
    with _lock:
        return d.keysym_to_keycode(keysym)


# ---- requests -----------------------------------------------------------


def _client_message(xid, msg_type, data):
    d = _get()
    if d is None or not xid:
        return
    ev = xevent.ClientMessage(
        window=_win(xid),
        client_type=atom(msg_type),
        data=(32, (list(data) + [0, 0, 0, 0, 0])[:5]),
    )
    with _lock:
        try:
            d.screen().root.send_event(
                ev,
                event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
            )
            d.flush()
        except Exception:
            pass


_NET_WM_STATE_REMOVE = 0
_NET_WM_STATE_ADD = 1


def set_state(xid, name, on):
    _client_message(
        xid,
        "_NET_WM_STATE",
        [_NET_WM_STATE_ADD if on else _NET_WM_STATE_REMOVE, atom(name), 0, 1],
    )


def activate(xid):
    # source indication 2 = pager/user action, honoured by most WMs
    _client_message(xid, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])


def set_window_types(xid, names):
    d = _get()
    if d is None or not xid:
        return
    with _lock:
        try:
            _win(xid).change_property(
                atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32, [atom(n) for n in names]
            )
            d.flush()
        except Exception:
            pass


def is_mapped(xid):
    d = _get()
    if d is None or not xid:
        return False
    with _lock:
        try:
            return _win(xid).get_attributes().map_state != X.IsUnmapped
        except Exception:
            return False


def set_state_property(xid, name, on):
    """Edit _NET_WM_STATE directly (for windows that are not mapped yet; the
    WM reads it at map time)."""
    d = _get()
    if d is None or not xid:
        return
    a = atom(name)
    with _lock:
        try:
            w = _win(xid)
            p = w.get_full_property(atom("_NET_WM_STATE"), Xatom.ATOM)
            cur = [int(_) for _ in p.value] if p is not None else []
            if on and a not in cur:
                cur.append(a)
            elif not on and a in cur:
                cur.remove(a)
            else:
                return
            w.change_property(atom("_NET_WM_STATE"), Xatom.ATOM, 32, cur)
            d.flush()
        except Exception:
            pass


def set_skip_taskbar(xid, skip):
    if is_mapped(xid):
        set_state(xid, "_NET_WM_STATE_SKIP_TASKBAR", skip)
    else:
        set_state_property(xid, "_NET_WM_STATE_SKIP_TASKBAR", skip)


def set_input_passthrough(xid, on):
    """Click-through: an empty input shape lets pointer events fall to the
    windows below (also honoured by Xwayland for the Wayland surface)."""
    d = _get()
    if d is None or not xid or not d.has_extension("SHAPE"):
        return False
    from Xlib.ext import shape

    with _lock:
        try:
            w = _win(xid)
            if on:
                w.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
            else:
                w.shape_mask(shape.SO.Set, shape.SK.Input, 0, 0, X.NONE)
            d.flush()
            return True
        except Exception:
            return False


def set_accepts_focus(xid, accepts):
    d = _get()
    if d is None or not xid:
        return
    from Xlib import Xutil

    with _lock:
        try:
            w = _win(xid)
            hints = w.get_wm_hints() or {}
            flags = getattr(hints, "flags", 0) | Xutil.InputHint
            w.set_wm_hints(flags=flags, input=1 if accepts else 0)
            d.flush()
        except Exception:
            pass


def configure(xid, x=None, y=None, w=None, h=None):
    d = _get()
    if d is None or not xid:
        return
    kw = {}
    if x is not None:
        kw["x"], kw["y"] = int(x), int(y)
    if w is not None:
        kw["width"], kw["height"] = max(1, int(w)), max(1, int(h))
    if not kw:
        return
    with _lock:
        try:
            _win(xid).configure(**kw)
            d.flush()
        except Exception:
            pass


def fake_key(keycode, press):
    d = _get()
    if d is None or not keycode or not d.has_extension("XTEST"):
        return
    from Xlib.ext import xtest

    with _lock:
        try:
            xtest.fake_input(d, X.KeyPress if press else X.KeyRelease, keycode)
            d.sync()
        except Exception:
            pass


def fake_button(button, press):
    d = _get()
    if d is None or not d.has_extension("XTEST"):
        return
    from Xlib.ext import xtest

    with _lock:
        try:
            xtest.fake_input(d, X.ButtonPress if press else X.ButtonRelease, button)
            d.sync()
        except Exception:
            pass


def warp_pointer(x, y):
    d = _get()
    if d is None:
        return
    with _lock:
        try:
            d.screen().root.warp_pointer(int(x), int(y))
            d.flush()
        except Exception:
            pass
