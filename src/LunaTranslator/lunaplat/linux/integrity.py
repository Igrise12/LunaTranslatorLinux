"""Startup integrity check for Linux (replaces the .dll/.exe list)."""

import os


def _missing_native():
    # only libNativeUtils is required to start; others degrade per feature
    required = ["files/DLL64/libNativeUtils.so"]
    return [
        os.path.normpath(os.path.abspath(f)) for f in required if not os.path.exists(f)
    ]


def checkintegrity_linux(_TR):
    missing = _missing_native()
    if not missing:
        return None
    return 1, _TR("找不到重要组件：\n{modules}\n请重新下载并关闭杀毒软件后重试").format(
        modules="\n".join(missing)
    ) + "\n\nBuild the native libraries first:\n  src/NativeImpl/scripts/linux/build_native.sh"
