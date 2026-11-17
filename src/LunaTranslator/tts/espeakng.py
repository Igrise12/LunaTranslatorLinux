# Linux offline TTS (the config entry is added by lunaplat/linux/ttsengines.py)
import os, re, shutil, subprocess
from tts.basettsclass import TTSbase, SpeechParam


class TTS(TTSbase):
    AUTO = ""

    def exe(self):
        p = self.config.get("path", "")
        if p and os.path.isfile(p):
            return p
        for name in ("espeak-ng", "espeak"):
            found = shutil.which(name)
            if found:
                return found
        raise Exception("espeak-ng not found")

    def getvoicelist(self):
        vals, names = [self.AUTO], ["Auto"]
        try:
            out = subprocess.run(
                [self.exe(), "--voices"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).stdout.decode("utf8", "replace")
        except Exception:
            return vals, names
        for line in out.splitlines()[1:]:
            # Pty Language Age/Gender VoiceName File Other Languages
            parts = line.split()
            if len(parts) < 5:
                continue
            lang, voicename = parts[1], parts[3]
            vals.append(lang)
            names.append("{} ({})".format(voicename.replace("_", " "), lang))
        return vals, names

    def autovoice(self, text):
        # pick the voice from the script of the text (the reader may read the
        # original or the translation), then from the language settings
        src = "" if self.is_src_auto else self.srclang_1.code
        tgt = self.tgtlang_1.code
        if re.search("[\u3040-\u30ff]", text):
            want = ["ja"]
        elif re.search("[\uac00-\ud7af]", text):
            want = ["ko"]
        elif re.search("[\u4e00-\u9fff]", text):
            want = [_ for _ in (src, tgt) if _ in ("ja", "zh", "cht")] + ["zh"]
        else:
            # a translation or a Latin/Cyrillic/... original
            want = [_ for _ in (tgt, src) if _ not in ("ja", "zh", "cht", "ko")]
        langs = set(self.voicelist)
        for c in want + ["en-us", "en"]:
            c = {"zh": "cmn", "cht": "cmn"}.get(c, c)
            if c and c in langs:
                return c
        return "en"

    def speak(self, content, voice, param: SpeechParam):
        if voice == self.AUTO:
            voice = self.autovoice(content)
        speed = int(175 * 2 ** (param.speed / 10))  # 87..350 words/min
        pitch = int(50 + param.pitch * 5)
        cmd = [
            self.exe(),
            "--stdout",
            "-b",
            "1",  # stdin is UTF-8
            "-v",
            voice,
            "-s",
            str(max(80, min(450, speed))),
            "-p",
            str(max(0, min(99, pitch))),
        ]
        r = subprocess.run(
            cmd,
            input=content.replace("\n", " ").encode("utf8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if r.returncode != 0 or not r.stdout:
            raise Exception(r.stderr.decode("utf8", "replace") or "espeak-ng failed")
        return _fix_wav_sizes(r.stdout)


def _fix_wav_sizes(data):
    """espeak-ng --stdout writes a streaming WAV header with ~2 GiB sizes;
    put the real sizes in so saved audio has the right length."""
    import struct

    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    data = bytearray(data)
    data[4:8] = struct.pack("<I", len(data) - 8)
    # find the "data" chunk (usually at offset 36)
    pos = 12
    while pos + 8 <= len(data):
        cid = bytes(data[pos : pos + 4])
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if cid == b"data":
            data[pos + 4 : pos + 8] = struct.pack("<I", len(data) - pos - 8)
            break
        pos += 8 + size + (size & 1)
    return bytes(data)
