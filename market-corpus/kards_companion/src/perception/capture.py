"""KARDS window discovery and passive frame capture (Python 3.11).

Backend order (first success wins, with an explicit degradation report):

1. An explicitly configured window-specific host capture bridge (WGC),
   opt-in through ``host_capture_base`` or an environment variable.  Every
   response is validated (success flag, ``source_id``, declared size, image
   data URL).  This path may return frames while the game is backgrounded or
   occluded, but we do *not* claim occlusion support until it is measured.
2. ``dxcam`` desktop duplication.  The old
   ``dxcam.create(region=..., target_fps=...)`` call was invalid;
   ``create()`` accepts device/output/colour options only, so the region is
   passed to ``grab()`` instead.  A desktop grab requires the target to be
   foreground and its client area to be provably unobstructed by known
   visible top-level windows.  A missing new frame is never replaced with a
   stale timestamp.
3. ``PIL.ImageGrab`` as an explicitly *degraded* fallback under the same
   foreground + proven-clear gate.  Unknown occlusion fails closed instead of
   being called trusted.  ``occluded`` stays ``False`` by contract because no
   backend here claims verified occlusion support.

The capture producer can run as a single-call-driven loop (default) or a
background worker (``capture_thread_enabled=True``).  录像帧回调链已随录像
功能移除：生产者只维护 ``latest_capture()`` 供感知循环消费最新帧。

Capture is read-only: it never clicks or types.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageGrab

from ..runtime.health import CaptureHealthSnapshot, evaluate_capture_health
from ..runtime.snapshot import FrameMeta

PROCESS_NAMES = {"kards.exe", "kards-win64-shipping.exe", "kards-win64-shipping-eac.exe"}
KARDS_TITLES = {"kards", "kards™"}
HOST_CAPTURE_ENV_KEYS = (
    "NEKO_CAPTURE_API_BASE",
    "NEKO_HOST_API_BASE",
    "KARDS_CAPTURE_API_BASE",
)
HOST_HEALTH_PATH = "/api/capture/health"
HOST_SCREENSHOT_PATH = "/api/capture/screenshot"
HOST_HEALTH_RETRY_SECONDS = 5.0
MAX_HOST_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_HOST_IMAGE_PIXELS = 50_000_000

# Failure reasons that describe a transient window/process state rather than a
# persistent backend problem.  They must not leak into a healthy/degraded
# snapshot after the window has recovered.
_WINDOW_STATE_REASONS = frozenset(
    {
        "process_not_found",
        "window_not_found",
        "window_minimized",
        "window_not_visible",
        "invalid_client_rect",
    }
)


def is_kards_process(name: str) -> bool:
    """Match KARDS process names, including Steam/EAC and renamed builds."""
    normalized = (name or "").strip().lower()
    return (
        normalized in PROCESS_NAMES
        or normalized.startswith("kards-")
        or normalized.startswith("kards_")
    )


_dpi_aware_done = False


def _ensure_dpi_aware() -> None:
    global _dpi_aware_done
    if _dpi_aware_done:
        return
    _dpi_aware_done = True
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    left: int
    top: int
    right: int
    bottom: int
    pid: int = 0
    process_name: str = ""
    title: str = ""
    minimized: bool = False
    visible: bool = True

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass(frozen=True)
class CaptureResult:
    """One validated frame plus the evidence used to accept it."""

    frame_id: int
    captured_at: float
    width: int
    height: int
    backend: str
    image: Image.Image = field(repr=False, compare=False)
    window: WindowInfo | None = None
    foreground: bool = False
    source: str = ""
    fallback_reason: str = ""
    # True means the frame is validated against its source and, for desktop
    # backends, the target/client area passed the foreground+occlusion gate.
    # It never means "occlusion-proof window capture"; that remains unclaimed.
    trusted: bool = False

    def to_frame_meta(self, valid: bool = True, reason: str = "") -> FrameMeta:
        return FrameMeta(
            frame_id=self.frame_id,
            captured_at=self.captured_at,
            width=self.width,
            height=self.height,
            backend=self.backend,
            valid=bool(valid),
            reason=reason,
        )


# ---------------------------------------------------------------- Win32 probes

def _process_name_for_pid(pid: int) -> str:
    """Resolve an executable basename for ``pid`` (best effort)."""
    if sys.platform != "win32" or pid <= 0:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        # PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1].strip().lower()
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def find_kards_processes() -> list[tuple[int, str]]:
    """Return ``(pid, process_name)`` for running KARDS processes."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snapshot:
            return []
        if int(snapshot) == int(wintypes.HANDLE(-1).value):
            return []
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found: dict[int, str] = {}
        try:
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    pid = int(entry.th32ProcessID)
                    name = str(entry.szExeFile).strip().lower()
                    if pid > 0 and is_kards_process(name):
                        found[pid] = name
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        return sorted(found.items())
    except Exception:
        return []


def _window_title(hwnd: int) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return str(buf.value).strip()[:256]
    except Exception:
        return ""


