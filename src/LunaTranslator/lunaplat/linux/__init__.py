"""Linux bootstrap: shims and the post-import patch registry.

install_linux() runs before anything else in main.py. It:

* chooses the Qt platform (XWayland by default on Wayland sessions, because
  overlay placement/stacking and Wine game windows are X11 concepts);
* replaces ctypes.windll with a stub loader so windows.py and other modules
  that bind Win32 APIs at import time load unchanged;
* installs a winreg stub, a registry-free proxy lookup and os.startfile;
* registers a sys.meta_path hook that, right after selected modules finish
  executing, calls lunaplat.linux.<impl>.patch(module) to swap in Linux
  implementations of their high-level names. The upstream modules stay
  untouched, which keeps merges with upstream cheap.
"""

import ctypes
import importlib.abc
import importlib.util
import os
import subprocess
import sys

# module name -> "lunaplat.linux.<module>" providing patch(module)
PATCHES = {
    "qtsymbols": "lunaplat.linux.qt6compat",
    "windows": "lunaplat.linux.windows_impl",
    "NativeUtils": "lunaplat.linux.nativeutils_impl",
    "myutils.hwnd": "lunaplat.linux.hwnd_impl",
    "textio.textsource.texthook": "lunaplat.linux.texthook_impl",
    "gui.fluent.frameless": "lunaplat.linux.fluent_impl",
    "ocrengines.tesseract5": "lunaplat.linux.tools_impl:patch_tesseract",
    "translator.cdp_helper": "lunaplat.linux.tools_impl:patch_cdp",
    "myutils.config": "lunaplat.linux.gameident:patch_config",
    "gui.gamemanager.common": "lunaplat.linux.gamescan:patch_common",
    "myutils.localetools": "lunaplat.linux.launchers",
}


ORIG_ENV_KEY = "LUNA_ORIG_QT_QPA_PLATFORM"


def _choose_qt_platform():
    """Prefer XWayland (xcb) on Wayland sessions.

    Wine games are X11 clients, and an overlay that must stay on top of,
    follow and send keys to them only works as an X11 client too. A global
    QT_QPA_PLATFORM such as "wayland;xcb" (common in compositor configs) is a
    desktop-wide preference, so it is overridden; set LUNA_NATIVE_WAYLAND=1
    to keep native Wayland (limited overlay features).
    """
    if os.environ.get("LUNA_NATIVE_WAYLAND") == "1":
        return
    if not os.environ.get("DISPLAY"):
        return
    cur = os.environ.get("QT_QPA_PLATFORM", "")
    wayland_session = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )
    if not wayland_session:
        return
    if cur and "wayland" not in cur:
        return  # explicitly xcb/offscreen/etc.
    os.environ[ORIG_ENV_KEY] = cur
    os.environ["QT_QPA_PLATFORM"] = "xcb"


def child_env(extra=None):
    """Environment for processes we launch: undo our Qt platform override
    so other Qt applications start the way the user configured them."""
    env = dict(os.environ)
    if ORIG_ENV_KEY in env:
        orig = env.pop(ORIG_ENV_KEY)
        if orig:
            env["QT_QPA_PLATFORM"] = orig
        else:
            env.pop("QT_QPA_PLATFORM", None)
    if extra:
        env.update(extra)
    return env


def _install_windll_stub():
    from lunaplat.stubs import StubLibraryLoader

    if not hasattr(ctypes, "windll"):
        ctypes.windll = StubLibraryLoader()
    if not hasattr(ctypes, "WinDLL"):
        loader = ctypes.windll

        def WinDLL(name, *a, **k):
            return loader.LoadLibrary(name)

        ctypes.WinDLL = WinDLL


def _install_winreg_stub():
    if "winreg" in sys.modules:
        return
    from lunaplat.linux import winreg_stub

    sys.modules["winreg"] = winreg_stub


def _install_proxy_shim():
    import urllib.request as ur

    if not hasattr(ur, "getproxies_registry"):
        ur.getproxies_registry = ur.getproxies_environment


def _startfile(path, operation=None, *args, **kwargs):
    path = str(path)
    if not path:
        return
    opener = "xdg-open"
    try:
        subprocess.Popen(
            [opener, path],
            env=child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        sys.stderr.write("[lunaplat] {} failed: {}\n".format(opener, e))


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, inner, patcher):
        self._inner = inner
        self._patcher = patcher

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        modname, _, func = self._patcher.partition(":")
        impl = importlib.import_module(modname)
        getattr(impl, func or "patch")(module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        patcher = PATCHES.get(fullname)
        if patcher is None:
            return None
        # ask the remaining finders for the real spec
        for finder in sys.meta_path:
            if finder is self:
                continue
            find = getattr(finder, "find_spec", None)
            if find is None:
                continue
            spec = find(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _PatchingLoader(spec.loader, patcher)
                return spec
        return None


def _install_patch_registry():
    for name in PATCHES:
        if name in sys.modules:
            raise RuntimeError(
                "lunaplat.install() must run before importing {}".format(name)
            )
    sys.meta_path.insert(0, _PatchFinder())


def install_linux():
    _choose_qt_platform()
    _install_windll_stub()
    _install_winreg_stub()
    _install_proxy_shim()
    os.startfile = _startfile
    _install_patch_registry()
