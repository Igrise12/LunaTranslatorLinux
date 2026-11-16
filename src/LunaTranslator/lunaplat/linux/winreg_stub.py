"""Minimal winreg replacement: an empty registry.

Every lookup fails the same way a missing key does on Windows
(FileNotFoundError, a subclass of OSError), which callers already handle.
"""

HKEY_CLASSES_ROOT = 0x80000000
HKEY_CURRENT_USER = 0x80000001
HKEY_LOCAL_MACHINE = 0x80000002
HKEY_USERS = 0x80000003
HKEY_CURRENT_CONFIG = 0x80000005

KEY_QUERY_VALUE = 0x0001
KEY_SET_VALUE = 0x0002
KEY_ENUMERATE_SUB_KEYS = 0x0008
KEY_READ = 0x20019
KEY_WRITE = 0x20006
KEY_ALL_ACCESS = 0xF003F
KEY_WOW64_64KEY = 0x0100
KEY_WOW64_32KEY = 0x0200

REG_SZ = 1
REG_EXPAND_SZ = 2
REG_BINARY = 3
REG_DWORD = 4
REG_MULTI_SZ = 7
REG_QWORD = 11


class _Missing(FileNotFoundError):
    def __init__(self):
        super().__init__(2, "The system cannot find the file specified (no registry on this platform)")


def _missing(*args, **kwargs):
    raise _Missing()


ConnectRegistry = _missing
OpenKey = _missing
OpenKeyEx = _missing
CreateKey = _missing
CreateKeyEx = _missing
QueryValue = _missing
QueryValueEx = _missing
EnumKey = _missing
EnumValue = _missing
SetValue = _missing
SetValueEx = _missing
DeleteKey = _missing
DeleteValue = _missing
QueryInfoKey = _missing


def CloseKey(hkey):
    pass


def ExpandEnvironmentStrings(s):
    import os

    return os.path.expandvars(s)