def _is_dwm_cloaked(hwnd: int) -> bool:
    """True only when DWM positively reports the window as cloaked."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        value = wintypes.DWORD(0)
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        hresult = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(14),  # DWMWA_CLOAKED
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return int(hresult) == 0 and int(value.value) != 0
    except Exception:
        # The visual check below (IsWindowVisible) remains authoritative.
        return False


def _window_rect(hwnd: int, minimized: bool) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if minimized:
            class WINDOWPLACEMENT(ctypes.Structure):
                _fields_ = [
                    ("length", wintypes.UINT),
                    ("flags", wintypes.UINT),
                    ("showCmd", wintypes.UINT),
                    ("ptMinPosition", wintypes.POINT),
                    ("ptMaxPosition", wintypes.POINT),
                    ("rcNormalPosition", wintypes.RECT),
                ]

            placement = WINDOWPLACEMENT()
            placement.length = ctypes.sizeof(WINDOWPLACEMENT)
            user32.GetWindowPlacement.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(WINDOWPLACEMENT),
            ]
            user32.GetWindowPlacement.restype = wintypes.BOOL
            if user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
                rect = placement.rcNormalPosition
                return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        rect = wintypes.RECT()
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None


def _iter_top_level_windows() -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnds: list[int] = []
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd: int, _lparam: int) -> int:
            hwnds.append(int(hwnd))
            return 1

        enum_proc = EnumProc(_cb)
        user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows(enum_proc, 0)
        return hwnds
    except Exception:
        return []


def _enumerate_windows() -> list[WindowInfo]:
    """Enumerate top-level windows owned by KARDS (including hidden/minimized)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    process_names = dict(find_kards_processes())
    found: list[WindowInfo] = []

    for raw_hwnd in _iter_top_level_windows():
        try:
            if not user32.IsWindow(raw_hwnd):
                continue
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(raw_hwnd, ctypes.byref(pid))
            pid_value = int(pid.value)
            title = _window_title(raw_hwnd)
            title_match = title.strip().lower() in KARDS_TITLES
            name = process_names.get(pid_value, "")
            if title_match and not name:
                # Resolve process metadata lazily; avoid OpenProcess for every
                # unrelated top-level window on the desktop.
                name = _process_name_for_pid(pid_value)
            if not (is_kards_process(name) or title_match):
                continue
            if not title:
                # Untitled helper/splash windows are not useful capture targets.
                continue
            visible = bool(user32.IsWindowVisible(raw_hwnd)) and not _is_dwm_cloaked(raw_hwnd)
            minimized = bool(user32.IsIconic(raw_hwnd))
            rect = _window_rect(raw_hwnd, minimized)
            if rect is None:
                continue
            left, top, right, bottom = rect
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                continue
            # The size gate only exists to reject tiny helper windows.
            if not minimized and (width < 800 or height < 450):
                continue
            found.append(
                WindowInfo(
                    hwnd=raw_hwnd,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    pid=pid_value,
                    process_name=name,
                    title=title,
                    minimized=minimized,
                    visible=visible,
                )
            )
        except Exception:
            continue
    return found


def _window_key(window: WindowInfo) -> tuple[int, int, int, int, int]:
    # Prefer a visible, restored window; then largest area.  This prevents a
    # hidden/oversized helper window from shadowing the real game window.
    return (
        int(window.visible and not window.minimized),
        int(window.visible),
        window.width * window.height,
        int(not window.minimized),
        int(window.hwnd),
    )


def find_kards_window(*, windows: list[WindowInfo] | None = None) -> WindowInfo | None:
    """Pick the best KARDS window.

    ``windows`` is an optional test seam; production callers let the function
    enumerate the desktop.  Hidden and minimized windows are returned with
    their metadata so health can report ``visible``/``minimized`` accurately.
    """
    _ensure_dpi_aware()
    candidates = _enumerate_windows() if windows is None else list(windows)
    kards = [
        w
        for w in candidates
        if is_kards_process(w.process_name) or w.title.strip().lower() in KARDS_TITLES
    ]
    if not kards:
        return None
    return max(kards, key=_window_key)


def is_foreground(window: WindowInfo) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground <= 0:
            return False
        target = int(window.hwnd)
        if foreground == target:
            return True
        # A dialog owned by the game still counts as the game being foreground.
        root = int(user32.GetAncestor(foreground, 2) or 0)  # GA_ROOT
        return root == target
    except Exception:
        return False


def client_bbox(window: WindowInfo) -> tuple[int, int, int, int]:
    """Client-area rectangle in physical pixels (title bar/borders removed)."""
    if sys.platform != "win32":
        return (0, 0, 0, 0)
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        if not user32.GetClientRect(window.hwnd, ctypes.byref(rect)):
            return (0, 0, 0, 0)
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return (0, 0, 0, 0)
        point_lt = wintypes.POINT(0, 0)
        point_rb = wintypes.POINT(rect.right, rect.bottom)
        if not user32.ClientToScreen(window.hwnd, ctypes.byref(point_lt)):
            return (0, 0, 0, 0)
        if not user32.ClientToScreen(window.hwnd, ctypes.byref(point_rb)):
            return (0, 0, 0, 0)
        left, top = int(point_lt.x), int(point_lt.y)
        right, bottom = int(point_rb.x), int(point_rb.y)
        if right <= left or bottom <= top:
            return (0, 0, 0, 0)
        return (left, top, right, bottom)
    except Exception:
        return (0, 0, 0, 0)


