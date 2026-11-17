"""Start LunaBridge inside a running game's Wine prefix.

The bridge must talk to the same wineserver as the game, so it is started
with the game's own Wine build (the `wine` of the install the game's
wine-preloader belongs to, or WINELOADER) and the game's environment
(WINEPREFIX, WINEFSYNC/WINEESYNC, ...).

Proton games usually run inside the Steam Linux Runtime container
(pressure-vessel: own mount and user namespace, same network and pid
namespace). How the bridge reaches them, in order:

* "host": the game's wineserver socket (/tmp/.wine-<uid>/server-<dev>-<ino>)
  is visible on the host. pressure-vessel shares /tmp and mounts Proton and
  the prefix at their host paths, so this covers Steam and umu-run; the
  bridge runs on the host with Proton's own `wine`.
* "nsenter": the socket or Proton are only visible inside the container
  (e.g. Flatpak Steam). The bridge is started inside the game's user and
  mount namespaces with nsenter (no privileges needed: the container's user
  namespace belongs to us).
* the bridge was started by Proton itself through the Steam launch option
  written by files/linux/lunatranslator-proton.sh (PROTON_REMOTE_DEBUG_CMD);
  it connects by itself and ensure_bridge only waits for it.

Files the game must load (LunaHook*.dll) or the bridge must run from are
copied into the prefix when the container cannot see the app's folder.
"""

import os
import shutil
import subprocess

from lunaplat.linux import proc

# variables that only make sense inside the game's process tree
_DROP = {
    "WINESERVERSOCKET",
    "WINELOADERNOEXEC",
    "WINEPRELOADRESERVE",
    "LD_PRELOAD",
    "PROTON_REMOTE_DEBUG_CMD",
}
# desktop variables whose container values point into the container
_HOST_KEYS = (
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "DBUS_SYSTEM_BUS_ADDRESS",
    "PULSE_SERVER",
    "PULSE_CLIENTCONFIG",
    "AT_SPI_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "DISPLAY",
    "WAYLAND_DISPLAY",
)
_CONTAINER_MARKS = ("/run/pressure-vessel", "/usr/lib/pressure-vessel", "/run/host/", "/run/parent/")

# subfolder of drive_c that receives copies of the bridge / hook files
STAGE_DIR = "LunaTranslator"


def _ns(pid, kind):
    try:
        return os.readlink("/proc/{}/ns/{}".format(pid, kind))
    except OSError:
        return None


def _same_file(a, b):
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino) or (
        sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)
    )


class WineContext(object):
    def __init__(self, unix_pid):
        self.unix_pid = int(unix_pid)
        self.env = proc.environ(unix_pid)
        # prefix as the game sees it, and as the server keys bridges
        self.prefix_view = os.path.normpath(proc.wine_prefix(self.env))
        self.prefix = os.path.realpath(self.prefix_view)
        self.root = "/proc/{}/root".format(self.unix_pid)
        self.foreign_mnt = _ns(self.unix_pid, "mnt") not in (None, _ns("self", "mnt"))
        self.foreign_user = _ns(self.unix_pid, "user") not in (None, _ns("self", "user"))
        self.foreign_net = _ns(self.unix_pid, "net") not in (None, _ns("self", "net"))
        self.container = (
            self.foreign_mnt
            or "container" in self.env
            or any(k.startswith("PRESSURE_VESSEL") for k in self.env)
        )
        self.exe = proc.exe_link(self.unix_pid) or ""
        self.loader = self._find_loader(self.exists_host)
        self.mode = None

    # -- paths in the game's view ------------------------------------------
    def view(self, path):
        """Host path through which the game's view of `path` is reached."""
        return self.root + path if self.foreign_mnt else path

    def exists_host(self, path):
        return os.path.exists(path)

    def exists_game(self, path):
        return os.path.exists(self.view(path))

    def _find_loader(self, exists):
        cand = []
        if self.env.get("WINELOADER"):
            cand.append(self.env["WINELOADER"])
        d = os.path.dirname(self.exe)
        if d:
            # Wine >= 9 / Proton: <root>/lib/wine/<arch>-unix/wine-preloader,
            # with the launcher in <root>/bin/wine
            root = os.path.normpath(os.path.join(d, "..", "..", ".."))
            cand += [os.path.join(root, "bin", n) for n in ("wine", "wine64")]
            cand += [os.path.join(d, n) for n in ("wine", "wine64")]
            cand += [os.path.normpath(os.path.join(d, "..", "bin", n)) for n in ("wine", "wine64")]
        if not self.container:
            found = shutil.which("wine")
            if found:
                cand.append(found)
        for c in cand:
            if c and exists(c):
                return os.path.normpath(c)
        return None

    def server_socket(self, in_game_view):
        """Path of the game's wineserver socket (Wine derives it from the
        prefix's device and inode), reached from the host or through the
        game's root."""
        root = self.root if in_game_view else ""
        try:
            st = os.stat(root + self.prefix_view)
        except OSError:
            return None
        return "{}/tmp/.wine-{}/server-{:x}-{:x}/socket".format(root, os.getuid(), st.st_dev, st.st_ino)

    def server_reachable(self, in_game_view):
        sock = self.server_socket(in_game_view)
        return bool(sock) and os.path.exists(sock)

    def self_started_bridge(self):
        """The game was started with the bridge as Proton's side command."""
        return "lunabridge" in self.env.get("PROTON_REMOTE_DEBUG_CMD", "").lower()

    # -- choosing how to start the bridge -----------------------------------
    def choose_mode(self):
        forced = os.environ.get("LUNA_BRIDGE_MODE", "")
        if not self.foreign_mnt:
            return "host" if self.loader else None
        if forced == "host":
            return "host" if self.loader else None
        if forced != "nsenter" and self.loader and self.server_reachable(False):
            return "host"
        if self.foreign_net:
            # 127.0.0.1 inside is not ours: only a bridge on the host side works
            return None
        if shutil.which("nsenter") and self.server_reachable(True):
            if self._find_loader(self.exists_game):
                return "nsenter"
        return None

    def bridge_env(self, extra, mode="host"):
        env = {k: v for k, v in self.env.items() if k not in _DROP}
        if mode == "host" and self.container:
            env = {
                k: v
                for k, v in env.items()
                if not k.startswith("PRESSURE_VESSEL") and not any(m in v for m in _CONTAINER_MARKS)
            }
            env.pop("container", None)
            for k in _HOST_KEYS:
                if k in os.environ:
                    env[k] = os.environ[k]
            # keep only the Wine build's own library folders
            if self.loader:
                root = os.path.dirname(os.path.dirname(self.loader))
                libs = [p for p in self.env.get("LD_LIBRARY_PATH", "").split(":") if p.startswith(root + os.sep)]
                libs += [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if p]
                if libs:
                    env["LD_LIBRARY_PATH"] = ":".join(libs)
                else:
                    env.pop("LD_LIBRARY_PATH", None)
        env["WINEPREFIX"] = self.prefix if mode == "host" else self.prefix_view
        env.setdefault("WINEDEBUG", "-all")
        if os.environ.get("LUNA_BRIDGE_DEBUG"):
            env["LUNA_BRIDGE_DEBUG"] = os.environ["LUNA_BRIDGE_DEBUG"]
        env.update(extra)
        return env

    # -- files the game's side must see -------------------------------------
    def visible_path(self, host_path):
        """A path to host_path that is valid inside the game's container:
        the same path when the container sees the same file, else a copy in
        <prefix>/drive_c/LunaTranslator/."""
        host_path = os.path.abspath(host_path)
        if not self.foreign_mnt or _same_file(host_path, self.view(host_path)):
            return host_path
        stage = os.path.join(self.prefix_view, "drive_c", STAGE_DIR)
        os.makedirs(self.view(stage), exist_ok=True)
        dst = os.path.join(stage, os.path.basename(host_path))
        if not _same_file(host_path, self.view(dst)):
            tmp = self.view(dst) + ".tmp"
            shutil.copy2(host_path, tmp)
            os.replace(tmp, self.view(dst))
        return dst

    def __repr__(self):
        return "<WineContext pid={} prefix={} loader={} container={} foreign_mnt={}>".format(
            self.unix_pid, self.prefix, self.loader, self.container, self.foreign_mnt
        )


