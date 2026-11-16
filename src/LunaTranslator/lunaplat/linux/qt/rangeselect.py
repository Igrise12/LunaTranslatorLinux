"""Full-screen region selector, replacing NativeUtils.CreateSelectRangeWindow.

Same contract as the Win32 version: blocks (modal, on the GUI thread) and,
on success, calls
    cb(x1, y1, x2, y2, xoff, yoff, image_ptr, image_size)
where image is the encoded screenshot of the whole desktop, (x1, y1, x2, y2)
are image coordinates and (xoff, yoff) is the desktop origin of the image.
Escape or right click cancels without calling cb.
"""

from ctypes import POINTER, c_char, cast, create_string_buffer

from PyQt6.QtCore import QEventLoop
from qtsymbols import QColor, QGuiApplication, QImage, QPainter, QPen, QRect, Qt, QWidget

from lunaplat.linux import capture, x11


class _Selector(QWidget):
    def __init__(self, shot, desk, alpha, color, width):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.shot = shot
        self.desk = desk
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self.color = color
        self.pen_width = max(1, int(round(width)))
        self.start = None
        self.end = None
        self.result = None
        self.loop = QEventLoop()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(desk)

    def showEvent(self, e):
        super().showEvent(e)
        self.activateWindow()
        self.setFocus()

    def _rect(self):
        if self.start is None or self.end is None:
            return QRect()
        return QRect(self.start, self.end).normalized()

    def paintEvent(self, e):
        p = QPainter(self)
        if self.shot is not None and not self.shot.isNull():
            p.drawImage(self.rect(), self.shot)
        dim = QColor(0, 0, 0, int(255 * self.alpha))
        r = self._rect()
        if r.isValid():
            # dim everything but the selection
            full = self.rect()
            p.fillRect(QRect(full.left(), full.top(), full.width(), r.top() - full.top()), dim)
            p.fillRect(QRect(full.left(), r.bottom() + 1, full.width(), full.bottom() - r.bottom()), dim)
            p.fillRect(QRect(full.left(), r.top(), r.left() - full.left(), r.height()), dim)
            p.fillRect(QRect(r.right() + 1, r.top(), full.right() - r.right(), r.height()), dim)
            pen = QPen(self.color)
            pen.setWidth(self.pen_width)
            p.setPen(pen)
            p.drawRect(r)
        else:
            p.fillRect(self.rect(), dim)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.finish(None)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.start = self.end = e.position().toPoint()
            self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        self.end = e.position().toPoint()
        r = self._rect()
        self.finish(r if r.width() > 2 and r.height() > 2 else None)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(None)

    def finish(self, rect):
        self.result = rect
        self.hide()
        self.loop.quit()


def select_range(parent, alpha, r, g, b, width, cb):
    desk = capture.virtual_geometry()
    data = capture.grab(desk)
    shot = QImage.fromData(data) if data else QImage()
    w = _Selector(shot, desk, alpha, QColor(int(r), int(g), int(b)), width)
    w.winId()
    x11.set_window_types(int(w.winId()), ["_NET_WM_WINDOW_TYPE_UTILITY", "_NET_WM_WINDOW_TYPE_NORMAL"])
    if len(QGuiApplication.screens()) == 1:
        w.showFullScreen()
    else:
        w.show()
        w.setGeometry(desk)
    w.raise_()
    w.loop.exec()
    rect = w.result
    w.deleteLater()
    if rect is None or not data:
        return
    # selection in widget coords -> image coords (image may be scaled)
    sx = shot.width() / float(max(1, desk.width()))
    sy = shot.height() / float(max(1, desk.height()))
    x1 = int(rect.left() * sx)
    y1 = int(rect.top() * sy)
    x2 = int((rect.right() + 1) * sx)
    y2 = int((rect.bottom() + 1) * sy)
    buf = create_string_buffer(data, len(data))
    cb(x1, y1, x2, y2, desk.x(), desk.y(), cast(buf, POINTER(c_char)), len(data))
