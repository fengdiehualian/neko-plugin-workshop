"""Screen capture for ICEY with a layered backend strategy.

The host interpreter does **not** ship ``mss`` / ``opencv`` / ``pyautogui``.
What it does ship is ``numpy``, ``Pillow`` and (as a compiled extension)
``dxcam``.  None of those is guaranteed to be importable from inside a frozen
plugin process, so this module tries backends in order and remembers the first
one that actually produced a frame:

1. ``dxgi``  - Desktop Duplication via ``dxcam``.  The only backend that can
   see an *exclusive fullscreen* D3D swap chain, which is how ICEY runs for
   this machine's saved settings (``Is Fullscreen mode = 1``).
2. ``gdi``   - ``BitBlt`` from the desktop DC through ``ctypes``.  Pure
   stdlib, always importable, works for windowed and borderless games.
3. ``pil``   - ``PIL.ImageGrab``.  Same GDI surface underneath, useful when
   ``Pillow`` is present but the raw DC path is blocked.

All backends return a top-down ``numpy`` ``uint8`` ``(h, w, 3)`` RGB array, so
``vision`` never has to care which one ran.
"""

from __future__ import annotations

import ctypes
import io
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Iterable

try:  # pragma: no cover - import environment dependent
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

__all__ = [
    "Frame",
    "ScreenCapturer",
    "available_backends",
    "encode_jpeg",
    "shrink",
]

_PREFERRED_ORDER = ("dxgi", "gdi", "pil")


@dataclass
class Frame:
    """One captured frame plus the metadata the rest of the plugin needs."""

    image: Any  # numpy.ndarray (h, w, 3) uint8, RGB, top-down
    captured_at: float
    backend: str
    region: tuple[int, int, int, int] | None = None

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    def summary(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "width": self.width,
            "height": self.height,
            "region": list(self.region) if self.region else None,
            "age_seconds": round(max(0.0, time.time() - self.captured_at), 2),
        }


def available_backends() -> dict[str, bool]:
    """Report which capture backends could be imported in this interpreter."""
    result = {"dxgi": False, "gdi": sys.platform == "win32", "pil": False,
              "numpy": np is not None}
    # dxgi: our own ctypes implementation. Check the module and its struct
    # layouts only — actually constructing it creates a D3D11 device, which is
    # far too heavy for a status call. A real failure surfaces in probe()/capture().
    try:
        from . import dxgi_capture

        result["dxgi"] = bool(dxgi_capture.DXGI_AVAILABLE
                              and not dxgi_capture.layout_problems())
    except Exception:
        result["dxgi"] = False
    try:
        from PIL import ImageGrab

        result["pil"] = bool(ImageGrab)
    except Exception:
        result["pil"] = False
    return result


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class _DxgiBackend:
    """Desktop Duplication capture, backed by our own ``ctypes`` implementation.

    This deliberately does **not** use the host's ``dxcam``: ``dxcam`` needs
    ``cv2`` for its colour conversion, OpenCV is absent from the frozen host
    interpreter, and installing it there is forbidden.  ``dxgi_capture.py``
    speaks the same DXGI APIs directly with ``ctypes`` + ``numpy``, so the one
    backend that can see an exclusive-fullscreen swap chain no longer depends on
    a package that cannot load.  Verified on the reference machine: the frame
    matches a GDI capture pixel for pixel at 1920x1080.

    DPI caveat: DXGI always reports *physical* pixels, while ``GetWindowRect``
    returns *virtualised* pixels when the process is DPI-unaware.  On a 125%
    display that is a 1920-vs-1536 mismatch, and cropping a physical frame with
    a virtualised rectangle silently captures the wrong part of the screen.  The
    backend therefore measures the ratio once and scales the requested region.
    """

    name = "dxgi"

    def __init__(self, monitor: int = 0) -> None:
        from .dxgi_capture import DxgiDuplicationCapturer

        self._capturer = DxgiDuplicationCapturer(monitor)
        self._scale: tuple[float, float] = (1.0, 1.0)
        self._bounds: tuple[int, int] | None = None
        self._measure_scale()

    def _measure_scale(self) -> None:
        try:
            output = self._capturer.status().get("output_px") or (0, 0)
            width, height = int(output[0]), int(output[1])
        except Exception:
            return
        if not width or not height:
            return
        self._bounds = (width, height)
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            virtual_w = int(user32.GetSystemMetrics(0))
            virtual_h = int(user32.GetSystemMetrics(1))
        except Exception:
            return
        if virtual_w <= 0 or virtual_h <= 0:
            return
        sx = width / float(virtual_w)
        sy = height / float(virtual_h)
        if 0.5 <= sx <= 2.0 and 0.5 <= sy <= 2.0 and (
                abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02):
            self._scale = (sx, sy)

    def _scale_region(self, region):
        if region is None:
            return None
        sx, sy = self._scale
        left, top, right, bottom = region
        scaled = (
            int(round(left * sx)), int(round(top * sy)),
            int(round(right * sx)), int(round(bottom * sy)),
        )
        bounds = self._bounds
        if bounds is not None:
            width, height = bounds
            scaled = (
                max(0, min(width - 1, scaled[0])), max(0, min(height - 1, scaled[1])),
                max(1, min(width, scaled[2])), max(1, min(height, scaled[3])),
            )
        if scaled[2] <= scaled[0] or scaled[3] <= scaled[1]:
            return None
        return scaled

    def grab(self, region: tuple[int, int, int, int] | None):
        # ``None`` means "the desktop did not change" (or the guaranteed blank
        # first frame was discarded) — normal, not an error.
        return self._capturer.grab(self._scale_region(region))

    def status(self) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        try:
            detail = dict(self._capturer.status())
        except Exception:
            pass
        detail["region_scale"] = list(self._scale)
        return detail

    def close(self) -> None:
        try:
            self._capturer.close()
        except Exception:
            pass


