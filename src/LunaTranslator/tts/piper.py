# Piper neural TTS, offline (the config entry is added by
# lunaplat/linux/ttsengines.py). Nothing is downloaded: the user installs
# piper and puts voice models (<voice>.onnx + <voice>.onnx.json) in a folder.
import os, json, shutil, subprocess, tempfile
from tts.basettsclass import TTSbase, SpeechParam


class TTS(TTSbase):
    arg_support_pitch = False

    def exe(self):
        p = self.config.get("path", "")
        if p and os.path.isfile(p):
            return p
        for name in ("piper", "piper-tts"):
            found = shutil.which(name)
            if found:
                return found
        raise Exception("piper not found, set its path in the settings")

    def models(self):
        d = self.config.get("modeldir", "")
        if not d or not os.path.isdir(d):
            return []
        found = []
        for root, _, files in os.walk(d):
            for f in files:
                if f.endswith(".onnx") and os.path.isfile(
                    os.path.join(root, f + ".json")
                ):
                    found.append(os.path.join(root, f))
        return sorted(found)

    def getvoicelist(self):
        vals, names = [], []
        for model in self.models():
            base = os.path.basename(model)[: -len(".onnx")]
            speakers = {}
            try:
                with open(model + ".json", "r", encoding="utf8") as ff:
                    speakers = json.load(ff).get("speaker_id_map") or {}
            except Exception:
                pass
            if len(speakers) > 1:
                for spk, sid in sorted(speakers.items(), key=lambda _: _[1]):
                    vals.append("{}#{}".format(model, sid))
                    names.append("{} ({})".format(base, spk))
            else:
                vals.append(model)
                names.append(base)
        if not vals:
            return None  # TTSbase shows a placeholder voice
        return vals, names

    def speak(self, content, voice, param: SpeechParam):
        if not voice:
            raise Exception("no Piper voice model, set the model folder in the settings")
        model, _, sid = voice.partition("#")
        exe = self.exe()  # may raise: resolve before creating the temp file
        fd, out = tempfile.mkstemp(prefix="luna_piper_", suffix=".wav")
        os.close(fd)
        cmd = [exe, "--model", model, "--output_file", out]
        if sid:
            cmd += ["--speaker", sid]
        if param.speed:
            # length_scale > 1 is slower; 0.5..2
            cmd += ["--length_scale", "{:.3f}".format(2 ** (-param.speed / 10))]
        try:
            r = subprocess.run(
                cmd,
                input=content.replace("\n", " ").encode("utf8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            with open(out, "rb") as ff:
                data = ff.read()
            if r.returncode != 0 or not data:
                raise Exception(
                    r.stderr.decode("utf8", "replace")[-500:] or "piper failed"
                )
            return data
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
