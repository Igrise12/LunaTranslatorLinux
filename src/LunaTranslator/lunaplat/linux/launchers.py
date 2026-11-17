"""Game launchers for Linux.

On Windows, myutils.localetools starts games through Locale Emulator,
Locale Remulator, ntleas, CreateProcess or ShellExecute. None of these exist
on Linux, where the games are Windows programs run by Wine, Proton (umu-run),
Steam or Lutris. patch() replaces the launcher list and the entry points of
localetools (getgamecamptools, fundlauncher, localeswitchedrun,
maycreatesettings) with the launchers below; the game manager keeps using
its "launch_method" combo and settings card unchanged.

Per-game settings live in savehook_new_data[gameuid]["linux_launch"]:

    prefix       WINEPREFIX ("" = the prefix the exe lives in, else $WINEPREFIX
                 or ~/.wine for Wine, umu's own default for umu-run)
    locale       LANG/LC_ALL for the game ("" = keep the environment's;
                 missing = ja_JP.UTF-8)
    wine         Wine loader: "" = system wine, a wine binary, or a
                 Wine/Proton directory (its files/bin/wine is used)
    protonpath   PROTONPATH for umu-run ("" = newest Proton found)
    gameid       GAMEID for umu-run ("" = umu-default)
    steam_appid  Steam AppID ("" = detect from appmanifest_*.acf)
    lutris_id    Lutris game id ("" = detect from pga.db)
    template     custom command template
    args         extra arguments for the game (shell syntax)
    env          extra environment, "NAME=value NAME2='a b'" (shell syntax)

Everything above the "launcher classes" section is free of Qt and of app
imports so it can be unit tested on its own.
"""

import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time

CFG_KEY = "linux_launch"
DEFAULT_LOCALE = "ja_JP.UTF-8"
DEFAULT_GAMEID = "umu-default"
DEFAULT_TEMPLATE = "{wine} {exe} {args}"
UMU_FALLBACK_PROTON = "GE-Proton"  # umu-run downloads the latest GE-Proton

AUTO = "linux_auto"
WINE = "linux_wine"
UMU = "linux_umu"
STEAM = "linux_steam"
LUTRIS = "linux_lutris"
CUSTOM = "linux_cmd"
DIRECT = "linux_direct"

_WINDOWS_EXTS = (".exe", ".lnk", ".bat", ".cmd", ".msi", ".com", ".scr")


class LaunchError(Exception):
    pass


# ---------------------------------------------------------------- VDF ----


_VDF_TOKEN = re.compile(
    r'"((?:[^"\\]|\\.)*)"'  # quoted string
    r"|(\{)|(\})"  # braces
    r"|(//[^\n]*)"  # comment
    r"|(\[[^\]\n]*\])"  # platform conditional, ignored
    r'|([^\s{}"]+)',  # bare word
)
_VDF_ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _vdf_unescape(s):
    return re.sub(r"\\(.)", lambda m: _VDF_ESC.get(m.group(1), m.group(1)), s)


def parse_vdf(text):
    """Parse Valve's text KeyValues format (libraryfolders.vdf, *.acf).

    Returns nested dicts; keys keep their case (use vdf_get for
    case-insensitive lookups). Duplicate keys: the last one wins.
    """
    root = {}
    stack = [root]
    key = None
    for m in _VDF_TOKEN.finditer(text):
        quoted, opn, cls, comment, cond, bare = m.groups()
        if comment is not None or cond is not None:
            continue
        if opn is not None:
            new = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
            continue
        if cls is not None:
            if len(stack) > 1:
                stack.pop()
            key = None
            continue
        word = _vdf_unescape(quoted) if quoted is not None else bare
        if key is None:
            key = word
        else:
            stack[-1][key] = word
            key = None
    return root


