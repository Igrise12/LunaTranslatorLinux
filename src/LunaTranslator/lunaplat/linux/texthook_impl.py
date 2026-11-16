"""Linux hook backend for textio/textsource/texthook.py.

On Windows, texthook loads LunaHost{32,64}.dll in-process. On Linux the
game runs under Wine, so hooking has to happen inside the game's Wine
prefix (the LunaBridge helper, phase 2). Until the bridge is available the
host library is loaded leniently: every Luna_* call is a no-op stub, so the
hook text source starts and simply produces no text.
"""

import lunaplat


def patch(mod):
    mod.CDLL = lunaplat.load_library
