"""Process inspection on Linux, Wine-aware.

For a process running under Wine, /proc/<pid>/exe is the Wine loader
(wine64-preloader, ...); Wine rewrites argv[0] to the Windows image path
(e.g. "Z:\\games\\vn\\game.exe" or "C:\\Program Files\\...\\game.exe"). That
path is resolved through the process's prefix (<prefix>/dosdevices/<drive>:)
to a real Unix path, which is what the app uses to identify games.
"""

import os
import re

_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WINE_LOADERS = {
    "wine",
    "wine64",
    "wine-preloader",
    "wine64-preloader",
    "wineserver",
}
# Wine/Proton/Steam helpers that are never games
_HELPERS = {
    "explorer.exe",
    "services.exe",
    "winedevice.exe",
    "plugplay.exe",
    "svchost.exe",
    "rpcss.exe",
    "conhost.exe",
    "tabtip.exe",
    "start.exe",
    "steam.exe",
    "steamwebhelper.exe",
    "steamerrorreporter.exe",
    "rundll32.exe",
    "wineboot.exe",
    "winemenubuilder.exe",
    "lunabridge32.exe",
    "lunabridge64.exe",
    "lunasubprocess32.exe",
    "lunasubprocess64.exe",
}


def _read(path, mode="rb"):
    try:
        with open(path, mode) as f:
            return f.read()
    except OSError:
        return None


def cmdline(pid):
    raw = _read("/proc/{}/cmdline".format(int(pid)))
    if not raw:
        return []
    return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]


def environ(pid):
    raw = _read("/proc/{}/environ".format(int(pid)))
    env = {}
    if not raw:
        return env
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return env


def exe_link(pid):
    try:
        return os.readlink("/proc/{}/exe".format(int(pid)))
    except OSError:
        return None


def wine_prefix(env):
    p = env.get("WINEPREFIX")
    if p:
        return p
    compat = env.get("STEAM_COMPAT_DATA_PATH")
    if compat:
        return os.path.join(compat, "pfx")
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".wine")


def _ci_join(base, parts):
    """Join path components below base, matching case-insensitively the way
    Wine does when the exact name does not exist."""
    cur = base
    for part in parts:
        if not part or part == ".":
            continue
        cand = os.path.join(cur, part)
        if os.path.exists(cand):
            cur = cand
            continue
        try:
            names = os.listdir(cur)
        except OSError:
            return os.path.join(cur, *parts[parts.index(part):])
        low = part.lower()
        match = [n for n in names if n.lower() == low]
        cur = os.path.join(cur, match[0] if match else part)
    return cur


def dos_to_unix(winpath, prefix):
    """'C:\\a\\b.exe' -> '<prefix>/dosdevices/c:/a/b.exe' resolved."""
    if not winpath or not _WIN_PATH.match(winpath):
        return winpath
    drive = winpath[0].lower()
    rest = re.split(r"[\\/]+", winpath[3:])
    base = os.path.join(prefix, "dosdevices", drive + ":")
    if not os.path.exists(base):
        if drive == "z":
            base = "/"
        elif drive == "c":
            base = os.path.join(prefix, "drive_c")
        else:
            return winpath
    try:
        base = os.path.realpath(base)
    except OSError:
        pass
    return _ci_join(base, rest)


def is_wine_process(pid):
    exe = exe_link(pid)
    if exe and os.path.basename(exe) in _WINE_LOADERS:
        return True
    args = cmdline(pid)
    return bool(args) and bool(_WIN_PATH.match(args[0]))


def windows_image_path(pid):
    """Windows path of a Wine process's image, or None."""
    args = cmdline(pid)
    if args and _WIN_PATH.match(args[0]):
        return args[0]
    return None


def process_file_name(pid):
    """Unix path of the program a process runs (the .exe for Wine games)."""
    win = windows_image_path(pid)
    if win:
        return dos_to_unix(win, wine_prefix(environ(pid)))
    return exe_link(pid)


def _is_helper(winpath, unixpath):
    name = os.path.basename(unixpath or "").lower()
    if name in _HELPERS:
        return True
    low = (winpath or "").lower().replace("/", "\\")
    return low.startswith("c:\\windows\\") or "\\windows\\system32\\" in low


def list_wine_processes():
    """[(pid, unix_path, windows_path)] for Wine processes of this user."""
    uid = os.getuid()
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            if os.stat("/proc/" + name).st_uid != uid:
                continue
        except OSError:
            continue
        win = windows_image_path(pid)
        if not win:
            continue
        unix = dos_to_unix(win, wine_prefix(environ(pid)))
        if _is_helper(win, unix):
            continue
        out.append((pid, unix, win))
    return out


def list_process_groups():
    """{unix exe path: [pids]} of candidate game processes (Wine)."""
    groups = {}
    for pid, unix, _ in list_wine_processes():
        groups.setdefault(unix, []).append(pid)
    return groups


def is_stopped(pid):
    raw = _read("/proc/{}/stat".format(int(pid)))
    if not raw:
        return False
    try:
        return raw[raw.rindex(b")") + 2:].split()[0] in (b"T", b"t")
    except (ValueError, IndexError):
        return False


def suspend_resume(pid):
    """Toggle a process between stopped and running (like the Win32
    SuspendResumeProcess). Wine games are single Unix processes."""
    import signal

    pid = int(pid)
    try:
        os.kill(pid, signal.SIGCONT if is_stopped(pid) else signal.SIGSTOP)
    except OSError:
        pass


def _socket_inodes(pid):
    inodes = set()
    fd_dir = "/proc/{}/fd".format(int(pid))
    try:
        for fd in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
    except OSError:
        pass
    return inodes


def listen_port(name):
    """TCP port a process named `name` (e.g. llama-server[.exe]) listens on,
    or 0."""
    want = os.path.basename(name).lower()
    want_noext = want[:-4] if want.endswith(".exe") else want
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        exe = process_file_name(entry) or ""
        base = os.path.basename(exe).lower()
        if base in (want, want_noext):
            pids.append(int(entry))
    for pid in pids:
        inodes = _socket_inodes(pid)
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            raw = _read(table, "r")
            if not raw:
                continue
            for line in raw.splitlines()[1:]:
                parts = line.split()
                # st == 0A: LISTEN
                if len(parts) > 9 and parts[3] == "0A" and parts[9] in inodes:
                    return int(parts[1].rsplit(":", 1)[1], 16)
    return 0


def is_running(pid):
    return os.path.exists("/proc/{}".format(int(pid)))


def parent_pid(pid):
    raw = _read("/proc/{}/stat".format(int(pid)))
    if not raw:
        return 0
    try:
        # comm may contain spaces/parens: split after the last ')'
        rest = raw[raw.rindex(b")") + 2:].split()
        return int(rest[1])
    except (ValueError, IndexError):
        return 0
