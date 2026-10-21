"""Screen capture through the DXGI Desktop Duplication API.

The GDI path asks the system to read the screen back out of the compositor, which on a
busy desktop costs hundreds of milliseconds and varies wildly from one call to the next.
Desktop Duplication instead hands over the frame the compositor already holds, so a
capture costs a few milliseconds and stays steady.

The win comes from keeping the duplication open between captures, so a `Duplicator` is
meant to be held for the life of a process rather than built per screenshot.

Everything here is COM reached through ctypes: each interface is a pointer to a table of
function pointers, and a method is called by its index in that table. The indices come
from the interface declarations in the Windows SDK headers, counting inherited methods
first, and a wrong one crashes the process rather than raising, so they are named
rather than written inline.
"""

from __future__ import annotations

import ctypes
import time
import uuid
from ctypes import wintypes

from PIL import Image

from .errors import ActionError

try:
    _d3d11 = ctypes.WinDLL("d3d11")
except (AttributeError, OSError):  # not Windows: FrameSource is never built there
    _d3d11 = None
_ole32 = None  # kept as a name only: GUIDs no longer need CLSIDFromString

S_OK = 0
DXGI_ERROR_WAIT_TIMEOUT = -2005270489      # 0x887A0027
DXGI_ERROR_ACCESS_LOST = -2005270490       # 0x887A0026
D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_SDK_VERSION = 7
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1

# Vtable slots. IUnknown occupies 0-2 everywhere; IDXGIObject adds 3-6.
QUERY_INTERFACE, RELEASE = 0, 2
DEVICE_GET_ADAPTER = 7
ADAPTER_ENUM_OUTPUTS = 7
OUTPUT_GET_DESC = 7
OUTPUT1_DUPLICATE_OUTPUT = 22
DUPL_ACQUIRE_NEXT_FRAME = 8
DUPL_RELEASE_FRAME = 14
DEVICE_CREATE_TEXTURE2D = 5
CONTEXT_MAP = 14
CONTEXT_UNMAP = 15
CONTEXT_COPY_RESOURCE = 47
TEXTURE_GET_DESC = 10

#: The formats the desktop is normally composited in, both 32-bit BGRA orderings.
BGRA_FORMATS = {87, 88}  # B8G8R8A8_UNORM, B8G8R8X8_UNORM

#: How long to wait for the very first frame of a new duplication, which has nothing
#: cached to fall back on. An idle display can take a moment to present one.
PRIME_TIMEOUT_MS = 500

#: DXGI_MODE_ROTATION values meaning the display is not turned: unspecified, identity.
UPRIGHT = {0, 1}


class GUID(ctypes.Structure):
    # Data1 is a 32-bit DWORD; c_ulong is 8 bytes on Linux, which would make the struct 24 bytes.
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    # bytes_le is the mixed-endian layout COM expects, so no ole32 call is needed and the
    # module imports on platforms without one.
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


IID_IDXGIDevice = _guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c")
IID_IDXGIOutput1 = _guid("00cddea8-939b-4b83-a340-a685226666cc")
IID_ID3D11Texture2D = _guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32),
                ("DesktopCoordinates", wintypes.RECT),
                ("AttachedToDesktop", wintypes.BOOL),
                ("Rotation", ctypes.c_uint),
                ("Monitor", wintypes.HMONITOR)]


class DXGI_OUTDUPL_POINTER_POSITION(ctypes.Structure):
    _fields_ = [("Position", wintypes.POINT), ("Visible", wintypes.BOOL)]


class DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
    _fields_ = [("LastPresentTime", ctypes.c_longlong),
                ("LastMouseUpdateTime", ctypes.c_longlong),
                ("AccumulatedFrames", ctypes.c_uint),
                ("RectsCoalesced", wintypes.BOOL),
                ("ProtectedContentMaskedOut", wintypes.BOOL),
                ("PointerPosition", DXGI_OUTDUPL_POINTER_POSITION),
                ("TotalMetadataBufferSize", ctypes.c_uint),
                ("PointerShapeBufferSize", ctypes.c_uint)]


class DXGI_SAMPLE_DESC(ctypes.Structure):
    _fields_ = [("Count", ctypes.c_uint), ("Quality", ctypes.c_uint)]


class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint),
                ("MipLevels", ctypes.c_uint), ("ArraySize", ctypes.c_uint),
                ("Format", ctypes.c_uint), ("SampleDesc", DXGI_SAMPLE_DESC),
                ("Usage", ctypes.c_uint), ("BindFlags", ctypes.c_uint),
                ("CPUAccessFlags", ctypes.c_uint), ("MiscFlags", ctypes.c_uint)]


class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", ctypes.c_uint),
                ("DepthPitch", ctypes.c_uint)]


def _method(pointer, index, restype, *argtypes):
    """Bind one entry of a COM object's function table."""
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    signature = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return signature(table[index])


def _release(pointer) -> None:
    if pointer:
        _method(pointer, RELEASE, ctypes.c_ulong)(pointer)


