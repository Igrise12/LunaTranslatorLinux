"""Mute the game's audio (NativeUtils.MonitorPidVolume / StartMonitorVolume /
SetCurrProcessMute) through PipeWire/PulseAudio with pactl.

A process's playback streams are the sink inputs whose
application.process.id equals its PID (true for Wine games, whose audio is
played by the game process itself). Changes are watched with
`pactl subscribe` and reported to the app's callback.
"""

import json
import shutil
import subprocess
import threading

_state = {"pid": 0, "callback": None, "muted": None, "watcher": None}
_lock = threading.Lock()


def _pactl(*args):
    try:
        return subprocess.run(
            ["pactl"] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            encoding="utf8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _sink_inputs(pid):
    r = _pactl("-f", "json", "list", "sink-inputs")
    if r is None or r.returncode != 0:
        return []
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    out = []
    for it in items:
        props = it.get("properties", {})
        if str(props.get("application.process.id", "")) == str(pid):
            out.append((it.get("index"), bool(it.get("mute"))))
    return out


_report_lock = threading.Lock()


def _report():
    with _report_lock:  # one report at a time, so each change fires once
        with _lock:
            pid, cb, last = _state["pid"], _state["callback"], _state["muted"]
        if not pid or cb is None:
            return
        streams = _sink_inputs(pid)
        muted = bool(streams) and all(m for _, m in streams)
        if muted == last:
            return
        with _lock:
            _state["muted"] = muted
    try:
        cb(muted)
    except Exception:
        pass


def _watch():
    try:
        # dies with the app (the app ends with os._exit, so no cleanup hook
        # would run)
        from lunaplat.linux.audio import _popen

        proc = _popen(["pactl", "subscribe"], stdout=subprocess.PIPE, encoding="utf8")
    except OSError:
        return
    for line in proc.stdout:
        if "sink-input" in line:
            _report()


def available():
    return shutil.which("pactl") is not None


def StartMonitorVolume(callback):
    with _lock:
        _state["callback"] = callback
        start = _state["watcher"] is None and available()
        if start:
            _state["watcher"] = threading.Thread(target=_watch, daemon=True)
    if start:
        _state["watcher"].start()


def MonitorPidVolume(pid):
    with _lock:
        _state["pid"] = int(pid or 0)
        _state["muted"] = None
    threading.Thread(target=_report, daemon=True).start()


def SetCurrProcessMute(mute):
    with _lock:
        pid = _state["pid"]
    if not pid:
        return
    for idx, _ in _sink_inputs(pid):
        _pactl("set-sink-input-mute", str(idx), "1" if mute else "0")
    _report()
