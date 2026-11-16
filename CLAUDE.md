# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

LunaTranslator is a visual novel translator: it pulls text out of running games (by injecting a text hook, via emulator hooks, OCR, clipboard and so on), runs it through translation engines (including LLMs), and shows it in an overlay. It can also do TTS, look up words in dictionaries, export to Anki, and embed translations into the game. The repo is a fork (`origin` = Igrise12/LunaTranslatorLinux) of `upstream` = HIllya51/LunaTranslator. Code comments and many config keys are in Chinese; pinyin identifiers are common (`fanyi` = translator, `cishu` = dictionary, `xiaoxueguan` = dictionary lookup, `copyed` = user-copied plugins).

Linux status: only the cross-platform parts of the native modules (`NativeUtils`, `CVUtils`) build on Linux. The Python app still assumes Windows. It uses `ctypes.wintypes`, a hand-rolled Win32 binding in `windows.py`, and loads native libraries by `.dll` name through `gobject.GetDllpath`. LunaHook (the injected hook DLLs), `LunaSubprocess` and `exec` are Windows-only.

## Layout

- `src/LunaTranslator/` holds the Python/PyQt5 application. Its entry point is `main.py`, which `chdir`s to `src/` and puts `src/LunaTranslator` on the path, so all imports are top-level (`import gobject`, `from myutils.config import ...`).
- `src/NativeImpl/` holds the C++ native code (CMake):
  - `NativeUtils/`: a Win32/WinRT utility DLL (screenshots, hwnd, audio, webview, MeCab, and more) loaded through ctypes in `NativeUtils.py`.
  - `CVUtils/`: the OpenCV and onnxruntime local OCR, loaded through `CVUtils.py`.
  - `LunaHook/`: the text hooking engine. `LunaHook{32,64}.dll` is injected into the game and `LunaHost{32,64}.dll` is the host side, loaded by `textio/textsource/texthook.py`.
  - `LunaSubprocess/` and `exec/` build the helper and launcher executables.
- `src/files/` holds runtime assets. `lang/*.json` are UI translation tables. Built binaries are deployed to the gitignored `DLL32/`, `DLL64/` and `LunaHook/` folders.
- `docs/` is the VitePress user guide in several languages (`zh`, `en`, `ja`, ...).

## Commands

There is no test suite and no linter config. The only formatter is black: `python src/formatalll.py` runs black over `src/LunaTranslator`.

Run from source (Windows): `cd src && python -B LunaTranslator\main.py` (this is `src/run.bat`). The app expects native binaries in `src/files/DLL{32,64}` and `src/files/LunaHook`.

Native build on Linux (x64; only NativeUtils and CVUtils). Dependencies: `build-essential cmake ninja-build protobuf-compiler libprotobuf-dev libopencv-dev`.
```sh
src/NativeImpl/scripts/configure64linux.sh   # cmake -G Ninja into src/NativeImpl/build/linux
src/NativeImpl/scripts/build64linux.sh       # builds, copies .so files into src/files/DLL64
```
Both scripts export `http_proxy` pointing at a WSL-host proxy on port 7897, because FetchContent downloads from GitHub. Edit or unset that if it does not apply to your machine. Build output goes to `src/NativeImpl/builds/Release_<arch>[_win10|_win7|_winxp]/`.

Windows builds (what CI does, see `.github/workflows/buildluna.yml`) go through `src/scripts/build_lunatranslator.py`:
```
python src/scripts/build_lunatranslator.py cpp   <x86|x64> <win10|win7|winxp>   # NativeImpl
python src/scripts/build_lunatranslator.py hook  <x86|x64> <win10|win7|winxp>   # LunaHook
python src/scripts/build_lunatranslator.py pyrt  <arch> <pyver> <target>        # bundle Python runtime
python src/scripts/build_lunatranslator.py merge <arch> <target>
python src/scripts/build_lunatranslator.py download [target]                    # third-party deps, OCR model
```
CI targets x86 winxp and x64 win7 (Python 3.7.9), and x64 win10 (Python 3.13). Python code must therefore stay compatible with Python 3.7: no walrus operator, no `match`, no `list[str]` in runtime annotations (string annotations are fine and already used).

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
- `main.py` monkey-patches `os.path.exists`, `os.path.isdir`, `os.path.isfile` and `os.startfile` with Win32-backed versions, so be aware of that when debugging path checks.

## LunaHook (C++)

- Engines live in `LunaHook/LunaHook/engine32/` (and `engine64/` and `engines/` for cross-arch ones such as Unity, Ren'Py and V8). Each engine subclasses `ENGINE` (`engine.h`). It sets `check_by` and `check_by_target` (a file name or names, a resource string, or a custom function) to detect the game, and implements `attach_function()`, which usually finds code with the `MemDbg::` pattern-search helpers and inserts a `HookParam`. To register a new engine, include its header and add it to `check_engines()` in `enginecollection32.cpp` or `enginecollection64.cpp`. Adding support for a specific game is often just a new static function inside an existing engine's `.cpp`.
- Emulator hooks (`emulators/`: PPSSPP, PCSX2, Yuzu/Ryujinx, Vita3K, RPCS3) keep tables of per-game hooks keyed by game ID. `emulators/genemusupportlist.py` parses those tables (the `// Game name` comments) to generate the docs' `emusupportlist`, so keep that comment format when adding entries.