def vdf_get(d, *keys):
    """Case-insensitive nested lookup; None when missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        if k in cur:
            cur = cur[k]
            continue
        low = k.lower()
        for kk, vv in cur.items():
            if kk.lower() == low:
                cur = vv
                break
        else:
            return None
    return cur


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, IOError):
        return None


def _home(home=None):
    return home or os.path.expanduser("~")


def _real(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return os.path.abspath(p)


def _is_under(path, base):
    path = os.path.normcase(_real(path))
    base = os.path.normcase(_real(base)).rstrip(os.sep)
    return path == base or path.startswith(base + os.sep)


# -------------------------------------------------------------- Steam ----


def steam_roots(home=None):
    """Steam installation roots (native and Flatpak), deduplicated."""
    home = _home(home)
    cands = [
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".steam", "debian-installation"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", "data", "Steam"),
    ]
    out, seen = [], set()
    for c in cands:
        if not os.path.isdir(os.path.join(c, "steamapps")):
            continue
        r = _real(c)
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def steam_libraries(roots=None, home=None):
    """Every Steam library folder: the roots plus libraryfolders.vdf entries."""
    if roots is None:
        roots = steam_roots(home)
    out, seen = [], set()

    def add(p):
        if not p or not os.path.isdir(os.path.join(p, "steamapps")):
            return
        r = _real(p)
        if r not in seen:
            seen.add(r)
            out.append(r)

    for root in roots:
        add(root)
        for vdf in (
            os.path.join(root, "steamapps", "libraryfolders.vdf"),
            os.path.join(root, "config", "libraryfolders.vdf"),
        ):
            text = _read_text(vdf)
            if not text:
                continue
            folders = vdf_get(parse_vdf(text), "libraryfolders")
            if not isinstance(folders, dict):
                continue
            for v in folders.values():
                if isinstance(v, dict):
                    add(vdf_get(v, "path"))
                elif isinstance(v, str) and not v.isdigit():
                    add(v)  # old format: "1" "/path"
    return out


def read_manifests(library):
    """[{appid, name, installdir, manifest}] of a library's appmanifest_*.acf."""
    apps = []
    d = os.path.join(library, "steamapps")
    try:
        names = os.listdir(d)
    except OSError:
        return apps
    for n in sorted(names):
        if not (n.startswith("appmanifest_") and n.endswith(".acf")):
            continue
        text = _read_text(os.path.join(d, n))
        if not text:
            continue
        st = vdf_get(parse_vdf(text), "AppState")
        if not isinstance(st, dict):
            continue
        appid = vdf_get(st, "appid") or n[len("appmanifest_") : -len(".acf")]
        apps.append(
            dict(
                appid=str(appid),
                name=vdf_get(st, "name") or "",
                installdir=vdf_get(st, "installdir") or "",
                manifest=os.path.join(d, n),
            )
        )
    return apps


def find_steam_app(exe, libraries=None):
    """The installed Steam app whose steamapps/common/<installdir> holds exe.

    Returns {appid, name, installdir, manifest, library} or None.
    """
    if not exe:
        return None
    if libraries is None:
        libraries = steam_libraries()
    exe = _real(exe)
    for lib in libraries:
        common = os.path.join(lib, "steamapps", "common")
        if not _is_under(exe, common):
            continue
        rel = os.path.relpath(exe, _real(common))
        top = rel.split(os.sep)[0]
        if not top or top == "..":
            continue
        for app in read_manifests(lib):
            if app["installdir"] and app["installdir"].lower() == top.lower():
                app["library"] = lib
                return app
    return None


def _proton_wine_in(d):
    for rel in (("files", "bin", "wine"), ("dist", "bin", "wine"), ("bin", "wine")):
        p = os.path.join(d, *rel)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def proton_wine(path):
    """The wine binary of a Wine/Proton directory, or path itself if a file."""
    if not path:
        return None
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        return _proton_wine_in(path)
    return None


def _proton_stamp(d):
    """Build timestamp from Proton's `version` file ("<unix time> <name>")."""
    text = _read_text(os.path.join(d, "version")) or ""
    m = re.match(r"\s*(\d+)", text)
    if m:
        return int(m.group(1))
    try:
        return int(os.path.getmtime(d))
    except OSError:
        return 0


SYSTEM_COMPAT_DIRS = (
    "/usr/share/steam/compatibilitytools.d",
    "/usr/local/share/steam/compatibilitytools.d",
)


def find_protons(roots=None, libraries=None, home=None, system_dirs=SYSTEM_COMPAT_DIRS):
    """Installed Proton builds, newest first: [{name, path, stamp}].

    Looks at compatibilitytools.d (GE-Proton & co.) of every Steam root and
    the system, and at steamapps/common/Proton* of every library.
    """
    if roots is None:
        roots = steam_roots(home)
    if libraries is None:
        libraries = steam_libraries(roots)
    dirs = []
    for base in [os.path.join(r, "compatibilitytools.d") for r in roots] + list(system_dirs):
        try:
            dirs += [os.path.join(base, n) for n in os.listdir(base)]
        except OSError:
            pass
    for lib in libraries:
        common = os.path.join(lib, "steamapps", "common")
        try:
            dirs += [
                os.path.join(common, n)
                for n in os.listdir(common)
                if n.lower().startswith("proton")
            ]
        except OSError:
            pass
    out, seen = [], set()
    for d in dirs:
        if not os.path.isfile(os.path.join(d, "proton")) or not _proton_wine_in(d):
            continue
        r = _real(d)
        if r in seen:
            continue
        seen.add(r)
        out.append(dict(name=os.path.basename(d), path=d, stamp=_proton_stamp(d)))
    out.sort(key=lambda p: p["stamp"], reverse=True)
    return out


def default_protonpath():
    protons = find_protons()
    return protons[0]["path"] if protons else UMU_FALLBACK_PROTON


# ------------------------------------------------------------- Lutris ----


def lutris_db_paths(home=None):
    home = _home(home)
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [
        os.path.join(data, "lutris", "pga.db"),
        os.path.join(home, ".var", "app", "net.lutris.Lutris", "data", "lutris", "pga.db"),
    ]


def lutris_config_dirs(home=None):
    home = _home(home)
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    conf = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    flat = os.path.join(home, ".var", "app", "net.lutris.Lutris")
    return [
        os.path.join(data, "lutris", "games"),  # Lutris >= 0.5.18
        os.path.join(conf, "lutris", "games"),
        os.path.join(flat, "data", "lutris", "games"),
        os.path.join(flat, "config", "lutris", "games"),
    ]


