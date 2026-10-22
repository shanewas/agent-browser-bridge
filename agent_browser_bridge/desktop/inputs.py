"""Synthetic mouse and keyboard input through SendInput."""

from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

from .errors import ActionError

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
except (AttributeError, OSError):  # not Windows: the module still imports, the Desktop gate answers
    _user32 = None

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_TAB = 0x09

#: Seconds between synthetic keystrokes. Typing with no gap loses characters in
#: applications that sample the keyboard rather than drain the message queue.
KEYSTROKE_DELAY = 0.005

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


if _user32 is not None:
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
    _user32.VkKeyScanW.restype = ctypes.c_short
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

_NAMED_KEYS = {
    "return": VK_RETURN, "enter": VK_RETURN, "kp_enter": VK_RETURN,
    "tab": VK_TAB, "escape": VK_ESCAPE, "esc": VK_ESCAPE,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "space": 0x20, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "pageup": 0x21, "prior": 0x21,
    "page_down": 0x22, "pagedown": 0x22, "next": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "caps_lock": 0x14, "num_lock": 0x90, "scroll_lock": 0x91,
    "print": 0x2C, "printscreen": 0x2C, "pause": 0x13,
    "menu": 0x5D, "apps": 0x5D,
    "plus": 0xBB, "equal": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "semicolon": 0xBA, "apostrophe": 0xDE, "grave": 0xC0,
    "bracketleft": 0xDB, "bracketright": 0xDD, "backslash": 0xDC,
}
_NAMED_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})

_MODIFIERS = {
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12, "meta": 0x12,
    "super": 0x5B, "win": 0x5B, "cmd": 0x5B,
}

#: Keys the driver only recognises with the extended-key flag set.
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x2C, 0x90, 0x5B}

_SHIFT_STATE = {1: 0x10, 2: 0x11, 4: 0x12}


