"""Start LunaBridge inside a running game's Wine prefix.

The bridge must talk to the same wineserver as the game, so it is started
with the game's own Wine build (the `wine` next to the game's
wine-preloader, or WINELOADER) and the game's environment (WINEPREFIX,
WINEFSYNC/WINEESYNC, ...). Proton games inside the Steam Linux Runtime
container may not be reachable this way; for those the bridge can also be
started by Steam (see docs/linux.md) and connects back by itself.
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
}


class WineContext(object):
    def __init__(self, unix_pid):
        self.unix_pid = int(unix_pid)
        self.env = proc.environ(unix_pid)
        self.prefix = os.path.realpath(proc.wine_prefix(self.env))
        self.loader = self._find_loader()
        self.container = any(k.startswith("PRESSURE_VESSEL") for k in self.env) or (
            "STEAM_COMPAT_CLIENT_INSTALL_PATH" in self.env and "SteamLinuxRuntime" in self.env.get("PATH", "")
        )

    def _find_loader(self):
        cand = []
        if self.env.get("WINELOADER"):
            cand.append(self.env["WINELOADER"])
        exe = proc.exe_link(self.unix_pid) or ""
        d = os.path.dirname(exe)
        if d:
            # Wine >= 9 layout: <lib>/wine/x86_64-unix/{wine,wine-preloader};
            # Proton: files/bin/{wine64,wine,wine64-preloader}
            cand += [os.path.join(d, n) for n in ("wine", "wine64")]
            cand += [os.path.join(d, "..", "bin", n) for n in ("wine", "wine64")]
        found = shutil.which("wine")
        if found:
            cand.append(found)
        for c in cand:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return os.path.realpath(c)
        return None

    def bridge_env(self, extra):
        env = {k: v for k, v in self.env.items() if k not in _DROP}
        env["WINEPREFIX"] = self.prefix
        env.setdefault("WINEDEBUG", "-all")
        env.update(extra)
        return env

    def __repr__(self):
        return "<WineContext pid={} prefix={} loader={}>".format(
            self.unix_pid, self.prefix, self.loader
        )


def bridge_dir():
    return os.path.abspath("files/LunaHook")


def bridge_exe(arch=64):
    return os.path.join(bridge_dir(), "LunaBridge{}.exe".format(arch))


def available():
    return os.path.isfile(bridge_exe(64)) or os.path.isfile(bridge_exe(32))


def launch(ctx, server_env, log_path=None):
    """Start a bridge for ctx; returns the Popen or raises."""
    if not ctx.loader:
        raise RuntimeError("cannot find the Wine loader used by pid {}".format(ctx.unix_pid))
    # a win32-only prefix cannot run the 64-bit build
    win32_prefix = not os.path.isdir(os.path.join(ctx.prefix, "drive_c", "windows", "syswow64"))
    exe = bridge_exe(32 if win32_prefix else 64)
    if not os.path.isfile(exe):
        raise RuntimeError("missing " + exe)
    out = open(log_path, "ab") if log_path else subprocess.DEVNULL
    return subprocess.Popen(
        [ctx.loader, exe],
        env=ctx.bridge_env(server_env),
        cwd=bridge_dir(),
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        start_new_session=True,
    )
