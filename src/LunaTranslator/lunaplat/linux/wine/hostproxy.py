"""LunaHost API proxy: the Luna_* functions texthook.py expects, served by
LunaBridge processes running inside Wine prefixes.

texthook.initdll() does CDLL("files/LunaHook/LunaHost64.dll") and binds
Luna_* with argtypes; on Linux that CDLL returns `proxy` (see
lunaplat.linux.texthook_impl), so initdll runs unchanged. Calls are routed
to the bridge that owns the target (Windows) pid; callbacks registered with
Luna_Start are remembered and replayed to every bridge that connects, and
bridge events are turned back into calls of those ctypes callbacks.

The hook side works in Windows pids (of the game's wineserver). The
BridgeManager below maps a game found on the Linux side (Unix exe path /
Unix pids) to its Windows pids.
"""

import ctypes
import os
import threading
import time
import traceback

from lunaplat.linux import proc
from lunaplat.linux.wine import launcher
from lunaplat.linux.wine import protocol as P
from lunaplat.linux.wine.server import get_server


class _Fn(object):
    """Callable that tolerates `.argtypes = ...` / `.restype = ...`."""

    def __init__(self, fn):
        self._fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._fn(*args)


def _tp(tp):
    return P.TP((int(tp.processId), int(tp.addr), int(tp.ctx), int(tp.ctx2)))


