# Running on Linux

LunaTranslator runs natively on Linux. The application itself is a Linux program (Python and PyQt6). It translates Windows games that run under Wine, Proton (Steam, umu-run), Lutris or Bottles. Text hooking, embedded translation, OCR, the clipboard, TTS and the game launcher all work. A few Windows-only engines are not available. They are listed in [Known limitations](#known-limitations).

::: info
The Linux port lives in the [LunaTranslatorLinux](https://github.com/Igrise12/LunaTranslatorLinux) fork (branch `linux-port`). Run it from source or build the AppImage described below; tagged releases of the fork also carry a prebuilt AppImage. The Linux version is not on the official download page.
:::

## Quick setup (recommended)

From a clone of the repository:

```sh
src/scripts/linux/setup.sh          # check only: lists what is missing and prints
                                    # one install command for your distribution
src/scripts/linux/setup.sh --all    # venv + native libraries + Windows binaries
                                    # + "lunatranslator" launcher and menu entry
lunatranslator                      # or: .venv/bin/python3 -B src/LunaTranslator/main.py
```

`setup.sh` never installs system packages itself. Run the command it prints, then run it again. Arch, Debian/Ubuntu, Fedora and openSUSE are recognised. Its options can also be used one at a time:

| Option | What it does |
|---|---|
| `--check` | Only check dependencies (the default) |
| `--venv [DIR]` | Create a virtual environment (default `.venv`) that can see the distribution's PyQt6, and pip-install whatever is missing |
| `--pip-qt` | Always take PyQt6 and PyQt6-WebEngine from pip |
| `--native [--ocr-model]` | Build `libNativeUtils.so` / `libCVUtils.so` (and download the built-in OCR model) |
| `--winbins build\|download\|auto` | Cross-compile the Windows binaries with msvc-wine (`build`), download a prebuilt archive (`download`, URL from `LUNA_WINBINS_URL`), or build if msvc-wine is installed and download otherwise (`auto`) |
| `--install-desktop` / `--uninstall-desktop` | Add or remove `~/.local/bin/lunatranslator` and the application menu entry |
| `--all` | `--venv --native --winbins auto --install-desktop` |

### AppImage

`src/scripts/linux/appimage/build_appimage.sh` builds a self-contained AppImage (Python 3.13, PyQt6 with WebEngine, the native libraries and the Windows binaries) that runs on glibc 2.34 or newer (Ubuntu 22.04, Debian 12, Fedora 35 and later). It needs podman or docker, because the native libraries are compiled in a `manylinux_2_28` container, and the Windows binaries already in `src/files/LunaHook` (see [Build the Windows-side binaries](#_3-build-the-windows-side-binaries)). The result is written to `dist/LunaTranslator-<version>-x86_64.AppImage`.

The AppImage runs from a writable data folder, `${XDG_DATA_HOME:-~/.local/share}/LunaTranslator` (set `LUNATRANSLATOR_HOME` to use another folder). Settings are in its `userconfig/`, logs and caches in its `cache/` (the bridge log is `cache/lunabridge.log`), and the Proton launch-option script is `files/linux/lunatranslator-proton.sh` there. Several instances, and AppImages of different versions, can run at the same time.

Not bundled, install them from your distribution:

- the programs Wine, ffmpeg, pacat or pw-cat, wl-clipboard, grim, xdg-utils and eSpeak NG (see [Requirements](#requirements));
- `libcurl.so.4` and the usual desktop libraries: OpenGL/EGL, fontconfig, xkbcommon (with its X11 part), D-Bus, GLib, NSS, ALSA, gbm and the X11 libraries Xcomposite, Xdamage, Xrandr and Xtst. Every desktop installation has them; on a minimal system, a missing one shows up as an `ImportError` naming the library.

If you prefer to do everything by hand, or the script fails on your distribution, follow [Running from source](#running-from-source).

## Requirements

You need:

- Python 3.10 or newer;
- PyQt6, including the WebEngine, Multimedia, DBus and Svg modules;
- python-xlib, pefile, fontTools and tinycss2;
- the command-line tools ffmpeg, pacat (PipeWire's PulseAudio server or PulseAudio), wl-clipboard, grim, xdg-utils, Wine and eSpeak NG.

::: tabs

== Arch / CachyOS / Manjaro

```sh
sudo pacman -S --needed python python-pyqt6 python-pyqt6-webengine \
    qt6-multimedia qt6-svg python-xlib python-pefile python-fonttools python-tinycss2 \
    ffmpeg pipewire-pulse libpulse wl-clipboard grim xdg-utils wine espeak-ng
# optional
sudo pacman -S --needed tesseract tesseract-data-jpn python-dbus
sudo pacman -S --needed umu-launcher   # if your repositories have it, else from the AUR
# optional, from the AUR: mecab-ipadic
```

== Debian / Ubuntu

```sh
sudo apt install python3 python3-pyqt6 python3-pyqt6.qtwebengine \
    python3-pyqt6.qtmultimedia python3-pyqt6.qtsvg python3-xlib \
    python3-pefile python3-fonttools python3-tinycss2 ffmpeg pipewire-pulse pulseaudio-utils \
    wl-clipboard grim xdg-utils wine espeak-ng
# Ubuntu 22.04 and other releases without python3-pyqt6: install
#   python3-venv libxcb-cursor0 python3-xlib python3-pefile python3-fonttools python3-tinycss2
# (and the tools above), then run: src/scripts/linux/setup.sh --venv --pip-qt
# 32-bit Windows games with Debian's Wine:
#   sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install wine32:i386
# optional
sudo apt install tesseract-ocr tesseract-ocr-jpn mecab-ipadic-utf8 python3-dbus
```

== Fedora

```sh
sudo dnf install python3 python3-pyqt6 python3-pyqt6-webengine \
    python3-xlib python3-pefile python3-fonttools python3-tinycss2 ffmpeg-free \
    pipewire-pulseaudio pulseaudio-utils wl-clipboard grim xdg-utils \
    wine espeak-ng
# optional
sudo dnf install tesseract tesseract-langpack-jpn mecab-ipadic python3-dbus
```

:::

Package names change between releases. If one isn't found, search for the tool or Python module by name. You can also install the Python packages into a virtual environment with pip: `pip install -r src/scripts/linux/requirements.txt`.

What the tools are used for:

| Tool | Used for |
|---|---|
| ffmpeg, ffprobe | Decoding audio for TTS playback, transcoding, screen recording |
| pacat / parec / pactl (or pw-cat / pw-record) | Audio playback, recording system audio, muting the game |
| wl-clipboard | The clipboard on Wayland sessions |
| grim | Screenshots for OCR on wlroots compositors (Hyprland, Sway, ...) |
| xdg-utils | Opening links and files (`xdg-open`) |
| wine | Running and hooking Windows games |
| espeak-ng | The default TTS engine |
| libcurl | Network requests (installed on almost every system) |

Optional:

- **umu-run** ([umu-launcher](https://github.com/Open-Wine-Components/umu-launcher)) runs games with Proton outside Steam, and generates the Japanese locale inside its container (see [Japanese locale](#japanese-locale)).
- **tesseract** (with the Japanese language data) is an extra OCR engine. The built-in local OCR doesn't need it.
- **MeCab dictionaries** are used for Japanese word segmentation and furigana. LunaTranslator looks for them in `/usr/lib/mecab/dic`, `/usr/lib64/mecab/dic` (Fedora), `/usr/share/mecab/dic` and `/var/lib/mecab/dic`, or in the folder set in the MeCab settings.
- **python-dbus** reads the desktop's accent colour more reliably.
- **wtype** is a fallback for synthetic key presses on wlroots compositors (see [Synthetic key presses](#synthetic-key-presses)).
- **piper** is a neural TTS engine (see [TTS](#text-to-speech)).

## Running from source

### 1. Get the code

```sh
git clone -b linux-port https://github.com/Igrise12/LunaTranslatorLinux.git
cd LunaTranslatorLinux
```

### 2. Build the native libraries

`libNativeUtils.so` and `libCVUtils.so` (the local OCR engine) are built from `src/NativeImpl` and copied to `src/files/DLL64`.

Build dependencies:

- Arch: `base-devel cmake ninja protobuf opencv curl`
- Debian/Ubuntu: `build-essential cmake ninja-build protobuf-compiler libprotobuf-dev libopencv-dev curl`
- Fedora: `gcc-c++ cmake ninja-build protobuf-compiler protobuf-devel opencv-devel curl`

```sh
src/NativeImpl/scripts/linux/build_native.sh
```

Options:

| Option | Effect |
|---|---|
| `--clean` | Delete the build folder (`src/NativeImpl/build/linux`) and configure from scratch |
| `--ocr-model` | Also download the built-in OCR model (Japanese, Chinese, English) into `src/files/ocrmodel`. The app can also download it later from the OCR settings. |

CMake downloads further sources (MeCab, md4c, onnxruntime, ...) from GitHub while configuring. If you need a proxy, export `http_proxy`/`https_proxy` before running the script. The script also fetches the application icon (`src/files/luna.ico`).

### 3. Build the Windows-side binaries

Text hooking needs Windows binaries that run inside the game's Wine prefix:

- `LunaHook32/64.dll` are injected into the game;
- `LunaHost32/64.dll` is the hook host;
- `LunaBridge32/64.exe` is the helper that LunaTranslator talks to (see [How it works](#how-it-works)).

They are cross-compiled with the real MSVC compiler running under Wine, through [msvc-wine](https://github.com/mstorsjo/msvc-wine).

**One-time toolchain setup.** This needs `git`, `python3` and `msitools` (for `msiextract`). The package is `msitools` on Arch, Debian/Ubuntu and Fedora.

```sh
git clone https://github.com/mstorsjo/msvc-wine ~/opt/msvc-wine
cd ~/opt/msvc-wine
./vsdownload.py --accept-license --dest ~/opt/msvc --with-atl yes \
    --architecture x86 x64 --host-arch x64
./install.sh ~/opt/msvc
```

`vsdownload.py` downloads the compiler and the Windows SDK from Microsoft (several GB). Using them means accepting the Visual Studio license. `--accept-license` accepts it for you; without it, the script asks you first. To keep the toolchain somewhere else, set `MSVC_WINE_DIR` when you build.

**Build** (needs `cmake`, `ninja` and `wine`):

```sh
src/NativeImpl/scripts/linux/build_winbins.sh            # x86 and x64
src/NativeImpl/scripts/linux/build_winbins.sh x64        # one architecture only
src/NativeImpl/scripts/linux/build_winbins.sh --clean    # reconfigure from scratch
```

The binaries are copied to `src/files/LunaHook`. Build both architectures: 32-bit games need the 32-bit DLLs, even in a 64-bit prefix.

The compiler gets its own Wine prefix (`~/.cache/lunatranslator/msvc-wine-prefix`, or `MSVC_WINE_PREFIX`). Your `~/.wine` is not touched.

If Mono's C# compiler (`mcs`) and its `mscorlib.dll` (in `/usr/lib/mono/4.5`) are installed, the script also builds `LunaTmpFontLoader.dll`, which changes fonts in Unity games (see [Embedded translation](#embedded-translation)). The package is `mono` on Arch, `mono-mcs` plus `mono-devel` on Debian/Ubuntu, and `mono-devel` on Fedora. Without Mono that DLL is skipped and everything else still builds.

### 4. Start the app

```sh
cd src
python3 -B LunaTranslator/main.py
```

Settings are stored in `src/userconfig/`, logs and caches in `src/cache/`.

## Display servers

### Wayland and XWayland

On a Wayland session LunaTranslator runs as an **XWayland** (X11) client by default, even if `QT_QPA_PLATFORM` says `wayland`. Wine games are X11 clients too, and keeping an overlay above a game, following its window and sending keys to it only work between X11 windows.

To run as a native Wayland client anyway, start the app with `LUNA_NATIVE_WAYLAND=1`. You lose the overlay features that need X11 window control: keeping the translation window above the game, following the game window, and click-through. Programs that LunaTranslator starts (browsers, games) still get your original `QT_QPA_PLATFORM`.

### Window managers

- **Hyprland, Sway, i3 and other tiling WMs:** the translation window is marked as a utility window, so it floats instead of being tiled.
- **KDE Plasma (KWin):** a fullscreen game stays above "keep above" windows, so the translation window ends up hidden behind it. Play in **borderless windowed** mode (or plain windowed) instead of exclusive fullscreen.

### Hotkeys

Global hotkeys are grabbed on X11. Under XWayland they fire while an X11 window has the keyboard focus, which covers the usual case of the Wine game being focused. They don't fire while a native Wayland window is focused.

### Synthetic key presses

Some features send keys to the game, for example auto-advance or holding Ctrl to skip. On Wayland, XWayland drops the usual X11 method (XTest) on many compositors, so LunaTranslator creates a virtual input device through `/dev/uinput`. That needs write access to `/dev/uinput`, usually through the `input` group:

```sh
sudo usermod -aG input "$USER"
# if /dev/uinput is not group-writable on your distribution:
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Log out and back in afterwards. Members of the `input` group can read and generate all keyboard and mouse input, so only do this on a machine you trust. Without uinput, keys fall back to `wtype` on wlroots compositors.

### Screenshots (OCR)

- **wlroots compositors** (Hyprland, Sway, ...): `grim`. It is fast and asks no questions.
- **Other Wayland compositors** (GNOME, KDE): the xdg-desktop-portal Screenshot interface. It is slower and may ask for permission the first time.
- **X11 sessions:** Qt captures the screen directly.

### Clipboard

On Wayland the clipboard goes through `wl-copy`/`wl-paste`, and clipboard changes are watched with `wl-paste --watch`. This needs a compositor with the data-control protocol (wlroots compositors and KDE). Elsewhere, for example on GNOME, LunaTranslator falls back to Qt's clipboard, which an XWayland window only sees updated after it gets focus. On X11 the Qt clipboard is used.

"Get selected text" reads the PRIMARY selection (the text you highlighted).

## Text hooking of Windows games

### How it works

LunaHook, the hook DLL injected into the game, is a Windows program. It talks to its host through named pipes, shared memory and events that only exist inside the game's **wineserver**. LunaTranslator therefore starts a small helper, `LunaBridge64.exe` or `LunaBridge32.exe`, **inside the game's Wine prefix**, using the game's own Wine build and environment. The bridge loads LunaHost, lists the prefix's processes, injects LunaHook into the game, and forwards the text to LunaTranslator over a local TCP connection (127.0.0.1, protected by a random token).

You don't need to set any of this up. Attach to a game as you would on Windows: select its process, or let LunaTranslator attach when the game window gets focus. Games started from LunaTranslator's game manager are attached automatically as soon as their window appears. Only Wine processes can be hooked. Native Linux games can use OCR or the clipboard instead.

The bridge needs the binaries from [step 3](#_3-build-the-windows-side-binaries) in `files/LunaHook`.

### Plain Wine

Start the game with Wine (from a terminal, a desktop shortcut, or LunaTranslator's [Wine launcher](#game-launchers)), then attach. The bridge uses the game's `WINEPREFIX` and the same `wine` that runs the game. 32-bit games in 64-bit prefixes are handled automatically.

Japanese games usually need a Japanese locale. See [Japanese locale](#japanese-locale).

### Proton: Steam and umu-run

This works automatically. Proton games normally run inside the Steam Linux Runtime container (pressure-vessel). LunaTranslator reaches the game's wineserver from the host with Proton's own Wine. If the wineserver is only visible inside the container, LunaTranslator starts the bridge inside the container's namespaces with `nsenter` (no root needed).

**Fallback: launch option.** If attaching fails, for example with Flatpak Steam or other sandboxes (`cache/lunabridge.log` then says the wineserver is not reachable), let Proton start the bridge together with the game. In Steam, open the game's **Properties → Launch Options** and enter:

```
"/path/to/LunaTranslator/files/linux/lunatranslator-proton.sh" %command%
```

When running from source, the path is `<repo>/src/files/linux/lunatranslator-proton.sh`; with the AppImage it is `~/.local/share/LunaTranslator/files/linux/lunatranslator-proton.sh` (see [AppImage](#appimage)). The path stays the same when the AppImage is updated. To also set the game's locale:

```
LANG=ja_JP.UTF-8 HOST_LC_ALL=ja_JP.UTF-8 "/path/to/LunaTranslator/files/linux/lunatranslator-proton.sh" %command%
```

The wrapper makes Proton run `LunaBridge64.exe` next to the game. The bridge then waits for LunaTranslator for as long as the game runs (set `LUNA_BRIDGE_RETRY` to a number of seconds to limit that), so you can start LunaTranslator before or after the game. The installation path must not contain a `:`.

For umu-run, Heroic, or Lutris games that run with Proton, put the script in front of the command, for example `.../lunatranslator-proton.sh umu-run game.exe`. In Lutris you can use the game's *Command prefix* system option.

### Lutris and Bottles

Lutris and Bottles run games with their own Wine builds and prefixes. LunaTranslator reads the Wine build and the prefix from the running game process, so hooking works as with plain Wine: start the game from Lutris or Bottles, then attach. Lutris games can also be started from LunaTranslator (see the [Lutris launcher](#game-launchers)), and both Lutris and Bottles prefixes can be scanned by **Batch Add** in the game manager.

Flatpak installs of Lutris or Bottles run the game in a sandbox. LunaTranslator then tries to start the bridge inside the sandbox. This path is less tested than the native packages. If it fails, check the log.

### Troubleshooting

- **`cache/lunabridge.log`** (`src/cache/lunabridge.log` when running from source, `~/.local/share/LunaTranslator/cache/lunabridge.log` with the AppImage) holds the output of every bridge LunaTranslator started, and the reason when it couldn't start one. Messages such as *the game's wineserver is not reachable from the host* mean you should use the [launch option](#proton-steam-and-umu-run).
- **`LUNA_BRIDGE_DEBUG=1`**: start LunaTranslator with this variable to make the bridge log more detail (it is passed on to the bridge).
- **`LUNA_BRIDGE_MODE=host`** or **`LUNA_BRIDGE_MODE=nsenter`** forces how the bridge is started for a game in a container, instead of choosing automatically.
- *missing …/LunaBridge64.exe*: the Windows-side binaries are not built. See [step 3](#_3-build-the-windows-side-binaries).
- *pid … is not a Wine process*: native Linux programs cannot be hooked.
- Japanese text in file names or the game shows up garbled: the game is not running in a Japanese locale (see [Japanese locale](#japanese-locale)).

::: warning Anti-cheat
Hooking injects a DLL into the game process. Do not attach LunaTranslator to online games that use anti-cheat (Easy Anti-Cheat, BattlEye, ...): the injection can be blocked, crash the game, or get your account flagged. This is the same as on Windows.
:::

## Game launchers

On Windows, games are started through Locale Emulator, Locale Remulator or ntleas. On Linux, those are replaced by the launchers below. Choose one per game in the game's settings under **Startup Method**. The settings card below the selection changes with the launcher.

| Launcher | What it does |
|---|---|
| **Auto** | Picks one: a native Linux program is started directly, a game in a Steam library uses Steam, a game in Lutris' library uses Lutris. Otherwise it uses Wine, or umu-run when there is no system Wine or when the Japanese locale is missing for system Wine (and the game is not inside an existing prefix). The detected launcher is shown in the settings. |
| **Wine** | `wine game.exe` with the chosen Wine build, prefix and locale. |
| **umu-run (Proton)** | `umu-run game.exe` with `PROTONPATH`, `GAMEID`, prefix and locale. |
| **Steam** | `steam -applaunch <AppID>` (also Flatpak Steam, or a `steam://` link). The AppID is read from the Steam library. Proton and the locale are set in Steam (the settings card shows the matching launch options). |
| **Lutris** | `lutris lutris:rungameid/<id>` (also Flatpak Lutris). The id is read from Lutris' database. The Wine version, prefix and locale are set in Lutris. |
| **Command Line Startup** | Your own command template (see below). |
| **Launch Directly** | Native programs are executed, Windows programs go through Wine, anything else is opened with `xdg-open`. |

Settings shared by the Wine, umu-run and custom launchers:

- **Wine**: the default system `wine`, a `wine` binary, or a Wine/Proton directory (its `files/bin/wine` is used). Installed Proton builds are listed.
- **WINEPREFIX**: empty means the prefix that contains the game. For a game outside any prefix, Wine uses `$WINEPREFIX` or `~/.wine`, and umu-run uses its own default `~/Games/umu/<GAMEID>`.
- **PROTONPATH** (umu-run): *Auto* is the newest Proton found in your Steam folders and `compatibilitytools.d`. *GE-Proton* lets umu download the latest GE-Proton.
- **GAMEID** (umu-run): default `umu-default`.
- **LANG**: the game's `LANG`/`LC_ALL`, default `ja_JP.UTF-8`. Leave it empty to keep your environment's locale.
- **Parameter**: extra arguments for the game, in shell syntax.
- **env**: extra environment variables, for example `WINEDLLOVERRIDES=… DXVK_HUD=1`.

The output of a launch goes to `cache/linux_launch/`. If the launcher exits with an error right away, the last lines are shown in LunaTranslator.

### Custom command templates

The template is split like a shell command first and then filled in word by word, so paths with spaces stay one argument. The default is `{wine} {exe} {args}`. Words of the form `NAME=value` at the start become environment variables. Placeholders:

| Placeholder | Value |
|---|---|
| `{exe}` (also `{exepath}`) | Unix path of the game executable |
| `{dir}` | Folder of the executable |
| `{prefix}` | The resolved `WINEPREFIX` (also exported as `WINEPREFIX`) |
| `{exe_dos}` | The executable as a Windows path inside the prefix (`C:\…` or `Z:\…`) |
| `{wine}` | The chosen Wine loader (or `wine`) |
| `{protonpath}` | The chosen Proton directory |
| `{locale}` | The locale, for example `ja_JP.UTF-8` (empty when it isn't installed) |
| `{gameid}` | umu `GAMEID` |
| `{appid}` | Steam AppID of the game (empty if not found) |
| `{args}` | The extra arguments. Must be a word of its own; it expands to several words. |

Unknown placeholders are kept literally. Example: `PROTONPATH={protonpath} GAMEID={gameid} umu-run {exe} {args}`.

### Japanese locale

Japanese games usually need a Japanese locale (code page 932). Otherwise text and non-ASCII paths come out garbled.

- **System Wine** only gets `LANG`/`LC_ALL` when glibc actually has that locale. An unknown locale would silently fall back to `C`. If `ja_JP.UTF-8` is missing, LunaTranslator warns at launch and the LANG field turns red. To install the locale, uncomment `ja_JP.UTF-8 UTF-8` in `/etc/locale.gen` and run `sudo locale-gen`. On Debian/Ubuntu, `sudo dpkg-reconfigure locales` does both. On Fedora, install `glibc-langpack-ja`. Alternatively use **umu-run**, whose container generates the locale itself.
- **Proton** (umu-run, Steam): Steam sets `LC_ALL=C`, and Proton only keeps the game's `LC_ALL` when `HOST_LC_ALL` carries the real value. The umu-run launcher sets `HOST_LC_ALL` for you. For Steam, add `LANG=ja_JP.UTF-8 HOST_LC_ALL=ja_JP.UTF-8 %command%` to the launch options.

## Embedded translation

[Embedded translation](/en/embedtranslate) works for Wine and Proton games through the same bridge, with the same settings as on Windows. In the font list, choose a font installed on your Linux system that has the characters you need. Wine normally finds the system fonts through fontconfig.

For Unity games with the Mono backend, changing the game font needs `LunaTmpFontLoader.dll`. It is only built when Mono's `mcs` is installed (see [step 3](#_3-build-the-windows-side-binaries)).

## Text-to-speech

Two offline engines are available on Linux:

- **eSpeak NG**: the default. It needs `espeak-ng`, picks a voice automatically from the script of the text, and lets you choose a voice in its settings.
- **Piper**: neural voices, with nothing downloaded automatically. Install `piper` (or `piper-tts`), download voice models (`<voice>.onnx` together with `<voice>.onnx.json`), and set the folder containing them as the model folder in Piper's settings.

All online TTS engines work as on Windows. Audio is played through ffmpeg and pacat (or pw-cat).

Windows-only engines are hidden from the settings on Linux:

| Kind | Hidden engines |
|---|---|
| TTS | WindowsTTS (SAPI and Microsoft voices), Tolk, VOICEROID2 |
| OCR | Windows OCR, SnippingTool OCR, WeChat OCR |
| Speech recognition | Windows Live Captions / Microsoft speech recognition |
| Translation | Offline DLL translators: ATLAS, LEC, ezTrans, Dreye, JBeijing, Kingsoft; Chrome built-in AI |

## Known limitations

- The engines in the table above are not available. Offline DLL translators may be supported later through the Wine bridge.
- Locale Emulator, Locale Remulator and ntleas don't exist on Linux. Use the [launchers](#game-launchers) and the [Japanese locale](#japanese-locale) instead.
- The Magpie window scaling integration is Windows-only.
- Speech recognition has no Linux engine yet.
- Under native Wayland (`LUNA_NATIVE_WAYLAND=1`), the overlay can't keep itself above or follow the game.
- On KWin, exclusive fullscreen games cover the translation window. Use borderless windowed mode.
- Global hotkeys under XWayland only work while an X11 window (normally the game) has focus.
- Synthetic key presses on Wayland need `/dev/uinput` access or `wtype`.
- Games running with Wine's Wayland driver (`winewayland.drv`) have no X11 window to follow. Keep Wine's default X11 driver.
- Sandboxed launchers (Flatpak Steam, Lutris or Bottles) may need the [launch option wrapper](#proton-steam-and-umu-run), and are less tested.
