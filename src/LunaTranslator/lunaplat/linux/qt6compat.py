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
            for member in attrib:
                if member.name in klass.__dict__:
                    continue
                try:
                    setattr(klass, member.name, member)
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


def patch(qtsymbols_module):
    if getattr(qtsymbols_module, "isqt5", True):
        return
    apply_all()
