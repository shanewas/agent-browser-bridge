"""Screen capture through the Windows GDI, with no capture dependency.

Coordinates here are virtual-desktop pixels: the primary display starts at the origin
and every other monitor sits at an offset from it, which is negative for anything to
the left of primary.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image

from .errors import ActionError

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0
DI_NORMAL = 0x0003
CURSOR_SHOWING = 0x0001
MONITORINFOF_PRIMARY = 0x0001
#: Every desktop-specific right, without the standard rights the real DESKTOP_ALL_ACCESS
#: (0x000F01FF) also asks for. SetThreadDesktop needs none of those.
DESKTOP_RIGHTS = 0x01FF
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SW_RESTORE = 9

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
except (AttributeError, OSError):  # not Windows: the module still imports, the Desktop gate answers
    _user32 = _gdi32 = _kernel32 = None


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


_MONITORENUMPROC = None
_WINDOWENUMPROC = None
if _user32 is not None:
    _MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    _WINDOWENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.EnumWindows.argtypes = [_WINDOWENUMPROC, wintypes.LPARAM]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    if _kernel32 is not None:
        _kernel32.OpenProcess.restype = wintypes.HANDLE
        _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                         wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        _kernel32.GetCurrentThreadId.argtypes = []

    # Handles are pointer-sized; without these the return values are truncated on 64-bit.
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    _user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.SetThreadDesktop.restype = wintypes.BOOL
    _user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    _user32.CloseDesktop.restype = wintypes.BOOL
    _user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT,
                                   wintypes.HBRUSH, wintypes.UINT]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.BitBlt.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
    ]
    _gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ]
    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]

_dpi_ready = False
_input_desktop = None
_attached = threading.local()
#: Two threads opening the desktop at once would leak whichever handle lost the race.
_desktop_lock = threading.Lock()


@dataclass(frozen=True)
class Monitor:
    """One display, placed on the virtual desktop."""

    index: int
    origin: tuple[int, int]
    size: tuple[int, int]
    primary: bool

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.origin[0], self.origin[1], self.size[0], self.size[1]

    def describe(self) -> dict:
        return {"monitor": self.index, "origin": list(self.origin),
                "size": list(self.size), "primary": self.primary}


def _process_name(pid) -> str | None:
    if not pid or _kernel32 is None:
        return None
    handle = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        path = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return path.value.rsplit("\\", 1)[-1]
        return None
    finally:
        _kernel32.CloseHandle(handle)


def _window_info(hwnd) -> dict:
    title = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, title, 512)
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = wintypes.RECT()
    if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        bounds = [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]
    else:
        bounds = [0, 0, 0, 0]
    return {"hwnd": int(hwnd), "title": title.value, "pid": pid.value,
            "process": _process_name(pid.value), "rect": bounds,
            "minimized": bool(_user32.IsIconic(hwnd))}


def foreground() -> dict:
    """The window that has the keyboard: title, process id and image name (None when the
    process cannot be opened, which is what an elevated one answers)."""
    info = _window_info(_user32.GetForegroundWindow())
    return {"title": info["title"], "pid": info["pid"], "process": info["process"],
            "hwnd": info["hwnd"], "rect": info["rect"]}


def windows() -> list[dict]:
    if _user32 is None:
        return []
    foreground_hwnd = _user32.GetForegroundWindow()
    found: list[dict] = []

    def collect(hwnd, _param):
        if _user32.IsWindowVisible(hwnd):
            info = _window_info(hwnd)
            if info["title"]:
                info["foreground"] = bool(hwnd == foreground_hwnd)
                found.append(info)
        return True

    _user32.EnumWindows(_WINDOWENUMPROC(collect), 0)
    return found


def find_window(title=None, process=None, pid=None) -> dict | None:
    for window in windows():
        if title is not None and title.lower() not in (window.get("title") or "").lower():
            continue
        if process is not None:
            wanted = process.lower()
            actual = (window.get("process") or "").lower()
            if wanted != actual and wanted + ".exe" != actual:
                continue
        if pid is not None and window.get("pid") != pid:
            continue
        return window
    return None


def focus_window(hwnd) -> bool:
    if _user32 is None or _kernel32 is None:
        return False
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        foreground_hwnd = _user32.GetForegroundWindow()
        if int(foreground_hwnd or 0) == int(hwnd):
            return True
        current_thread = _kernel32.GetCurrentThreadId()
        pid = wintypes.DWORD()
        foreground_thread = _user32.GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(pid)) if foreground_hwnd else 0
        attached = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                attached = bool(_user32.AttachThreadInput(current_thread, foreground_thread, True))
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(current_thread, foreground_thread, False)
        return bool(int(_user32.GetForegroundWindow() or 0) == int(hwnd))
    except (AttributeError, OSError):
        return False


def attach_input_desktop() -> bool:
    """Attach the calling thread to the active input desktop.

    Under a service or any non-interactive shell, threads start on a background desktop
    where BitBlt, GetCursorPos, SendInput and Desktop Duplication all fail with
    ERROR_ACCESS_DENIED.

    The desktop handle is opened once and held for the life of the process, because the
    threads using it keep needing it. The attachment itself is per thread, though, so
    every thread has to make the call: a server that answers requests on worker threads
    would otherwise leave all of them on the wrong desktop.
    """
    global _input_desktop
    if getattr(_attached, "done", False):
        return True
    try:
        with _desktop_lock:
            if _input_desktop is None:
                handle = _user32.OpenInputDesktop(0, False, DESKTOP_RIGHTS)
                if not handle:
                    return False
                _input_desktop = handle
        if not _user32.SetThreadDesktop(_input_desktop):
            # SetThreadDesktop refuses a thread that already owns windows or hooks.
            return False
    except (AttributeError, OSError):
        return False
    _attached.done = True
    return True


def set_dpi_awareness() -> None:
    """Report true pixels. Without this, capture size and cursor coordinates disagree
    on any display scaled above 100%."""
    attach_input_desktop()
    global _dpi_ready
    if _dpi_ready:
        return
    per_monitor_v2 = ctypes.c_void_p(-4)
    try:
        if _user32.SetProcessDpiAwarenessContext(per_monitor_v2):
            _dpi_ready = True
            return
    except AttributeError:
        pass
    try:
        result = ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        # E_ACCESSDENIED means an application manifest already chose the mode.
        _dpi_ready = result in (0, 0x80070005)
    except (AttributeError, OSError):
        _dpi_ready = bool(_user32.SetProcessDPIAware())


def monitors() -> list[Monitor]:
    """Every display, numbered from 1 in the order they are laid out left to right."""
    set_dpi_awareness()
    found: list[tuple[tuple[int, int], tuple[int, int], bool]] = []

    def collect(handle, _dc, _rect, _param):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if _user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            area = info.rcMonitor
            found.append((
                (area.left, area.top),
                (area.right - area.left, area.bottom - area.top),
                bool(info.dwFlags & MONITORINFOF_PRIMARY),
            ))
        return True

    if not _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(collect), 0):
        raise ActionError("could not enumerate the displays")
    return number_left_to_right(found)


def number_left_to_right(found: list) -> list[Monitor]:
    """Number displays by horizontal position, which is how someone sitting at the desk
    would count them. Height only breaks a tie between two at the same x."""
    ordered = sorted(found, key=lambda entry: (entry[0][0], entry[0][1]))
    return [Monitor(number, origin, size, primary)
            for number, (origin, size, primary) in enumerate(ordered, start=1)]


def primary_monitor() -> Monitor:
    for monitor in monitors():
        if monitor.primary:
            return monitor
    raise ActionError("no display is marked as the primary one")


def virtual_monitor() -> Monitor:
    """Every display treated as one wide surface."""
    boxes = [monitor.box for monitor in monitors()]
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return Monitor(0, (left, top), (right - left, bottom - top), False)


def screen_size() -> tuple[int, int]:
    """Primary display size in physical pixels."""
    set_dpi_awareness()
    return _user32.GetSystemMetrics(SM_CXSCREEN), _user32.GetSystemMetrics(SM_CYSCREEN)


def grab(region: tuple[int, int, int, int] | None = None,
         cursor: bool = False) -> Image.Image:
    """Capture `(left, top, width, height)` of the virtual desktop, or the primary
    display when no region is given."""
    set_dpi_awareness()
    if region is None:
        left, top = 0, 0
        width, height = screen_size()
    else:
        left, top, width, height = region
    if width <= 0 or height <= 0:
        raise ActionError(f"capture region has no area: {width}x{height}")

    screen_dc = _user32.GetDC(None)
    if not screen_dc:
        raise ActionError("could not open a device context for the screen")
    mem_dc = bitmap = previous = None
    try:
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        bitmap = _gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bitmap:
            raise ActionError("could not allocate a bitmap for the capture")
        previous = _gdi32.SelectObject(mem_dc, bitmap)
        if not _gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, left, top,
                             SRCCOPY | CAPTUREBLT):
            raise ActionError(f"screen copy failed (error {ctypes.get_last_error()})")
        if cursor:
            _draw_cursor(mem_dc, left, top)

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # negative height gives top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                  ctypes.byref(info), DIB_RGB_COLORS)
        if copied != height:
            raise ActionError(f"read {copied} of {height} scan lines from the capture")
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    finally:
        if previous:
            _gdi32.SelectObject(mem_dc, previous)
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if mem_dc:
            _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(None, screen_dc)


def cursor_overlay() -> tuple[Image.Image, tuple[int, int]] | None:
    """The pointer as a standalone image, plus where its top left corner belongs on the
    virtual desktop. Used by capture paths that hand over a frame with no pointer in it.

    Returns None when the pointer is hidden or cannot be drawn, which is not worth
    failing a screenshot over.
    """
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not _user32.GetCursorInfo(ctypes.byref(info)):
        return None
    if not (info.flags & CURSOR_SHOWING) or not info.hCursor:
        return None

    icon = ICONINFO()
    if not _user32.GetIconInfo(info.hCursor, ctypes.byref(icon)):
        return None
    for handle in (icon.hbmMask, icon.hbmColor):
        if handle:
            _gdi32.DeleteObject(handle)

    size = 64
    header = BITMAPINFO()
    header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.bmiHeader.biWidth = size
    header.bmiHeader.biHeight = -size
    header.bmiHeader.biPlanes = 1
    header.bmiHeader.biBitCount = 32
    header.bmiHeader.biCompression = BI_RGB

    screen_dc = _user32.GetDC(None)
    mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
    bits = ctypes.c_void_p()
    bitmap = _gdi32.CreateDIBSection(mem_dc, ctypes.byref(header), DIB_RGB_COLORS,
                                     ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits:
            return None
        previous = _gdi32.SelectObject(mem_dc, bitmap)
        drawn = _user32.DrawIconEx(mem_dc, 0, 0, info.hCursor, 0, 0, 0, None, DI_NORMAL)
        _gdi32.SelectObject(mem_dc, previous)
        if not drawn:
            return None
        raw = ctypes.string_at(bits, size * size * 4)
    finally:
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(None, screen_dc)

    overlay = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1).copy()
    if not overlay.getbbox() or overlay.getchannel("A").getbbox() is None:
        # A monochrome cursor leaves the alpha channel empty; drawing it would paint a
        # black square over the screen, so it is skipped rather than guessed at.
        return None
    return overlay, (info.ptScreenPos.x - icon.xHotspot, info.ptScreenPos.y - icon.yHotspot)


def _draw_cursor(target_dc, left: int, top: int) -> None:
    """Paint the pointer into a capture.

    BitBlt copies what windows painted, and Windows draws the pointer over the top of
    that, so a capture never contains it unless it is composited in afterwards. A
    failure here is not worth losing the screenshot over, so it is left undrawn.
    """
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not _user32.GetCursorInfo(ctypes.byref(info)):
        return
    if not (info.flags & CURSOR_SHOWING) or not info.hCursor:
        return

    icon = ICONINFO()
    hotspot_x = hotspot_y = 0
    if _user32.GetIconInfo(info.hCursor, ctypes.byref(icon)):
        hotspot_x, hotspot_y = icon.xHotspot, icon.yHotspot
        # GetIconInfo hands over two bitmaps that belong to the caller now.
        for handle in (icon.hbmMask, icon.hbmColor):
            if handle:
                _gdi32.DeleteObject(handle)

    _user32.DrawIconEx(target_dc,
                       info.ptScreenPos.x - left - hotspot_x,
                       info.ptScreenPos.y - top - hotspot_y,
                       info.hCursor, 0, 0, 0, None, DI_NORMAL)
