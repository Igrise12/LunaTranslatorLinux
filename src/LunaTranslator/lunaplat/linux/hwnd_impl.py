"""Linux replacements for names in myutils/hwnd.py."""

import hashlib
import os


def patch(mod):
    import gobject
    from qtsymbols import QIcon, QPixmap

    from lunaplat.linux.icons import exe_pixmap

    def getExeIcon(name, icon=True, cache=False, large=False):
        fn = None
        if cache:
            fn = gobject.getcachedir(
                "icon/{}.png".format(hashlib.md5(name.encode("utf8")).hexdigest())
            )
            if os.path.exists(fn):
                pix = QPixmap(fn)
                if not pix.isNull():
                    return QIcon(pix) if icon else pix
        pix = exe_pixmap(name, large=large)
        if fn:
            pix.save(fn)
        return QIcon(pix) if icon else pix

    mod.getExeIcon = getExeIcon