def _yaml_scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if " #" in v:
        v = v.split(" #", 1)[0].rstrip()
    return v


def read_lutris_game_config(path):
    """The `game:` section of a Lutris game YAML as a flat dict of scalars
    (exe, prefix, args, working_dir, ...). Only what Lutris writes is
    understood: two-space indented `key: value` lines."""
    text = _read_text(path)
    out = {}
    if not text:
        return out
    insection = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            insection = line.rstrip() == "game:"
            continue
        if not insection:
            continue
        m = re.match(r"^\s+([A-Za-z_][\w-]*):(?:\s+(.*))?$", line)
        if m and m.group(2) is not None:
            out[m.group(1)] = _yaml_scalar(m.group(2))
    return out


def _lutris_rows(db):
    """Rows of pga.db `games` as dicts; opened read-only."""
    uri = "file:{}?mode=ro".format(db.replace("?", "%3f").replace("#", "%23"))
    con = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(games)")]
        want = [
            c
            for c in ("id", "name", "slug", "runner", "directory", "configpath", "installed")
            if c in cols
        ]
        if "id" not in want:
            return []
        rows = con.execute("SELECT {} FROM games".format(", ".join(want))).fetchall()
        return [dict(zip(want, r)) for r in rows]
    finally:
        con.close()


def find_lutris_game(exe, db_paths=None, config_dirs=None):
    """Find the Lutris game for exe. Returns {id, name, slug, runner, prefix,
    match} or None. An exact `exe` match in the game's YAML wins over the exe
    merely lying inside the game's install directory (deepest one wins)."""
    if not exe:
        return None
    if db_paths is None:
        db_paths = lutris_db_paths()
    if config_dirs is None:
        config_dirs = lutris_config_dirs()
    exe_r = os.path.normcase(_real(exe))
    best, best_rank = None, None
    home = os.path.normcase(_real(_home()))
    for db in db_paths:
        if not os.path.isfile(db):
            continue
        try:
            rows = _lutris_rows(db)
        except sqlite3.Error as e:
            sys.stderr.write("[lunaplat] cannot read {}: {}\n".format(db, e))
            continue
        for row in rows:
            if row.get("installed") in (0, "0"):
                continue
            conf = {}
            if row.get("configpath"):
                for cd in config_dirs:
                    p = os.path.join(cd, row["configpath"] + ".yml")
                    if os.path.isfile(p):
                        conf = read_lutris_game_config(p)
                        break
            directory = row.get("directory") or ""
            rank = None
            gexe = conf.get("exe")
            if gexe:
                if not os.path.isabs(gexe):
                    base = directory or conf.get("prefix") or ""
                    gexe = os.path.join(base, gexe)
                if os.path.normcase(_real(gexe)) == exe_r:
                    rank = (2, 0)
            if rank is None and directory:
                d = os.path.normcase(_real(directory))
                if d not in ("/", home) and _is_under(exe, directory):
                    rank = (1, len(d))
            if rank is None:
                continue
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = dict(
                    id=str(row["id"]),
                    name=row.get("name") or "",
                    slug=row.get("slug") or "",
                    runner=row.get("runner") or "",
                    prefix=conf.get("prefix") or "",
                    match="exe" if rank[0] == 2 else "directory",
                )
    return best


# ----------------------------------------------------- prefixes & paths ----


def prefix_of_path(path):
    """The Wine prefix a path lies in (the parent of its drive_c), or None."""
    if not path:
        return None
    parts = os.path.abspath(path).split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        if parts[i].lower() != "drive_c":
            continue
        pfx = os.sep.join(parts[:i]) or os.sep
        if os.path.isdir(os.path.join(pfx, "dosdevices")) or os.path.isfile(
            os.path.join(pfx, "system.reg")
        ):
            return pfx
    return None


def unix_to_dos(path, prefix=None):
    """/a/b.exe -> Z:\\a\\b.exe, <prefix>/drive_c/x.exe -> C:\\x.exe.

    Uses the prefix's dosdevices symlinks when available (longest match)."""
    path = os.path.abspath(path)
    best = None
    if prefix:
        dd = os.path.join(prefix, "dosdevices")
        try:
            names = os.listdir(dd)
        except OSError:
            names = []
        for n in names:
            if len(n) != 2 or n[1] != ":" or not n[0].isalpha():
                continue
            target = _real(os.path.join(dd, n))
            if _is_under(path, target) and (best is None or len(target) > len(best[1])):
                best = (n[0].upper(), target)
        if best is None and _is_under(path, os.path.join(prefix, "drive_c")):
            best = ("C", _real(os.path.join(prefix, "drive_c")))
    if best is None:
        best = ("Z", "/")
    rel = os.path.relpath(_real(path), best[1]) if best[1] != "/" else _real(path).lstrip("/")
    if rel == ".":
        rel = ""
    return "{}:\\{}".format(best[0], rel.replace("/", "\\"))


def is_windows_program(path):
    if path.lower().endswith(_WINDOWS_EXTS):
        return True
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except (OSError, IOError):
        return False


