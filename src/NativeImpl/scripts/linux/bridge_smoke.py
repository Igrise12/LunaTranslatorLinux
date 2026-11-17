#!/usr/bin/env python3
"""End-to-end check of the Wine hook path, without the GUI.

Starts hooktarget{32,64}.exe in a Wine prefix, launches LunaBridge for that
prefix, lists processes, injects LunaHook, enables the GDI PC hooks and
waits for hooked text to arrive.

    bridge_smoke.py [--arch 64|32] [--prefix DIR] [--seconds N]

Needs the binaries from build_winbins.sh (hooktarget is built next to the
bridge in src/NativeImpl/LunaHook/builds/Release_win10).
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(SRC, "LunaTranslator"))
os.chdir(SRC)

from ctypes import CFUNCTYPE, Structure, c_bool, c_char_p, c_int, c_uint, c_uint64, c_void_p, c_wchar_p  # noqa: E402
from ctypes.wintypes import DWORD  # noqa: E402

from lunaplat.linux.wine.hostproxy import BridgeManager  # noqa: E402


class ThreadParam(Structure):
    _fields_ = [("processId", c_uint), ("addr", c_uint64), ("ctx", c_uint64), ("ctx2", c_uint64)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="64", choices=["64", "32"])
    ap.add_argument("--prefix", default=os.path.expanduser("~/.cache/lunatranslator/smoke-prefix"))
    ap.add_argument("--seconds", type=int, default=25)
    args = ap.parse_args()

    target = os.path.join(SRC, "NativeImpl", "LunaHook", "builds", "Release_win10", "hooktarget{}.exe".format(args.arch))
    if not os.path.isfile(target):
        sys.exit("missing " + target + " (run build_winbins.sh)")

    env = dict(os.environ, WINEPREFIX=args.prefix, WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=")
    os.makedirs(args.prefix, exist_ok=True)
    game = subprocess.Popen([shutil.which("wine"), target], env=env)
    print("started target, unix pid", game.pid)

    mgr = BridgeManager()
    mgr.proxy.ThreadParam = ThreadParam
    lib = mgr.library()
    texts = []
    events = []
    procs = [
        CFUNCTYPE(None, DWORD)(lambda pid: events.append(("connect", pid))),
        CFUNCTYPE(None, DWORD)(lambda pid: events.append(("disconnect", pid))),
        CFUNCTYPE(None, c_wchar_p, c_char_p, ThreadParam, c_bool)(lambda c, n, tp, e: events.append(("thread", c, n))),
        CFUNCTYPE(None, c_wchar_p, c_char_p, ThreadParam)(lambda c, n, tp: None),
        CFUNCTYPE(None, c_wchar_p, c_char_p, ThreadParam, c_wchar_p)(lambda c, n, tp, t: texts.append((c, t))),
        CFUNCTYPE(None, c_int, c_wchar_p)(lambda t, s: events.append(("info", t, s))),
        CFUNCTYPE(None, DWORD, c_uint64, c_wchar_p)(lambda p, a, c: events.append(("hookinsert", c))),
        CFUNCTYPE(None, c_wchar_p, ThreadParam)(lambda t, tp: None),
        CFUNCTYPE(c_void_p, c_wchar_p)(lambda s: lib.Luna_AllocString(s)),
        CFUNCTYPE(None, c_wchar_p, c_wchar_p, c_wchar_p)(lambda a, b, c: None),
    ]
    lib.Luna_Start(*procs)
    lib.Luna_Settings(500, 932, 3000, 1000000, True)

    # wait for the target's process to show up, then map it to Windows pids
    deadline = time.time() + 30
    winpids = []
    while time.time() < deadline and not winpids:
        time.sleep(1)
        winpids = mgr.windows_pids(os.path.realpath(target))
    print("windows pids:", winpids)
    if not winpids:
        game.kill()
        sys.exit("FAIL: bridge did not find the target process")

    for pid in winpids:
        lib.Luna_ConnectProcess(pid)
        need = lib.Luna_CheckIfNeedInject(pid)
        print("pid", pid, "needs inject:", need, "64-bit:", mgr.is64(pid))
        if need:
            ok = mgr.inject([pid], os.path.join(SRC, "files", "LunaHook", "LunaHook{}.dll".format(args.arch)))
            print("inject ok:", ok)

    end = time.time() + args.seconds
    while time.time() < end and not any(e[0] == "connect" for e in events):
        time.sleep(0.5)
    for pid in winpids:
        lib.Luna_InsertPCHooks(pid, 0)
        lib.Luna_InsertPCHooks(pid, 1)
    while time.time() < end and not texts:
        time.sleep(0.5)

    print("events:", events[:12])
    print("texts:", texts[:5])
    game.kill()
    for c in list(mgr.server.connections):
        c.close()
    ok = any("テスト" in t for _, t in texts)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