def _query(pointer, iid: GUID):
    out = ctypes.c_void_p()
    call = _method(pointer, QUERY_INTERFACE, ctypes.c_long,
                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    if call(pointer, ctypes.byref(iid), ctypes.byref(out)) != S_OK or not out:
        raise ActionError("this display does not offer the interface duplication needs")
    return out


class FrameSource:
    """Keeps one duplication per display alive and hands out frames.

    Opening a duplication costs about as much as a whole GDI capture, so this only pays
    for itself in a process that stays up. A display that refuses to be duplicated is
    remembered as unavailable and left to the GDI path.
    """

    def __init__(self):
        self._by_monitor: dict[int, Duplicator] = {}
        self._refused: set[int] = set()

    def frame(self, monitor):
        """The current frame for a display, or None to say the caller should use GDI."""
        if monitor.index in self._refused:
            return None
        duplicator = self._by_monitor.get(monitor.index)
        if duplicator is None:
            try:
                duplicator = Duplicator(monitor)
                # Wait for the first frame here. Later grabs can return immediately
                # because they fall back to this one when nothing has changed.
                duplicator.grab(timeout_ms=PRIME_TIMEOUT_MS)
            except (ActionError, OSError):
                self._refused.add(monitor.index)
                return None
            self._by_monitor[monitor.index] = duplicator
        try:
            return duplicator.grab(timeout_ms=0)
        except (ActionError, OSError):
            # A duplication can be lost when the display mode changes or another
            # program takes it. Drop it and let the next capture open a fresh one
            # rather than writing the display off for the life of the process.
            duplicator.close()
            self._by_monitor.pop(monitor.index, None)
            return None

    def close(self) -> None:
        for duplicator in self._by_monitor.values():
            duplicator.close()
        self._by_monitor.clear()


class Duplicator:
    """A live duplication of one display. Hold it; do not build one per capture."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.device = ctypes.c_void_p()
        self.context = ctypes.c_void_p()
        self.duplication = ctypes.c_void_p()
        self._staging = None
        self._staging_size = None
        self._last: Image.Image | None = None
        self._open()

    def _open(self) -> None:
        level = ctypes.c_uint()
        created = _d3d11.D3D11CreateDevice(
            None, D3D_DRIVER_TYPE_HARDWARE, None, 0, None, 0, D3D11_SDK_VERSION,
            ctypes.byref(self.device), ctypes.byref(level), ctypes.byref(self.context))
        if created != S_OK or not self.device:
            raise ActionError("could not open a Direct3D device for duplication")

        dxgi_device = _query(self.device, IID_IDXGIDevice)
        try:
            adapter = ctypes.c_void_p()
            get_adapter = _method(dxgi_device, DEVICE_GET_ADAPTER, ctypes.c_long,
                                  ctypes.POINTER(ctypes.c_void_p))
            if get_adapter(dxgi_device, ctypes.byref(adapter)) != S_OK or not adapter:
                raise ActionError("could not reach the display adapter")
        finally:
            _release(dxgi_device)

        try:
            output = self._find_output(adapter)
        finally:
            _release(adapter)

        try:
            output1 = _query(output, IID_IDXGIOutput1)
        finally:
            _release(output)

        try:
            duplicate = _method(output1, OUTPUT1_DUPLICATE_OUTPUT, ctypes.c_long,
                                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            if duplicate(output1, self.device,
                         ctypes.byref(self.duplication)) != S_OK:
                raise ActionError("the system refused to duplicate this display; "
                                  "another program may already be duplicating it")
        finally:
            _release(output1)

    def _find_output(self, adapter):
        """The DXGI output whose desktop rectangle matches the monitor asked for."""
        wanted = (self.monitor.origin[0], self.monitor.origin[1],
                  self.monitor.origin[0] + self.monitor.size[0],
                  self.monitor.origin[1] + self.monitor.size[1])
        enumerate_outputs = _method(adapter, ADAPTER_ENUM_OUTPUTS, ctypes.c_long,
                                    ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while True:
            output = ctypes.c_void_p()
            if enumerate_outputs(adapter, index, ctypes.byref(output)) != S_OK:
                raise ActionError(f"no display adapter output covers monitor "
                                  f"{self.monitor.index}")
            desc = DXGI_OUTPUT_DESC()
            get_desc = _method(output, OUTPUT_GET_DESC, ctypes.c_long,
                               ctypes.POINTER(DXGI_OUTPUT_DESC))
            if get_desc(output, ctypes.byref(desc)) == S_OK:
                area = desc.DesktopCoordinates
                if (area.left, area.top, area.right, area.bottom) == wanted:
                    if desc.Rotation not in UPRIGHT:
                        # A rotated display is duplicated in its native orientation, so
                        # the frame would come back sideways against a desktop rectangle
                        # that is not. Leaving it to GDI keeps the pixels and the
                        # coordinates agreeing with each other.
                        _release(output)
                        raise ActionError(f"monitor {self.monitor.index} is rotated")
                    return output
            _release(output)
            index += 1

    def grab(self, timeout_ms: int = 200) -> Image.Image:
        """The current frame.

        A duplicated frame only carries a desktop image when the compositor actually
        presented one; the surface that comes with a mouse-only or empty update is
        undefined and reads back black. Those are treated as "nothing changed", which is
        what makes polling an idle desktop nearly free.
        """
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000 * 5
        while True:
            info = DXGI_OUTDUPL_FRAME_INFO()
            resource = ctypes.c_void_p()
            acquire = _method(self.duplication, DUPL_ACQUIRE_NEXT_FRAME, ctypes.c_long,
                              ctypes.c_uint, ctypes.POINTER(DXGI_OUTDUPL_FRAME_INFO),
                              ctypes.POINTER(ctypes.c_void_p))
            result = acquire(self.duplication, timeout_ms, ctypes.byref(info),
                             ctypes.byref(resource))

            if result == DXGI_ERROR_ACCESS_LOST:
                # A lock screen, a mode change or another program taking the display.
                # The cached frame is from before that, so returning it would hand back
                # a picture of a screen that no longer exists.
                self.close()
                self._last = None
                self._open()
                continue
            if result == DXGI_ERROR_WAIT_TIMEOUT:
                fresh = False
            elif result != S_OK:
                raise ActionError("could not take a frame from the display "
                                  f"(0x{result & 0xFFFFFFFF:08X})")
            else:
                fresh = info.AccumulatedFrames > 0
                try:
                    if fresh:
                        texture = _query(resource, IID_ID3D11Texture2D)
                        try:
                            self._last = self._read(texture)
                        finally:
                            _release(texture)
                finally:
                    _release(resource)
                    _method(self.duplication, DUPL_RELEASE_FRAME,
                            ctypes.c_long)(self.duplication)

            if fresh or self._last is not None:
                return self._last
            if time.monotonic() > deadline:
                raise ActionError("this display has not presented a frame to copy")

    def _read(self, texture) -> Image.Image:
        source = D3D11_TEXTURE2D_DESC()
        _method(texture, TEXTURE_GET_DESC, None,
                ctypes.POINTER(D3D11_TEXTURE2D_DESC))(texture, ctypes.byref(source))
        if source.Format not in BGRA_FORMATS:
            raise ActionError(f"this display composites in an unsupported pixel format "
                              f"({source.Format})")
        width, height = source.Width, source.Height
        staging = self._staging_for(width, height, source.Format)

        copy = _method(self.context, CONTEXT_COPY_RESOURCE, None,
                       ctypes.c_void_p, ctypes.c_void_p)
        copy(self.context, staging, texture)

        mapped = D3D11_MAPPED_SUBRESOURCE()
        map_call = _method(self.context, CONTEXT_MAP, ctypes.c_long,
                           ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                           ctypes.POINTER(D3D11_MAPPED_SUBRESOURCE))
        if map_call(self.context, staging, 0, D3D11_MAP_READ, 0,
                    ctypes.byref(mapped)) != S_OK:
            raise ActionError("could not read the captured frame")
        try:
            # Rows are padded to the texture's pitch, which is rarely width * 4.
            raw = ctypes.string_at(mapped.pData, mapped.RowPitch * height)
            image = Image.frombuffer("RGB", (width, height), raw, "raw", "BGRX",
                                     mapped.RowPitch, 1)
            return image.copy()  # detach from the buffer before it is unmapped
        finally:
            _method(self.context, CONTEXT_UNMAP, None, ctypes.c_void_p,
                    ctypes.c_uint)(self.context, staging, 0)

    def _staging_for(self, width: int, height: int, pixel_format: int):
        """A CPU-readable texture matching the frame. Copying between two different
        formats or sizes silently leaves the destination untouched, which reads back as
        a black screenshot, so this mirrors the source exactly."""
        if self._staging is not None and self._staging_size == (width, height, pixel_format):
            return self._staging
        if self._staging is not None:
            # Forget it before the release, so a failure below cannot leave a pointer
            # behind that the next call would release a second time.
            stale, self._staging, self._staging_size = self._staging, None, None
            _release(stale)
        desc = D3D11_TEXTURE2D_DESC(
            Width=width, Height=height, MipLevels=1, ArraySize=1,
            Format=pixel_format,
            SampleDesc=DXGI_SAMPLE_DESC(1, 0), Usage=D3D11_USAGE_STAGING,
            BindFlags=0, CPUAccessFlags=D3D11_CPU_ACCESS_READ, MiscFlags=0)
        texture = ctypes.c_void_p()
        create = _method(self.device, DEVICE_CREATE_TEXTURE2D, ctypes.c_long,
                         ctypes.POINTER(D3D11_TEXTURE2D_DESC), ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_void_p))
        if create(self.device, ctypes.byref(desc), None,
                  ctypes.byref(texture)) != S_OK or not texture:
            raise ActionError("could not allocate a readable copy of the frame")
        self._staging, self._staging_size = texture, (width, height, pixel_format)
        return texture

    def close(self) -> None:
        for handle in ("_staging", "duplication", "context", "device"):
            pointer = getattr(self, handle, None)
            if pointer:
                _release(pointer)
            setattr(self, handle, None if handle == "_staging" else ctypes.c_void_p())
        self._staging_size = None

    def __del__(self):
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter shutdown
            pass
