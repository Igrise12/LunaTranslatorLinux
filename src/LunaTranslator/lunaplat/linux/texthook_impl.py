"""Linux backend for textio/textsource/texthook.py.

The game runs under Wine, so the LunaHost DLL runs in a LunaBridge helper
inside the game's prefix (lunaplat.linux.wine). texthook.py itself stays
unchanged; after it is imported we:

* make its CDLL("…/LunaHost64.dll") return the bridge proxy, so initdll()
  binds the same Luna_* names;
* swap its module-level NativeUtils / windows / LunaSubProcess for adapters
  whose process functions work in Windows pids through the bridge (window
  lookups stay on the X11 side);
* wrap texthook.start() to translate the game's Unix pids into its Windows
  pids, starting the bridge first if needed.

Without bridge binaries (files/LunaHook/LunaBridge*.exe) hooking stays a
no-op and the other text sources keep working.
"""

import os
import threading

import lunaplat


class _Passthrough(object):
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


class _NativeUtilsAdapter(_Passthrough):
    def __init__(self, real, mgr):
        super().__init__(real)
        self._mgr = mgr

    def ListProcess(self, exe=None):
        if exe is None:
            return self._real.ListProcess()
        return self._mgr.windows_pids(exe)

    def Is64bit(self, winpid):
        return self._mgr.is64(winpid)

    def test_injectable(self, *pids):
        return True

    def collect_running_pids(self, pids):
        return [p for p in pids if self._mgr.alive(p)]


class _ProcHandle(object):
    def __init__(self, pid):
        self.pid = pid


class _WindowsAdapter(_Passthrough):
    def __init__(self, real, mgr):
        super().__init__(real)
        self._mgr = mgr

    def OpenProcess(self, access, inherit, pid):
        return _ProcHandle(pid)

    def WaitForSingleObject(self, handle, ms=None):
        if isinstance(handle, _ProcHandle):
            self._mgr.wait_exit(handle.pid)
        return 0


class _LunaSubProcessAdapter(_Passthrough):
    def __init__(self, real, mgr):
        super().__init__(real)
        self._mgr = mgr

    def dllinject_run(self, pids, bit64, dll):
        return self._mgr.inject(pids, dll)

    def dllinject_elevated(self, pids, bit64, dll):
        return 42 if self._mgr.inject(pids, dll) else 5

    def listpm(self, pids, is64bit=True):
        return self._mgr.list_modules(pids)


def _host_cdll_factory(mgr):
    def cdll(path, *a, **k):
        name = os.path.basename(str(path)).lower()
        if name in ("lunahost64.dll", "lunahost32.dll"):
            return mgr.library()
        return lunaplat.load_library(path)

    return cdll


def patch(mod):
    from lunaplat.linux.wine import launcher

    if not launcher.available():
        # no Wine-side binaries: keep hooking as a harmless no-op
        mod.CDLL = lunaplat.load_library
        return

    from lunaplat.linux.wine.hostproxy import manager

    mgr = manager()
    mgr.proxy.ThreadParam = mod.ThreadParam
    mod.CDLL = _host_cdll_factory(mgr)
    mod.NativeUtils = _NativeUtilsAdapter(mod.NativeUtils, mgr)
    mod.windows = _WindowsAdapter(mod.windows, mgr)
    mod.LunaSubProcess = _LunaSubProcessAdapter(mod.LunaSubProcess, mgr)

    base = mod.texthook
    base_start = base.start

    def start(self, hwnd, pids, gamepath, gameuid, autostart=False):
        def run():
            winpids = mgr.windows_pids(gamepath, pids)
            if not winpids:
                print("[LunaBridge] no Wine process found for", gamepath)
                return
            base_start(self, hwnd, winpids, gamepath, gameuid, autostart)

        from lunaplat.linux.qtthread import in_gui_thread

        # launching a bridge can take seconds: never block the GUI thread
        if in_gui_thread():
            threading.Thread(target=run, daemon=True).start()
        else:
            run()

    base.start = start
