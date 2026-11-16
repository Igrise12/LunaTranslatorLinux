"""Platform dispatch for LunaTranslator.

This module is imported on every platform, including the Python 3.7
Windows runtime, so it must stay 3.7-compatible and must not change any
Windows behaviour: on Windows every function here is a pass-through.

On Linux, install() sets up the shims (ctypes.windll stubs, winreg stub,
proxy, os.startfile) and the post-import patch registry that swaps Linux
implementations into windows.py / NativeUtils.py / texthook.py after they
are imported. See lunaplat/linux/__init__.py.
"""

import os
import sys

iswindows = os.name == "nt"
islinux = sys.platform.startswith("linux")

_installed = False


def install():
    global _installed
    if _installed or iswindows:
        return
    _installed = True
    from lunaplat.linux import install_linux

    install_linux()


# Windows DLL name -> Linux library. A value starting with "lib" and not
# found next to the app is treated as a system soname (resolved by ld.so).
_DLLMAP = {
    "NativeUtils.dll": "libNativeUtils.so",
    "CVUtils.dll": "libCVUtils.so",
    "libcurl.dll": "libcurl.so.4",
    "libcurl-x64.dll": "libcurl.so.4",
}


def dllpath(base, name):
    if iswindows:
        return os.path.join(base, name)
    mapped = _DLLMAP.get(name, name)
    local = os.path.join(base, mapped)
    if os.path.exists(local):
        return local
    if mapped != name:
        # system library, let the dynamic loader find it
        return mapped
    return local


def load_library(path):
    """CDLL on Windows; on Linux a CDLL that stubs missing symbols."""
    from ctypes import CDLL

    if iswindows:
        return CDLL(path)
    from lunaplat.stubs import LenientCDLL

    return LenientCDLL(path)


def windowsversion():
    """(major, minor, build) of Windows. On other systems, pretend to be a
    current Windows 10 so version gates pick the modern code paths."""
    if iswindows:
        import platform

        return tuple(int(_) for _ in platform.version().split("."))
    return (10, 0, 19045)


def supported(kind, key):
    """Whether a plugin (kind: fanyi/ocr/reader/cishu/...) works here."""
    if iswindows:
        return True
    from lunaplat.linux.features import supported as _supported

    return _supported(kind, key)
