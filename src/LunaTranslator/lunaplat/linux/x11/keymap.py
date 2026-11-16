"""Windows virtual-key codes <-> X11 keysyms, and MOD_* <-> X modifier masks.

The app stores hotkeys and synthesises key presses with Win32 VK codes
(myutils/keycode.py), so the X11 backend translates at the edge.
"""

from Xlib import X, XK

# MOD_* bits from RegisterHotKey / myutils/keycode.py
MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8

MOD_TO_MASK = {
    MOD_ALT: X.Mod1Mask,
    MOD_CONTROL: X.ControlMask,
    MOD_SHIFT: X.ShiftMask,
    MOD_WIN: X.Mod4Mask,
}


def mods_to_mask(mods):
    mask = 0
    for bit, xmask in MOD_TO_MASK.items():
        if mods & bit:
            mask |= xmask
    return mask


_SPECIAL = {
    0x08: "BackSpace",
    0x09: "Tab",
    0x0C: "Clear",
    0x0D: "Return",
    0x10: "Shift_L",
    0x11: "Control_L",
    0x12: "Alt_L",
    0x13: "Pause",
    0x14: "Caps_Lock",
    0x1B: "Escape",
    0x20: "space",
    0x21: "Prior",
    0x22: "Next",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5B: "Super_L",
    0x5C: "Super_R",
    0x5D: "Menu",
    0x6A: "KP_Multiply",
    0x6B: "KP_Add",
    0x6C: "KP_Separator",
    0x6D: "KP_Subtract",
    0x6E: "KP_Decimal",
    0x6F: "KP_Divide",
    0x90: "Num_Lock",
    0x91: "Scroll_Lock",
    0xA0: "Shift_L",
    0xA1: "Shift_R",
    0xA2: "Control_L",
    0xA3: "Control_R",
    0xA4: "Alt_L",
    0xA5: "Alt_R",
    0xBA: "semicolon",
    0xBB: "equal",
    0xBC: "comma",
    0xBD: "minus",
    0xBE: "period",
    0xBF: "slash",
    0xC0: "grave",
    0xDB: "bracketleft",
    0xDC: "backslash",
    0xDD: "bracketright",
    0xDE: "apostrophe",
}


def vk_to_keysym(vk):
    vk = int(vk) & 0xFF
    if 0x41 <= vk <= 0x5A:  # A-Z
        return XK.string_to_keysym(chr(vk).lower())
    if 0x30 <= vk <= 0x39:  # 0-9
        return XK.string_to_keysym(chr(vk))
    if 0x60 <= vk <= 0x69:  # numpad 0-9
        return XK.string_to_keysym("KP_{}".format(vk - 0x60))
    if 0x70 <= vk <= 0x87:  # F1-F24
        return XK.string_to_keysym("F{}".format(vk - 0x70 + 1))
    name = _SPECIAL.get(vk)
    if name:
        return XK.string_to_keysym(name)
    return 0


# mouse buttons in GetKeyState / GetAsyncKeyState
VK_BUTTON_MASK = {
    0x01: X.Button1Mask,  # VK_LBUTTON
    0x02: X.Button3Mask,  # VK_RBUTTON
    0x04: X.Button2Mask,  # VK_MBUTTON
}

# generic modifier VKs also match their right-hand keysym
VK_ALSO = {
    0x10: ("Shift_R",),
    0x11: ("Control_R",),
    0x12: ("Alt_R", "ISO_Level3_Shift"),
}
