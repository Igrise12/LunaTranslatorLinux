"""Linux offline TTS engines: add their config entries at load time.

The engines live in tts/espeakng.py and tts/piper.py (the plugin loader only
looks there). Upstream's defaultconfig/config.json does not know them, so
patch_config() (run right after myutils.config is imported) adds them to
globalconfig["reader"] and defaultglobalconfig["reader"], keeping whatever
the user saved for them.

eSpeak NG is the default reader on Linux, as WindowsTTS is on Windows: it is
switched on when no other usable reader is.
"""

import copy

# shown in this order, before the other offline engines
READERS = {
    "espeakng": {
        "use": True,
        "name": "eSpeak NG",
        "type": "offline",
        "args": {"path": ""},
        "argstype": {
            "path": {
                "type": "file",
                "name": "espeak-ng (optional)",
            }
        },
    },
    "piper": {
        "use": False,
        "name": "Piper",
        "type": "offline",
        "args": {"path": "", "modeldir": ""},
        "argstype": {
            "path": {
                "type": "file",
                "name": "piper (optional)",
            },
            "modeldir": {
                "type": "file",
                "dir": True,
                "name": "Voice models (.onnx + .onnx.json)",
            },
        },
    },
}


def _apply(syncconfig, globalconfig, defaultglobalconfig, supported):
    readers = globalconfig.setdefault("reader", {})
    defaultglobalconfig.setdefault("reader", {})
    other_in_use = any(
        isinstance(v, dict) and v.get("use") and k not in READERS and supported(k)
        for k, v in readers.items()
    )
    # eSpeak replaces a Windows-only engine that was on (fresh configs have
    # WindowsTTS on); users who switched every engine off keep TTS off
    windows_only_in_use = any(
        isinstance(v, dict) and v.get("use") and k not in READERS and not supported(k)
        for k, v in readers.items()
    )
    for key, entry in READERS.items():
        defaultglobalconfig["reader"][key] = copy.deepcopy(entry)
        cur = readers.get(key)
        if not isinstance(cur, dict):
            cur = copy.deepcopy(entry)
            if key == "espeakng":
                cur["use"] = windows_only_in_use and not other_in_use
            readers[key] = cur
        else:
            syncconfig(cur, entry)
    # Linux engines first; reorder in place (TTSbase keeps a reference)
    items = list(readers.items())
    readers.clear()
    for k, v in items:
        if k in READERS:
            readers[k] = v
    for k, v in items:
        if k not in READERS:
            readers[k] = v


def patch_config(mod):
    from lunaplat.linux.features import supported

    _apply(
        mod.syncconfig,
        mod.globalconfig,
        mod.defaultglobalconfig,
        lambda k: supported("reader", k),
    )
