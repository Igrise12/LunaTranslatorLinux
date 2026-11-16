"""Linux support for gui/fluent/frameless.py (FluentFramelessWindowMixin).

On Windows the mixin keeps a native frame and strips the caption through
WM_NCCALCSIZE, then answers WM_NCHITTEST for dragging and resizing. On
Linux there are no such messages, so:

* when a window installs its FluentTitleBar, it becomes a frameless window
  (otherwise the window manager would add a second title bar);
* an application event filter turns presses on the title bar / borders into
  QWindow.startSystemMove() / startSystemResize(), reusing the mixin's own
  _hit_test() so the drag area and interactive title-bar widgets behave the
  same as on Windows; double-clicking the caption toggles maximize.
"""

from qtsymbols import QApplication, QEvent, QObject, QPoint, Qt

HTCAPTION = 2
_EDGES = {
    10: Qt.Edge.LeftEdge,
    11: Qt.Edge.RightEdge,
    12: Qt.Edge.TopEdge,
    13: Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
    14: Qt.Edge.TopEdge | Qt.Edge.RightEdge,
    15: Qt.Edge.BottomEdge,
    16: Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
    17: Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
}
_BORDER = 6  # logical px of resize border


def _fluent_window(widget):
    try:
        win = widget.window()
    except Exception:
        return None
    if win is None or not getattr(win, "_fluent_frameless_linux", False):
        return None
    if not win._fluent_frameless_active():
        return None
    return win


class _MoveResizeFilter(QObject):
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return False
        if ev.button() != Qt.MouseButton.LeftButton:
            return False
        win = _fluent_window(obj)
        if win is None:
            return False
        gp = ev.globalPosition().toPoint()
        try:
            hit = win._hit_test(gp)
        except Exception:
            return False
        if hit is None:
            return False
        handle = win.windowHandle()
        if handle is None:
            return False
        if hit == HTCAPTION:
            if t == QEvent.Type.MouseButtonDblClick:
                win.showNormal() if win.isMaximized() else win.showMaximized()
                return True
            handle.startSystemMove()
            return True
        edges = _EDGES.get(hit)
        if edges is not None and t == QEvent.Type.MouseButtonPress:
            handle.startSystemResize(edges)
            return True
        return False


_filter = None


def _install_filter():
    global _filter
    app = QApplication.instance()
    if _filter is None and app is not None:
        _filter = _MoveResizeFilter()
        app.installEventFilter(_filter)


def patch(mod):
    mixin = mod.FluentFramelessWindowMixin

    def _get_bar(self):
        return self.__dict__.get("_fluent_title_bar_linux")

    def _set_bar(self, bar):
        self.__dict__["_fluent_title_bar_linux"] = bar
        if bar is not None:
            self._fluent_frameless_linux = True
            if not self.isVisible():
                self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            _install_filter()

    mixin._fluent_title_bar = property(_get_bar, _set_bar)

    # replacements for the Win32 coordinate helpers used by _hit_test
    def _unpack_nc_lparam(l_param):
        if isinstance(l_param, QPoint):
            return l_param.x(), l_param.y()
        return l_param

    def _native_local_pos(self, x, y):
        return self.mapFromGlobal(QPoint(x, y))

    def _frame_thickness(self):
        return int(_BORDER * (self.devicePixelRatioF() or 1.0))

    def _setup_frameless_native(self):
        pass

    # The Python nativeEvent override only handles Win32 messages; on xcb it
    # would route every X event through Python, and PyQt6 crashes converting
    # its result. Drop it so Qt's own handler runs.
    if "nativeEvent" in mixin.__dict__:
        delattr(mixin, "nativeEvent")

    mixin._unpack_nc_lparam = staticmethod(_unpack_nc_lparam)
    mixin._native_local_pos = _native_local_pos
    mixin._frame_thickness = _frame_thickness
    mixin._setup_frameless_native = _setup_frameless_native

    # Qt 6.12.0: QMainWindow.setMenuWidget() on a window that has never been
    # shown schedules the new (non-QMenuBar) widget for deletion, so the
    # title bar vanishes. Defer the call to the first show.
    orig_show = mixin.showEvent

    def setMenuWidget(self, widget):
        base = super(mixin, self)
        if self.isVisible() or widget is None:
            self.__dict__.pop("_pending_menu_widget_linux", None)
            return base.setMenuWidget(widget)
        self.__dict__["_pending_menu_widget_linux"] = widget

    def menuWidget(self):
        pending = self.__dict__.get("_pending_menu_widget_linux")
        if pending is not None:
            return pending
        return super(mixin, self).menuWidget()

    def showEvent(self, event):
        pending = self.__dict__.pop("_pending_menu_widget_linux", None)
        if pending is not None:
            super(mixin, self).setMenuWidget(pending)
        return orig_show(self, event)

    mixin.setMenuWidget = setMenuWidget
    mixin.menuWidget = menuWidget
    mixin.showEvent = showEvent