def is_valid_bbox(bbox: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = bbox
    return right > left and bottom > top


def desktop_client_occluded(
    window: WindowInfo,
    bbox: tuple[int, int, int, int],
) -> bool | None:
    """Check known top-level windows above the target's client area.

    Returns ``True`` when a known, visible, non-minimized, non-cloaked window
    higher in Z-order intersects the client rectangle.  Small overlays are
    intentionally treated the same as large ones: desktop capture pauses
    conservatively instead of guessing.

    Returns ``False`` only when the target is present in the Z-order list and
    no known window above it intersects.  ``None`` means the check could not
    be proven (non-Windows, target missing from enumeration, Win32 error); a
    caller must not claim a trusted desktop frame in that case.
    """
    if not is_valid_bbox(bbox):
        return None
    if sys.platform != "win32":
        return None
    hwnds = _iter_top_level_windows()
    if not hwnds or int(window.hwnd) not in hwnds:
        return None
    target_index = hwnds.index(int(window.hwnd))
    if target_index <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        left, top, right, bottom = bbox
        for other in hwnds[:target_index]:
            try:
                other = int(other)
                if other <= 0 or other == int(window.hwnd):
                    continue
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(other, ctypes.byref(pid))
                other_pid = int(pid.value)
                # The game's own helper/dialog windows are part of the target
                # surface, not a third-party obstruction.
                if window.pid > 0 and other_pid == int(window.pid):
                    continue
                if not bool(user32.IsWindowVisible(other)):
                    continue
                if bool(user32.IsIconic(other)) or _is_dwm_cloaked(other):
                    continue
                rect = _window_rect(other, False)
                if rect is None:
                    continue
                oleft, otop, oright, obottom = rect
                if oright <= oleft or obottom <= otop:
                    continue
                if (
                    oleft < right
                    and oright > left
                    and otop < bottom
                    and obottom > top
                ):
                    return True
            except Exception:
                # A handle can disappear mid-enumeration.  Because it cannot
                # be re-checked, the clear result is no longer provable.
                return None
        return False
    except Exception:
        return None


# ---------------------------------------------------------------- image diffs

def dhash(image: Image.Image, size: int = 8) -> int:
    """numpy dHash (host has no imagehash/cv2)."""
    gray = image.convert("L").resize((size + 1, size), Image.BILINEAR)
    arr = np.asarray(gray, dtype=np.int16)
    bits = (arr[:, 1:] > arr[:, :-1]).ravel()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------------------------------------------------------------- host bridge

def _resolve_host_capture_base(explicit: str | None) -> str:
    """Opt-in only: explicit argument, then documented environment keys."""
    if explicit is not None:
        value = str(explicit).strip()
        if value:
            return value.rstrip("/")
        return ""
    for key in HOST_CAPTURE_ENV_KEYS:
        value = str(os.environ.get(key, "")).strip()
        if value:
            return value.rstrip("/")
    return ""


def _valid_host_base(base: str) -> bool:
    if not base:
        return False
    try:
        parts = urllib.parse.urlsplit(base)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _loopback_host(hostname: str) -> bool:
    """The host capture bridge is local-only; never fetch a remote URL."""
    name = str(hostname or "").strip().lower().rstrip(".")
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _http_json(url: str, *, payload: dict[str, Any] | None = None, timeout: float = 0.25) -> Any:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not _loopback_host(parts.hostname or ""):
        raise ValueError("host capture URL must be loopback")
    data = None
    headers: dict[str, str] = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=float(timeout)) as response:
        status = int(getattr(response, "status", 200))
        if status < 200 or status >= 300:
            raise OSError(f"host capture HTTP {status}")
        raw = response.read(MAX_HOST_RESPONSE_BYTES + 1)
    if len(raw) > MAX_HOST_RESPONSE_BYTES:
        raise ValueError("host capture response too large")
    return json.loads(raw.decode("utf-8", "replace"))


def _unwrap_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    nested = data.get("data")
    if isinstance(nested, dict):
        return nested
    return data


def _explicit_success(payload: dict[str, Any], *, allow_status: bool = True) -> bool:
    for key in ("ok", "success", "succeeded"):
        if key in payload:
            return payload.get(key) is True
    if allow_status:
        return str(payload.get("status", "")).strip().lower() in {
            "ok",
            "success",
            "ready",
            "captured",
            "healthy",
            "available",
        }
    return False


def _normalize_source_id(value: Any) -> str:
    """Return the opaque host ``source_id`` as a comparable string.

    The bridge contract treats ``source_id`` as an opaque token (it may be a
    UUID, path or numeric handle) that is *not* required to equal the HWND.
    """
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    return text[:128]


def _decode_image_data_url(value: Any) -> Image.Image | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    header, separator, encoded = text.partition(",")
    if not separator:
        return None
    lowered = header.lower()
    if not lowered.startswith("data:image/"):
        return None
    if ";base64" not in lowered:
        return None
    compact = "".join(encoded.split())
    if not compact:
        return None
    try:
        raw = base64.b64decode(compact, validate=True)
    except Exception:
        return None
    if not raw or len(raw) > MAX_HOST_RESPONSE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.load()
            if probe.width <= 0 or probe.height <= 0:
                return None
            if probe.width * probe.height > MAX_HOST_IMAGE_PIXELS:
                return None
            return probe.convert("RGB")
    except Exception:
        return None


