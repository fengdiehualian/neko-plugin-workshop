"""DXGI Desktop Duplication capture, implemented directly with ``ctypes``.

Why hand-rolled
---------------
The host ships ``dxcam``, but ``dxcam`` needs **cv2** for its colour conversion
and OpenCV is not present in the frozen interpreter (and installing packages
there is forbidden by the plugin format).  Desktop Duplication is the only
capture path that can see an **exclusive-fullscreen** swap chain, which is how
ICEY runs by default, so the capability is worth having without the broken
dependency.  The GDI backend cannot substitute: it returns a black image for an
exclusive-fullscreen game.

Everything here is stdlib ``ctypes`` + ``numpy``.

ABI correctness
---------------
The COM vtable slot numbers below were read out of the Windows SDK headers
(``dxgi.h`` / ``dxgi1_2.h`` / ``d3d11.h``) rather than from memory, because
guessing them is how this kind of code dies with an access violation.  Two are
worth calling out because they are easy to get wrong:

* ``IDXGIOutput1::DuplicateOutput`` is the **last** of that interface's four
  methods (after ``GetDisplayModeList1``, ``FindClosestMatchingMode1`` and
  ``GetDisplaySurfaceData1``) — slot **22**, not 19.
* ``IDXGIOutputDuplication`` declares ``GetFrameDirtyRects`` *before*
  ``GetFrameMoveRects`` and includes ``GetFramePointerShape`` /
  ``MapDesktopSurface`` / ``UnMapDesktopSurface``, so ``ReleaseFrame`` is slot
  **14**, not 11.

Value-level constants that the headers define only inside enums
(``D3D11_USAGE_STAGING`` and friends) carry their documented values.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Any

try:  # pragma: no cover - import environment dependent
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

__all__ = ["DXGI_AVAILABLE", "DxgiDuplicationCapturer", "describe_availability"]

_IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

D3D11_SDK_VERSION = 7
D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1
DXGI_FORMAT_B8G8R8A8_UNORM = 87

S_OK = 0
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = 0x887A0022
DXGI_ERROR_UNSUPPORTED = 0x887A0004

#: Interface ids, transcribed from the SDK headers (DEFINE_GUID).
IID_IDXGIDevice = "54ec77fa-1377-44e6-8c32-88fd5f44c84c"
IID_IDXGIOutput1 = "00cddea8-939b-4b83-a340-a685226666cc"
IID_ID3D11Texture2D = "6f15aaf2-d208-4e89-9ab4-489535d34f9c"

#: Verified vtable slots.
SLOT_QUERYINTERFACE = 0
SLOT_RELEASE = 2
SLOT_IDXGIDEVICE_GETADAPTER = 7
SLOT_IDXGIADAPTER_ENUMOUTPUTS = 7
SLOT_IDXGIOUTPUT1_DUPLICATEOUTPUT = 22
SLOT_DUPLICATION_GETDESC = 7
SLOT_DUPLICATION_ACQUIRENEXTFRAME = 8
SLOT_DUPLICATION_RELEASEFRAME = 14
SLOT_ID3D11DEVICE_CREATETEXTURE2D = 5   # ID3D11Device: ... CreateTexture2D
SLOT_ID3D11CONTEXT_COPYRESOURCE = 47    # ID3D11DeviceContext::CopyResource
SLOT_ID3D11CONTEXT_MAP = 14             # ID3D11DeviceContext::Map
SLOT_ID3D11CONTEXT_UNMAP = 15           # ID3D11DeviceContext::Unmap


class DxgiUnavailable(RuntimeError):
    """Desktop Duplication could not be initialised on this machine."""


def _guid(text: str):
    """Build a GUID ctypes buffer from ``xxxxxxxx-xxxx-...`` text."""
    import uuid

    parsed = uuid.UUID(text)
    fields = (parsed.time_low, parsed.time_mid, parsed.time_hi_version,
              *parsed.bytes[8:])
    return (ctypes.c_byte * 16).from_buffer_copy(
        ctypes.c_uint32(fields[0]).value.to_bytes(4, "little")
        + ctypes.c_uint16(fields[1]).value.to_bytes(2, "little")
        + ctypes.c_uint16(fields[2]).value.to_bytes(2, "little")
        + bytes(fields[3:]))


def _hr(value: int) -> int:
    """Interpret an HRESULT as a signed 32-bit value."""
    return ctypes.c_long(value).value


def _hresult_text(hr: int) -> str:
    names = {
        DXGI_ERROR_WAIT_TIMEOUT: "DXGI_ERROR_WAIT_TIMEOUT",
        DXGI_ERROR_ACCESS_LOST: "DXGI_ERROR_ACCESS_LOST",
        DXGI_ERROR_NOT_CURRENTLY_AVAILABLE: "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE",
        DXGI_ERROR_UNSUPPORTED: "DXGI_ERROR_UNSUPPORTED",
    }
    return names.get(hr & 0xFFFFFFFF, f"0x{hr & 0xFFFFFFFF:08X}")


# ---------------------------------------------------------------------------
# Structures (layouts taken from the headers)
# ---------------------------------------------------------------------------


class DXGI_MODE_DESC(ctypes.Structure):
    _fields_ = [
        ("Width", wintypes.UINT),
        ("Height", wintypes.UINT),
        ("RefreshRateNumerator", wintypes.UINT),
        ("RefreshRateDenominator", wintypes.UINT),
        ("Format", wintypes.UINT),
        ("ScanlineOrdering", wintypes.UINT),
        ("Scaling", wintypes.UINT),
    ]


class DXGI_OUTDUPL_DESC(ctypes.Structure):
    _fields_ = [
        ("ModeDesc", DXGI_MODE_DESC),
        ("Rotation", wintypes.UINT),
        ("DesktopImageInSystemMemory", wintypes.BOOL),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DXGI_OUTDUPL_POINTER_POSITION(ctypes.Structure):
    _fields_ = [("Position", POINT), ("Visible", wintypes.BOOL)]


class LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
    _fields_ = [
        ("LastPresentTime", LARGE_INTEGER),
        ("LastMouseUpdateTime", LARGE_INTEGER),
        ("AccumulatedFrames", wintypes.UINT),
        ("RectsCoalesced", wintypes.BOOL),
        ("ProtectedContentMaskedOut", wintypes.BOOL),
        ("PointerPosition", DXGI_OUTDUPL_POINTER_POSITION),
        ("TotalMetadataBufferSize", wintypes.UINT),
        ("PointerShapeBufferSize", wintypes.UINT),
    ]


class DXGI_SAMPLE_DESC(ctypes.Structure):
    _fields_ = [("Count", wintypes.UINT), ("Quality", wintypes.UINT)]


class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [
        ("Width", wintypes.UINT),
        ("Height", wintypes.UINT),
        ("MipLevels", wintypes.UINT),
        ("ArraySize", wintypes.UINT),
        ("Format", wintypes.UINT),
        ("SampleDesc", DXGI_SAMPLE_DESC),
        ("Usage", wintypes.UINT),
        ("BindFlags", wintypes.UINT),
        ("CPUAccessFlags", wintypes.UINT),
        ("MiscFlags", wintypes.UINT),
    ]


class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [
        ("pData", ctypes.c_void_p),
        ("RowPitch", wintypes.UINT),
        ("DepthPitch", wintypes.UINT),
    ]


#: Layout self-checks: if these fail, ctypes and the headers disagree and the
#: rest of this module cannot be trusted.
_LAYOUT_EXPECTATIONS = {
    "D3D11_TEXTURE2D_DESC": (D3D11_TEXTURE2D_DESC, 44),
    "D3D11_MAPPED_SUBRESOURCE": (D3D11_MAPPED_SUBRESOURCE, 16),
    "DXGI_MODE_DESC": (DXGI_MODE_DESC, 28),
    "DXGI_OUTDUPL_DESC": (DXGI_OUTDUPL_DESC, 36),
}


def layout_problems() -> list[str]:
    problems = []
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        problems.append("this module is written for 64-bit Windows")
    for name, (struct, expected) in _LAYOUT_EXPECTATIONS.items():
        actual = ctypes.sizeof(struct)
        if actual != expected:
            problems.append(f"{name} sizeof={actual}, expected {expected}")
    return problems


def describe_availability() -> dict[str, Any]:
    return {
        "platform": sys.platform,
        "numpy": np is not None,
        "layout_problems": layout_problems(),
        "module_importable": _IS_WINDOWS and np is not None,
    }


DXGI_AVAILABLE = _IS_WINDOWS and np is not None


# ---------------------------------------------------------------------------
# COM helpers
# ---------------------------------------------------------------------------


def _vtbl(ptr: int) -> ctypes.POINTER(ctypes.c_void_p):
    """Read an object's vtable pointer."""
    return ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]