def _send(*events: INPUT) -> None:
    array = (INPUT * len(events))(*events)
    sent = _user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent == len(events):
        return

    # A thread on a background desktop has its synthetic input refused. Attaching to the
    # input desktop and re-sending only the events that did not go through avoids
    # repeating the ones that did, which would double a keystroke or a button press.
    from .screen import attach_input_desktop

    if attach_input_desktop():
        remaining = len(events) - sent
        tail = (INPUT * remaining)(*events[sent:])
        sent += _user32.SendInput(remaining, tail, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise ActionError(f"input was blocked after {sent} of {len(events)} events "
                          f"(error {getattr(ctypes, 'get_last_error', lambda: 0)()})")


def _mouse_event(flags: int, data: int = 0) -> INPUT:
    event = INPUT(type=INPUT_MOUSE)
    event.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    return event


def _move_event(x: int, y: int) -> INPUT:
    """A pointer move as real input, in the 0..65535 space of the virtual desktop.

    SetCursorPos moves the pointer but applications that read raw pointer input (WinUI ones
    such as Paint) never see it, so a stroke made of cursor jumps draws a straight line from
    press to release. These moves they do see.
    """
    left, top = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN), _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width, height = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    event = INPUT(type=INPUT_MOUSE)
    event.mi = MOUSEINPUT(round((x - left) * 65535 / max(1, width - 1)),
                          round((y - top) * 65535 / max(1, height - 1)), 0,
                          MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    return event


def _key_event(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return event


def _unit_event(code_unit: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki = KEYBDINPUT(0, code_unit, flags, 0, 0)
    return event


def parse_combo(combo: str) -> tuple[list[int], int]:
    """Split `"ctrl+shift+s"` into modifier virtual-key codes and the key's code.

    A trailing `+` is the plus key itself, so `"ctrl++"` presses plus with control held.
    """
    combo = combo.strip()
    if not combo:
        raise ActionError("empty key combination")
    if combo.endswith("+"):
        tokens = [t for t in combo[:-1].rstrip("+").split("+") if t] + ["plus"]
    else:
        tokens = combo.split("+")
    modifiers = []
    for token in tokens[:-1]:
        vk = _MODIFIERS.get(token.strip().lower())
        if vk is None:
            raise ActionError(f"unknown modifier: {token}")
        modifiers.append(vk)
    vk, implied = resolve_key(tokens[-1])
    for extra in implied:
        if extra not in modifiers:
            modifiers.append(extra)
    return modifiers, vk


def resolve_key(name: str) -> tuple[int, list[int]]:
    """Map one key name to its virtual-key code plus any modifiers the current
    keyboard layout needs to produce it (`!` on a US layout implies shift)."""
    key = name.strip()
    named = _NAMED_KEYS.get(key.lower())
    if named is not None:
        return named, []
    if key.lower() in _MODIFIERS:
        return _MODIFIERS[key.lower()], []
    if len(key) != 1:
        raise ActionError(f"unknown key: {name}")
    if _user32 is None:
        # Letters and digits share their ASCII code with their virtual-key code; anything
        # else depends on the keyboard layout, which only Windows can answer.
        if key.isascii() and key.isalnum():
            return ord(key.upper()), ([0x10] if key.isalpha() and key.isupper() else [])
        raise ActionError(f"key {name!r} needs the Windows keyboard layout")
    scan = _user32.VkKeyScanW(key)
    if scan == -1:
        raise ActionError(f"key {name!r} is not reachable on the active layout")
    vk = scan & 0xFF
    implied = [flag for bit, flag in _SHIFT_STATE.items() if (scan >> 8) & bit]
    return vk, implied


def parse_modifiers(text: str | None) -> list[int]:
    """Virtual-key codes for a `"ctrl+shift"` style modifier string."""
    if not text:
        return []
    codes = []
    for token in text.split("+"):
        vk = _MODIFIERS.get(token.strip().lower())
        if vk is None:
            raise ActionError(f"unknown modifier: {token}")
        codes.append(vk)
    return codes


@contextlib.contextmanager
def modifiers_held(text: str | None):
    codes = parse_modifiers(text)
    if codes:
        _send(*[_key_event(vk, up=False) for vk in codes])
    try:
        yield
    finally:
        if codes:
            _send(*[_key_event(vk, up=True) for vk in reversed(codes)])


def abort_requested() -> bool:
    """True while Escape is physically held down."""
    return bool(_user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        from .screen import attach_input_desktop
        attach_input_desktop()
        if not _user32.GetCursorPos(ctypes.byref(point)):
            raise ActionError("could not read the cursor position")
    return point.x, point.y


def move(x: int, y: int) -> None:
    # SetCursorPos lands on the exact pixel. Applications that read raw input rather
    # than the cursor position, full-screen games mostly, will need absolute SendInput
    # moves instead.
    if not _user32.SetCursorPos(int(x), int(y)):
        from .screen import attach_input_desktop
        attach_input_desktop()
        if not _user32.SetCursorPos(int(x), int(y)):
            raise ActionError(f"could not move the cursor to ({x}, {y}); a daemon started by a "
                              "scheduled task or a service cannot drive input, start it from your own shell")


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def _button_flags(button: str) -> tuple[int, int]:
    try:
        return _BUTTONS[button]
    except KeyError:
        raise ActionError(f"unknown mouse button: {button}") from None


def mouse_down(button: str = "left") -> None:
    _send(_mouse_event(_button_flags(button)[0]))


def mouse_up(button: str = "left") -> None:
    _send(_mouse_event(_button_flags(button)[1]))


def click(button: str = "left", count: int = 1) -> None:
    down, up = _button_flags(button)
    _send(*[event for _ in range(count) for event in (_mouse_event(down), _mouse_event(up))])


def drag(start: tuple[int, int], end: tuple[int, int], button: str = "left",
         through=()) -> None:
    move(*start)
    mouse_down(button)
    try:
        # A single jump is ignored by drag targets that track mouse movement.
        steps = 8
        here = start
        for there in (*through, end):
            for step in range(1, steps + 1):
                _send(_move_event(here[0] + (there[0] - here[0]) * step // steps,
                                  here[1] + (there[1] - here[1]) * step // steps))
                time.sleep(0.01)
            here = there
        move(*end)  # absolute moves round to the 65535 grid; the release lands on the exact pixel
    finally:
        mouse_up(button)


def scroll(direction: str, amount: int) -> None:
    axis = {"up": (MOUSEEVENTF_WHEEL, 1), "down": (MOUSEEVENTF_WHEEL, -1),
            "right": (MOUSEEVENTF_HWHEEL, 1), "left": (MOUSEEVENTF_HWHEEL, -1)}
    if direction not in axis:
        raise ActionError(f"unknown scroll direction: {direction}")
    flag, sign = axis[direction]
    _send(_mouse_event(flag, sign * WHEEL_DELTA * int(amount)))


def press(combo: str, repeat: int = 1) -> None:
    modifiers, vk = parse_combo(combo)
    _send(*[_key_event(mod, up=False) for mod in modifiers])
    try:
        for _ in range(max(1, repeat)):
            _send(_key_event(vk, up=False), _key_event(vk, up=True))
            time.sleep(KEYSTROKE_DELAY)
    finally:
        _send(*[_key_event(mod, up=True) for mod in reversed(modifiers)])


def hold(combo: str, duration: float) -> None:
    modifiers, vk = parse_combo(combo)
    _send(*[_key_event(mod, up=False) for mod in modifiers], _key_event(vk, up=False))
    try:
        time.sleep(duration)
    finally:
        _send(_key_event(vk, up=True),
              *[_key_event(mod, up=True) for mod in reversed(modifiers)])


def type_text(text: str) -> None:
    """Type literal text. Characters go in as Unicode, so the result does not depend
    on the active keyboard layout."""
    for char in text:
        if char == "\n":
            _send(_key_event(VK_RETURN, up=False), _key_event(VK_RETURN, up=True))
        elif char == "\t":
            _send(_key_event(VK_TAB, up=False), _key_event(VK_TAB, up=True))
        elif char == "\r":
            continue
        else:
            for unit in _code_units(char):
                _send(_unit_event(unit, up=False), _unit_event(unit, up=True))
        time.sleep(KEYSTROKE_DELAY)


def _code_units(char: str) -> list[int]:
    """UTF-16 code units for one character. Anything above the basic plane, emoji
    included, needs its surrogate pair sent as two events."""
    encoded = char.encode("utf-16-le")
    return [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]
