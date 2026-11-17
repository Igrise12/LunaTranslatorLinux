"""Game identity across the path forms a Wine game can have on Linux.

The same game.exe may be known as a Unix path (what process discovery
returns), as "Z:\\games\\vn\\game.exe", or as "C:\\Program Files\\VN\\game.exe"
inside some prefix, with any letter case (Wine is case-insensitive). Games
are stored by path (savehook_new_data[uid]["gamepath"]), so lookups compare
an identity of each form:

* the real Unix path (DOS paths resolved through a known prefix: the
  prefixes of games seen running, WINEPREFIX and ~/.wine), lower-cased;
* the DOS path, lower-cased ("c:\\..." for files below a prefix's drive_c,
  "z:\\..." for any other Unix path), which matches a stored "C:\\..." path
  even when its prefix is unknown.

patch_config() makes myutils.config.findgameuidofpath fall back to this
comparison when the exact path is not in its index.
"""

import os
import re
import threading

from lunaplat.linux import proc

_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_lock = threading.Lock()
_prefixes = []  # known Wine prefixes, most recent first
_memo = {}


def _default_prefixes():
    out = []
    if os.environ.get("WINEPREFIX"):
        out.append(os.environ["WINEPREFIX"])
    out.append(os.path.expanduser("~/.wine"))
    return [p for p in out if os.path.isdir(p)]


def register_prefix(prefix):
    """Remember a prefix a game was seen running in (resolves C:\\ forms)."""
    if not prefix:
        return
    prefix = os.path.realpath(prefix)
    with _lock:
        if _prefixes and _prefixes[0] == prefix:
            return
        if prefix in _prefixes:
            _prefixes.remove(prefix)
        _prefixes.insert(0, prefix)
        _memo.clear()


def known_prefixes():
    with _lock:
        cur = list(_prefixes)
    for p in _default_prefixes():
        p = os.path.realpath(p)
        if p not in cur:
            cur.append(p)
    return cur


def is_dos_path(path):
    return bool(path) and bool(_WIN_PATH.match(proc.strip_nt_prefix(path)))


def _dos_norm(path):
    path = proc.strip_nt_prefix(path).replace("/", "\\")
    drive, rest = path[:2], path[2:]
    parts = [p for p in rest.split("\\") if p and p != "."]
    out = []
    for p in parts:
        if p == "..":
            if out:
                out.pop()
        else:
            out.append(p)
    return (drive + "\\" + "\\".join(out)).lower()


def dos_form(unixpath):
    """Lower-cased DOS form of a Unix path: c:\\... below a prefix's
    drive_c (or dosdevices/<x>:), else z:\\..."""
    p = os.path.normpath(unixpath)
    m = re.search(r"/drive_c(/|$)", p)
    if m:
        return _dos_norm("c:" + p[m.end(1) - 1 :] if m.group(1) else "c:\\")
    m = re.search(r"/dosdevices/([a-zA-Z]):(/|$)", p)
    if m:
        return _dos_norm(m.group(1) + ":" + p[m.end(2) - 1 :] if m.group(2) else m.group(1) + ":\\")
    return _dos_norm("z:" + p)


def to_unix(path, prefix=None):
    """Unix path of path (DOS paths resolved through prefix or a known
    prefix in which the file exists), or None if it cannot be resolved."""
    if not path:
        return None
    if not is_dos_path(path):
        return os.path.realpath(os.path.expanduser(path))
    path = proc.strip_nt_prefix(path)
    drive = path[0].lower()
    if drive == "z" and not prefix:
        return os.path.realpath(proc._ci_join("/", re.split(r"[\\/]+", path[3:])))
    cands = [prefix] if prefix else known_prefixes()
    for pfx in cands:
        if not os.path.isdir(os.path.join(pfx, "dosdevices")) and not os.path.isdir(os.path.join(pfx, "drive_c")):
            continue
        u = proc.dos_to_unix(path, pfx)
        if u and not is_dos_path(u) and (prefix or os.path.exists(u)):
            return os.path.realpath(u)
    return None


def identity(path, prefix=None):
    """(unix key, dos key) of a path; either may be None."""
    if not path:
        return (None, None)
    key = (path, prefix)
    with _lock:
        hit = _memo.get(key)
    if hit is not None:
        return hit
    if is_dos_path(path):
        unix = to_unix(path, prefix)
        dos = _dos_norm(path)
        if unix and dos.startswith("z:"):
            dos = dos_form(unix)
    else:
        unix = to_unix(path)
        dos = dos_form(unix)
    ident = (unix.lower() if unix else None, dos)
    with _lock:
        if len(_memo) > 20000:
            _memo.clear()
        _memo[key] = ident
    return ident


def _match(ia, ib):
    if ia[0] and ib[0]:
        return ia[0] == ib[0]
    # one side is a DOS path whose prefix is unknown: compare DOS forms
    return bool(ia[1]) and ia[1] == ib[1]


def same_game(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    return _match(identity(a), identity(b))


def find_uids(gamepath, savehook_new_data):
    """uids whose stored gamepath is the same game as gamepath."""
    want = identity(gamepath)
    if not want[0] and not want[1]:
        return []
    out = []
    for uid, data in list(savehook_new_data.items()):
        stored = data.get("gamepath") if hasattr(data, "get") else None
        if not stored:
            continue
        if _match(want, identity(stored)):
            out.append(uid)
    return out


class _FuzzyIndex(dict):
    """gamepath2uid_index whose lookups (findgameuidofpath uses .get) fall
    back to identity matching; membership tests and updates are plain."""

    savehook = None

    def get(self, key, default=None):
        v = dict.get(self, key)
        if v:
            return v
        if self.savehook is None or not key:
            return v if v is not None else default
        try:
            uids = find_uids(key, self.savehook)
        except Exception:
            uids = []
        if uids:
            return uids
        return v if v is not None else default


def patch_config(mod):
    idx = _FuzzyIndex(mod.gamepath2uid_index)
    idx.savehook = mod.savehook_new_data
    mod.gamepath2uid_index = idx
