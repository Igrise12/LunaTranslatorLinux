"""Icons for executables on Linux.

* the app itself: files/luna.ico (fetched by the Linux build script)
* Windows game executables (.exe under a Wine prefix): the first icon
  group from the PE resources, read with pefile when available
* anything else: the icon theme, then a generated placeholder

Always returns a non-null pixmap; several widgets divide by its size.
"""

import os
import struct

APP_ICON = "files/luna.ico"

_RT_ICON = 3
_RT_GROUP_ICON = 14


def _pe_icon_bytes(path, large):
    """Return the bytes of a .ico assembled from the PE's first icon group."""
    try:
        import pefile
    except ImportError:
        return None
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
    except Exception:
        return None
    root = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if root is None:
        return None

    icons = {}
    group = None
    for typ in root.entries:
        if typ.id == _RT_ICON:
            for name in typ.directory.entries:
                for lang in name.directory.entries:
                    d = lang.data.struct
                    icons[name.id] = pe.get_data(d.OffsetToData, d.Size)
                    break
        elif typ.id == _RT_GROUP_ICON and group is None:
            for name in typ.directory.entries:
                for lang in name.directory.entries:
                    d = lang.data.struct
                    group = pe.get_data(d.OffsetToData, d.Size)
                    break
                if group is not None:
                    break
    if not group or len(group) < 6:
        return None

    _, _, count = struct.unpack_from("<HHH", group, 0)
    entries = []
    for i in range(count):
        off = 6 + i * 14
        if off + 14 > len(group):
            break
        w, h, colors, res, planes, bits, size, icon_id = struct.unpack_from(
            "<BBBBHHIH", group, off
        )
        data = icons.get(icon_id)
        if data:
            entries.append(((w or 256), (h or 256), colors, res, planes, bits, data))
    if not entries:
        return None
    # keep the largest (large=True) or the one closest to 32px
    if large:
        entries.sort(key=lambda e: (e[0] * e[1], e[5]), reverse=True)
    else:
        entries.sort(key=lambda e: (abs(e[0] - 32), -e[5]))
    entries = entries[:1]

    out = [struct.pack("<HHH", 0, 1, len(entries))]
    offset = 6 + 16 * len(entries)
    blobs = []
    for w, h, colors, res, planes, bits, data in entries:
        out.append(
            struct.pack(
                "<BBBBHHII",
                w & 0xFF,
                h & 0xFF,
                colors,
                res,
                planes,
                bits,
                len(data),
                offset,
            )
        )
        offset += len(data)
        blobs.append(data)
    return b"".join(out + blobs)


def _placeholder():
    from qtsymbols import QColor, QPainter, QPixmap, Qt

    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#FF69B4"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.end()
    return pix


def exe_pixmap(path, large=False):
    from qtsymbols import QIcon, QPixmap

    pix = QPixmap()
    if path:
        from lunaplat.linux.nativeutils_impl import getcurrexe

        if os.path.abspath(path) == os.path.abspath(getcurrexe() or ""):
            if os.path.exists(APP_ICON):
                icon = QIcon(APP_ICON)
                pix = icon.pixmap(256 if large else 48)
        elif path.lower().endswith((".exe", ".dll")) and os.path.isfile(path):
            data = _pe_icon_bytes(path, large)
            if data:
                pix.loadFromData(data, "ICO")
        elif path.lower().endswith((".png", ".svg", ".ico", ".jpg")):
            pix.load(path)
    if pix.isNull():
        icon = QIcon.fromTheme("application-x-ms-dos-executable")
        if not icon.isNull():
            pix = icon.pixmap(256 if large else 48)
    if pix.isNull():
        pix = _placeholder()
    return pix