class LunaHostProxy(object):
    def __init__(self):
        self.callbacks = None  # the 10 ctypes callbacks from Luna_Start
        self.settings = None
        self.reset_lang = False
        self.conns = []
        self.pid_conn = {}
        self.lock = threading.Lock()
        self.find_callbacks = {}
        self.allocs = {}
        self.exit_events = {}
        self.ThreadParam = None  # set by texthook_impl

    # -- bridge management -------------------------------------------------
    def attach(self, conn):
        conn.on_event = self._on_event
        conn.on_close = self._on_close
        with self.lock:
            self.conns.append(conn)
        if self.callbacks is not None:
            conn.post(P.START)
        if self.settings is not None:
            conn.post(P.SETTINGS, *self.settings)
        if self.reset_lang:
            conn.post(P.RESET_LANG)

    def _on_close(self, conn):
        with self.lock:
            if conn in self.conns:
                self.conns.remove(conn)
            gone = [pid for pid, c in self.pid_conn.items() if c is conn]
            for pid in gone:
                self.pid_conn.pop(pid, None)
        # the processes are unreachable now: report them as disconnected
        for pid in gone:
            self._cb(1, pid)
            ev = self.exit_events.get(pid)
            if ev:
                ev.set()

    def bind_pid(self, pid, conn):
        with self.lock:
            self.pid_conn[int(pid)] = conn

    def conn_for(self, pid=None):
        with self.lock:
            if pid is not None and int(pid) in self.pid_conn:
                c = self.pid_conn[int(pid)]
                if c.alive:
                    return c
            live = [c for c in self.conns if c.alive]
        return live[-1] if live else None

    def _all(self):
        with self.lock:
            return [c for c in self.conns if c.alive]

    # -- events from bridges ----------------------------------------------
    def _cb(self, idx, *args):
        if self.callbacks is None:
            return None
        try:
            return self.callbacks[idx](*args)
        except Exception:
            traceback.print_exc()
            return None

    def _mk_tp(self, t):
        return self.ThreadParam(*t)

    def _on_event(self, conn, msgtype, msgid, v):
        if msgtype == P.PROC_CONNECT:
            self.bind_pid(v[0], conn)
            self._cb(0, v[0])
        elif msgtype == P.PROC_DISCONNECT:
            self._cb(1, v[0])
        elif msgtype == P.THREAD_CREATE:
            self._cb(2, v[0], v[1], self._mk_tp(v[2]), bool(v[3]))
        elif msgtype == P.THREAD_REMOVE:
            self._cb(3, v[0], v[1], self._mk_tp(v[2]))
        elif msgtype == P.OUTPUT:
            self._cb(4, v[0], v[1], self._mk_tp(v[2]), v[3])
        elif msgtype == P.HOST_INFO:
            self._cb(5, v[0], v[1])
        elif msgtype == P.HOOK_INSERT:
            self._cb(6, v[0], v[1], v[2])
        elif msgtype == P.EMBED_REQUEST:
            self._cb(7, v[0], self._mk_tp(v[1]))
        elif msgtype == P.I18N_QUERY:
            ptr = self._cb(8, v[0])
            text = v[0]
            if ptr:
                text = ctypes.wstring_at(ptr)
                self.allocs.pop(ptr, None)
            conn.reply(msgid, text)
        elif msgtype == P.EMU_GAME_INFO:
            self._cb(9, v[0], v[1], v[2])
        elif msgtype == P.FIND_HOOK_RESULT:
            cb = self.find_callbacks.get(v[0])
            if cb is not None:
                try:
                    cb(v[1], v[2])
                except Exception:
                    traceback.print_exc()
        elif msgtype == P.PROCESS_EXIT:
            ev = self.exit_events.get(v[0])
            if ev:
                ev.set()
        elif msgtype == P.LOG:
            print("[LunaBridge]", v[0] if v else "")

    # -- Luna_* API --------------------------------------------------------
    def Luna_Start(self, *callbacks):
        self.callbacks = list(callbacks)
        for c in self._all():
            c.post(P.START)

    def Luna_Settings(self, flush, codepage, maxbuf, maxhist, pchooks):
        self.settings = (int(flush), int(codepage), int(maxbuf), int(maxhist), bool(pchooks))
        for c in self._all():
            c.post(P.SETTINGS, *self.settings)

    def Luna_ResetLang(self):
        self.reset_lang = True
        for c in self._all():
            c.post(P.RESET_LANG)

    def Luna_AllocString(self, s):
        buf = ctypes.create_unicode_buffer(s or "")
        addr = ctypes.addressof(buf)
        self.allocs[addr] = buf
        return addr

    def Luna_ConnectProcess(self, pid):
        c = self.conn_for(pid)
        if c:
            c.post(P.CONNECT, int(pid))

    def Luna_CheckIfNeedInject(self, pid):
        c = self.conn_for(pid)
        r = c.call(P.NEED_INJECT, int(pid)) if c else None
        return bool(r and r[0])

    def Luna_DetachProcess(self, pid):
        c = self.conn_for(pid)
        if c:
            c.post(P.DETACH, int(pid))

    def Luna_InsertPCHooks(self, pid, which):
        c = self.conn_for(pid)
        if c:
            c.post(P.INSERT_PC_HOOKS, int(pid), int(which))

    def Luna_InsertHookCode(self, pid, code):
        c = self.conn_for(pid)
        if c:
            c.post(P.INSERT_HOOK, int(pid), code)

    def Luna_RemoveHook(self, pid, addr):
        c = self.conn_for(pid)
        if c:
            c.post(P.REMOVE_HOOK, int(pid), P.U(addr))

    def Luna_FindHooks(self, pid, sp, callback, addresses):
        c = self.conn_for(pid)
        if not c:
            return
        cls = type(sp)
        pattern = ctypes.string_at(ctypes.addressof(sp) + cls.pattern.offset, cls.pattern.size)
        fields = [
            int(pid),
            P.B(pattern),
            sp.address_method,
            sp.search_method,
            sp.length,
            sp.offset,
            sp.searchTime,
            sp.maxRecords,
            sp.codepage,
            sp.padding,
            P.U(sp.minAddress),
            P.U(sp.maxAddress),
            sp.boundaryModule,
            sp.exportModule,
            sp.text,
            bool(sp.isjithook),
            sp.sharememname,
            P.U(sp.sharememsize),
            addresses or "",
        ]
        # the bridge tags results with this request's id
        with c._pending_lock:
            msgid = c._next_id
            c._next_id += 1
        self.find_callbacks[msgid] = callback
        c._send(P.FIND_HOOKS, msgid, fields)

    def Luna_SettingsEx(self, pid, waittime, charset, charset_enabled, family, mode,
                        fastskip, clear, changesize, rel, veh, unityfontdir):
        c = self.conn_for(pid)
        if not c:
            return
        if unityfontdir and unityfontdir.startswith("/"):
            r = c.call(P.TO_DOS, unityfontdir)
            unityfontdir = r[0] if r else ""
        c.post(
            P.SETTINGS_EX, int(pid), int(waittime), int(charset), bool(charset_enabled),
            family or "", int(mode), bool(fastskip), bool(clear), bool(changesize),
            float(rel), bool(veh), unityfontdir or "",
        )

    def Luna_QueryThreadHistory(self, tp, latest, callback):
        c = self.conn_for(tp.processId)
        r = c.call(P.QUERY_HISTORY, _tp(tp), bool(latest)) if c else None
        callback(r[0] if r else "")

    def Luna_CheckIsUsingEmbed(self, tp):
        c = self.conn_for(tp.processId)
        r = c.call(P.CHECK_USING_EMBED, _tp(tp)) if c else None
        return bool(r and r[0])

    def Luna_UseEmbed(self, tp, use):
        c = self.conn_for(tp.processId)
        if c:
            c.post(P.USE_EMBED, _tp(tp), bool(use))

    def Luna_EmbedCallback(self, tp, text, trans):
        c = self.conn_for(tp.processId)
        if c:
            c.post(P.EMBED_REPLY, _tp(tp), text or "", trans or "")

    def Luna_SyncThread(self, tp, sync):
        c = self.conn_for(tp.processId)
        if c:
            c.post(P.SYNC_THREAD, _tp(tp), bool(sync))

    def __getattr__(self, name):
        raise AttributeError(name)


