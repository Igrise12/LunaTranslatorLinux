"""TCP endpoint that LunaBridge processes (inside Wine prefixes) connect to.

* listens on 127.0.0.1:<random port>; every bridge must present the
  per-session token in its HELLO frame;
* the endpoint and token are handed to bridges we launch through their
  environment, and to bridges started by the user (e.g. from a Steam launch
  option) through $XDG_RUNTIME_DIR/lunatranslator/bridge.txt (mode 0600);
* each connection has a reader thread (replies resolve waiting calls) and an
  ordered dispatcher thread for events, so callbacks may call back into the
  bridge without deadlocking.
"""

import hmac
import os
import queue
import secrets
import socket
import struct
import threading
import traceback

from lunaplat.linux.wine import protocol as P


def _runtime_file():
    d = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    d = os.path.join(d, "lunatranslator")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, "bridge.txt")


class BridgeConnection(object):
    def __init__(self, server, sock, hello):
        self.server = server
        self.sock = sock
        self.token, self.proto, self.arch, self.prefix, self.winpid = hello[:5]
        self.prefix_key = os.path.realpath(self.prefix) if self.prefix else ""
        self.alive = True
        self._send_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._next_id = 1
        self._events = queue.Queue()
        self.on_event = None  # callable(type, id, values)
        self.on_close = None
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._dispatcher, daemon=True).start()

    # -- sending ---------------------------------------------------------
    def _send(self, msgtype, msgid, fields):
        frame = P.encode(msgtype, msgid, fields)
        with self._send_lock:
            try:
                self.sock.sendall(frame)
                return True
            except OSError:
                self._closed()
                return False

    def post(self, msgtype, *fields):
        """Fire-and-forget command (kept in order by the bridge worker)."""
        return self._send(msgtype, 0, fields)

    def call(self, msgtype, *fields, timeout=15.0):
        """Command with a REPLY; returns its values or None."""
        with self._pending_lock:
            msgid = self._next_id
            self._next_id += 1
            ev = threading.Event()
            box = []
            self._pending[msgid] = (ev, box)
        if not self._send(msgtype, msgid, fields):
            return None
        ev.wait(timeout)
        with self._pending_lock:
            self._pending.pop(msgid, None)
        return box[0] if box else None

    def reply(self, msgid, *fields):
        return self._send(P.REPLY, msgid, fields)

    # -- receiving -------------------------------------------------------
    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("bridge closed")
            buf += chunk
        return buf

    def _reader(self):
        try:
            while True:
                (ln,) = struct.unpack("<I", self._recv_exact(4))
                body = self._recv_exact(ln)
                msgtype, msgid, vals = P.decode(body)
                if msgtype == P.REPLY:
                    with self._pending_lock:
                        waiter = self._pending.get(msgid)
                    if waiter:
                        waiter[1].append(vals)
                        waiter[0].set()
                    continue
                self._events.put((msgtype, msgid, vals))
        except (OSError, ConnectionError, struct.error, ValueError):
            pass
        self._closed()

    def _dispatcher(self):
        while True:
            item = self._events.get()
            if item is None:
                break
            cb = self.on_event
            if cb is None:
                continue
            try:
                cb(self, *item)
            except Exception:
                traceback.print_exc()

    def _closed(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass
        with self._pending_lock:
            for ev, _ in self._pending.values():
                ev.set()
        self._events.put(None)
        self.server._forget(self)
        if self.on_close:
            try:
                self.on_close(self)
            except Exception:
                traceback.print_exc()

    def close(self):
        self.post(P.SHUTDOWN)
        self._closed()


class BridgeServer(object):
    def __init__(self):
        self.token = secrets.token_hex(24)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connections = []
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.on_connect = None  # callable(conn)
        self._write_rendezvous()
        threading.Thread(target=self._accept, daemon=True).start()

    def env(self):
        return {
            "LUNA_BRIDGE_ENDPOINT": "127.0.0.1:{}".format(self.port),
            "LUNA_BRIDGE_TOKEN": self.token,
        }

    def _write_rendezvous(self):
        path = _runtime_file()
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("{}\n{}\n".format(self.port, self.token))
        os.replace(path + ".tmp", path)

    def _accept(self):
        while True:
            try:
                sock, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handshake, args=(sock,), daemon=True).start()

    def _handshake(self, sock):
        try:
            sock.settimeout(10)
            (ln,) = struct.unpack("<I", _recv_exact(sock, 4))
            if ln > 65536:
                raise ValueError("oversized hello")
            msgtype, _, vals = P.decode(_recv_exact(sock, ln))
            if msgtype != P.HELLO or len(vals) < 5:
                raise ValueError("expected HELLO")
            if not hmac.compare_digest(str(vals[0]), self.token):
                raise ValueError("bad token")
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            sock.close()
            return
        conn = BridgeConnection(self, sock, vals)
        with self.cond:
            # one bridge per prefix: a newer one replaces the old one
            for old in [c for c in self.connections if c.prefix_key == conn.prefix_key]:
                old.close()
            self.connections.append(conn)
            self.cond.notify_all()
        if self.on_connect:
            try:
                self.on_connect(conn)
            except Exception:
                traceback.print_exc()

    def _forget(self, conn):
        with self.cond:
            if conn in self.connections:
                self.connections.remove(conn)

    def find(self, prefix):
        key = os.path.realpath(prefix) if prefix else ""
        with self.lock:
            for c in self.connections:
                if c.alive and c.prefix_key == key:
                    return c
        return None

    def wait_for(self, prefix, timeout):
        key = os.path.realpath(prefix) if prefix else ""
        with self.cond:
            ok = self.cond.wait_for(
                lambda: any(c.alive and c.prefix_key == key for c in self.connections),
                timeout,
            )
        return self.find(prefix) if ok else None


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


_server = None
_server_lock = threading.Lock()


def get_server():
    global _server
    with _server_lock:
        if _server is None:
            _server = BridgeServer()
        return _server