def is_native_program(path):
    return (
        os.path.isfile(path)
        and os.access(path, os.X_OK)
        and not is_windows_program(path)
    )


# ---------------------------------------------------------- commands ----


class Command(object):
    def __init__(self, argv, env=None, cwd=None, launcher=""):
        self.argv = [str(a) for a in argv]
        self.env = dict(env or {})
        self.cwd = cwd
        self.launcher = launcher

    def __repr__(self):
        envs = " ".join(
            "{}={}".format(k, shlex.quote(v)) if v is not None else "-u " + k
            for k, v in sorted(self.env.items())
        )
        return "<Command {} cwd={!r} {}{}>".format(
            self.launcher, self.cwd, envs + " " if envs else "", " ".join(map(shlex.quote, self.argv))
        )


def settings(config):
    """The per-game linux_launch dict (created on demand)."""
    cfg = config.get(CFG_KEY)
    if not isinstance(cfg, dict):
        cfg = {}
        config[CFG_KEY] = cfg
    return cfg


def _split(s):
    try:
        return shlex.split(s or "")
    except ValueError as e:
        raise LaunchError("cannot parse {!r}: {}".format(s, e))


def user_env(cfg):
    env = {}
    for tok in _split(cfg.get("env", "")):
        k, eq, v = tok.partition("=")
        if eq and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            env[k] = v
    return env


_locales = []


def _norm_locale(name):
    name = name.strip()
    base, _, rest = name.partition(".")
    enc, _, mod = rest.partition("@")
    enc = enc.lower().replace("-", "")
    return base + ("." + enc if enc else "") + ("@" + mod if mod else "")