class _GdiBackend:
    """``BitBlt`` from the desktop DC, via ``ctypes``.  Stdlib only."""

    name = "gdi"

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        u, g = self._user32, self._gdi32
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        u.ReleaseDC.restype = ctypes.c_int
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.BitBlt.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
        ]
        g.BitBlt.restype = wintypes.BOOL
        g.GetDIBits.argtypes = [
            wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
        ]
        g.GetDIBits.restype = ctypes.c_int
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteObject.restype = wintypes.BOOL
        g.DeleteDC.argtypes = [wintypes.HDC]
        g.DeleteDC.restype = wintypes.BOOL

    def grab(self, region: tuple[int, int, int, int] | None):
        if np is None:
            raise RuntimeError("numpy is required for the gdi capture backend")
        if region is None:
            # Virtual screen covers every monitor; SM_XVIRTUALSCREEN etc.
            x = self._user32.GetSystemMetrics(76)
            y = self._user32.GetSystemMetrics(77)
            cx = self._user32.GetSystemMetrics(78)
            cy = self._user32.GetSystemMetrics(79)
        else:
            x, y, x2, y2 = region
            cx, cy = x2 - x, y2 - y
        if cx <= 0 or cy <= 0:
            raise RuntimeError(f"invalid capture region: {(x, y, cx, cy)}")

        screen_dc = self._user32.GetDC(None)
        if not screen_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        mem_dc = bitmap = old = None
        try:
            mem_dc = self._gdi32.CreateCompatibleDC(screen_dc)
            bitmap = self._gdi32.CreateCompatibleBitmap(screen_dc, cx, cy)
            if not mem_dc or not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            old = self._gdi32.SelectObject(mem_dc, bitmap)
            if not self._gdi32.BitBlt(mem_dc, 0, 0, cx, cy, screen_dc, x, y, 0x00CC0020):
                raise ctypes.WinError(ctypes.get_last_error())

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
                ]

            header = BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            header.biWidth = cx
            header.biHeight = -cy  # negative => top-down rows
            header.biPlanes = 1
            header.biBitCount = 32
            header.biCompression = 0  # BI_RGB

            buffer = ctypes.create_string_buffer(cx * cy * 4)
            scanned = self._gdi32.GetDIBits(
                mem_dc, bitmap, 0, cy, buffer, ctypes.byref(header), 0)
            if scanned != cy:
                raise ctypes.WinError(ctypes.get_last_error())
            arr = np.frombuffer(buffer.raw, dtype=np.uint8).reshape(cy, cx, 4)
            return np.ascontiguousarray(arr[:, :, 2::-1])  # BGRA -> RGB
        finally:
            if mem_dc and old:
                self._gdi32.SelectObject(mem_dc, old)
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if mem_dc:
                self._gdi32.DeleteDC(mem_dc)
            if screen_dc:
                self._user32.ReleaseDC(None, screen_dc)

    def close(self) -> None:
        return None


