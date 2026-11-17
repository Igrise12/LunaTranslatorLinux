"""PyQt5-style compatibility for PyQt6 (Linux only).

Upstream develops against PyQt5 and keeps writing Qt.UserRole,
Qt.AlignLeft, Qt.WA_DeleteOnClose, dialog.exec_() and so on. PyQt6 only
has scoped enums (Qt.ItemDataRole.UserRole) and exec(). Rather than editing
every upstream file, promote enum members onto their classes and add
exec_ aliases, the same approach qtpy uses.
"""

import enum
import importlib

_MODULES = (
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
    "PyQt6.QtNetwork",
    "PyQt6.QtMultimedia",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtDBus",
)

_done = set()


def _enum_value(self):
    return self.value


def _make_int_convertible(enum_cls):
    """PyQt5 enums/flags were ints; PyQt6 ones are enum.Enum/Flag and reject
    int(). Upstream code does int(QStyle.State_Enabled) and the like."""
    if issubclass(enum_cls, int):
        return
    for name in ("__int__", "__index__"):
        if name not in enum_cls.__dict__:
            try:
                setattr(enum_cls, name, _enum_value)
            except (AttributeError, TypeError):
                pass


def promote_enums(module):
    for class_name in dir(module):
        if not class_name.startswith("Q"):
            continue
        klass = getattr(module, class_name, None)
        if not isinstance(klass, type):
            continue
        for attrib_name in dir(klass):
            if not attrib_name[:1].isupper():
                continue
            attrib = getattr(klass, attrib_name, None)
            if not isinstance(attrib, enum.EnumMeta):
                continue
            _make_int_convertible(attrib)
            # __members__ includes aliases and composite flags (e.g.
            # Qt.AlignCenter = AlignHCenter | AlignVCenter), which plain
            # iteration over an enum.Flag skips
            for name, member in attrib.__members__.items():
                if name in klass.__dict__:
                    continue
                try:
                    setattr(klass, name, member)
                except (AttributeError, TypeError):
                    pass


def add_exec_aliases(module):
    for class_name in dir(module):
        klass = getattr(module, class_name, None)
        if not isinstance(klass, type):
            continue
        if "exec" in klass.__dict__ and "exec_" not in klass.__dict__:
            try:
                klass.exec_ = klass.exec
            except (AttributeError, TypeError):
                pass


def apply_all():
    for name in _MODULES:
        if name in _done:
            continue
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        promote_enums(module)
        add_exec_aliases(module)
        _done.add(name)


def _wrap_qapplication(qtsymbols_module):
    """Make `from qtsymbols import QApplication` hand out a subclass that
    installs our window-manager hints as soon as the application exists."""
    base = qtsymbols_module.QApplication

    class QApplication(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                from lunaplat.linux.qt import windowtypes

                windowtypes.install(self)
            except Exception:
                import traceback

                traceback.print_exc()
            try:
                # create the cross-thread invoker on the GUI thread up front
                from lunaplat.linux import qtthread

                qtthread._get_invoker()
            except Exception:
                import traceback

                traceback.print_exc()
            try:
                from lunaplat.linux import fonts

                fonts.ensure_fluent_icon_font()
            except Exception:
                import traceback

                traceback.print_exc()

    QApplication.__name__ = base.__name__
    QApplication.__qualname__ = base.__qualname__
    qtsymbols_module.QApplication = QApplication


def patch(qtsymbols_module):
    if not getattr(qtsymbols_module, "isqt5", True):
        apply_all()
    _wrap_qapplication(qtsymbols_module)
