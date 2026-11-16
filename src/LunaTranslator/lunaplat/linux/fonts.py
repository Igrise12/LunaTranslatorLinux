"""Stand-in for the "Segoe Fluent Icons" font on Linux.

The Fluent UI code draws its icons as glyphs of Segoe Fluent Icons (on
Windows the style plugin registers that font). It cannot be redistributed,
so unless the font is installed (e.g. from a Windows install), we build a
compatibility font from the bundled FontAwesome 4.7 (SIL OFL 1.1): the
Segoe codepoints the UI uses are mapped to the closest FontAwesome glyphs and
the family is named "Segoe Fluent Icons", so upstream drawing code works
unchanged. The generated file is cached; generating needs fontTools.
"""

import json
import os

FAMILY = "Segoe Fluent Icons"
FA_TTF = "files/static/fonts/fontawesome4.7-webfont.ttf"
FA_MAP = "files/static/fonts/fontawesome4.7-webfont-charmap.json"
_VERSION = "2"

# Segoe Fluent Icons codepoint -> FontAwesome 4.7 icon name
SEGOE_TO_FA = {
    0xE700: "bars",  # GlobalNavigationButton
    0xE706: "sun-o",  # Brightness
    0xE70D: "chevron-down",
    0xE70E: "chevron-up",
    0xE713: "cog",  # Setting
    0xE718: "thumb-tack",  # Pin
    0xE721: "search",
    0xE765: "keyboard-o",
    0xE76B: "chevron-left",
    0xE76C: "chevron-right",
    0xE774: "globe",
    0xE77A: "thumb-tack",  # Unpin
    0xE790: "paint-brush",  # Color
    0xE7C4: "th-large",  # TaskView
    0xE7F3: "volume-up",
    0xE823: "clock-o",  # Recent
    0xE82D: "book",  # Dictionary
    0xE895: "refresh",  # Sync
    0xE8B9: "picture-o",
    0xE8BB: "times",  # ChromeClose
    0xE8C1: "font",  # Characters
    0xE8CB: "sort",
    0xE8D6: "music",  # Audio
    0xE8F1: "folder-open",  # Library
    0xE921: "window-minimize",  # ChromeMinimize
    0xE922: "window-maximize",  # ChromeMaximize
    0xE923: "window-restore",  # ChromeRestore
    0xE946: "info-circle",
    0xE96D: "chevron-up",  # ChevronUpSmall
    0xE96E: "chevron-down",  # ChevronDownSmall
    0xE96F: "chevron-left",  # ChevronLeftSmall
    0xE970: "chevron-right",  # ChevronRightSmall
    0xE971: "chevron-up",  # ChevronUpMed
    0xE972: "chevron-down",  # ChevronDownMed
    0xE973: "chevron-left",  # ChevronLeftMed
    0xE974: "chevron-right",  # ChevronRightMed
    0xE9D2: "area-chart",
    0xE9F5: "cogs",
    0xEA37: "language",
    0xF8B0: "square",
    0xE73E: "check",  # CheckMark
    0xE71B: "link",
}


def _cache_path():
    import gobject

    return gobject.getcachedir("fonts/segoe-fluent-compat-{}.ttf".format(_VERSION))


def _generate(path):
    from fontTools.ttLib import TTFont

    with open(FA_MAP, "r", encoding="utf8") as f:
        names = json.load(f)
    font = TTFont(FA_TTF)
    best = font.getBestCmap()
    extra = {}
    for seg, fa in SEGOE_TO_FA.items():
        cp = int(names[fa], 16) if fa in names else None
        glyph = best.get(cp) if cp is not None else None
        if glyph:
            extra[seg] = glyph
    for table in font["cmap"].tables:
        if table.isUnicode():
            table.cmap.update(extra)
    for rec in font["name"].names:
        if rec.nameID in (1, 4, 16):
            rec.string = FAMILY
        elif rec.nameID == 6:
            rec.string = "SegoeFluentIconsCompat"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    font.save(tmp)
    os.replace(tmp, path)


def ensure_fluent_icon_font():
    """Register the compatibility font unless the real one is installed."""
    from qtsymbols import QFontDatabase

    if FAMILY in QFontDatabase.families():
        return True
    path = _cache_path()
    if not os.path.exists(path):
        try:
            _generate(path)
        except Exception as e:
            import sys

            sys.stderr.write("[lunaplat] cannot build Fluent icon font: {}\n".format(e))
            return False
    return QFontDatabase.addApplicationFont(path) != -1