class _PilBackend:
    """``PIL.ImageGrab`` fallback."""

    name = "pil"

    def __init__(self) -> None:
        from PIL import ImageGrab  # noqa: F401  (import check)

        self._grab = ImageGrab.grab

    def grab(self, region: tuple[int, int, int, int] | None):
        if np is None:
            raise RuntimeError("numpy is required for the pil capture backend")
        kwargs: dict[str, Any] = {"all_screens": True}
        if region is not None:
            kwargs["bbox"] = region
        image = self._grab(**kwargs)
        if image.mode != "RGB":
            image = image.convert("RGB")
        return np.asarray(image, dtype=np.uint8)

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------
# Facade
# --------------------------------------------------------------------------


class ScreenCapturer:
    """Tries backends in order and sticks with the first one that works.

    Failure handling is the subtle part.  A backend can fail in two different
    places, and only one of them is visible at build time:

    * ``_build`` raises — the dependency is not importable at all.
    * ``grab`` raises — the dependency imported, but the device/duplication
      could not be set up (this is how ``dxcam`` fails on a session where
      Desktop Duplication is unavailable).

    The second case used to re-select the same backend on every retry, so the
    capturer spun on a backend that could never work and never reached the
    fallback.  Every backend that raises is therefore *quarantined* by name and
    skipped until the retry window elapses, which also lets a transient failure
    recover (e.g. the game switching to exclusive fullscreen).
    """

    def __init__(
        self,
        *,
        logger=None,
        monitor: int = 0,
        preferred: str = "auto",
        retry_failed_after_seconds: float = 30.0,
    ) -> None:
        self._logger = logger
        self._monitor = int(monitor)
        self._preferred = (preferred or "auto").strip().lower()
        self._backend = None
        self._backend_name = ""
        self.last_error = ""
        self.frames_captured = 0
        #: ``name -> reason`` for backends quarantined by a failure.
        self._failed: dict[str, str] = {}
        self._failed_at = 0.0
        self.retry_failed_after_seconds = float(retry_failed_after_seconds)

    # -- lifecycle --------------------------------------------------------

    def _order(self) -> tuple[str, ...]:
        """Candidate backends, preferred first, quarantined ones excluded."""
        if self._preferred in _PREFERRED_ORDER:
            # An explicit preference is honoured first but must not make the
            # plugin blind when it cannot work on this machine.
            candidates = (self._preferred,) + tuple(
                name for name in _PREFERRED_ORDER if name != self._preferred)
        else:
            candidates = _PREFERRED_ORDER
        return tuple(name for name in candidates if name not in self._failed)

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            getattr(logger, level)("[capture] " + message, *args)
        except Exception:
            pass

    def _build(self, name: str):
        if name == "dxgi":
            return _DxgiBackend(self._monitor)
        if name == "gdi":
            return _GdiBackend()
        if name == "pil":
            return _PilBackend()
        raise ValueError(f"unknown capture backend: {name}")

    def _ensure_backend(self):
        if self._backend is not None:
            return self._backend
        self._maybe_requarantine_reset()
        errors: list[str] = []
        for name in self._order():
            try:
                backend = self._build(name)
            except Exception as exc:
                self._quarantine(name, exc)
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            self._backend = backend
            self._backend_name = name
            self._log("info", "using {} backend", name)
            return backend
        self.last_error = "; ".join(errors) or self._failure_summary()
        raise RuntimeError(f"no capture backend available ({self.last_error})")

    def _maybe_requarantine_reset(self) -> None:
        """Periodically forgive failures so a transient fault can recover."""
        if not self._failed:
            return
        if self.retry_failed_after_seconds <= 0:
            return
        if (time.time() - self._failed_at) < self.retry_failed_after_seconds:
            return
        self._log("info", "retrying previously failed backend(s) after cooldown: {}",
                  sorted(self._failed))
        self._failed.clear()

    def _quarantine(self, name: str, exc: BaseException) -> None:
        reason = f"{type(exc).__name__}: {exc}"
        first_time = name not in self._failed
        self._failed[name] = reason
        self._failed_at = time.time()
        if first_time:
            # Log the *actual* reason exactly once per quarantine window: a
            # bare "failed; falling back" is what made the original spin
            # impossible to diagnose from the log.
            self._log("warning", "capture backend {} failed ({})", name, reason)

    def _failover(self, backend) -> None:
        """Drop a backend that raised and let the next capture rebuild."""
        name = self._backend_name
        try:
            backend.close()
        except Exception:
            pass
        if name:
            self._quarantine(name, RuntimeError(self.last_error or "grab failed"))
        self._backend = None
        self._backend_name = ""
        remaining = self._order()
        self._log("warning", "backend {} disabled; next candidate: {}",
                  name or "?", remaining[0] if remaining else "none")

    def _failure_summary(self) -> str:
        if not self._failed:
            return "no capture backend was attempted"
        return "; ".join(f"{name}: {reason}" for name, reason in sorted(self._failed.items()))

    # -- capture ----------------------------------------------------------

    def capture(
        self,
        region: tuple[int, int, int, int] | None = None,
        *,
        retries: int = 2,
    ) -> Frame | None:
        """Capture one frame, or ``None`` when nothing was available.

        ``None`` is a normal outcome for the DXGI backend (no desktop change
        since the previous grab), so callers must tolerate it.
        """
        attempts = 0
        while attempts <= max(0, retries):
            attempts += 1
            try:
                backend = self._ensure_backend()
                image = backend.grab(region)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                if self._backend is not None:
                    self._failover(self._backend)
                # Every backend has now been quarantined: report the combined
                # reason instead of silently looping.
                if not self._order():
                    self.last_error = self._failure_summary()
                    return None
                continue
            if image is None:
                return None
            self.frames_captured += 1
            return Frame(
                image=image,
                captured_at=time.time(),
                backend=self._backend_name,
                region=region,
            )
        return None

    def probe(self) -> dict[str, Any]:
        """Try every backend once and report the concrete outcome of each.

        Used by the self-check so a user (or the character) can see *why* a
        backend is unusable instead of guessing at a missing dependency.
        """
        report: dict[str, Any] = {}
        for name in _PREFERRED_ORDER:
            if name == self._backend_name and self._backend is not None:
                report[name] = {"ok": True, "detail": "active backend"}
                continue
            backend = None
            try:
                backend = self._build(name)
                image = backend.grab(None)
                if image is None:
                    report[name] = {"ok": True, "detail": "no new frame (normal for dxgi)"}
                else:
                    shape = getattr(image, "shape", None)
                    report[name] = {"ok": True,
                                    "detail": f"frame {shape[1]}x{shape[0]}" if shape else "frame"}
            except Exception as exc:
                report[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            finally:
                if backend is not None:
                    try:
                        backend.close()
                    except Exception:
                        pass
        return report

    def close(self) -> None:
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                pass
        self._backend = None
        self._backend_name = ""

    def status(self) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        backend_status = getattr(self._backend, "status", None)
        if callable(backend_status):
            try:
                detail = dict(backend_status())
            except Exception:
                detail = {}
        return {
            "backend": self._backend_name or "uninitialised",
            "active": self._backend is not None,
            "frames_captured": self.frames_captured,
            "available": available_backends(),
            "failed_backends": dict(self._failed),
            "candidates_left": list(self._order()),
            "last_error": self.last_error,
            "detail": detail,
        }


# --------------------------------------------------------------------------
# Frame helpers
# --------------------------------------------------------------------------


def shrink(image, max_edge: int):
    """Downscale so the longest edge is at most ``max_edge`` pixels.

    Uses plain slicing when numpy is present (nearest-neighbour, no pillow
    dependency) — a vision model needs neither 4MP nor a resampling kernel.
    """
    if np is None or max_edge <= 0:
        return image
    height, width = image.shape[0], image.shape[1]
    longest = max(height, width)
    if longest <= max_edge:
        return image
    step = int(longest // max_edge) + 1
    return np.ascontiguousarray(image[::step, ::step])


def encode_jpeg(
    image,
    *,
    max_edge: int = 1024,
    quality: int = 75,
    max_bytes: int = 0,
) -> bytes | None:
    """Encode a frame as JPEG, stepping quality/size down to fit ``max_bytes``.

    Returns ``None`` when Pillow is unavailable — the plugin then falls back to
    a textual frame summary instead of pushing pixels.
    """
    try:
        from PIL import Image
    except Exception:
        return None
    if np is None:
        return None
    quality = max(1, min(95, int(quality)))
    edge = max(64, int(max_edge))
    current = image
    for _ in range(4):
        current = shrink(current, edge)
        buffer = io.BytesIO()
        try:
            Image.fromarray(current).save(buffer, format="JPEG", quality=quality)
        except Exception:
            return None
        payload = buffer.getvalue()
        if not max_bytes or len(payload) <= max_bytes:
            return payload
        # Too big: prefer dropping quality before resolution.
        if quality > 45:
            quality -= 15
        else:
            edge = max(64, int(edge * 0.75))
    return payload


def region_from_window(rect: Iterable[int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = (int(v) for v in rect)
    if right <= left or bottom <= top:
        raise ValueError(f"invalid window rect: {(left, top, right, bottom)}")
    return (left, top, right, bottom)
