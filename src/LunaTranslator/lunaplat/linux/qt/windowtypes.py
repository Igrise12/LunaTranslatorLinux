"""Window-manager hints applied to our top-level windows right before they
are mapped (QEvent.Show is delivered before the native window is shown).

* Overlay-like windows (frameless + stay-on-top or translucent) get
  _NET_WM_WINDOW_TYPE_UTILITY so tiling window managers (Hyprland, Sway,
  i3, ...) float them instead of tiling them; utility is also the right type
  for a floating tool window on stacking window managers.
* Pending skip-taskbar requests (NativeUtils.SetWindowInTaskbar) are
  re-applied, because Qt rewrites _NET_WM_STATE when mapping.
"""

import os

from qtsymbols import QEvent, QObject, Qt, QTimer, QWidget

from lunaplat.linux import x11

_skip_taskbar = {}  # xid -> bool


def native_id(w):
    """X11 window id of a widget whose native window already exists, else 0
    (never forces creation; PyQt6 has no QWidget.internalWinId)."""
    try:
        if not w.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            return 0
        return int(w.winId())
    except Exception:
        return 0


def remember_skip_taskbar(xid, skip):
    _skip_taskbar[int(xid)] = bool(skip)
    x11.set_skip_taskbar(int(xid), bool(skip))


def _is_overlay(w):
    flags = w.windowFlags()
    wtype = flags & Qt.WindowType.WindowType_Mask
    if wtype in (
        Qt.WindowType.Popup,
        Qt.WindowType.ToolTip,
        Qt.WindowType.SplashScreen,
        Qt.WindowType.Dialog,
        Qt.WindowType.Sheet,
    ):
        return False
    if not (flags & Qt.WindowType.FramelessWindowHint):
        return False
    return bool(flags & Qt.WindowType.WindowStaysOnTopHint) or w.testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground
    )


_DEBUG = os.environ.get("LUNA_DEBUG_WM") == "1"


class _ShowFilter(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            try:
                xid = native_id(obj)
                if _DEBUG:
                    print(
                        "[wm] show", type(obj).__name__, hex(xid),
                        "mapped", x11.is_mapped(xid) if xid else None,
                        "overlay", _is_overlay(obj), flush=True,
                    )
                if xid and not x11.is_mapped(xid):
                    if _is_overlay(obj):
                        x11.set_window_types(
                            xid,
                            ["_NET_WM_WINDOW_TYPE_UTILITY", "_NET_WM_WINDOW_TYPE_NORMAL"],
                        )
                    skip = _skip_taskbar.get(xid)
                    if skip is not None:
                        QTimer.singleShot(
                            0, lambda x=xid, s=skip: x11.set_skip_taskbar(x, s)
                        )
            except Exception:
                if _DEBUG:
                    import traceback

                    traceback.print_exc()
        return False


_filter = None


def install(app):
    global _filter
    if _filter is not None or not x11.available():
        return
    _filter = _ShowFilter()
    app.installEventFilter(_filter)
