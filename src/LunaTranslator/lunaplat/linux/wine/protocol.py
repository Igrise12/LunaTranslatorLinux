"""Wire format shared with LunaBridge (src/NativeImpl/LunaHook/LunaBridge).

frame = u32 body_len | u16 type | u32 id | fields...
field = 'i' i64 | 'u' u64 | 'f' f64 | 's' u32 len utf8 | 'b' u32 len bytes
      | 'T' u32 pid u64 addr u64 ctx u64 ctx2        (ThreadParam)
"""

import struct

VERSION = 1

HELLO = 1
REPLY = 2
LOG = 3
# app -> bridge
START = 10
SETTINGS = 11
SETTINGS_EX = 12
RESET_LANG = 13
CONNECT = 14
NEED_INJECT = 15
DETACH = 16
INSERT_PC_HOOKS = 17
INSERT_HOOK = 18
REMOVE_HOOK = 19
FIND_HOOKS = 20
QUERY_HISTORY = 21
CHECK_USING_EMBED = 22
USE_EMBED = 23
EMBED_REPLY = 24
SYNC_THREAD = 25
LIST_PROCESSES = 30
INJECT = 31
WATCH_PROCESS = 32
LIST_MODULES = 33
TO_DOS = 34
TO_UNIX = 35
SHUTDOWN = 36
PROCESS_ALIVE = 37
# bridge -> app
PROC_CONNECT = 50
PROC_DISCONNECT = 51
THREAD_CREATE = 52
THREAD_REMOVE = 53
OUTPUT = 54
HOST_INFO = 55
HOOK_INSERT = 56
EMBED_REQUEST = 57
I18N_QUERY = 58
EMU_GAME_INFO = 59
FIND_HOOK_RESULT = 60
PROCESS_EXIT = 61


class I(int):
    """Marks an int to be sent as signed i64 ('i')."""


class U(int):
    """Marks an int to be sent as u64 ('u')."""


class B(bytes):
    """Marks bytes to be sent raw ('b') instead of as a UTF-8 string."""


class TP(tuple):
    """ThreadParam as (pid, addr, ctx, ctx2)."""


def encode(msgtype, msgid, fields):
    out = [struct.pack("<HI", msgtype, msgid)]
    for v in fields:
        if isinstance(v, TP):
            out.append(b"T" + struct.pack("<IQQQ", *v))
        elif isinstance(v, U):
            out.append(b"u" + struct.pack("<Q", int(v) & 0xFFFFFFFFFFFFFFFF))
        elif isinstance(v, bool) or isinstance(v, I) or isinstance(v, int):
            out.append(b"i" + struct.pack("<q", int(v)))
        elif isinstance(v, float):
            out.append(b"f" + struct.pack("<d", v))
        elif isinstance(v, B):
            out.append(b"b" + struct.pack("<I", len(v)) + bytes(v))
        elif isinstance(v, (str, bytes)):
            data = v.encode("utf-8", "surrogatepass") if isinstance(v, str) else v
            out.append(b"s" + struct.pack("<I", len(data)) + data)
        elif v is None:
            out.append(b"s" + struct.pack("<I", 0))
        else:
            raise TypeError("cannot encode {!r}".format(v))
    body = b"".join(out)
    return struct.pack("<I", len(body)) + body


def decode(body):
    """-> (type, id, [values]); strings are str, 'b' fields bytes,
    ThreadParams TP tuples."""
    msgtype, msgid = struct.unpack_from("<HI", body, 0)
    pos = 6
    vals = []
    n = len(body)
    while pos < n:
        tag = body[pos : pos + 1]
        pos += 1
        if tag == b"i":
            vals.append(struct.unpack_from("<q", body, pos)[0])
            pos += 8
        elif tag == b"u":
            vals.append(struct.unpack_from("<Q", body, pos)[0])
            pos += 8
        elif tag == b"f":
            vals.append(struct.unpack_from("<d", body, pos)[0])
            pos += 8
        elif tag in (b"s", b"b"):
            (ln,) = struct.unpack_from("<I", body, pos)
            pos += 4
            raw = body[pos : pos + ln]
            pos += ln
            vals.append(raw.decode("utf-8", "replace") if tag == b"s" else bytes(raw))
        elif tag == b"T":
            vals.append(TP(struct.unpack_from("<IQQQ", body, pos)))
            pos += 28
        else:
            raise ValueError("bad field tag {!r}".format(tag))
    return msgtype, msgid, vals
