# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

LunaTranslator is a visual novel translator: it pulls text out of running games (by injecting a text hook, via emulator hooks, OCR, clipboard and so on), runs it through translation engines (including LLMs), and shows it in an overlay. It can also do TTS, look up words in dictionaries, export to Anki, and embed translations into the game. The repo is a fork (`origin` = Igrise12/LunaTranslatorLinux) of `upstream` = HIllya51/LunaTranslator. Code comments and many config keys are in Chinese; pinyin identifiers are common (`fanyi` = translator, `cishu` = dictionary, `xiaoxueguan` = dictionary lookup, `copyed` = user-copied plugins).

Linux status: the `linux-port` branch runs the app natively on Linux (PyQt6, Python 3.10+) without rewriting upstream modules. All Linux code lives in `src/LunaTranslator/lunaplat/` (see "Linux port" below). Windows games are hooked through `LunaBridge`, a Windows helper started inside the game's Wine/Proton prefix. Only `NativeUtils` and `CVUtils` build as Linux `.so` files; LunaHook, LunaHost and LunaBridge are cross-compiled Windows binaries. `LunaSubprocess` and `exec` are Windows-only. User guide: `docs/en/linux.md`.

## Layout

- `src/LunaTranslator/` holds the Python/PyQt5 application (PyQt6 on Linux). Its entry point is `main.py`, which `chdir`s to `src/` and puts `src/LunaTranslator` on the path, so all imports are top-level (`import gobject`, `from myutils.config import ...`).
- `src/NativeImpl/` holds the C++ native code (CMake):
  - `NativeUtils/`: a Win32/WinRT utility DLL (screenshots, hwnd, audio, webview, MeCab, and more) loaded through ctypes in `NativeUtils.py`.
  - `CVUtils/`: the OpenCV and onnxruntime local OCR, loaded through `CVUtils.py`.
  - `LunaHook/`: the text hooking engine. `LunaHook{32,64}.dll` is injected into the game and `LunaHost{32,64}.dll` is the host side, loaded by `textio/textsource/texthook.py`. `LunaHook/LunaBridge/` (CMake option `BUILD_BRIDGE`, off by default) builds `LunaBridge{32,64}.exe` and the `hooktarget{32,64}.exe` test program for the Linux port.
  - `LunaSubprocess/` and `exec/` build the helper and launcher executables.
- `src/files/` holds runtime assets. `lang/*.json` are UI translation tables. `linux/lunatranslator-proton.sh` is the Proton launch-option wrapper. Built binaries are deployed to the gitignored `DLL32/`, `DLL64/` and `LunaHook/` folders.
- `docs/` is the VitePress user guide in several languages (`zh`, `en`, `ja`, ...).

## Commands

There is no test suite and no linter config. The only formatter is black: `python src/formatalll.py` runs black over `src/LunaTranslator`.

Run from source (Windows): `cd src && python -B LunaTranslator\main.py` (this is `src/run.bat`). The app expects native binaries in `src/files/DLL{32,64}` and `src/files/LunaHook`.

Linux (x64). The workflow `.github/workflows/buildlinux.yml` runs the same steps in CI.
```sh
src/NativeImpl/scripts/linux/build_native.sh [--clean] [--ocr-model]   # NativeUtils/CVUtils -> src/files/DLL64
src/NativeImpl/scripts/linux/build_winbins.sh [--clean] [x86] [x64]    # LunaHook/LunaHost/LunaBridge -> src/files/LunaHook
cd src && python3 -B LunaTranslator/main.py
python3 src/NativeImpl/scripts/linux/bridge_smoke.py --arch 32|64 [--embed] [--umu [--proton DIR]] [--attach] [--prefix DIR] [--seconds N]
```
- `src/scripts/linux/setup.sh` is the user-facing from-source setup. It only checks by default, printing the missing pieces and one distro install command; it never installs system packages. `--venv` builds `.venv` with `--system-site-packages`; `--pip-qt` takes PyQt6 from pip instead (Ubuntu 22.04 has no PyQt6 package). `--native` and `--winbins build|download|auto` wrap the scripts below; `download` fetches the `lunatranslator-winbins.tar.gz` release asset. `--install-desktop` installs `~/.local/bin/lunatranslator` and the menu entry. pip names are in `src/scripts/linux/requirements.txt`.
- The AppImage is built by `src/scripts/linux/appimage/build_appimage.sh` (podman or docker); it needs the Windows binaries in `src/files/LunaHook` and writes to `dist/`.
  - The native libraries are rebuilt in a `manylinux_2_28` container (`build_native_compat.sh` → `native_in_container.sh`), with protobuf and OpenCV linked statically. The base is a python-appimage Python 3.13; the PyQt6 wheels are pinned in `constraints.txt` and pruned by `prune_qt.py`.
  - `AppRun` copies the read-only payload once per build to `$LUNATRANSLATOR_HOME/app/<build>/` (default `~/.local/share/LunaTranslator`) and runs from that folder through symlinks. Each instance holds a shared `flock` on its build, and old builds are removed when unused. `files/LunaHook`, `files/linux` and `luna.ico` are real copies at fixed paths, because Wine and Steam launch options use them.
  - The desktop entry and AppStream metainfo are `src/files/linux/io.github.Igrise12.LunaTranslatorLinux.*`, the same ID that `qt6compat` sets as `desktopFileName`.
  - `.github/workflows/buildlinux.yml` builds the AppImage and the winbins archive, and publishes both on `v*` tags.