def _method(ptr: int, slot: int, restype, argtypes):
    """Bind one COM method (a function pointer) out of an object's vtable."""
    table = _vtbl(ptr)
    address = table[slot]
    if not address:
        raise DxgiUnavailable(f"vtable slot {slot} is null")
    proto = ctypes.WINFUNCTYPE(restype, *argtypes)
    return proto(address)


def _release(ptr: int | None) -> None:
    if not ptr:
        return
    try:
        _method(ptr, SLOT_RELEASE, ctypes.c_ulong, [ctypes.c_void_p])(ptr)
    except Exception:
        pass


def _query_interface(ptr: int, iid_text: str) -> int:
    iid = _guid(iid_text)
    out = ctypes.c_void_p()
    hr = _method(ptr, SLOT_QUERYINTERFACE, ctypes.c_long,
                 [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])(
        ptr, ctypes.byref(iid), ctypes.byref(out))
    if _hr(hr) != S_OK or not out.value:
        raise DxgiUnavailable(
            f"QueryInterface({iid_text}) failed: {_hresult_text(hr)}")
    return int(out.value)


# ---------------------------------------------------------------------------
# Capturer
# ---------------------------------------------------------------------------


class DxgiDuplicationCapturer:
    """Grabs the desktop through DXGI Desktop Duplication."""

    name = "dxgi"

    def __init__(self, monitor: int = 0, *, acquire_timeout_ms: int = 200,
                 discard_blank_first_frames: int = 6) -> None:
        if not DXGI_AVAILABLE:
            raise DxgiUnavailable(f"DXGI capture unavailable: {describe_availability()}")
        problems = layout_problems()
        if problems:
            raise DxgiUnavailable("struct layout mismatch: " + "; ".join(problems))

        self._monitor = int(monitor)
        self._acquire_timeout_ms = max(1, int(acquire_timeout_ms))
        self._discard_budget = max(0, int(discard_blank_first_frames))
        self._discards_left = 0
        self._device = None
        self._context = None
        self._duplication = None
        self._staging = None
        self._width = 0
        self._height = 0
        self._closed = False
        self._rotation = 0
        self._blank_discarded = 0
        self.reacquires = 0
        self.frames = 0
        self.last_error = ""
        self._init()

    # -- setup ------------------------------------------------------------

    def _init(self) -> None:
        d3d11 = ctypes.WinDLL("d3d11", use_last_error=True)
        self._d3d11 = d3d11
        device = ctypes.c_void_p()
        context = ctypes.c_void_p()
        feature_level = wintypes.UINT()
        hr = d3d11.D3D11CreateDevice(
            None, D3D_DRIVER_TYPE_HARDWARE, None, 0, None, 0,
            D3D11_SDK_VERSION, ctypes.byref(device), ctypes.byref(feature_level),
            ctypes.byref(context))
        if _hr(hr) != S_OK or not device.value:
            raise DxgiUnavailable(
                f"D3D11CreateDevice failed: {_hresult_text(hr)}. Desktop Duplication "
                "needs a working D3D11 device; virtual/RDP displays often refuse."
            )
        self._device = int(device.value)
        self._context = int(context.value) if context.value else None
        self._create_duplication()

    def _create_duplication(self) -> None:
        dxgi_device = _query_interface(self._device, IID_IDXGIDevice)
        adapter = ctypes.c_void_p()
        try:
            hr = _method(dxgi_device, SLOT_IDXGIDEVICE_GETADAPTER, ctypes.c_long,
                         [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])(
                dxgi_device, ctypes.byref(adapter))
            if _hr(hr) != S_OK or not adapter.value:
                raise DxgiUnavailable(f"IDXGIDevice::GetAdapter failed: {_hresult_text(hr)}")

            output = ctypes.c_void_p()
            hr = _method(int(adapter.value), SLOT_IDXGIADAPTER_ENUMOUTPUTS, ctypes.c_long,
                         [ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)])(
                int(adapter.value), self._monitor, ctypes.byref(output))
            if _hr(hr) != S_OK or not output.value:
                raise DxgiUnavailable(
                    f"IDXGIAdapter::EnumOutputs({self._monitor}) failed: "
                    f"{_hresult_text(hr)} — no such monitor")

            output1 = _query_interface(int(output.value), IID_IDXGIOutput1)
            _release(int(output.value))
            try:
                duplication = ctypes.c_void_p()
                hr = _method(output1, SLOT_IDXGIOUTPUT1_DUPLICATEOUTPUT, ctypes.c_long,
                             [ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_void_p)])(
                    output1, ctypes.c_void_p(self._device), ctypes.byref(duplication))
                if _hr(hr) != S_OK or not duplication.value:
                    raise DxgiUnavailable(
                        f"IDXGIOutput1::DuplicateOutput failed: {_hresult_text(hr)}"
                        + (" (access lost)" if (hr & 0xFFFFFFFF) == DXGI_ERROR_ACCESS_LOST
                           else " (already in use / unsupported on this adapter)"
                           if (hr & 0xFFFFFFFF) in (DXGI_ERROR_NOT_CURRENTLY_AVAILABLE,
                                                    DXGI_ERROR_UNSUPPORTED)
                           else ""))
                self._duplication = int(duplication.value)
            finally:
                _release(output1)
        finally:
            _release(dxgi_device)
            _release(int(adapter.value) if adapter.value else None)

        desc = DXGI_OUTDUPL_DESC()
        _method(self._duplication, SLOT_DUPLICATION_GETDESC, None,
                [ctypes.c_void_p, ctypes.POINTER(DXGI_OUTDUPL_DESC)])(
            self._duplication, ctypes.byref(desc))
        self._width = int(desc.ModeDesc.Width)
        self._height = int(desc.ModeDesc.Height)
        self._rotation = int(desc.Rotation)
        if self._width <= 0 or self._height <= 0:
            raise DxgiUnavailable("duplication reported a zero-sized output")
        self._create_staging()
        # A freshly created duplication hands over one all-black frame before
        # the real desktop image, so the first grab after (re)acquiring must not
        # be trusted. Measured on the reference machine: frame 0 mean=0.00,
        # frame 1 mean=246.09. Without this the autopilot would treat a healthy
        # capture as "blind" every time the duplication is recreated.
        self._discards_left = self._discard_budget

    def _create_staging(self) -> None:
        _release(self._staging)
        self._staging = None
        desc = D3D11_TEXTURE2D_DESC()
        desc.Width = self._width
        desc.Height = self._height
        desc.MipLevels = 1
        desc.ArraySize = 1
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM
        desc.SampleDesc.Count = 1
        desc.SampleDesc.Quality = 0
        desc.Usage = D3D11_USAGE_STAGING
        desc.BindFlags = 0
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ
        desc.MiscFlags = 0
        texture = ctypes.c_void_p()
        hr = _method(self._device, SLOT_ID3D11DEVICE_CREATETEXTURE2D, ctypes.c_long,
                     [ctypes.c_void_p, ctypes.POINTER(D3D11_TEXTURE2D_DESC),
                      ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])(
            self._device, ctypes.byref(desc), None, ctypes.byref(texture))
        if _hr(hr) != S_OK or not texture.value:
            raise DxgiUnavailable(f"CreateTexture2D(staging) failed: {_hresult_text(hr)}")
        self._staging = int(texture.value)

    # -- capture ----------------------------------------------------------

    def grab(self, region: tuple[int, int, int, int] | None = None):
        """Return an ``(h, w, 3)`` RGB uint8 array, or ``None`` if nothing new.

        ``None`` is not an error: Desktop Duplication only hands over a frame
        when the desktop actually changed, so a static screen legitimately
        yields nothing.  It is also returned for the guaranteed all-black first
        frame after a (re)acquire, which is discarded rather than reported.
        """
        frame = self._grab_once(region)
        # A freshly created duplication emits one black frame first. Consume a
        # bounded number of them so callers never see "working capture, black
        # image" — that state is indistinguishable from a real failure and would
        # make the autopilot refuse to play.
        guard = 0
        while (frame is not None and self._discards_left > 0
               and float(frame.mean()) < 1.0 and guard < self._discard_budget):
            self._discards_left -= 1
            self._blank_discarded += 1
            guard += 1
            time.sleep(0.03)
            frame = self._grab_once(region)
        return frame

    def _grab_once(self, region: tuple[int, int, int, int] | None = None):
        if self._closed:
            raise DxgiUnavailable("capturer is closed")
        if self._duplication is None:
            self._create_duplication()

        info = DXGI_OUTDUPL_FRAME_INFO()
        resource = ctypes.c_void_p()
        hr = _method(self._duplication, SLOT_DUPLICATION_ACQUIRENEXTFRAME, ctypes.c_long,
                     [ctypes.c_void_p, wintypes.UINT,
                      ctypes.POINTER(DXGI_OUTDUPL_FRAME_INFO),
                      ctypes.POINTER(ctypes.c_void_p)])(
            self._duplication, self._acquire_timeout_ms, ctypes.byref(info),
            ctypes.byref(resource))
        status = hr & 0xFFFFFFFF

        if status == DXGI_ERROR_WAIT_TIMEOUT:
            return None
        if status == DXGI_ERROR_ACCESS_LOST or _hr(hr) != S_OK or not resource.value:
            self.last_error = f"AcquireNextFrame: {_hresult_text(hr)}"
            self._reset_duplication()
            if status == DXGI_ERROR_ACCESS_LOST:
                return None
            raise DxgiUnavailable(self.last_error)

        try:
            source = _query_interface(int(resource.value), IID_ID3D11Texture2D)
            try:
                _method(self._context, SLOT_ID3D11CONTEXT_COPYRESOURCE, None,
                        [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p])(
                    self._context, ctypes.c_void_p(self._staging), ctypes.c_void_p(source))
                mapped = D3D11_MAPPED_SUBRESOURCE()
                hr2 = _method(self._context, SLOT_ID3D11CONTEXT_MAP, ctypes.c_long,
                              [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                               wintypes.UINT, wintypes.UINT,
                               ctypes.POINTER(D3D11_MAPPED_SUBRESOURCE)])(
                    self._context, ctypes.c_void_p(self._staging), 0,
                    D3D11_MAP_READ, 0, ctypes.byref(mapped))
                if _hr(hr2) != S_OK or not mapped.pData:
                    raise DxgiUnavailable(f"Map(staging) failed: {_hresult_text(hr2)}")
                try:
                    frame = self._read_rows(mapped)
                finally:
                    _method(self._context, SLOT_ID3D11CONTEXT_UNMAP, None,
                            [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT])(
                        self._context, ctypes.c_void_p(self._staging), 0)
            finally:
                _release(source)
        finally:
            _method(self._duplication, SLOT_DUPLICATION_RELEASEFRAME, ctypes.c_long,
                    [ctypes.c_void_p])(self._duplication)

        self.frames += 1
        if region is not None:
            left, top, right, bottom = (int(v) for v in region)
            left = max(0, min(self._width - 1, left))
            top = max(0, min(self._height - 1, top))
            right = max(left + 1, min(self._width, right))
            bottom = max(top + 1, min(self._height, bottom))
            frame = frame[top:bottom, left:right]
        return np.ascontiguousarray(frame)

    def _read_rows(self, mapped: D3D11_MAPPED_SUBRESOURCE):
        """Copy the mapped surface row by row, honouring ``RowPitch``."""
        pitch = int(mapped.RowPitch)
        row_bytes = self._width * 4
        buffer = ctypes.cast(mapped.pData, ctypes.POINTER(ctypes.c_ubyte))
        if pitch == row_bytes:
            raw = ctypes.string_at(mapped.pData, row_bytes * self._height)
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape(
                self._height, self._width, 4)
        else:
            # RowPitch is frequently larger than the row; copying per row is the
            # only correct way to avoid a diagonal skew in the image.
            rows = []
            for y in range(self._height):
                offset = y * pitch
                chunk = bytes(buffer[offset: offset + row_bytes])
                rows.append(np.frombuffer(chunk, dtype=np.uint8).reshape(
                    self._width, 4))
            bgra = np.stack(rows)
        return bgra[:, :, 2::-1]  # BGRA -> RGB

    def _reset_duplication(self) -> None:
        self.reacquires += 1
        _release(self._duplication)
        self._duplication = None
        _release(self._staging)
        self._staging = None
        try:
            self._create_duplication()
        except Exception as exc:
            self.last_error = f"re-acquire failed: {exc}"
            self._duplication = None

    # -- lifecycle --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "backend": "dxgi-ctypes",
            "output_px": [self._width, self._height],
            "monitor": self._monitor,
            # Reported for diagnostics only. This machine advertises ROTATE90
            # while delivering correctly-oriented pixels (verified pixel-for-
            # pixel against a GDI capture), so the image is NOT rotated here.
            "rotation": self._rotation,
            "frames": self.frames,
            "reacquires": self.reacquires,
            "blank_frames_discarded": self._blank_discarded,
            "duplication": self._duplication is not None,
            "last_error": self.last_error,
            "layout_problems": layout_problems(),
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _release(self._staging)
        _release(self._duplication)
        _release(self._context)
        _release(self._device)
        self._staging = None
        self._duplication = None
        self._context = None
        self._device = None


def smoke_test(seconds: float = 5.0) -> dict[str, Any]:
    """Local diagnostic: try to produce one frame. Used by the self-check."""
    result: dict[str, Any] = {"available": DXGI_AVAILABLE,
                              "availability": describe_availability()}
    capturer = None
    started = time.time()
    try:
        capturer = DxgiDuplicationCapturer(0)
        result["status"] = capturer.status()
        attempts = 0
        frame = None
        while time.time() - started < seconds:
            attempts += 1
            frame = capturer.grab()
            if frame is not None:
                break
            time.sleep(0.05)
        if frame is None:
            result["error"] = "no frame within the timeout (desktop never changed?)"
        else:
            result["attempts"] = attempts
            result["elapsed"] = round(time.time() - started, 2)
            result["shape"] = list(frame.shape)
            result["dtype"] = str(frame.dtype)
            result["mean_rgb"] = [round(float(frame[:, :, i].mean()), 2) for i in range(3)]
            result["non_black"] = bool(frame.mean() > 5.0)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if capturer is not None:
            capturer.close()
    return result


if __name__ == "__main__":  # pragma: no cover - manual diagnostic
    import json

    print(json.dumps(smoke_test(), indent=2, ensure_ascii=False))