def locale_available(name):
    """Whether glibc has the locale (system Wine needs it: an unknown LANG
    makes glibc fall back to "C" for every category, which leaves the game
    with code page 1252 and breaks non-ASCII paths)."""
    if not name or name.upper() in ("C", "POSIX") or name.lower().startswith("c."):
        return True
    if not _locales:
        try:
            out = subprocess.run(
                ["locale", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding="utf-8", errors="replace", timeout=5,
            ).stdout
            _locales.append({_norm_locale(l) for l in out.split()})
        except (OSError, subprocess.SubprocessError):
            _locales.append(None)
    known = _locales[0]
    return True if known is None else _norm_locale(name) in known


def requested_locale(cfg):
    loc = cfg.get("locale", DEFAULT_LOCALE)
    if loc is None:
        loc = DEFAULT_LOCALE
    return loc.strip()


def locale_env(cfg, container=False):
    """LANG/LC_ALL for the game. Proton's container generates the locale it
    is asked for; for system Wine a locale glibc does not have is skipped
    (the launch reports how to install it)."""
    loc = requested_locale(cfg)
    if not loc:
        return {}
    if not container and not locale_available(loc):
        return {}
    return {"LANG": loc, "LC_ALL": loc}


def missing_locale_hint(loc):
    return (
        "The {0} locale is not installed, so the game runs with the current "
        "locale (Japanese games may show garbled text). Enable \"{0}\" in "
        "/etc/locale.gen and run `sudo locale-gen`, or use the umu-run (Proton) "
        "launcher.".format(loc)
    )


def resolve_prefix(exe, cfg, umu=False, environ=None):
    """WINEPREFIX for exe: configured > the prefix the exe is in > (Wine
    only) $WINEPREFIX > ~/.wine. For umu-run None lets umu pick its default
    (~/Games/umu/<GAMEID>)."""
    environ = os.environ if environ is None else environ
    p = (cfg.get("prefix") or "").strip()
    if p:
        return os.path.abspath(os.path.expanduser(p))
    p = prefix_of_path(exe)
    if p:
        return p
    if umu:
        return None
    return environ.get("WINEPREFIX") or os.path.join(_home(), ".wine")


def wine_loader(cfg):
    w = (cfg.get("wine") or "").strip()
    if w:
        w = os.path.abspath(os.path.expanduser(w))
        found = proton_wine(w)
        if not found:
            raise LaunchError("no wine binary at {}".format(w))
        return found
    found = shutil.which("wine") or shutil.which("wine64")
    if not found:
        raise LaunchError("wine is not installed (or choose a Wine/Proton build)")
    return found


def protonpath(cfg):
    return (cfg.get("protonpath") or "").strip() or default_protonpath()


def _wine_common_env(exe, cfg, prefix, container=False):
    env = {}
    if prefix:
        env["WINEPREFIX"] = prefix
    env.update(locale_env(cfg, container=container))
    if "WINEDEBUG" not in os.environ:
        env["WINEDEBUG"] = "-all"
    env.update(user_env(cfg))
    return env


def build_wine(exe, cfg):
    loader = wine_loader(cfg)
    prefix = resolve_prefix(exe, cfg)
    if exe.lower().endswith(".exe"):
        argv = [loader, exe]
    else:  # .lnk/.bat/.msi: let `start` pick the handler
        argv = [loader, "start", "/unix", exe]
    argv += _split(cfg.get("args", ""))
    return Command(argv, _wine_common_env(exe, cfg, prefix), os.path.dirname(exe), WINE)


def build_umu(exe, cfg):
    umu = shutil.which("umu-run")
    if not umu:
        raise LaunchError("umu-run is not installed")
    prefix = resolve_prefix(exe, cfg, umu=True)
    # Proton's container generates the requested locale itself
    env = _wine_common_env(exe, cfg, prefix, container=True)
    if not prefix:
        # let umu use its own default prefix, not the session's WINEPREFIX
        env["WINEPREFIX"] = None
    env["PROTONPATH"] = protonpath(cfg)
    env["GAMEID"] = (cfg.get("gameid") or "").strip() or DEFAULT_GAMEID
    if "LC_ALL" in env:
        # Proton drops LC_ALL (Steam sets LC_ALL=C) unless HOST_LC_ALL says
        # what the real value is; without it the inherited LC_CTYPE wins
        env["HOST_LC_ALL"] = env["LC_ALL"]
    env.update(user_env(cfg))  # user values win over the defaults above
    argv = [umu, exe] + _split(cfg.get("args", ""))
    return Command(argv, env, os.path.dirname(exe), UMU)


def _flatpak_has(appid):
    return bool(shutil.which("flatpak")) and os.path.isdir(
        os.path.join(_home(), ".var", "app", appid)
    )


def steam_appid(exe, cfg):
    a = str(cfg.get("steam_appid") or "").strip()
    if a:
        return a
    app = find_steam_app(exe)
    return app["appid"] if app else None


def build_steam(exe, cfg):
    appid = steam_appid(exe, cfg)
    if not appid:
        raise LaunchError("no Steam AppID: the game is not in a Steam library, set it in the launcher settings")
    if not appid.isdigit():
        raise LaunchError("invalid Steam AppID {!r}".format(appid))
    args = _split(cfg.get("args", ""))
    steam = shutil.which("steam")
    if steam:
        argv = [steam, "-applaunch", appid] + args
    elif _flatpak_has("com.valvesoftware.Steam"):
        argv = ["flatpak", "run", "com.valvesoftware.Steam", "-applaunch", appid] + args
    else:
        argv = ["xdg-open", "steam://rungameid/" + appid]
    return Command(argv, {}, None, STEAM)


def lutris_id(exe, cfg):
    g = str(cfg.get("lutris_id") or "").strip()
    if g:
        return g
    game = find_lutris_game(exe)
    return game["id"] if game else None


def build_lutris(exe, cfg):
    gid = lutris_id(exe, cfg)
    if not gid:
        raise LaunchError("no Lutris game id: the game is not in Lutris' library, set it in the launcher settings")
    uri = "lutris:rungameid/" + gid
    lutris = shutil.which("lutris")
    if lutris:
        argv = [lutris, uri]
    elif _flatpak_has("net.lutris.Lutris"):
        argv = ["flatpak", "run", "net.lutris.Lutris", uri]
    else:
        argv = ["xdg-open", uri]
    return Command(argv, {}, None, LUTRIS)


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def build_custom(exe, cfg):
    """Template, shell-split first and then formatted per word (so paths with
    spaces stay one argument). Leading NAME=value words become environment.
    A word that is exactly {args} expands to the extra arguments."""
    template = cfg.get("template") or DEFAULT_TEMPLATE
    prefix = resolve_prefix(exe, cfg)

    def lazy_wine():
        try:
            return wine_loader(cfg)
        except LaunchError:
            return "wine"

    values = _Keep(
        exe=exe,
        exepath=exe,  # the Windows launcher's placeholder
        dir=os.path.dirname(exe),
        prefix=prefix,
        exe_dos=unix_to_dos(exe, prefix),
        locale=locale_env(cfg).get("LANG", ""),
        gameid=(cfg.get("gameid") or "").strip() or DEFAULT_GAMEID,
    )
    if "{wine}" in template:
        values["wine"] = lazy_wine()
    if "{protonpath}" in template:
        values["protonpath"] = protonpath(cfg)
    if "{appid}" in template:
        values["appid"] = steam_appid(exe, cfg) or ""
    env = {"WINEPREFIX": prefix}
    env.update(locale_env(cfg))
    env.update(user_env(cfg))
    argv = []
    words = _split(template)
    leading = True
    for w in words:
        if leading and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            k, _, v = w.partition("=")
            env[k] = v.format_map(values) if "{" in v else v
            continue
        leading = False
        if w == "{args}":
            argv += _split(cfg.get("args", ""))
            continue
        try:
            argv.append(w.format_map(values))
        except (ValueError, IndexError, AttributeError, KeyError, TypeError):
            # not a valid placeholder expression: keep the word literally
            argv.append(w)
    if not argv:
        raise LaunchError("the command template is empty")
    return Command(argv, env, os.path.dirname(exe), CUSTOM)


def build_direct(exe, cfg):
    if is_native_program(exe):
        argv = [exe] + _split(cfg.get("args", ""))
        env = user_env(cfg)
    elif is_windows_program(exe):
        return build_wine(exe, cfg)
    else:
        argv, env = ["xdg-open", exe], {}
    return Command(argv, env, os.path.dirname(exe), DIRECT)


def detect(exe, cfg=None):
    """Default launcher for a game: (launcher id, human readable reason)."""
    if is_native_program(exe):
        return DIRECT, "native Linux program"
    app = find_steam_app(exe)
    if app:
        return STEAM, "Steam: {} (AppID {})".format(app["name"], app["appid"])
    game = find_lutris_game(exe)
    if game:
        return LUTRIS, "Lutris: {} (id {})".format(game["name"], game["id"])
    if not (shutil.which("wine") or shutil.which("wine64")) and shutil.which("umu-run"):
        return UMU, "umu-run (no system wine)"
    loc = requested_locale(cfg or {})
    if (
        loc
        and not locale_available(loc)
        and shutil.which("umu-run")
        and not prefix_of_path(exe)
        and not (cfg or {}).get("prefix")
    ):
        # system Wine cannot run the game in that locale; Proton can
        return UMU, "umu-run ({} locale not installed for system Wine)".format(loc)
    return WINE, "Wine"


BUILDERS = {
    WINE: build_wine,
    UMU: build_umu,
    STEAM: build_steam,
    LUTRIS: build_lutris,
    CUSTOM: build_custom,
    DIRECT: build_direct,
}


def build(launcher_id, exe, cfg):
    if launcher_id == AUTO or launcher_id not in BUILDERS:
        launcher_id = detect(exe, cfg)[0]
    return BUILDERS[launcher_id](exe, cfg)


def spawn(cmd, log_path=None):
    """Start cmd detached from LunaTranslator (own session, no inherited
    stdio) with the user's environment (lunaplat.linux.child_env)."""
    from lunaplat.linux import child_env

    env = child_env(cmd.env)
    # a None value removes an inherited variable (e.g. the session's
    # WINEPREFIX when umu-run should use its own default prefix)
    env = {k: v for k, v in env.items() if v is not None}
    out = subprocess.DEVNULL
    if log_path:
        try:
            out = open(log_path, "wb")
            out.write(("$ {!r}\n".format(cmd)).encode("utf-8", "replace"))
            out.flush()
        except (OSError, IOError):
            out = subprocess.DEVNULL
    cwd = cmd.cwd if cmd.cwd and os.path.isdir(cmd.cwd) else None
    try:
        return subprocess.Popen(
            cmd.argv,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as e:
        raise LaunchError("cannot run {}: {}".format(cmd.argv[0], e))
    finally:
        if out is not subprocess.DEVNULL:
            out.close()


# ------------------------------------------------- launcher classes ----


def _gamepath(config):
    return config.get("launchpath") or config.get("gamepath") or ""


_NOISE = re.compile(r"MESA|pci id for fd|libEGL|^\$ |^\s*$")


def _tail(path, n=3):
    """Last few meaningful log lines (graphics driver chatter removed)."""
    try:
        with open(path, "rb") as f:
            data = f.read()[-20000:]
    except (OSError, IOError):
        return ""
    lines = [l for l in data.decode("utf-8", "replace").splitlines() if not _NOISE.search(l)]
    msg = "\n".join(l.strip()[:300] for l in lines[-n:])
    return msg + "\n(log: {})".format(path)


def _report(msg):
    sys.stderr.write("[lunaplat] launch: {}\n".format(msg))
    try:
        import gobject
        from lunaplat.linux.qtthread import post_to_gui

        post_to_gui(lambda: gobject.base.displayinfomessage(msg, "<msg_error_Origin>"))
    except Exception:
        pass


def launch(launcher_id, exe, config, log_name=None):
    """Build, start and watch briefly; returns the Popen (or None on error)."""
    cfg = settings(config)
    try:
        cmd = build(launcher_id, exe, cfg)
    except LaunchError as e:
        _report(str(e))
        return None
    loc = requested_locale(cfg)
    if loc and cmd.launcher in (WINE, CUSTOM) and not locale_available(loc):
        _report(missing_locale_hint(loc))
    log_path = None
    try:
        import gobject

        log_path = gobject.getcachedir("linux_launch/{}.log".format(log_name or "last"))
    except Exception:
        pass
    sys.stderr.write("[lunaplat] launch: {!r}\n".format(cmd))
    try:
        p = spawn(cmd, log_path)
    except LaunchError as e:
        _report(str(e))
        return None
    # a launcher that fails right away (bad wine, missing prefix dir...)
    for _ in range(20):
        rc = p.poll()
        if rc is not None:
            if rc != 0:
                _report(
                    "{} exited with {}: {}".format(
                        os.path.basename(cmd.argv[0]), rc, _tail(log_path) if log_path else ""
                    )
                )
            break
        time.sleep(0.1)
    if p.poll() in (None, 0) and cmd.launcher != DIRECT:
        attach_when_ready(exe)
    return p


ATTACH_TIMEOUT = 120


def attach_when_ready(exe, timeout=ATTACH_TIMEOUT):
    """Hook a game we started as soon as its window exists.

    Upstream attaches by watching the foreground window, which works when
    the freshly started game takes the focus. On Linux that is not reliable
    (the compositor may keep focus elsewhere, or the overlay may be raised
    while the game starts), so for games launched from LunaTranslator we
    look for the game's own window and attach to it directly."""
    import threading

    def run():
        try:
            import gobject
            from lunaplat.linux import gameident, proc, x11
        except Exception:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.0)
            ts = getattr(gobject.base, "textsource", None)
            if ts is None or not hasattr(ts, "connecthwnd"):
                return  # not in hook mode
            try:
                if len(ts.pids[ts.gameuid]):
                    return  # already attached (e.g. by the foreground watcher)
            except Exception:
                pass
            pids = {
                pid
                for pid, unix, _ in proc.list_wine_processes()
                if gameident.same_game(unix, exe)
            }
            if not pids or not x11.available():
                continue
            for xid in x11.client_list():
                if x11.window_pid(xid) in pids and x11.is_viewable(xid):
                    try:
                        if ts.connecthwnd(xid):
                            return
                    except Exception:
                        pass

    threading.Thread(target=run, name="attach-launched-game", daemon=True).start()


class LinuxLauncher(object):
    name = ""
    id = ""

    def run(self, gameexe, config, log_name=None):
        return launch(self.id, gameexe, config, log_name)

    def setting(self, layout, config):
        ui = _SettingsUI(layout, config)
        self.fill(ui, _gamepath(config))

    def fill(self, ui, exe):
        pass


class AutoLauncher(LinuxLauncher):
    name = "自动"
    id = AUTO

    def fill(self, ui, exe):
        lid, why = detect(exe, ui.cfg)
        ui.info("Detected", why)
        LAUNCHERS_BY_ID[lid]().fill(ui, exe)


class WineLauncher(LinuxLauncher):
    name = "Wine"
    id = WINE

    def fill(self, ui, exe):
        ui.wine()
        ui.prefix(exe)
        ui.locale()
        ui.args()
        ui.env()


class UmuLauncher(LinuxLauncher):
    name = "umu-run (Proton)"
    id = UMU

    def fill(self, ui, exe):
        ui.proton()
        ui.prefix(exe, umu=True)
        ui.line("GAMEID", "gameid", DEFAULT_GAMEID)
        ui.locale()
        ui.args()
        ui.env()


class SteamLauncher(LinuxLauncher):
    name = "Steam"
    id = STEAM

    def fill(self, ui, exe):
        app = find_steam_app(exe)
        ui.line("Steam AppID", "steam_appid", app["appid"] if app else "", placeholder=True)
        if app:
            ui.info("", "{} ({})".format(app["name"], app["library"]))
        loc = locale_env(ui.cfg).get("LANG")
        ui.info(
            "Steam",
            "Locale and Proton are set in Steam. Launch options: "
            + ("LANG={0} HOST_LC_ALL={0} %command%".format(loc) if loc else "%command%"),
            copyable=True,
        )
        ui.args()


class LutrisLauncher(LinuxLauncher):
    name = "Lutris"
    id = LUTRIS

    def fill(self, ui, exe):
        game = find_lutris_game(exe)
        ui.line("Lutris ID", "lutris_id", game["id"] if game else "", placeholder=True)
        if game:
            ui.info("", "{} ({}, {})".format(game["name"], game["slug"], game["runner"]))
        ui.info("Lutris", "Wine version, prefix and locale are set in Lutris.")


class CustomLauncher(LinuxLauncher):
    name = "命令行启动"
    id = CUSTOM

    def fill(self, ui, exe):
        ui.line("命令行启动", "template", DEFAULT_TEMPLATE, emptydefault=True)
        ui.info(
            "",
            "{exe} {dir} {prefix} {exe_dos} {wine} {protonpath} {locale} {gameid} {appid} {args}",
        )
        ui.wine()
        ui.prefix(exe)
        ui.locale()
        ui.args()
        ui.env()


class DirectLauncher(LinuxLauncher):
    name = "直接启动"
    id = DIRECT

    def fill(self, ui, exe):
        if exe and not is_native_program(exe) and is_windows_program(exe):
            WineLauncher().fill(ui, exe)
        else:
            ui.args()
            ui.env()


LAUNCHERS = [
    AutoLauncher,
    WineLauncher,
    UmuLauncher,
    SteamLauncher,
    LutrisLauncher,
    CustomLauncher,
    DirectLauncher,
]
LAUNCHERS_BY_ID = {c.id: c for c in LAUNCHERS}


class _SettingsUI(object):
    """Builds rows into the game manager's launcher settings layout (an
    object with addRow(label, widget_or_layout)) bound to linux_launch."""

    def __init__(self, layout, config):
        self.layout = layout
        self.cfg = settings(config)

    def _qt(self):
        from qtsymbols import QLineEdit, QLabel, QHBoxLayout, QFileDialog
        from gui import usefulwidget

        return QLineEdit, QLabel, QHBoxLayout, QFileDialog, usefulwidget

    def info(self, label, text, copyable=False):
        QLineEdit, QLabel, _, _, _ = self._qt()
        if copyable:
            w = QLineEdit(text)
            w.setReadOnly(True)
        else:
            w = QLabel(text)
            w.setWordWrap(True)
        self.layout.addRow(label, w)

    def line(self, label, key, default, placeholder=False, emptydefault=False):
        """placeholder: show default greyed, store only what the user types;
        emptydefault: an empty field means default."""
        _, _, _, _, uw = self._qt()
        if placeholder:
            w = uw.getlineedit(self.cfg, key)
            w.setPlaceholderText(default)
        else:
            if key not in self.cfg and not emptydefault:
                self.cfg[key] = default
            w = uw.getlineedit(self.cfg, key, default=default)
            if emptydefault:
                w.setPlaceholderText(default)
        self.layout.addRow(label, w)
        return w

    def locale(self):
        w = self.line("LANG", "locale", DEFAULT_LOCALE)
        tip = "LANG / LC_ALL of the game. Empty: keep the current environment."
        loc = requested_locale(self.cfg)
        if loc and not locale_available(loc):
            tip += "\n\n" + missing_locale_hint(loc)
            w.setStyleSheet("color: #d9534f;")
        w.setToolTip(tip)

    def args(self):
        self.line("参数", "args", "", placeholder=True)

    def env(self):
        w = self.line("env", "env", "WINEDLLOVERRIDES=… DXVK_HUD=1", placeholder=True)
        w.setToolTip("Extra environment variables, NAME=value separated by spaces")

    def prefix(self, exe, umu=False):
        _, _, _, _, uw = self._qt()
        cfg = self.cfg

        def setp(p):
            cfg["prefix"] = p or ""

        lay = uw.getsimplepatheditor(
            cfg.get("prefix", ""),
            isdir=True,
            callback=setp,
            icons=("fa.gear", "fa.undo"),
            clearset=lambda: "",
            editable=True,
        )
        try:
            default = resolve_prefix(exe, {}, umu=umu) if exe else None
        except Exception:
            default = None
        edit = lay.itemAt(0).widget()
        if edit is not None:
            edit.setPlaceholderText(
                default or "~/Games/umu/<GAMEID>"
            )
        self.layout.addRow("WINEPREFIX", lay)

    def _choice(self, label, key, entries, filedialog):
        """Combo of (title, value) entries plus a browse button; a configured
        value that is not in the list is added as its own entry."""
        _, _, QHBoxLayout, QFileDialog, uw = self._qt()
        cur = self.cfg.get(key, "")
        if cur and cur not in [v for _, v in entries]:
            entries = entries + [(cur, cur)]
        combo = uw.getsimplecombobox(
            [t for t, _ in entries], self.cfg, key, internal=[v for _, v in entries], static=True
        )
        combo.setCurrentData(cur)

        def browse():
            if filedialog == "dir":
                p = QFileDialog.getExistingDirectory(directory=cur or os.path.expanduser("~"))
            else:
                p = QFileDialog.getOpenFileName(directory=cur or os.path.expanduser("~"))[0]
            if not p:
                return
            self.cfg[key] = p
            if combo.findText(p) < 0:
                combo.addItem(p, p)
            combo.setCurrentData(p)

        btn = uw.getIconButton(browse, icon="fa.gear")
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(combo, 1)
        lay.addWidget(btn)
        self.layout.addRow(label, lay)

    def wine(self):
        from myutils.config import _TR

        system = shutil.which("wine") or shutil.which("wine64")
        entries = [("{} ({})".format(_TR("默认"), system or "wine: not found"), "")]
        entries += [(p["name"], p["path"]) for p in find_protons()]
        self._choice("Wine", "wine", entries, "file")

    def proton(self):
        from myutils.config import _TR

        protons = find_protons()
        auto = protons[0]["name"] if protons else UMU_FALLBACK_PROTON
        entries = [("{} ({})".format(_TR("自动"), auto), "")]
        entries += [(p["name"], p["path"]) for p in protons]
        entries += [("GE-Proton (umu download)", UMU_FALLBACK_PROTON)]
        self._choice("PROTONPATH", "protonpath", entries, "dir")


# ------------------------------------------------------------- patch ----


def _getgamecamptools(gameexe=None, b=None):
    return list(LAUNCHERS)


def _fundlauncher(_id):
    return LAUNCHERS_BY_ID.get(_id)


def _localeswitchedrun(gameuid):
    from myutils.config import savehook_new_data, get_launchpath

    config = savehook_new_data[gameuid]
    gameexe = os.path.abspath(get_launchpath(gameuid))
    cls = LAUNCHERS_BY_ID.get(config.get("launch_method"), AutoLauncher)
    return cls().run(gameexe, config, log_name=re.sub(r"[^\w.-]", "_", str(gameuid)))


def _maycreatesettings(layout, config, launcherid):
    LAUNCHERS_BY_ID.get(launcherid, AutoLauncher)().setting(layout, config)


def patch(mod):
    mod.x86tools = list(LAUNCHERS)
    mod.x64tools = list(LAUNCHERS)
    mod.getgamecamptools = _getgamecamptools
    mod.fundlauncher = _fundlauncher
    mod.localeswitchedrun = _localeswitchedrun
    mod.maycreatesettings = _maycreatesettings
