"""Win32 clipboard for Unicode text."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .errors import ActionError

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
except (AttributeError, OSError):  # not Windows: the module still imports, the Desktop gate answers
    _user32 = _kernel32 = None

if _user32 is not None:
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = []
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.argtypes = []
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]


def _open_clipboard() -> None:
    for attempt in range(10):
        if _user32.OpenClipboard(None):
            return
        if attempt < 9:
            time.sleep(0.05)
    raise ActionError("could not open the clipboard")


def set_text(text: str) -> None:
    if _user32 is None or _kernel32 is None:
        raise ActionError("clipboard is only available on Windows")
    handle = None
    ownership_passed = False
    opened = False
    try:
        _open_clipboard()
        opened = True
        if not _user32.EmptyClipboard():
            raise ActionError("could not empty the clipboard")
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise ActionError("could not allocate clipboard memory")
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise ActionError("could not lock clipboard memory")
        try:
            buffer = ctypes.create_string_buffer(data, len(data))
            ctypes.memmove(pointer, buffer, len(data))
        finally:
            _kernel32.GlobalUnlock(handle)
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ActionError("could not set the clipboard text")
        ownership_passed = True
    except ActionError:
        raise
    except (AttributeError, OSError) as exc:
        raise ActionError(f"could not set the clipboard text: {exc}") from exc
    finally:
        if handle and not ownership_passed and _kernel32 is not None:
            _kernel32.GlobalFree(handle)
        if opened:
            _user32.CloseClipboard()


def get_text() -> str:
    if _user32 is None or _kernel32 is None:
        raise ActionError("clipboard is only available on Windows")
    opened = False
    try:
        _open_clipboard()
        opened = True
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    except ActionError:
        raise
    except (AttributeError, OSError) as exc:
        raise ActionError(f"could not read the clipboard text: {exc}") from exc
    finally:
        if opened:
            _user32.CloseClipboard()
