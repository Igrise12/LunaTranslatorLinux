"""Stand-ins for native symbols that do not exist on this platform.

A StubFunction behaves like a ctypes foreign function: it accepts
argtypes/restype/errcheck assignments and, when called, returns the zero
value of its restype (NULL, 0, False, None). Callers in this codebase already
treat those values as "failed / not available".

Set LUNA_PLATFORM_STRICT=1 to make stubs raise instead, and
LUNA_PLATFORM_REPORT=1 to print, at exit, every stub that was called. That
list is the remaining porting work.
"""

import atexit
import ctypes
import os
import sys
import threading

STRICT = os.environ.get("LUNA_PLATFORM_STRICT") == "1"
REPORT = os.environ.get("LUNA_PLATFORM_REPORT") == "1"

_called = {}
_called_lock = threading.Lock()


class StubNotImplemented(NotImplementedError):
    pass


def _zero(restype):
    if restype is None:
        return None
    try:
        if isinstance(restype, type) and issubclass(restype, ctypes.Structure):
            return restype()
    except TypeError:
        pass
    if restype in (ctypes.c_char_p, ctypes.c_wchar_p, ctypes.c_void_p):
        return None
    if restype in (ctypes.c_float, ctypes.c_double):
        return 0.0
    if restype is ctypes.c_bool:
        return False
    try:
        if isinstance(restype, type) and issubclass(restype, ctypes._Pointer):
            return None
    except TypeError:
        pass
    # c_int and the other integer types, plus classes such as AutoHandle
    # that derive from an integer type.
    try:
        return restype(0) if not issubclass(restype, ctypes._SimpleCData) else 0
    except Exception:
        return 0


class StubFunction(object):
    def __init__(self, libname, name):
        self.__name__ = name
        self._libname = libname
        self.argtypes = None
        self.restype = ctypes.c_int
        self.errcheck = None

    def __call__(self, *args, **kwargs):
        key = "{}!{}".format(self._libname, self.__name__)
        with _called_lock:
            first = key not in _called
            _called[key] = _called.get(key, 0) + 1
        if STRICT:
            raise StubNotImplemented(key)
        if first and REPORT:
            sys.stderr.write("[lunaplat] stub called: {}\n".format(key))
        return _zero(self.restype)

    def __bool__(self):
        # `if lib.Func:` style feature probes should see "not available"
        return False

    __nonzero__ = __bool__

    def __repr__(self):
        return "<StubFunction {}!{}>".format(self._libname, self.__name__)


class StubLibrary(object):
    """Library whose every attribute is a StubFunction (cached)."""

    def __init__(self, name):
        self._name = name
        self._funcs = {}

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        f = self._funcs.get(name)
        if f is None:
            f = StubFunction(self._name, name)
            self._funcs[name] = f
        return f

    def __getitem__(self, name):
        return self.__getattr__(name)


class StubLibraryLoader(object):
    """Replacement for ctypes.windll on systems without stdcall DLLs."""

    def __init__(self):
        self._libs = {}

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self.LoadLibrary(name)

    def __getitem__(self, name):
        return self.LoadLibrary(name)

    def LoadLibrary(self, name):
        key = os.path.basename(str(name)).lower()
        lib = self._libs.get(key)
        if lib is None:
            lib = StubLibrary(key)
            self._libs[key] = lib
        return lib


class LenientCDLL(object):
    """CDLL wrapper: real symbols when present, StubFunction otherwise.

    If the library itself cannot be loaded, every symbol is a stub, so the
    importing module still loads and features degrade instead of crashing.
    """

    def __init__(self, path):
        self._path = path
        self._name = os.path.basename(str(path))
        self._funcs = {}
        try:
            self._lib = ctypes.CDLL(path)
        except OSError as e:
            sys.stderr.write("[lunaplat] cannot load {}: {}\n".format(path, e))
            self._lib = None

    @property
    def loaded(self):
        return self._lib is not None

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        f = self._funcs.get(name)
        if f is not None:
            return f
        f = None
        if self._lib is not None:
            try:
                f = getattr(self._lib, name)
            except AttributeError:
                f = None
        if f is None:
            f = StubFunction(self._name, name)
        self._funcs[name] = f
        return f

    def __getitem__(self, name):
        return self.__getattr__(name)


def called_stubs():
    with _called_lock:
        return dict(_called)


def _report():
    if not REPORT:
        return
    calls = called_stubs()
    if not calls:
        return
    sys.stderr.write("[lunaplat] stubs called this session:\n")
    for k in sorted(calls):
        sys.stderr.write("  {:6d}  {}\n".format(calls[k], k))


atexit.register(_report)
