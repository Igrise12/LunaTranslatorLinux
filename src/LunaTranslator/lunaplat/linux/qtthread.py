"""Run a callable on the Qt GUI thread and wait for its result.

Several Win32 APIs that the app calls from worker threads (clipboard, window
queries) map to Qt objects that must only be used from the GUI thread.
"""

import threading

from qtsymbols import QApplication, QObject, QThread, Qt, pyqtSignal


class _Invoker(QObject):
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job):
        fn, box, done = job
        try:
            box.append((True, fn()))
        except BaseException as e:
            box.append((False, e))
        finally:
            done.set()


_invoker = None
_invoker_lock = threading.Lock()


def _get_invoker():
    global _invoker
    if _invoker is None:
        with _invoker_lock:
            if _invoker is None:
                app = QApplication.instance()
                inv = _Invoker()
                if app is not None:
                    inv.moveToThread(app.thread())
                _invoker = inv
    return _invoker


def in_gui_thread():
    app = QApplication.instance()
    return app is not None and QThread.currentThread() == app.thread()


def run_in_gui(fn, timeout=5.0, default=None):
    """Call fn() on the GUI thread. Returns default if there is no
    QApplication yet or the GUI thread does not answer in time."""
    app = QApplication.instance()
    if app is None:
        return default
    if in_gui_thread():
        return fn()
    box = []
    done = threading.Event()
    _get_invoker().call.emit((fn, box, done))
    if not done.wait(timeout):
        return default
    ok, value = box[0]
    if ok:
        return value
    raise value