def _payload_size(payload: dict[str, Any]) -> tuple[int, int] | None:
    width = payload.get("width")
    height = payload.get("height")
    size = payload.get("size")
    if isinstance(size, dict):
        width = width if width is not None else size.get("width")
        height = height if height is not None else size.get("height")
    try:
        width_i, height_i = int(width), int(height)
    except (TypeError, ValueError):
        return None
    if width_i <= 0 or height_i <= 0:
        return None
    return width_i, height_i


# ---------------------------------------------------------------- capture

class ScreenCapture:
    """Single-owner frame capture with health evidence.

    Existing attributes (``fps``, ``latest``, ``latest_hash``, ``window``,
    ``foreground``, ``backend``, ``stats``) are preserved for the
    orchestration layer.  ``health_snapshot()`` and ``latest_capture()`` are
    the new, validated interfaces.  录像专用的 ``on_frame``/``ring`` 链路已
    删除；``ring_enabled`` 参数仅为兼容旧调用保留并被忽略。

    Capture ownership is explicit: by default ``start()`` only marks the
    capture as running and the caller drives :meth:`capture_frame` from its
    own loop.  Set ``capture_thread_enabled=True`` only when no external loop
    captures frames; then the background worker is the sole producer.
    """

    def __init__(
        self,
        *,
        fps: float = 8.0,
        ring_seconds: float = 4.0,
        host_capture_base: str | None = None,
        host_capture_timeout: float = 0.25,
        window_finder: Callable[[], WindowInfo | None] | None = None,
        process_finder: Callable[[], list[tuple[int, str]]] | None = None,
        image_grabber: Callable[..., Image.Image | None] | None = None,
        foreground_probe: Callable[[WindowInfo], bool] | None = None,
        occlusion_probe: Callable[
            [WindowInfo, tuple[int, int, int, int]], bool | None
        ] | None = None,
        ring_enabled: bool = False,
        capture_thread_enabled: bool = False,
    ) -> None:
        del ring_seconds  # JPEG 环形缓冲已随录像功能移除
        self._capture_thread_enabled = bool(capture_thread_enabled)
        self._fps = max(0.5, float(fps))
        self.latest: tuple[float, Image.Image] | None = None
        self.latest_hash: int | None = None
        self.window: WindowInfo | None = None
        self.foreground = False
        self.backend = "imagegrab"
        self.source = ""
        self.fallback_reason = ""
        self.occlusion_state = "unknown"
        self.stats = {
            "frames": 0,
            "errors": 0,
            "fallback_frames": 0,
        }

        # New evidence / health state.
        self.process_found = False
        self.capture_reason = ""
        self.host_bridge_confirmed = False
        self.max_frame_age_ms = self._max_frame_age_for(self._fps)

        self._window_finder = window_finder or find_kards_window
        self._process_finder = process_finder or find_kards_processes
        self._image_grabber = image_grabber or ImageGrab.grab
        self._foreground_probe = foreground_probe or is_foreground
        self._occlusion_probe = occlusion_probe

        self._host_base = _resolve_host_capture_base(host_capture_base)
        self._host_enabled = bool(self._host_base)
        self._host_timeout = min(2.0, max(0.05, float(host_capture_timeout)))
        self._host_health_next_at = 0.0
        self._host_degraded_reason = ""
        # Opaque token learned from /health; screenshots must echo it.
        self._host_source_id = ""

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._running = False
        self._ever_started = False
        self._lock = threading.Lock()
        # Serializes capture attempts so one instance never lets a background
        # worker and a synchronous caller grab/write state at the same time.
        self._capture_lock = threading.Lock()

        self._frame_id = 0
        self._last_frame_ts: float | None = None
        self._latest_result: CaptureResult | None = None
        self._last_frame_trusted = False

        # dxcam has no type stubs; keep the dynamic object boundary here.
        self._dxcam: Any = None
        self._dxcam_failed = False
        self._dxcam_error = ""

    # ------------------------------------------------------------- lifecycle

    @property
    def fps(self) -> float:
        return self._fps

    @fps.setter
    def fps(self, value: float) -> None:
        """Update the producer rate and the derived staleness budget."""
        fps_value = max(0.5, float(value))
        self._fps = fps_value
        self.max_frame_age_ms = self._max_frame_age_for(fps_value)

    @staticmethod
    def _max_frame_age_for(fps: float) -> float:
        return max(500.0, 3000.0 / max(0.5, float(fps)))

    @property
    def capture_thread_enabled(self) -> bool:
        return self._capture_thread_enabled

    def start(self) -> None:
        """Mark capture active, optionally starting the background worker.

        With ``capture_thread_enabled=False`` (the default) no thread is
        created: the orchestration loop calls :meth:`capture_frame` itself, so
        a single producer owns ``latest``/``stats``.  Opting in starts the
        worker; synchronous callers then receive the worker's latest result
        instead of starting a competing capture.
        """
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._running = True
            self._ever_started = True
            if not self._capture_thread_enabled:
                self._thread = None
                return
            thread = threading.Thread(
                target=self._loop, name="kards-capture", daemon=True
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        """Stop the owned worker without discarding captured state.

        An old worker may be joined by a different caller; only the thread
        object that owns the slot may clear it.  ``latest``/``latest_capture``
        are intentionally retained for diagnostics after stop.
        """
        self._stop.set()
        with self._thread_lock:
            thread = self._thread
        current = threading.current_thread()
        if thread is not None and thread.is_alive() and thread is not current:
            thread.join(timeout=2.0)
        with self._thread_lock:
            if self._thread is thread:
                self._thread = None
            self._running = False
        self._dispose_dxcam()

    # ------------------------------------------------------------- host bridge

    @property
    def host_bridge_enabled(self) -> bool:
        return self._host_enabled

    def _host_lost(self, reason: str) -> None:
        """Forget the confirmed association; the next tick re-probes /health."""
        self.host_bridge_confirmed = False
        self._host_degraded_reason = reason
        self._host_source_id = ""

    def _probe_host_health(self, *, now: float) -> bool:
        if not _valid_host_base(self._host_base):
            self._host_lost("host_capture_unavailable")
            self._host_health_next_at = now + HOST_HEALTH_RETRY_SECONDS
            return False
        url = self._host_base + HOST_HEALTH_PATH
        try:
            payload = _unwrap_payload(
                _http_json(url, timeout=self._host_timeout)
            )
        except Exception:
            self._host_lost("host_capture_unavailable")
            self._host_health_next_at = now + HOST_HEALTH_RETRY_SECONDS
            return False
        if payload is None or not _explicit_success(payload):
            self._host_lost("host_capture_unavailable")
            self._host_health_next_at = now + HOST_HEALTH_RETRY_SECONDS
            return False
        source_id = _normalize_source_id(
            payload.get("source_id", payload.get("sourceId")))
        if not source_id:
            self._host_lost("host_capture_invalid_response")
            self._host_health_next_at = now + HOST_HEALTH_RETRY_SECONDS
            return False
        self._host_source_id = source_id
        self.host_bridge_confirmed = True
        self._host_degraded_reason = ""
        self._host_health_next_at = now + HOST_HEALTH_RETRY_SECONDS
        return True

    def _ensure_host_bridge(self, *, now: float) -> bool:
        if self.host_bridge_confirmed:
            return True
        if now < self._host_health_next_at:
            return False
        return self._probe_host_health(now=now)

    def _host_screenshot(self, window: WindowInfo, *, now: float) -> Image.Image | None:
        """Fetch and validate a WGC frame.  Any failure fails closed."""
        if not self._ensure_host_bridge(now=now):
            self.capture_reason = self._host_degraded_reason or "host_capture_unavailable"
            return None
        url = self._host_base + HOST_SCREENSHOT_PATH
        try:
            payload = _unwrap_payload(
                _http_json(
                    url,
                    payload={"target_id": int(window.hwnd)},
                    timeout=self._host_timeout,
                )
            )
        except Exception:
            self._host_invalid("host_capture_unavailable")
            return None
        if payload is None or not _explicit_success(payload):
            self._host_invalid("host_capture_invalid_response")
            return None
        source_id = _normalize_source_id(
            payload.get("source_id", payload.get("sourceId")))
        # ``source_id`` is opaque: bind it to the id advertised by /health
        # instead of comparing it numerically with the HWND.
        if not source_id or not self._host_source_id or source_id != self._host_source_id:
            self._host_invalid("host_capture_source_mismatch")
            return None
        declared = _payload_size(payload)
        if declared is None:
            self._host_invalid("host_capture_invalid_size")
            return None
        image = _decode_image_data_url(
            payload.get("image", payload.get("image_url", payload.get("data_url")))
        )
        if image is None:
            self._host_invalid("host_capture_invalid_image")
            return None
        if image.size != declared:
            self._host_invalid("host_capture_size_mismatch")
            return None
        self.host_bridge_confirmed = True
        self._host_degraded_reason = ""
        self.capture_reason = ""
        return image

    def _host_invalid(self, reason: str) -> None:
        self._host_lost(reason)
        self._host_health_next_at = time.monotonic() + HOST_HEALTH_RETRY_SECONDS
        self.capture_reason = reason

    # ------------------------------------------------------------- dxcam

    def _try_dxcam(self) -> Any:
        if self._dxcam_failed:
            return None
        if self._dxcam is not None:
            self.backend = "dxcam"
            return self._dxcam
        try:
            import dxcam

            # Correct dxcam signature: create() has no region/target_fps.
            # Region is supplied per grab() call below.
            self._dxcam = dxcam.create(output_color="RGB")
            self.backend = "dxcam"
            self._dxcam_error = ""
            return self._dxcam
        except Exception as exc:
            self._disable_dxcam(f"{type(exc).__name__}: {exc}")
            return None

    def _disable_dxcam(self, error: str) -> None:
        self._dispose_dxcam()
        self._dxcam_failed = True
        self._dxcam_error = str(error)[:160]
        self.backend = "imagegrab"

    def _dispose_dxcam(self) -> None:
        camera = self._dxcam
        self._dxcam = None
        if camera is None:
            return
        for name in ("stop", "release", "close"):
            method = getattr(camera, name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass

    # ------------------------------------------------------------- capture

    def _probe_window(self) -> WindowInfo | None:
        window = self._window_finder()
        self.window = window
        if window is not None:
            self.process_found = True
        else:
            try:
                self.process_found = bool(self._process_finder())
            except Exception:
                self.process_found = False
        return window

    def _probe_occlusion(
        self, window: WindowInfo, bbox: tuple[int, int, int, int]
    ) -> bool | None:
        probe = self._occlusion_probe or desktop_client_occluded
        return probe(window, bbox)

    def _desktop_gate(
        self,
        window: WindowInfo,
        bbox: tuple[int, int, int, int],
    ) -> tuple[bool, str]:
        """Require foreground and a proven-clear client area for desktop grabs."""
        if not self.foreground:
            self.occlusion_state = "foreground_required"
            return False, "desktop_requires_foreground"
        try:
            state = self._probe_occlusion(window, bbox)
        except Exception:
            state = None
        if state is None:
            # Unknown cannot be promoted to trusted.  Pausing one tick is
            # safer than recognising text from an unrelated foreground window.
            self.occlusion_state = "unverified"
            return False, "desktop_occlusion_unverified"
        if bool(state):
            self.occlusion_state = "occluded"
            return False, "desktop_client_occluded"
        self.occlusion_state = "clear"
        return True, ""

    def _grab_dxcam(self, bbox: tuple[int, int, int, int]) -> Image.Image | None:
        camera = self._try_dxcam()
        if camera is None:
            return None
        try:
            frame = camera.grab(bbox)
        except Exception as exc:
            self._disable_dxcam(f"{type(exc).__name__}: {exc}")
            return None
        if frame is None:
            # Keep dxcam selected; a missing frame is not a backend failure.
            self.capture_reason = "dxcam_no_frame"
            return None
        try:
            image = Image.fromarray(np.asarray(frame))
            self.capture_reason = ""
            return image
        except Exception as exc:
            self._disable_dxcam(f"{type(exc).__name__}: {exc}")
            return None

    def _grab_imagegrab(self, bbox: tuple[int, int, int, int]) -> Image.Image | None:
        if not self.foreground:
            self.capture_reason = self._join_reason("fallback_requires_foreground")
            return None
        try:
            image = self._image_grabber(bbox=bbox, all_screens=True)
        except Exception as exc:
            self.capture_reason = self._join_reason(
                f"imagegrab_failed:{type(exc).__name__}"
            )
            return None
        if image is None:
            self.capture_reason = self._join_reason("imagegrab_no_frame")
            return None
        self.capture_reason = "graphics_capture_unavailable"
        return image

    def _join_reason(self, *details: str) -> str:
        seen: list[str] = []
        for detail in details:
            for part in str(detail or "").split(":"):
                part = part.strip()
                if part and part not in seen:
                    seen.append(part)
        return ":".join(seen)

    def capture_frame(self) -> CaptureResult | None:
        """Probe the target and capture one frame synchronously.

        Never raises for a missing window/process; returns ``None`` and leaves
        ``capture_reason`` describing the fail-closed decision.

        If a background worker is running it is the single capture owner, so a
        caller on another thread gets the worker's latest result instead of
        opening a second, competing capture path.
        """
        worker = self._thread
        if (
            worker is not None
            and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            # The worker is the single capture owner; a caller on another
            # thread gets the worker's latest result instead of opening a
            # second, competing capture path.
            return self._latest_result
        with self._capture_lock:
            return self._capture_frame_locked()

    def _capture_frame_locked(self) -> CaptureResult | None:
        self.fallback_reason = ""
        self.source = ""
        # A failed attempt must not leave a previous frame claiming to be a
        # trustworthy reading; the frame data may remain for diagnostics.
        self._last_frame_trusted = False
        window = self._probe_window()
        self.foreground = False
        if window is None:
            self.capture_reason = (
                "window_not_found" if self.process_found else "process_not_found"
            )
            return None

        self.foreground = bool(self._foreground_probe(window))
        if window.minimized:
            self.capture_reason = "window_minimized"
            return None
        if not window.visible:
            self.capture_reason = "window_not_visible"
            return None

        bbox = client_bbox(window)
        if not is_valid_bbox(bbox):
            self.capture_reason = "invalid_client_rect"
            return None

        image: Image.Image | None = None
        source = ""
        fallback_reasons: list[str] = []
        trusted = False

        # 1. Explicit window-specific host bridge has priority.  A failed
        # bridge no longer fails closed for the whole tick: the user selected a
        # multi-level degradation ladder, so a safely gated desktop grab may
        # follow.  The failure remains visible as fallback_reason/degraded.
        if self._host_enabled:
            self.backend = "host_wgc"
            image = self._host_screenshot(window, now=time.monotonic())
            if image is not None:
                source = "host_wgc"
                trusted = True
                self.occlusion_state = "not_required"
                self.capture_reason = ""
            else:
                host_reason = (
                    self._host_degraded_reason
                    or self.capture_reason
                    or "host_capture_unavailable"
                )
                fallback_reasons.append(host_reason)

        if image is None:
            # 2/3. Desktop backends need foreground plus proven-empty client
            # area.  Both dxcam and ImageGrab read the desktop, not the game's
            # swapchain, so neither may run behind a known obstruction.
            desktop_ok, gate_reason = self._desktop_gate(window, bbox)
            if not desktop_ok:
                self.capture_reason = self._join_reason(
                    *fallback_reasons, gate_reason
                )
                self.fallback_reason = self._join_reason(*fallback_reasons)
                return None

            if not self._dxcam_failed:
                self.backend = "dxcam"
                image = self._grab_dxcam(bbox)
                if image is not None:
                    source = "dxcam"
                    trusted = True
                else:
                    fallback_reasons.append(
                        self.capture_reason
                        or (
                            "graphics_capture_unavailable"
                            if self._dxcam_failed
                            else "dxcam_no_frame"
                        )
                    )
            else:
                fallback_reasons.append("graphics_capture_unavailable")

            if image is None:
                # A fresh ImageGrab frame is acceptable only because the gate
                # above proved foreground and no known overlap.  It remains an
                # explicitly degraded backend.
                self.backend = "imagegrab"
                image = self._grab_imagegrab(bbox)
                if image is not None:
                    source = "imagegrab"
                    trusted = True
                    fallback_reasons.append("graphics_capture_unavailable")
                else:
                    fallback_reasons.append(
                        self.capture_reason or "imagegrab_no_frame"
                    )

        if image is None:
            self.capture_reason = self._join_reason(*fallback_reasons)
            self.fallback_reason = self.capture_reason
            self.source = ""
            self._last_frame_trusted = False
            return None

        fallback_reason = self._join_reason(*fallback_reasons)
        self.fallback_reason = fallback_reason
        self.source = source
        self._last_frame_trusted = bool(trusted)
        # Keep the public reason aligned with the successful path: a host
        # outage explains degraded health while a normal dxcam frame is clean.
        if source == "host_wgc":
            self.capture_reason = ""
        elif source == "dxcam":
            self.capture_reason = fallback_reason
        else:
            self.capture_reason = self._join_reason(
                fallback_reason, "graphics_capture_unavailable"
            )

        captured_at = time.time()
        with self._lock:
            self._frame_id += 1
            frame_id = self._frame_id
        result = CaptureResult(
            frame_id=frame_id,
            captured_at=captured_at,
            width=int(image.width),
            height=int(image.height),
            backend=source,
            image=image,
            window=window,
            foreground=self.foreground,
            source=source,
            fallback_reason=fallback_reason,
            trusted=trusted,
        )
        self.latest = (captured_at, image)
        self.latest_hash = dhash(image)
        self.stats["frames"] += 1
        if fallback_reason:
            self.stats["fallback_frames"] += 1
        self._last_frame_ts = captured_at
        self._latest_result = result
        return result

    def _loop(self) -> None:
        # The thread object is the ownership token.  If another lifecycle
        # replaces it, this worker exits instead of writing into the new one.
        while not self._stop.is_set() and self._thread is threading.current_thread():
            started = time.monotonic()
            try:
                self.capture_frame()
            except Exception:
                self.stats["errors"] += 1
            interval = 1.0 / max(0.5, float(self.fps))
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.005, interval - elapsed))

    # ------------------------------------------------------------- consumers

    def latest_frame(self) -> tuple[float, Image.Image] | None:
        return self.latest

    def latest_capture(self) -> CaptureResult | None:
        return self._latest_result

    def clear_when_background(self) -> None:
        if not self.foreground:
            self.latest = None
            self.latest_hash = None
            self._latest_result = None
            self._last_frame_trusted = False

    @property
    def last_source(self) -> str:
        """Backend that produced the last validated frame ('' if none)."""
        return self.source

    def diagnostics(self) -> dict[str, Any]:
        """Plain diagnostic evidence; contains no image/OCR/push payload."""
        return {
            "backend": self.backend,
            "source": self.source,
            "fallback_reason": self.fallback_reason,
            "capture_reason": self._effective_capture_reason(),
            "occlusion_state": self.occlusion_state,
            "trusted": bool(self._last_frame_trusted),
            "host_bridge_enabled": self._host_enabled,
            "host_bridge_confirmed": self.host_bridge_confirmed,
            "frame_id": self._frame_id,
            "stats": dict(self.stats),
        }

    # ------------------------------------------------------------- health

    def _effective_capture_reason(self) -> str:
        """Drop a stale window-state reason once that state no longer holds."""
        reason = self.capture_reason
        window = self.window
        if (
            reason in _WINDOW_STATE_REASONS
            and self.process_found
            and window is not None
            and window.visible
            and not window.minimized
        ):
            return ""
        return reason

    def _degraded_reason(self) -> str:
        # A configured host bridge owns the degradation story even before the
        # first probe; the default ``imagegrab`` backend placeholder must not
        # masquerade as a graphics-capture failure.
        if self._host_enabled:
            return self._host_degraded_reason or "host_capture_unavailable"
        if self.backend in {"imagegrab", "fallback"}:
            return "graphics_capture_unavailable"
        return "degraded"

    def _status_reason(self, status: str) -> str:
        window = self.window
        if not self._ever_started:
            return "capture_not_started"
        if not self._running:
            return "capture_stopped"
        if not self.process_found:
            return "process_not_found"
        if window is None:
            return "window_not_found"
        if window.minimized:
            return "window_minimized"
        if not window.visible:
            return "window_not_visible"
        reason = self._effective_capture_reason()
        if status == "capturing":
            return ""
        if status == "stale_frame":
            return self._merge_reason(reason, "stale_frame")
        if status == "degraded":
            return reason or self._degraded_reason()
        if not reason:
            reason = "frame_unavailable"
        return reason

    @staticmethod
    def _merge_reason(first: str, second: str) -> str:
        if not first:
            return second
        if first == second:
            return first
        return f"{first}:{second}"

    def health_snapshot(
        self,
        *,
        max_age_ms: float | None = None,
        now: float | None = None,
    ) -> CaptureHealthSnapshot:
        """Build a public health snapshot from the last probe/frame."""
        current = time.time() if now is None else float(now)
        window = self.window
        latest = self.latest
        last_ts = self._last_frame_ts
        frame_age_ms: float | None = None
        if last_ts is not None:
            frame_age_ms = max(0.0, (current - last_ts) * 1000.0)
        max_age = float(self.max_frame_age_ms if max_age_ms is None else max_age_ms)
        frame_available = latest is not None and self._latest_result is not None
        width, height = (latest[1].size if latest is not None else (0, 0))

        degraded_backend = self.backend in {"imagegrab", "fallback"}
        desktop_source = self.source in {"dxcam", "imagegrab"}
        degraded_bridge = self._host_enabled and (
            not self.host_bridge_confirmed or bool(self._host_degraded_reason)
        )
        fallback_desktop = desktop_source and bool(self.fallback_reason)
        gate_blocked = self.occlusion_state in {
            "foreground_required",
            "occluded",
            "unverified",
        }
        degraded = degraded_backend or degraded_bridge or fallback_desktop or gate_blocked
        foreground = bool(self.foreground and window is not None)
        # Both desktop backends read the desktop rather than a window's own
        # surface, so reading is gated on foreground.  Host WGC may return a
        # valid frame while the game is backgrounded; that path does not
        # require foreground.  Window-capture occlusion support is still not
        # claimed anywhere (``occluded`` remains False).
        requires_foreground = desktop_source or degraded_backend
        if requires_foreground and not foreground:
            degraded = True
        desktop_trusted = bool(self._last_frame_trusted)
        if requires_foreground and foreground and window is not None:
            # The cached flag may predate the current tick (UI polling calls
            # health_snapshot between captures).  Re-probe so losing focus
            # immediately clears ``reading`` instead of trusting a stale True.
            try:
                foreground = bool(self._foreground_probe(window))
            except Exception:
                foreground = False
            if not foreground:
                self.foreground = False
            if desktop_source and foreground:
                # A top-level overlay can appear without taking focus.  Only
                # a provably clear client area keeps a desktop frame readable.
                try:
                    occlusion = self._probe_occlusion(window, client_bbox(window))
                except Exception:
                    occlusion = None
                if occlusion is None or bool(occlusion):
                    desktop_trusted = False
                    degraded = True

        if not self._running:
            status = "stopped"
            reading = False
        else:
            status = evaluate_capture_health(
                process_found=self.process_found,
                window_found=window is not None,
                visible=bool(window is not None and window.visible),
                minimized=bool(window is not None and window.minimized),
                frame_available=frame_available,
                frame_age_ms=frame_age_ms,
                max_age_ms=max_age,
                backend=self.backend,
                degraded=degraded,
            )
            # Dependency outage is explicit even when no frame is available.
            # Staleness keeps its own status so the reason and reading gate
            # never contradict each other.
            if (
                status == "frame_unavailable"
                and self.process_found
                and window is not None
                and window.visible
                and not window.minimized
                and degraded
            ):
                status = "degraded"
            fresh = (
                frame_available
                and frame_age_ms is not None
                and frame_age_ms <= max_age
            )
            reading = bool(
                fresh
                and status in {"capturing", "degraded"}
                and desktop_trusted
                and (not requires_foreground or foreground)
            )

        reason = self._status_reason(status)

        return CaptureHealthSnapshot(
            status=status,
            reason=reason,
            reading=reading,
            backend=self.backend,
            frame_id=self._frame_id,
            frame_age_ms=None if frame_age_ms is None else round(frame_age_ms, 1),
            width=int(width),
            height=int(height),
            hwnd=None if window is None else int(window.hwnd),
            pid=None if window is None else int(window.pid),
            process_name="" if window is None else window.process_name,
            window_title="" if window is None else window.title,
            foreground=foreground,
            visible=bool(window is not None and window.visible),
            minimized=bool(window is not None and window.minimized),
            # ImageGrab/dxcam cannot prove that another window is not covering
            # the target.  We therefore never infer or claim occlusion support.
            occluded=False,
        )
