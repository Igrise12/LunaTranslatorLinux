"""Audio on Linux: playback, transcoding and system-audio recording.

Upstream plays, transcodes and records audio through BASS and WASAPI
(NativeUtils.bass_* / loopbackrecorder), which do not exist here.

Playback: ffmpeg decodes any format it knows (mp3, wav, ogg/opus, aac, spx,
m4a, ...) to s16le PCM and pacat (PulseAudio / pipewire-pulse) plays it, with
pw-cat as a fallback player. A stream is drained before pacat exits, so "the
player process is alive" is exactly "audio is playing". Streamed TTS
(generators of chunks) is piped into ffmpeg as it arrives, so playback starts
with the first chunks. Killing the processes stops playback at once.

Recording: parec (or pw-record) on the default sink's monitor, kept as raw
PCM in memory and returned as WAV bytes, like WASAPI loopback capture.

Patched in through the lunaplat registry:
  myutils.audioplayer -> patch_audioplayer (playonce)
  NativeUtils         -> patch_nativeutils (bass_code_cast, loopbackrecorder)
  myutils.utils       -> patch_utils       (ffmpeg_record: gdigrab -> x11grab)
"""

import ctypes
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types

RATE = 48000
CHANNELS = 2
SAMPLE_BYTES = 2  # s16le

_PR_SET_PDEATHSIG = 1
try:
    _prctl = ctypes.CDLL(None, use_errno=True).prctl
except (OSError, AttributeError):
    _prctl = None


def _pdeathsig():
    # helpers must not outlive the app
    if _prctl is not None:
        _prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)


def _log(*a):
    sys.stderr.write("[lunaplat.audio] " + " ".join(str(_) for _ in a) + "\n")


_which_cache = {}


def which(name):
    if name not in _which_cache:
        _which_cache[name] = shutil.which(name)
    return _which_cache[name]


class _Spawner(object):
    """PR_SET_PDEATHSIG fires when the *thread* that forked the child exits,
    and playback/recording start from short-lived worker threads. So every
    helper is forked by one long-lived daemon thread (or the main thread):
    the helpers then die with the app, and only then."""

    def __init__(self):
        import queue

        self.q = queue.Queue()
        self.thread = None
        self.lock = threading.Lock()

    def _loop(self):
        while True:
            fn, box, ev = self.q.get()
            try:
                box.append((True, fn()))
            except BaseException as e:
                box.append((False, e))
            ev.set()

    def run(self, fn):
        if threading.current_thread() is threading.main_thread():
            return fn()
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(
                    target=self._loop, name="luna-audio-spawner", daemon=True
                )
                self.thread.start()
        box, ev = [], threading.Event()
        self.q.put((fn, box, ev))
        ev.wait()
        ok, val = box[0]
        if not ok:
            raise val
        return val


_spawner = _Spawner()


def _popen(cmd, **kw):
    def fn():
        return subprocess.Popen(
            cmd,
            stderr=subprocess.DEVNULL,
            preexec_fn=_pdeathsig,
            close_fds=True,
            **kw
        )

    return _spawner.run(fn)


def _kill(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(2)
    except Exception:
        pass


def _sink_cmd():
    """Command that plays s16le/48k/stereo PCM from stdin, or None."""
    sink = os.environ.get("LUNA_AUDIO_SINK")  # tests / power users
    if which("pacat"):
        cmd = [
            which("pacat"),
            "--playback",
            "--raw",
            "--format=s16le",
            "--rate={}".format(RATE),
            "--channels={}".format(CHANNELS),
            "--latency-msec=100",
            "--client-name=LunaTranslator",
            "--stream-name=LunaTranslator TTS",
        ]
        if sink:
            cmd.append("--device=" + sink)
        return cmd
    if which("pw-cat"):
        cmd = [
            which("pw-cat"),
            "--playback",
            "--raw",
            "--format=s16",
            "--rate={}".format(RATE),
            "--channels={}".format(CHANNELS),
            "--latency=100ms",
        ]
        if sink:
            cmd += ["--target", sink]
        return cmd + ["-"]
    return None


def _is_mono(path):
    probe = which("ffprobe")
    if not probe:
        return False
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=channels", "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
        )
        return r.stdout.strip() == b"1"
    except (OSError, subprocess.SubprocessError):
        return False


