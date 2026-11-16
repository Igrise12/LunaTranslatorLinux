"""xdg-desktop-portal helpers over QtDBus (no extra dependencies)."""

import uuid

from PyQt6.QtCore import QEventLoop, QObject, QTimer, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusMessage

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"


def session():
    bus = QDBusConnection.sessionBus()
    return bus if bus.isConnected() else None


def new_token():
    return "luna_" + uuid.uuid4().hex[:12]


def request_path(bus, token):
    sender = bus.baseService().lstrip(":").replace(".", "_")
    return "{}/request/{}/{}".format(PATH, sender, token)


class _ResponseWaiter(QObject):
    def __init__(self):
        super().__init__()
        self.result = None
        self.loop = QEventLoop()

    @pyqtSlot(QDBusMessage)
    def on_response(self, msg):
        args = msg.arguments()
        code = args[0] if args else 2
        results = args[1] if len(args) > 1 else {}
        self.result = (int(code), results)
        self.loop.quit()


def call_with_response(interface, method, args, timeout_ms=60000):
    """Call a portal method that returns a Request handle and wait (nested
    event loop, GUI thread) for its Response. Returns (code, results) where
    code 0 = success, 1 = cancelled, 2 = other; None on failure."""
    bus = session()
    if bus is None:
        return None
    token = args[-1].get("handle_token") if isinstance(args[-1], dict) else None
    if token is None:
        return None
    waiter = _ResponseWaiter()
    path = request_path(bus, token)
    bus.connect(
        BUS,
        path,
        "org.freedesktop.portal.Request",
        "Response",
        waiter.on_response,
    )
    msg = QDBusMessage.createMethodCall(BUS, PATH, interface, method)
    msg.setArguments(list(args))
    reply = bus.call(msg)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        return None
    QTimer.singleShot(timeout_ms, waiter.loop.quit)
    if waiter.result is None:
        waiter.loop.exec()
    bus.disconnect(
        BUS, path, "org.freedesktop.portal.Request", "Response", waiter.on_response
    )
    return waiter.result
