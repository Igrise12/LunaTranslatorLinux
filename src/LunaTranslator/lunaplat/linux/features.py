"""Which plugins work on Linux.

Keys are the plugin keys used in defaultconfig/config.json. Anything not
listed here is assumed to work (all HTTP-based engines do).
"""

# needs a Windows component with no Linux equivalent yet
UNSUPPORTED = {
    # offline DLL translators: 32-bit LunaSubprocess helpers (phase 7, via
    # the Wine bridge)
    "fanyi": {"atlas", "lec", "eztrans", "dreye", "jb7", "kingsoft", "chromeai"},
    # WinRT OCR, Snipping Tool's oneocr.dll, WeChat/QQ OCR
    "ocr": {"windowsocr", "SnippingTool", "weixinocr"},
    # SAPI / NeoSpeech / MS natural voices, Tolk screen readers, VOICEROID2
    "reader": {"windowstts", "tolk", "voiceroid2"},
    # Windows Live Captions / MS speech recognition
    "sourcestatus2": {"mssr"},
}


_ANY = set().union(*UNSUPPORTED.values())


def supported(kind, key):
    """kind=None checks the key against every plugin kind (used where only
    the config dict is known, e.g. splitocrtypes for OCR and TTS)."""
    if kind is None:
        return key not in _ANY
    return key not in UNSUPPORTED.get(kind, ())