class _Playback(object):
    """One clip: ffmpeg (decoder) | pacat (player)."""

    def __init__(self, volume):
        self.volume = max(0.0, float(volume)) / 100.0
        self.started = False
        self.stopped = False
        self.decoder = None
        self.player = None
        self.tmpfile = None
        self.lock = threading.Lock()

    def _spawn(self, source):
        """source: a file path, or None to read the input from stdin."""
        ffmpeg = which("ffmpeg")
        sinkcmd = _sink_cmd()
        if not sinkcmd:
            raise Exception("no audio player found (install pulseaudio-utils/pipewire)")
        if not ffmpeg:
            return self._spawn_noffmpeg(source)
        dec = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
        if source is None:
            dec += ["-i", "pipe:0"]
        else:
            dec += ["-i", source]
        dec += ["-vn", "-sn"]
        filters = []
        if source is not None and _is_mono(source):
            # ffmpeg's own mono->stereo upmix is -3 dB; duplicate instead
            filters.append("pan=stereo|c0=c0|c1=c0")
        if abs(self.volume - 1.0) > 1e-3:
            filters.append("volume={:.4f}".format(self.volume))
        if filters:
            dec += ["-af", ",".join(filters)]
        dec += [
            "-flush_packets",
            "1",
            "-f",
            "s16le",
            "-ar",
            str(RATE),
            "-ac",
            str(CHANNELS),
            "pipe:1",
        ]
        with self.lock:
            if self.stopped:
                return False
            self.decoder = _popen(
                dec,
                stdin=subprocess.PIPE if source is None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
            )
            try:
                self.player = _popen(
                    sinkcmd, stdin=self.decoder.stdout, stdout=subprocess.DEVNULL
                )
            except Exception:
                _kill(self.decoder)
                raise
            finally:
                self.decoder.stdout.close()  # the player owns the read end now
        return True

    def _spawn_noffmpeg(self, source):
        # without ffmpeg only what libsndfile reads (wav/ogg/flac/...) plays
        if source is None:
            raise Exception("streamed audio needs ffmpeg")
        if which("paplay"):
            cmd = [which("paplay"), "--volume={}".format(int(65536 * self.volume))]
            if os.environ.get("LUNA_AUDIO_SINK"):
                cmd.append("--device=" + os.environ["LUNA_AUDIO_SINK"])
            cmd.append(source)
        elif which("pw-play"):
            cmd = [which("pw-play"), "--volume={:.3f}".format(self.volume), source]
        else:
            raise Exception("ffmpeg not found")
        with self.lock:
            if self.stopped:
                return False
            self.player = _popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL
            )
        return True

    def _cleanup_later(self):
        if not self.tmpfile:
            return
        player, path = self.player, self.tmpfile

        def _wait():
            try:
                if player is not None:
                    player.wait()
            except Exception:
                pass
            try:
                os.remove(path)
            except OSError:
                pass

        threading.Thread(target=_wait, daemon=True).start()

    def play_file(self, path):
        if self._spawn(path):
            self.started = True

    def play_bytes(self, data):
        # a seekable file decodes every container (mp4/m4a keep their index
        # at the end, which a pipe cannot reach)
        # in the app's per-process temp folder, which is cleared even when
        # the app ends with os._exit before the cleanup thread runs
        try:
            import gobject

            tmpdir = gobject.gettempdir()
        except Exception:
            tmpdir = None
        fd, path = tempfile.mkstemp(prefix="luna_audio_", suffix=".bin", dir=tmpdir)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self.tmpfile = path
        try:
            ok = self._spawn(path)
        finally:
            self._cleanup_later()
        if ok:
            self.started = True
        elif self.player is None:
            try:
                os.remove(path)
            except OSError:
                pass

    def play_stream(self, gen):
        stdin = None
        try:
            for chunk in gen:
                if self.stopped:
                    break
                if not chunk:
                    continue
                if stdin is None:
                    if not which("ffmpeg"):
                        # no streaming decoder: collect, then play as bytes
                        self.play_bytes(chunk + b"".join(_ for _ in gen if _))
                        return
                    if not self._spawn(None):
                        break
                    stdin = self.decoder.stdin
                    stdin.write(chunk)
                    stdin.flush()
                    self.started = True
                    continue
                stdin.write(chunk)
                stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass  # stopped: the decoder is gone
        finally:
            if stdin is not None:
                try:
                    stdin.close()
                except Exception:
                    pass

    def isplaying(self):
        p = self.player
        playing = p is not None and p.poll() is None
        if not playing and self.decoder is not None:
            # reap the decoder too, so it does not linger as a zombie
            self.decoder.poll()
        return playing

    def stop(self):
        with self.lock:
            self.stopped = True
            dec, ply = self.decoder, self.player
        _kill(ply)
        _kill(dec)


