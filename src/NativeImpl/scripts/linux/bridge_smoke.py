#!/usr/bin/env python3
"""End-to-end check of the Wine hook path, without the GUI.

Starts hooktarget{32,64}.exe in a Wine prefix, launches LunaBridge for that
prefix, lists processes, injects LunaHook, enables the GDI PC hooks and
waits for hooked text to arrive.

    bridge_smoke.py [--arch 64|32] [--prefix DIR] [--seconds N] [--embed]
                    [--umu [--proton DIR]] [--attach]

--umu runs the target with umu-run (Proton inside the Steam Linux Runtime
container, like a Steam game); the default prefix is then
~/.cache/lunatranslator/umu-prefix. --attach does not start the target and
uses an already running one (and leaves it running). LUNA_BRIDGE_MODE=host
or nsenter forces how the bridge is started for a containerised game.

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

import lunaplat  # noqa: E402

lunaplat.install()

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
    ap.add_argument("--embed", action="store_true", help="also test embedded translation (checked with local OCR)")
    ap.add_argument("--umu", action="store_true", help="run the target with umu-run (Proton in pressure-vessel)")
    ap.add_argument(
        "--proton",
        default=os.path.expanduser("~/.local/share/Steam/steamapps/common/Proton - Experimental"),
        help="PROTONPATH for --umu",
    )
    ap.add_argument("--attach", action="store_true", help="use an already running target")
    args = ap.parse_args()
    if args.umu and args.prefix == ap.get_default("prefix"):
        args.prefix = os.path.expanduser("~/.cache/lunatranslator/umu-prefix")

    target = os.path.join(SRC, "NativeImpl", "LunaHook", "builds", "Release_win10", "hooktarget{}.exe".format(args.arch))
    if not os.path.isfile(target):
        sys.exit("missing " + target + " (run build_winbins.sh)")

    game = start_target(args, target)

    mgr = BridgeManager()
    mgr.proxy.ThreadParam = ThreadParam
    lib = mgr.library()
    texts = []
    events = []
    embeds = []

    def on_thread(code, name, tp, embeddable):
        events.append(("thread", code, name, embeddable))
        if args.embed and embeddable:
            lib.Luna_UseEmbed(ThreadParam(tp.processId, tp.addr, tp.ctx, tp.ctx2), True)

    def on_embed(text, tp):
        # same length as the original: TextOutW keeps its character count
        n = len(embeds) + 1
        trans = ("EMBEDDED-{}".format(n) + " " * len(text))[: len(text)]
        embeds.append((text, trans))
        lib.Luna_EmbedCallback(ThreadParam(tp.processId, tp.addr, tp.ctx, tp.ctx2), text, trans)

    procs = [
        CFUNCTYPE(None, DWORD)(lambda pid: events.append(("connect", pid))),
        CFUNCTYPE(None, DWORD)(lambda pid: events.append(("disconnect", pid))),
        CFUNCTYPE(None, c_wchar_p, c_char_p, ThreadParam, c_bool)(on_thread),
        CFUNCTYPE(None, c_wchar_p, c_char_p, ThreadParam)(lambda c, n, tp: None),
        CFUNCTYPE(None, c_wchar_p, c_char_p, ThreadParam, c_wchar_p)(lambda c, n, tp, t: texts.append((c, t))),
        CFUNCTYPE(None, c_int, c_wchar_p)(lambda t, s: events.append(("info", t, s))),
        CFUNCTYPE(None, DWORD, c_uint64, c_wchar_p)(lambda p, a, c: events.append(("hookinsert", c))),
        CFUNCTYPE(None, c_wchar_p, ThreadParam)(on_embed),
        CFUNCTYPE(c_void_p, c_wchar_p)(lambda s: lib.Luna_AllocString(s)),
        CFUNCTYPE(None, c_wchar_p, c_wchar_p, c_wchar_p)(lambda a, b, c: None),
    ]
    lib.Luna_Start(*procs)
    lib.Luna_Settings(500, 932, 3000, 1000000, True)

    # wait for the target's process to show up, then map it to Windows pids
    deadline = time.time() + 30
    winpids = []
    if args.umu:
        deadline += 90  # first run creates the Proton prefix
    while time.time() < deadline and not winpids:
        time.sleep(1)
        winpids = mgr.windows_pids(os.path.realpath(target))
    print("windows pids:", winpids)
    if not winpids:
        game.kill()
        sys.exit("FAIL: bridge did not find the target process")
    conn = mgr.proxy.conn_for(winpids[0])
    ctx = mgr.context_for(conn)
    print("bridge:", conn.arch, "bit, prefix", conn.prefix, "mode", ctx.mode if ctx else None, ctx)

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

    embed_ok = None
    if args.embed:
        embed_ok = check_embed(args, lib, winpids, embeds, end, game)

    print("events:", [e for e in events if e[0] != "info"][:12])
    print("texts:", texts[:5])
    game.kill()
    for c in list(mgr.server.connections):
        c.close()
    ok = any("テスト" in t for _, t in texts) and embed_ok is not False
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


class _Game(object):
    """Stands in for the Popen of the target: kill() ends the Wine process
    (and with it umu-run / the container) by pid."""

    def __init__(self, popen, target):
        self.popen = popen
        self.target = os.path.realpath(target)

    @property
    def pid(self):
        from lunaplat.linux import proc

        for path, pids in proc.list_process_groups().items():
            if os.path.realpath(path) == self.target:
                return pids[0]
        return self.popen.pid if self.popen else 0

    def kill(self):
        import signal
        from lunaplat.linux import proc

        if self.popen is None:
            return  # --attach: not ours to stop

        for path, pids in proc.list_process_groups().items():
            if os.path.realpath(path) == self.target:
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
        if self.popen:
            try:
                self.popen.wait(30)
            except subprocess.TimeoutExpired:
                self.popen.kill()


def start_target(args, target):
    if args.attach:
        return _Game(None, target)
    os.makedirs(args.prefix, exist_ok=True)
    if args.umu:
        umu = shutil.which("umu-run")
        if not umu:
            sys.exit("umu-run not found")
        env = dict(
            os.environ,
            WINEPREFIX=args.prefix,
            PROTONPATH=args.proton,
            GAMEID="umu-default",
            UMU_RUNTIME_UPDATE="0",
            WINEDEBUG="-all",
        )
        p = subprocess.Popen([umu, target], env=env, stdin=subprocess.DEVNULL, start_new_session=True)
        print("started target with umu-run, pid", p.pid)
        return _Game(p, target)
    env = dict(os.environ, WINEPREFIX=args.prefix, WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=")
    p = subprocess.Popen([shutil.which("wine"), target], env=env)
    print("started target, unix pid", p.pid)
    return _Game(p, target)


def check_embed(args, lib, winpids, embeds, end, game):
    """Insert an embeddable copy of the TextOutW hook, let on_thread enable
    embedding and on_embed answer with "EMBEDDED-n", then OCR the window."""
    code = "ENHQ10@0:gdi32.dll:TextOutW" if args.arch == "32" else "ENHQ-24@0:gdi32.dll:TextOutW"
    for pid in winpids:
        # waittime 2000ms, charset 2, displaymode TRANS (0)
        lib.Luna_SettingsEx(pid, 2000, 2, False, "", 0, True, False, False, 1.0, True, "")
        lib.Luna_InsertHookCode(pid, code)
    while time.time() < end and len(embeds) < 3:
        time.sleep(0.5)
    print("embed requests:", embeds[:3])
    if not embeds:
        return False
    time.sleep(1.5)
    shot = grab_window_text(game.pid)
    print("window OCR:", shot)
    return bool(shot) and "EMBEDDED" in shot.replace(" ", "")


def grab_window_text(unix_pid):
    from lunaplat.linux import x11, proc
    from qtsymbols import QApplication, QImage, QRect

    app = QApplication.instance() or QApplication(sys.argv)
    pids = {unix_pid} | {p for p, _, _ in proc.list_wine_processes()}
    for xid in x11.client_list():
        if x11.window_pid(xid) in pids and "hooktarget" in x11.window_class(xid)[0].lower():
            l, t, r, b = x11.window_rect(xid)
            x11.set_state(xid, "_NET_WM_STATE_ABOVE", True)  # make sure it is visible
            time.sleep(1.0)
            from lunaplat.linux import capture

            data = capture.grab(QRect(l, t, r - l, b - t))
            if not data:
                return ""
            import importlib

            ocr = importlib.import_module("ocrengines.local").OCR("local")
            return " ".join(ocr.ocr(QImage.fromData(data)).texts)
    return ""


if __name__ == "__main__":
    main()
