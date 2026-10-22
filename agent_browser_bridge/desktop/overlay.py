"""A small always-on-top strip that tells the person at the keyboard what the bridge is doing.

Win32 through ctypes on its own thread. WS_EX_NOACTIVATE is the flag that matters: the strip
must never take the keyboard focus away from the window the agent is driving. The display
affinity call keeps it out of screenshots on Windows 10 2004 and later.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

WIDTH, HEIGHT, MARGIN = 380, 44, 16
IDLE_SECONDS = 1.5
#: COLORREF is 0x00bbggrr.
COLORS = {"action": 0x002B2B2B, "notify": 0x00C85A1E, "record": 0x001E1EBE}
WHITE = 0x00FFFFFF

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_LAYERED = 0x8, 0x80, 0x08000000, 0x80000
#: Clicks fall through to whatever is under the strip (a maximized window's close button sits there).
WS_EX_TRANSPARENT = 0x20
LWA_ALPHA = 0x2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
HWND_TOPMOST = -1
WM_DESTROY, WM_CLOSE, WM_PAINT, WM_TIMER, WM_APP = 0x2, 0x10, 0xF, 0x113, 0x8000
WM_REFRESH = WM_APP + 1
WDA_EXCLUDEFROMCAPTURE = 0x11
DT_SINGLELINE, DT_VCENTER, DT_END_ELLIPSIS = 0x20, 0x4, 0x8000
TRANSPARENT = 1
CLASS_NAME = "AgentBridgeOverlay"


class NullOverlay:
    """What the facade uses off Windows, under EDGE_BRIDGE_NO_OVERLAY, or when the real one fails."""

    def show(self, text: str, kind: str = "action", seconds: float | None = None) -> None:
        pass

    def idle(self) -> None:
        pass

    def record(self, text: str | None) -> None:
        pass

    def close(self) -> None:
        pass


class Overlay:
    def __init__(self):
        self._lock = threading.Lock()
        self._text, self._kind = "", "action"
        self._until: float | None = None
        self._record: str | None = None
        self._hwnd = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    # -- the API the facade calls, from any thread --------------------------------------

    def show(self, text: str, kind: str = "action", seconds: float | None = None) -> None:
        with self._lock:
            if self._record and kind != "record":
                # A notice must not hide the fact that keys are being kept.
                kind, text = "record", f"{self._record.split(':', 1)[0]} · {text}"
            self._text, self._kind = text, kind
            self._until = None if seconds is None else time.monotonic() + seconds
        self._post()

    def idle(self) -> None:
        """Let the current message fade in IDLE_SECONDS, unless it carries its own time."""
        with self._lock:
            if self._until is None:
                self._until = time.monotonic() + IDLE_SECONDS

    def record(self, text: str | None) -> None:
        with self._lock:
            self._record = text
        if text:
            self.show(text, "record")
        else:
            with self._lock:
                self._until = time.monotonic()
            self._post()

    def close(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            self._thread.join(2.0)

    # -- the window thread ----------------------------------------------------------------

    def _post(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_REFRESH, 0, 0)

    def _run(self) -> None:
        try:
            self._window_loop()
        except Exception as exc:  # noqa: BLE001 - a broken strip must not take the daemon down
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._hwnd = None
            self._ready.set()

    def _window_loop(self) -> None:
        from ctypes import wintypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long,
                                     wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                        ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL),
                        ("rgbReserved", ctypes.c_byte * 32)]

        user32.DefWindowProcW.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        ctypes.windll.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        # Handles are pointer-sized; without these ctypes truncates them to 32 bits.
        user32.BeginPaint.restype = wintypes.HDC
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
        user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                     ctypes.POINTER(wintypes.RECT), wintypes.UINT]
        user32.InvalidateRect.argtypes = [wintypes.HWND, wintypes.LPVOID, wintypes.BOOL]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        gdi32.CreateFontW.restype = wintypes.HFONT
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        gdi32.SetTextColor.restype = wintypes.COLORREF
        font = gdi32.CreateFontW(-15, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 5, 0, "Segoe UI")

        def paint(hwnd):
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            with self._lock:
                text, kind = self._text, self._kind
            rect = wintypes.RECT(0, 0, WIDTH, HEIGHT)
            brush = gdi32.CreateSolidBrush(COLORS.get(kind, COLORS["action"]))
            user32.FillRect(hdc, ctypes.byref(rect), brush)
            gdi32.DeleteObject(brush)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, WHITE)
            old = gdi32.SelectObject(hdc, font)
            inset = wintypes.RECT(12, 0, WIDTH - 12, HEIGHT)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(inset), DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS)
            gdi32.SelectObject(hdc, old)
            user32.EndPaint(hwnd, ctypes.byref(ps))

        def refresh(hwnd):
            user32.InvalidateRect(hwnd, None, True)
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            user32.SetTimer(hwnd, 1, 100, None)

        def tick(hwnd):
            with self._lock:
                due = self._until is not None and time.monotonic() >= self._until
                record = self._record
                if due:
                    self._until = None
                    if record:
                        self._text, self._kind = record, "record"
            if due:
                if record:
                    user32.InvalidateRect(hwnd, None, True)
                else:
                    user32.KillTimer(hwnd, 1)
                    user32.ShowWindow(hwnd, SW_HIDE)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_PAINT:
                paint(hwnd)
                return 0
            if msg == WM_REFRESH:
                refresh(hwnd)
                return 0
            if msg == WM_TIMER:
                tick(hwnd)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # kept alive for the window's lifetime
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.lpszClassName = CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))
        x = user32.GetSystemMetrics(0) - WIDTH - MARGIN
        hwnd = user32.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
                                      | WS_EX_TRANSPARENT,
                                      CLASS_NAME, "agent-bridge", WS_POPUP, x, MARGIN, WIDTH, HEIGHT,
                                      None, None, wc.hInstance, None)
        if hwnd:
            user32.SetLayeredWindowAttributes(hwnd, 0, 230, LWA_ALPHA)
            user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            self._hwnd = hwnd
        self._ready.set()
        if hwnd:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            gdi32.DeleteObject(font)
            self._hwnd = None
        # A class left registered would hand the next strip this instance's message handler.
        user32.UnregisterClassW(CLASS_NAME, wc.hInstance)


def make_overlay():
    if sys.platform != "win32" or os.environ.get("EDGE_BRIDGE_NO_OVERLAY") == "1":
        return NullOverlay()
    try:
        overlay = Overlay()
    except (OSError, AttributeError):
        return NullOverlay()
    return overlay if overlay._hwnd else NullOverlay()