def _run(pb, data):
    try:
        if isinstance(data, bytes):
            pb.play_bytes(data)
        elif isinstance(data, str):
            pb.play_file(data)
        elif isinstance(data, types.GeneratorType):
            pb.play_stream(data)
    except Exception as e:
        _log("playback failed:", e)


class playonce(object):
    """Drop-in for myutils.audioplayer.playonce.

    The worker thread only holds the _Playback, never this object, so the
    series player dropping its reference (a new line was read, or stop())
    runs __del__ and silences the clip right away."""

    def __init__(self, fileormem, volume):
        self._pb = _Playback(volume)
        threading.Thread(target=_run, args=(self._pb, fileormem), daemon=True).start()

    @property
    def idle(self):
        return not self._pb.started

    @property
    def isplaying(self):
        return self._pb.isplaying()

    def stop(self):
        self._pb.stop()

    def __del__(self):
        try:
            self._pb.stop()
        except Exception:
            pass


def patch_audioplayer(mod):
    mod.playonce = playonce


# ---------------------------------------------------------------- transcoding


def code_cast(bs, to="mp3", mp3kbps=0, opusbitrate=0):
    """NativeUtils.bass_code_cast: encode bs (any format) to mp3 or opus/ogg.
    Returns the new bytes, or None (the caller keeps the original)."""
    if to not in ("mp3", "opus") or not bs:
        return None
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        return None
    if to == "mp3":
        enc = ["-c:a", "libmp3lame", "-b:a", "{}k".format(int(mp3kbps or 64)), "-f", "mp3"]
    else:
        kbps = max(6, int(opusbitrate or 10))
        enc = ["-c:a", "libopus", "-b:a", "{}k".format(kbps), "-f", "ogg"]
    fd, path = tempfile.mkstemp(prefix="luna_cast_", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bs)
        r = subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path, "-vn"]
            + enc
            + ["pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if r.returncode != 0 or not r.stdout:
            _log("transcode failed:", r.stderr.decode("utf8", "replace")[-300:])
            return None
        return r.stdout
    except Exception as e:
        _log("transcode failed:", e)
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- recording


def wav_header(datasize, rate=RATE, channels=CHANNELS, bits=16):
    block = channels * bits // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + datasize,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        rate * block,
        block,
        bits,
        b"data",
        datasize,
    )


def _record_cmd():
    """Capture the default output's monitor as raw s16le/48k/stereo."""
    src = os.environ.get("LUNA_LOOPBACK_SOURCE")  # tests / power users
    fmt = ["--format=s16le", "--rate={}".format(RATE), "--channels={}".format(CHANNELS)]
    if which("parec"):
        return [
            which("parec"),
            "--raw",
            "--device=" + (src or "@DEFAULT_MONITOR@"),
            "--latency-msec=50",
            "--client-name=LunaTranslator",
            "--stream-name=LunaTranslator loopback",
        ] + fmt
    if which("pw-record"):
        cmd = [
            which("pw-record"),
            "--raw",
            "--format=s16",
            "--rate={}".format(RATE),
            "--channels={}".format(CHANNELS),
            "-P",
            "stream.capture.sink=true",
        ]
        if src:
            cmd += ["--target", src]
        return cmd + ["-"]
    return None