_LUNA_NAMES = [n for n in LunaHostProxy.__dict__ if n.startswith("Luna_")]


class _ProxyLibrary(object):
    """What CDLL("LunaHost64.dll") returns on Linux."""

    def __init__(self, proxy):
        for n in _LUNA_NAMES:
            setattr(self, n, _Fn(getattr(proxy, n)))


# ---- game <-> bridge mapping --------------------------------------------------


class BridgeManager(object):
    LAUNCH_TIMEOUT = 30

    def __init__(self):
        self.proxy = LunaHostProxy()
        self.server = get_server()
        self.server.on_connect = self.proxy.attach
        self.procinfo = {}  # (conn id, winpid) -> info dict
        self.lock = threading.Lock()
        self.launched = {}
        self.contexts = {}  # prefix -> WineContext of the last game seen there

    def library(self):
        return _ProxyLibrary(self.proxy)

    def _game_unix_pids(self, gamepath, hint_pids=()):
        from lunaplat.linux import gameident

        # only Wine processes: starting a bridge for a native program would
        # create/boot a Wine prefix (possibly ~/.wine) for nothing
        pids = [
            p
            for p in hint_pids
            if proc.is_wine_process(p) and gameident.same_game(proc.process_file_name(p), gamepath)
        ]
        if pids:
            return pids
        for path, ps in proc.list_process_groups().items():
            if gameident.same_game(path, gamepath):
                return ps
        return []

    # a bridge started by Proton itself (launch option) needs this long to
    # come up after the game's process appears
    SELF_START_WAIT = 20

    def ensure_bridge(self, unix_pid):
        from lunaplat.linux import gameident

        if not proc.is_wine_process(unix_pid):
            print("[LunaBridge] pid {} is not a Wine process, not hooking it".format(unix_pid))
            return None
        ctx = launcher.WineContext(unix_pid)
        gameident.register_prefix(ctx.prefix)
        with self.lock:
            self.contexts[ctx.prefix] = ctx
        conn = self.server.find(ctx.prefix)
        if conn:
            return conn
        if ctx.self_started_bridge():
            conn = self.server.wait_for(ctx.prefix, self.SELF_START_WAIT)
            if conn:
                return conn
        import gobject

        log = gobject.getcachedir("lunabridge.log")
        with self.lock:
            p = self.launched.get(ctx.prefix)
            if p is None or p.poll() is not None:
                try:
                    self.launched[ctx.prefix] = launcher.launch(ctx, self.server.env(), log)
                except (RuntimeError, OSError) as e:
                    print("[LunaBridge] cannot start the bridge for", ctx, ":", e)
                    return None
        return self.server.wait_for(ctx.prefix, self.LAUNCH_TIMEOUT)

    def context_for(self, conn):
        with self.lock:
            return self.contexts.get(conn.prefix_key)

    def list_processes(self, conn):
        r = conn.call(P.LIST_PROCESSES)
        out = []
        if not r:
            return out
        n = r[0]
        vals = r[1:]
        for i in range(n):
            pid, ppid, name, winpath, unixpath, is64 = vals[i * 6 : i * 6 + 6]
            info = dict(pid=pid, ppid=ppid, name=name, winpath=winpath, unixpath=unixpath, is64=bool(is64), conn=conn)
            out.append(info)
            with self.lock:
                self.procinfo[pid] = info
        return out

    def windows_pids(self, gamepath, hint_pids=()):
        """Windows pids of the game at gamepath, starting its bridge if
        needed. [] if the game is not running under Wine or no bridge."""
        unix_pids = self._game_unix_pids(gamepath, hint_pids)
        if not unix_pids:
            return []
        conn = self.ensure_bridge(unix_pids[0])
        if conn is None:
            return []
        from lunaplat.linux import gameident

        found = []
        for info in self.list_processes(conn):
            up = info["unixpath"]
            if up and gameident.same_game(up, gamepath):
                found.append(info["pid"])
                self.proxy.bind_pid(info["pid"], conn)
        return found

    def is64(self, winpid):
        info = self.procinfo.get(winpid)
        return bool(info and info["is64"])

    def alive(self, winpid):
        c = self.proxy.conn_for(winpid)
        r = c.call(P.PROCESS_ALIVE, int(winpid)) if c else None
        return bool(r and r[0])

    def wait_exit(self, winpid):
        c = self.proxy.conn_for(winpid)
        if c is None:
            return
        ev = self.proxy.exit_events.setdefault(int(winpid), threading.Event())
        c.post(P.WATCH_PROCESS, int(winpid))
        ev.wait()
        self.proxy.exit_events.pop(int(winpid), None)

    def to_dos(self, conn, path):
        r = conn.call(P.TO_DOS, path)
        return r[0] if r and r[0] else "Z:" + path.replace("/", "\\")

    def inject(self, winpids, dll_unix):
        ok = True
        for pid in winpids:
            c = self.proxy.conn_for(pid)
            if c is None:
                ok = False
                continue
            dll = os.path.abspath(dll_unix)
            ctx = self.context_for(c)
            if ctx is not None:
                # a containerised game may not see the app's folder
                try:
                    dll = ctx.visible_path(dll)
                except OSError as e:
                    print("[LunaBridge] cannot copy", dll, "into the prefix:", e)
            r = c.call(P.INJECT, int(pid), self.to_dos(c, dll), timeout=30)
            ok = ok and bool(r and r[0])
        return ok

    def list_modules(self, winpids):
        mods = []
        for pid in winpids:
            c = self.proxy.conn_for(pid)
            r = c.call(P.LIST_MODULES, int(pid)) if c else None
            if r:
                mods += r[1 : 1 + r[0]]
        return mods


_manager = None
_manager_lock = threading.Lock()


def manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BridgeManager()
        return _manager
