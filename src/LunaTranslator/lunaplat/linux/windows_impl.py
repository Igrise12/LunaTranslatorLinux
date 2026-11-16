"""Linux replacements for names in windows.py (applied after it imports).

windows.py binds Win32 APIs through the ctypes.windll stub, so every raw
binding already exists and returns a zero value. This module overrides the
helpers whose result the app actually depends on.
"""

import locale
import os
import subprocess
import threading

_tls = threading.local()


def GetLastError():
    return getattr(_tls, "lasterror", 0)


def SetLastError(code):
    _tls.lasterror = code


def GetLocale():
    """(language, country), e.g. ("zh", "CN"), like GetLocaleInfoW."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val and val not in ("C", "POSIX"):
            break
    else:
        try:
            val = locale.getlocale()[0] or ""
        except Exception:
            val = ""
    val = val.split(".")[0].split("@")[0]
    lang, _, country = val.partition("_")
    return lang or "en", country or ""


def PathFileExists(path):
    return os.path.exists(path)


def ShellExecute(hwnd, op, file, params, _dir, bShow):
    """Win32 ShellExecute: returns > 32 on success."""
    if op == "runas":
        # no UAC on Linux; the caller falls back to its non-elevated path
        return 5  # SE_ERR_ACCESSDENIED
    if not file:
        return 2
    from lunaplat.linux import child_env

    try:
        if params:
            import shlex

            cmd = [file] + shlex.split(params)
            subprocess.Popen(
                cmd, cwd=_dir or None, env=child_env(), start_new_session=True
            )
        else:
            subprocess.Popen(
                ["xdg-open", file],
                cwd=_dir or None,
                env=child_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return 42
    except OSError:
        return 2


def IsUserAnAdmin():
    return os.geteuid() == 0


def patch(mod):
    mod.GetLastError = GetLastError
    mod.SetLastError = SetLastError
    mod.GetLocale = GetLocale
    mod.PathFileExists = PathFileExists
    mod.ShellExecute = ShellExecute
    mod.IsUserAnAdmin = IsUserAnAdmin