- `build_native.sh` needs `cmake ninja protoc curl` plus OpenCV and protobuf dev packages. FetchContent downloads from GitHub, so set `http_proxy` yourself if needed. `--ocr-model` also fetches the built-in OCR model. The older `scripts/configure64linux.sh`/`build64linux.sh` hardcode a WSL-host proxy on port 7897; prefer `build_native.sh`.
- `build_winbins.sh` cross-compiles with real MSVC under Wine through msvc-wine, installed in `~/opt/msvc` (`MSVC_WINE_DIR` to override; the one-time `vsdownload.py --accept-license` setup is in the script header and needs `msitools`). It uses the toolchain file `NativeImpl/cmake/msvc-wine.cmake` and its own prefix `~/.cache/lunatranslator/msvc-wine-prefix`, and configures LunaHook with `-DBUILD_HOOK=ON -DBUILD_HOST=ON -DBUILD_BRIDGE=ON`. `LunaTmpFontLoader.dll` (C#) is built only when Mono's `mcs` is found (`-DBUILD_TMPFONTLOADER`).
- `bridge_smoke.py` is the end-to-end hook test without the GUI. It runs `hooktarget{32,64}.exe` in a scratch prefix (`~/.cache/lunatranslator/smoke-prefix`, or `umu-prefix` with `--umu`), starts the bridge, injects, and waits for `TextOutW` text. `--embed` also checks embedded translation (via local OCR). `--umu` runs the target under umu-run/Proton (`--proton` sets PROTONPATH). `--attach` uses an already running target. `LUNA_BRIDGE_MODE=host|nsenter` forces how the bridge enters a Proton container.
- There is no unit test suite for the port either. Test GUI changes by running the app (it forces XWayland on Wayland; `LUNA_NATIVE_WAYLAND=1` keeps native Wayland).

Windows builds (what CI does, see `.github/workflows/buildluna.yml`) go through `src/scripts/build_lunatranslator.py`:
```
python src/scripts/build_lunatranslator.py cpp   <x86|x64> <win10|win7|winxp>   # NativeImpl
python src/scripts/build_lunatranslator.py hook  <x86|x64> <win10|win7|winxp>   # LunaHook
python src/scripts/build_lunatranslator.py pyrt  <arch> <pyver> <target>        # bundle Python runtime
python src/scripts/build_lunatranslator.py merge <arch> <target>
python src/scripts/build_lunatranslator.py download [target]                    # third-party deps, OCR model
```
CI targets x86 winxp and x64 win7 (Python 3.7.9), and x64 win10 (Python 3.13). Python code that Windows can import must therefore stay compatible with Python 3.7: no walrus operator, no `match`, no `X | Y` or `list[str]` in runtime annotations (string annotations are fine and already used). That is every file except `lunaplat/linux/**`, including `lunaplat/__init__.py`, `lunaplat/stubs.py` and `tts/espeakng.py`/`tts/piper.py`. Check a file with `python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read(), feature_version=(3,7))" FILE`; CI runs this over the whole tree (`buildlinux.yml`, job `python-checks`).

Docs: `cd docs && npm run docs:dev` (or `docs:build`).

Version: `src/version.txt`. A "Bump version" commit changes only that file.

## Python architecture

- `gobject.py` holds process-global state. `gobject.base` is the singleton `BASEOBJECT` (defined in `LunaTranslator.py`), which owns everything at runtime: the text source, the translators, TTS, OCR, dictionaries, the UI windows and signals. The path helpers live here too: `getconfig()` resolves into `src/userconfig/` and `getcachedir()` into `src/cache/`.
- `myutils/config.py` loads the defaults in `defaultconfig/*.json` and merges the user's saved JSON from `userconfig/` on top (`syncconfig`). The main dicts are `globalconfig`, `translatorsetting`, `ocrsetting`, `postprocessconfig` and `savehook_new_data` (per-game settings, keyed by game uid). `_TR()` translates a UI string through `files/lang/<lang>.json`, and the source strings are Chinese.
- Text flow: a `textio/textsource/*` class (`texthook`, `ocrtext`, `copyboard`, `filetrans`, `mssr`) produces text and hands it to `BASEOBJECT.textgetmethod`. The text then goes through the pre-processing configured in `postprocessconfig`, then the translators run in parallel, then the `transoptimi/*` passes (noun dictionaries, error fixes, Arabic reshaping), and finally the UI (`gui/translatorUI.py`, `gui/rendertext/`) and the `textio/textoutput/*` outputs (clipboard, websocket).
- Plugins are loaded dynamically by module name with `importlib`, and each kind must expose a fixed class name:
  - `translator/<key>.py` → `TS` (subclass of `basetranslator.basetrans`)
  - `ocrengines/<key>.py` → `OCR`
  - `tts/<key>.py` → `TTS`
  - `cishu/<key>.py` → `Cishu` (or a class named after the key)
  - `textio/textoutput/<key>.py` → `Outputer`
  - `metadata/<key>.py` → `searcher`

  Text sources are the exception: they are listed in a hardcoded dict in `BASEOBJECT.starttextsource`. Registering a plugin takes more than adding the file. It also needs an entry in `defaultconfig/config.json` (under `fanyi`, `ocr`, `reader`, `cishu`, ...), and any user-configurable parameters go in `defaultconfig/translatorsetting.json` or `ocrsetting.json` as `args` plus `argstype` (the settings UI is generated from `argstype`). Users can override plugins by copying them into `userconfig/copyed/`; see `translate_exits` in `myutils/utils.py`.
- Most LLM translators reuse `translator/chatgpt-3rd-party.py` (OpenAI-compatible) and set `is_gpt_like: true` in config. The `.js` files in `translator/` are injected into a browser over CDP for web-scraping engines.
- `main.py` monkey-patches `os.path.exists`, `os.path.isdir`, `os.path.isfile` and `os.startfile` with Win32-backed versions on Windows (on Linux only `os.startfile` is replaced, with `xdg-open`), so be aware of that when debugging path checks.

## Linux port (`lunaplat`)

- `main.py` calls `lunaplat.install()` before any other app import. On Windows it is a no-op, and `lunaplat/__init__.py` only passes through (`dllpath`, `load_library`, `windowsversion`, `supported`). On Linux, `lunaplat/linux/__init__.py:install_linux()` picks the Qt platform (xcb under Wayland unless `LUNA_NATIVE_WAYLAND=1`; `child_env()` restores the user's value for child processes), stubs `ctypes.windll`/`WinDLL` and `winreg`, maps `getproxies_registry` and `os.startfile`, and installs the patch registry.
- Lenient loading: `gobject.GetDllpath` → `lunaplat.dllpath` maps `NativeUtils.dll`/`CVUtils.dll` to `libNativeUtils.so`/`libCVUtils.so` (and `libcurl*.dll` to the system `libcurl.so.4`). They are loaded with `lunaplat.stubs.LenientCDLL`: the portable exports are real, and every missing (Windows-only) export becomes a `StubFunction` that returns the zero value of its `restype`. `LUNA_PLATFORM_REPORT=1` prints the stubs that were called at exit (= unported functionality); `LUNA_PLATFORM_STRICT=1` makes them raise.
- **Patch registry.** `PATCHES` in `lunaplat/linux/__init__.py` maps an upstream module name to `"lunaplat.linux.<mod>"` (calls its `patch(module)`), `"lunaplat.linux.<mod>:<func>"`, or a list of those (applied in order). A `sys.meta_path` finder runs them right after the upstream module executes, and they replace names in it (e.g. `windows` → `windows_impl`, `NativeUtils` → `nativeutils_impl` + `audio:patch_nativeutils`, `myutils.localetools` → `launchers`). **Add Linux behaviour here, not by editing upstream files.** That keeps upstream merges cheap. If a shared file must change, keep the diff tiny, guard it with `os.name != "nt"` or route it through `lunaplat`, and keep it Python 3.7-safe. Patched modules must not be imported before `install()` (it raises).
- Plugin availability: `lunaplat/linux/features.py:UNSUPPORTED` hides Windows-only plugins (consulted by `useExCheck` and `splitocrtypes` via `lunaplat.supported(kind, key)`). Linux-only plugins (`tts/espeakng.py`, `tts/piper.py`) get their config entries injected by `ttsengines.patch_config` and are hidden on Windows by `_LINUX_ONLY`.
- PyQt6: `lunaplat/linux/qt6compat.py` (patched into `qtsymbols`) promotes scoped enum members onto their classes (`Qt.AlignLeft`), makes flags `int()`-able and adds `exec_` aliases. Upstream keeps writing PyQt5-style code, so don't "fix" upstream call sites for PyQt6; extend `qt6compat` instead.
- Desktop backends: `x11/` (python-xlib: window ops, events, `XGrabKey` hotkeys, window picking; the "HWND"s are X11 window ids), `capture.py` (grim / portal / Qt), `clipboard.py` (wl-clipboard or QClipboard), `uinput.py` (synthetic input on Wayland), `audio.py` (ffmpeg + pacat/pw-cat, parec), `qt/webengine.py` (QtWebEngine in place of WebView2), `qtthread.py` (`run_in_gui`, `post_to_gui`), `proc.py` (Wine-aware `/proc`: Windows image path, prefix, environment).
- **Wine bridge.** LunaHost talks to LunaHook through pipes, shared memory and events that only exist inside the game's wineserver, so the host runs in `LunaBridge{32,64}.exe` (`NativeImpl/LunaHook/LunaBridge/main.cpp`) inside the game's prefix. It forwards the `Luna_*` calls and callbacks over token-authenticated 127.0.0.1 TCP. The framing and message ids are in `lunaplat/linux/wine/protocol.py` and must match `main.cpp`; bump `VERSION` on both sides when the protocol changes. Python side: `wine/server.py` (listener and the rendezvous file in `$XDG_RUNTIME_DIR/lunatranslator`), `wine/launcher.py` (`WineContext(unix_pid)`: the game's Wine loader and environment; host vs `nsenter` mode for Proton containers), and `wine/hostproxy.py` (`BridgeManager`: what `CDLL("LunaHost64.dll")` returns, so `texthook.py` runs unchanged). The app logs to `cache/lunabridge.log`; `LUNA_BRIDGE_DEBUG=1` makes the bridge verbose.
- Game launching on Linux: `lunaplat/linux/launchers.py` replaces Locale Emulator & co. (Wine, umu-run, Steam, Lutris, custom template). Per-game settings are in `savehook_new_data[uid]["linux_launch"]`.

## LunaHook (C++)

- Engines live in `LunaHook/LunaHook/engine32/` (and `engine64/` and `engines/` for cross-arch ones such as Unity, Ren'Py and V8). Each engine subclasses `ENGINE` (`engine.h`). It sets `check_by` and `check_by_target` (a file name or names, a resource string, or a custom function) to detect the game, and implements `attach_function()`, which usually finds code with the `MemDbg::` pattern-search helpers and inserts a `HookParam`. To register a new engine, include its header and add it to `check_engines()` in `enginecollection32.cpp` or `enginecollection64.cpp`. Adding support for a specific game is often just a new static function inside an existing engine's `.cpp`.
- Emulator hooks (`emulators/`: PPSSPP, PCSX2, Yuzu/Ryujinx, Vita3K, RPCS3) keep tables of per-game hooks keyed by game ID. `emulators/genemusupportlist.py` parses those tables (the `// Game name` comments) to generate the docs' `emusupportlist`, so keep that comment format when adding entries.