def bridge_dir():
    return os.path.abspath("files/LunaHook")


def bridge_exe(arch=64):
    return os.path.join(bridge_dir(), "LunaBridge{}.exe".format(arch))


def available():
    return os.path.isfile(bridge_exe(64)) or os.path.isfile(bridge_exe(32))


def _bridge_arch(ctx):
    # a win32-only prefix cannot run the 64-bit build
    syswow64 = os.path.join(ctx.prefix_view, "drive_c", "windows", "syswow64")
    if not os.path.isdir(ctx.view(syswow64)) or not os.path.isfile(bridge_exe(64)):
        return 32
    return 64


def launch(ctx, server_env, log_path=None):
    """Start a bridge for ctx; returns the Popen or raises."""
    mode = ctx.choose_mode()
    ctx.mode = mode
    if mode is None:
        if ctx.foreign_net:
            why = "the game runs in its own network namespace"
        elif not ctx.loader:
            why = "cannot find the Wine loader used by pid {}".format(ctx.unix_pid)
        else:
            why = "the game's wineserver is not reachable from the host"
        raise RuntimeError(
            why + "; start the bridge from the game's launch options "
            "(files/linux/lunatranslator-proton.sh %command%)"
        )
    arch = _bridge_arch(ctx)
    exe = bridge_exe(arch)
    if not os.path.isfile(exe):
        raise RuntimeError("missing " + exe)
    out = open(log_path, "ab") if log_path else subprocess.DEVNULL
    if mode == "host":
        argv = [ctx.loader, exe]
        cwd = bridge_dir()
    else:
        # inside the container: the bridge and the LunaHost dll next to it
        # must be visible there
        exe = ctx.visible_path(exe)
        ctx.visible_path(os.path.join(bridge_dir(), "LunaHost{}.dll".format(arch)))
        loader = ctx._find_loader(ctx.exists_game)
        argv = ["nsenter", "--target", str(ctx.unix_pid), "--preserve-credentials"]
        if ctx.foreign_user:
            argv.append("--user")
        argv += ["--mount", "--wd=" + os.path.dirname(exe), "--", loader, exe]
        cwd = "/"
    try:
        if log_path:
            out.write("[lunaplat] starting bridge ({}): {}\n".format(mode, argv).encode("utf-8", "replace"))
            out.flush()
        return subprocess.Popen(
            argv,
            env=ctx.bridge_env(server_env, mode),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )
    finally:
        # the child has its own copy of the log fd
        if out is not subprocess.DEVNULL:
            out.close()