class loopbackrecorder(object):
    """NativeUtils.loopbackrecorder: records what the system plays.

    __init__ starts (raises when recording is impossible, which callers show
    as "loopback recording is not supported"), stop() ends it and leaves the
    WAV bytes in .data. Unlike the Windows process-loopback capture, the
    monitor also contains LunaTranslator's own TTS."""

    def __init__(self):
        self.data = None
        self._chunks = []
        cmd = _record_cmd()
        if not cmd:
            raise Exception("parec/pw-record not found")
        self._proc = _popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        time.sleep(0.05)
        if self._proc.poll() is not None:
            self._reader.join(1)
            raise Exception("loopback recording failed to start")

    def _read(self):
        out = self._proc.stdout
        read = getattr(out, "read1", out.read)
        while True:
            try:
                d = read(65536)
            except (OSError, ValueError):
                break
            if not d:
                break
            self._chunks.append(d)

    def stop(self):
        proc = getattr(self, "_proc", None)
        if proc is None:
            return
        self._proc = None
        try:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)  # parec flushes on SIGINT
            proc.wait(3)
        except Exception:
            _kill(proc)
        self._reader.join(3)
        pcm = b"".join(self._chunks)
        self._chunks = []
        block = CHANNELS * SAMPLE_BYTES
        pcm = pcm[: len(pcm) - len(pcm) % block]
        self.data = wav_header(len(pcm)) + pcm

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass


def patch_nativeutils(mod):
    mod.bass_code_cast = code_cast
    mod.loopbackrecorder = loopbackrecorder


# ---------------------------------------------------------- screen recording


def patch_utils(mod):
    """ffmpeg_record used gdigrab (Windows GDI); use x11grab. Games under
    Wine/Proton are X11 (XWayland) windows, so this covers them."""

    def ffmpeg_record(sema, rect=None, split=False):
        import gobject

        ffmpeg = which("ffmpeg")
        if not ffmpeg:
            raise Exception("can't find ffmpeg")
        display = os.environ.get("DISPLAY")
        if not display:
            raise Exception("screen recording needs an X11 display (DISPLAY)")
        file = gobject.gettempdir(str(time.time()) + (".avif" if split else ".mp4"))
        if rect:
            arg = [
                "-video_size",
                "{}x{}".format(rect.width(), rect.height()),
                "-i",
                "{}+{},{}".format(display, rect.x(), rect.y()),
            ]
        else:
            hwnd = gobject.base.hwnd
            if not hwnd:
                raise Exception("window is none")
            arg = ["-window_id", str(int(hwnd)), "-i", display]
        # encoders need even sizes; yuv420p plays everywhere
        vf = ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"]
        codec = ["-c:v", "libsvtav1"] if split else []
        proc = _popen(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "x11grab"]
            + ["-framerate", "30", "-draw_mouse", "0"]
            + arg
            + vf
            + codec
            + [file],
            stdin=subprocess.PIPE,
        )
        recorders = mod.loopbackrecorder()
        sema.acquire()
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
        except OSError:
            pass
        proc.wait()
        mp3 = recorders.stop_save()
        if proc.returncode != 0 or not os.path.exists(file):
            raise Exception("ffmpeg x11grab failed")
        if split:
            return file, mp3
        tmsp = gobject.gettempdir(
            mod.get_time_stamp(forfilename=True).replace(" ", "_") + ".mp4"
        )
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", file]
            + (["-i", mp3, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"] if mp3 else [])
            + ["-c:v", "copy", tmsp],
            stdin=subprocess.DEVNULL,
        )
        return tmsp

    mod.ffmpeg_record = ffmpeg_record
