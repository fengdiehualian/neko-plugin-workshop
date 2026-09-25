"""Screen capture + OCR for keyboard_controller.

Captures the target window (or fullscreen) as a PIL image and runs OCR via
the shared RapidOCR backend (``plugin/plugins/_shared/rapidocr``), so
non-vision LLM tools can "see" the screen as text.

Backends:
- capture: mss (multi-monitor) with a DPI-aware rect, fallback pyautogui/ImageGrab
- OCR: shared ``RapidOcrBackend`` (lazy; reuses galgame/study runtime when present)
"""

from __future__ import annotations

import base64
import ctypes
import importlib
import io
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from ._input_backend import backend as _input_backend

logger = logging.getLogger(__name__)

# 兼容 shim：Windows 专属符号仅在该平台存在；linux 路径不会触达这些引用。
RECT = getattr(_input_backend, "RECT", None)
find_window_for_pid = _input_backend.find_window_for_pid
is_windows = getattr(_input_backend, "is_windows", (lambda: sys.platform == "win32"))

_CAPTURE_MAX_LONG_EDGE = 1920
_OCR_RESULT_MAX_CHARS = 2000

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_ocr_backend: Any = None
_ocr_backend_lock = threading.Lock()


def _run_with_thread_dpi_awareness(fn):
    windll = getattr(ctypes, "windll", None)
    user32 = getattr(windll, "user32", None) if windll is not None else None
    set_context = (
        getattr(user32, "SetThreadDpiAwarenessContext", None)
        if user32 is not None
        else None
    )
    if not callable(set_context):
        return fn()
    try:
        set_context.restype = ctypes.c_void_p
        set_context.argtypes = [ctypes.c_void_p]
    except Exception:
        pass
    old_context = None
    try:
        old_context = set_context(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        old_context = None
    try:
        return fn()
    finally:
        if old_context is not None:
            try:
                set_context(old_context)
            except Exception:
                pass


def _window_rect_dpi_aware(hwnd: int) -> tuple[int, int, int, int]:
    if sys.platform.startswith("linux"):
        try:
            from ._linux_input import raw_window_rect

            rect = raw_window_rect(hwnd)
            return rect if rect is not None else (0, 0, 0, 0)
        except Exception as exc:
            logger.debug("linux window rect lookup failed: {}", exc)
            return (0, 0, 0, 0)
    user32 = ctypes.windll.user32

    def _read() -> tuple[int, int, int, int]:
        rect = RECT()
        if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            return (0, 0, 0, 0)
        return (
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )

    return _run_with_thread_dpi_awareness(_read)


def capture_fullscreen() -> Image.Image:
    """Capture the virtual screen as a PIL RGB image."""
    try:
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[0]  # virtual screen union
            shot = sct.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        from PIL import ImageGrab

        image = ImageGrab.grab()
    return _normalize_image(image)


def capture_window(window: dict[str, Any]) -> Image.Image:
    """Capture a target window (``find_window_for_pid`` result) as PIL RGB."""
    hwnd = int(window.get("hwnd") or 0)
    if hwnd <= 0:
        raise RuntimeError("target window has no hwnd")
    left, top, right, bottom = _window_rect_dpi_aware(hwnd)
    if right <= left or bottom <= top:
        raise RuntimeError(f"target window has invalid rect ({left},{top},{right},{bottom})")

    # PrintWindow 后台捕获优先：窗口被遮挡/不在前台也能截到自身内容
    # （PW_RENDERFULLCONTENT 支持 DirectComposition/Chromium）。
    if sys.platform == "win32":
        try:
            image = _print_window_image(hwnd, int(right - left), int(bottom - top))
        except Exception as exc:
            logger.debug("print_window capture failed: {}", exc)
            image = None
        if image is not None:
            return _normalize_image(image)

    try:
        import mss

        with mss.mss() as sct:
            monitor = {
                "left": int(left),
                "top": int(top),
                "width": int(right - left),
                "height": int(bottom - top),
            }
            shot = sct.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=(int(left), int(top), int(right), int(bottom)))
    return _normalize_image(image)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def _print_window_image(hwnd: int, width: int, height: int) -> Optional[Image.Image]:
    """PrintWindow 全窗捕获（含标题栏），返回 RGB Image；失败/全黑返回 None。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    PW_RENDERFULLCONTENT = 0x00000002
    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0  # BI_RGB
    bits = ctypes.c_void_p()
    bmi_ptr = ctypes.byref(bmi)
    bmp = gdi32.CreateDIBSection(hdc_mem, bmi_ptr, 0, ctypes.byref(bits), None, 0)
    image: Optional[Image.Image] = None
    try:
        if not bmp or not bits.value:
            return None
        old = gdi32.SelectObject(hdc_mem, bmp)
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        gdi32.SelectObject(hdc_mem, old)
        if not ok:
            return None
        size = width * height * 4
        buf = (ctypes.c_char * size).from_address(bits.value)
        img = Image.frombuffer("RGB", (width, height), bytes(buf), "raw", "BGRX", 0, 1)
        extrema = img.convert("L").getextrema()
        if extrema == (0, 0):  # 纯黑帧：应用不支持 PrintWindow，回退屏幕抓取
            return None
        image = img
        return image
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)


def _normalize_image(image: Image.Image) -> Image.Image:
    frame = image.convert("RGB") if hasattr(image, "convert") else image
    width, height = frame.size
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid capture dimensions {width}x{height}")
    scale = min(1.0, float(_CAPTURE_MAX_LONG_EDGE) / float(max(width, height)))
    if scale < 1.0:
        try:
            resampling = Image.Resampling.LANCZOS
        except AttributeError:  # Pillow < 9.1
            resampling = Image.LANCZOS
        frame = frame.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resampling,
        )
    return frame


def encode_jpeg_base64(image: Image.Image, *, quality: int = 72) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    raw = buffer.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def save_png(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


def ocr_is_available() -> bool:
    try:
        return _resolve_ocr_backend().is_available()
    except Exception:
        return False


def ocr_image(image: Image.Image) -> tuple[str, str]:
    """Run OCR on a PIL image. Returns (text, status)."""
    backend = _resolve_ocr_backend()
    if backend is None or not backend.is_available():
        return "", "unavailable"
    try:
        text = backend.extract_text(image)
    except Exception:
        return "", "ocr_failed"
    text = str(text or "").strip()
    if not text:
        return "", "empty"
    if len(text) > _OCR_RESULT_MAX_CHARS:
        text = text[:_OCR_RESULT_MAX_CHARS] + "\n…[truncated]"
    return text, "ok"


def ocr_image_with_boxes(
    image: Image.Image,
    *,
    max_boxes: int = 0,
    query: str = "",
) -> tuple[str, list[dict[str, Any]], str]:
    """Run OCR and return (text, boxes, status).

    Each box is ``{"text", "left", "top", "right", "bottom", "score"}`` in image
    pixel coordinates. When ``query`` is non-empty only matching boxes (case-
    insensitive substring) are returned, which keeps the payload tiny.
    """
    backend = _resolve_ocr_backend()
    if backend is None or not backend.is_available():
        return "", [], "unavailable"
    try:
        text, boxes = backend.extract_text_with_boxes(image)
    except Exception:
        return "", [], "ocr_failed"
    if not boxes:
        return "", [], "empty"

    query = str(query or "").strip().lower()
    def _norm_text(s: str) -> str:
        return s.lower().replace(" ", "").replace("-", "").replace("_", "")
    norm_query = _norm_text(query)
    result: list[dict[str, Any]] = []
    for box in boxes:
        box_text = str(getattr(box, "text", "") or "")
        if norm_query and norm_query not in _norm_text(box_text):
            continue
        result.append({
            "text": box_text,
            "left": int(round(getattr(box, "left", 0) or 0)),
            "top": int(round(getattr(box, "top", 0) or 0)),
            "right": int(round(getattr(box, "right", 0) or 0)),
            "bottom": int(round(getattr(box, "bottom", 0) or 0)),
            "score": round(float(getattr(box, "score", 0) or 0), 3),
        })
        if max_boxes and len(result) >= int(max_boxes):
            break

    if not result:
        return "", [], "no_match"
    joined = "\n".join(b["text"] for b in result)
    return joined, result, "ok"


def _resolve_ocr_backend() -> Any:
    global _ocr_backend
    with _ocr_backend_lock:
        if _ocr_backend is not None:
            return _ocr_backend
        if not is_windows():
            return None
        from plugin.plugins._shared.rapidocr.ocr_backends import RapidOcrBackend

        _ocr_backend = RapidOcrBackend(
            install_target_dir_raw="",
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv4",
            plugin_id="keyboard_controller",
        )
        return _ocr_backend


def close_ocr_backend() -> None:
    global _ocr_backend
    with _ocr_backend_lock:
        backend = _ocr_backend
        _ocr_backend = None
    if backend is None:
        return
    close = getattr(backend, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def describe_capture() -> dict[str, Any]:
    """Status payload for the panel and status entry."""
    mss_ok = importlib.util.find_spec("mss") is not None
    return {
        "windows_supported": is_windows(),
        "ocr_available": ocr_is_available(),
        "mss_available": mss_ok,
        "max_ocr_chars": _OCR_RESULT_MAX_CHARS,
    }


def target_window_for_capture(pid: int) -> Optional[dict[str, Any]]:
    if pid <= 0:
        return None
    return find_window_for_pid(pid)


__all__ = [
    "capture_fullscreen",
    "capture_window",
    "close_ocr_backend",
    "describe_capture",
    "encode_jpeg_base64",
    "ocr_image",
    "ocr_is_available",
    "save_png",
    "target_window_for_capture",
    "windows_fast_ocr_available",
    "windows_fast_ocr_text",
]


def windows_fast_ocr_text(image: Image.Image, *, timeout: float = 15.0) -> tuple[Optional[str], str]:
    """Windows 内置 OCR（Windows.Media.Ocr）快速识别，仅返回文本（无坐标框）。

    毫秒级、零模型下载；适合高频轮询的存在性检查。失败返回 (None, 状态)。
    """
    if sys.platform != "win32":
        return None, "unsupported"
    import base64 as _b64
    import subprocess as _sp

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    import tempfile as _tf
    fd, png_path = _tf.mkstemp(suffix=".png", prefix="neko_ocr_")
    os.write(fd, buffer.getvalue())
    os.close(fd)

    script = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t,$rt){ $at=$asTaskGeneric.MakeGenericMethod($rt); $nt=$at.Invoke($null,@($t)); $nt.Wait(-1)|Out-Null; $nt.Result }
$path = '%IMGPATH%'
try {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if (-not $engine) { Write-Output 'STATUS=engine_null'; exit }
  $f = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
  $s = Await ($f.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($s)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  Write-Output ('STATUS=ok')
  Write-Output ('TEXT=[' + $res.Text + ']')
} finally { Remove-Item $path -Force -ErrorAction SilentlyContinue }
"""
    script = script.replace("%IMGPATH%", png_path)
    try:
        proc = _sp.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             _b64.b64encode(script.encode("utf-16-le")).decode("ascii")],
            capture_output=True,
            timeout=max(5.0, float(timeout)),
        )
        out = proc.stdout.decode("utf-8", "replace")
    except Exception as exc:
        logger.debug("fast ocr failed: {}", exc)
        try:
            os.remove(png_path)
        except Exception:
            pass
        return None, "error"
    text_parts: list[str] = []
    status = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("#<") or line.startswith("<Objs"):
            continue
        if line.startswith("STATUS="):
            status = line[len("STATUS="):].strip()
        elif line.startswith("TEXT=[") and line.endswith("]"):
            text_parts.append(line[len("TEXT=["):-1])
    text = "".join(text_parts).strip()
    if status == "ok":
        return (text, "ok") if text else ("", "ok")
    return None, status or "error"


def windows_fast_ocr_available() -> bool:
    """粗检：Windows 且系统 OcrEngine 可创建。"""
    if sys.platform != "win32":
        return False

    def _check() -> bool:
        try:
            import base64 as _b64
            import subprocess as _sp

            script = (
                "[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]|Out-Null;"
                "if([Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()){'yes'}else{'no'}"
            )
            enc = _b64.b64encode(script.encode("utf-16-le")).decode("ascii")
            proc = _sp.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                capture_output=True,
                timeout=10.0,
            )
            return b"yes" in proc.stdout
        except Exception:
            return False

    return _check()
