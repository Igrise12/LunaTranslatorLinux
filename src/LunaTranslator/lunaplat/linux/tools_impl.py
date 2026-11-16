"""Locate external programs on Linux (registry / Program Files on Windows).

Patches applied after the plugin modules load:
* ocrengines.tesseract5: tesseract from PATH
* translator.cdp_helper: Chromium-family browsers from PATH; command as argv
"""

import hashlib
import os
import shutil

BROWSERS = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge-stable",
    "microsoft-edge",
    "brave-browser",
    "brave",
    "vivaldi-stable",
)


def patch_tesseract(mod):
    def findts(self):
        return shutil.which("tesseract")

    mod.OCR.findts = findts


def patch_cdp(mod):
    cls = mod.Commonloadchromium

    def getpath(self, config):
        p = config.get("chromepath") or ""
        if p and os.path.isfile(p):
            return p
        for name in BROWSERS:
            found = shutil.which(name)
            if found:
                return found
        return None

    def gencmd(self, path, port):
        hash_ = hashlib.md5(path.encode("utf8")).hexdigest()
        cache = os.path.abspath(os.path.join("chrome_cache", hash_))
        return [
            path,
            "--no-first-run",
            "--remote-debugging-port=%d" % port,
            "--user-data-dir=%s" % cache,
        ]

    cls.getpath = getpath
    cls.gencmd = gencmd
