"""HUD OCR 感知：屏幕截取 + 宿主 RapidOCR 识别。"""

from __future__ import annotations

from typing import Any, Dict, Optional


class HudOcr:
    """mss 截屏 + RapidOCR 识别屏幕文本。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._backend: Optional[Any] = None
        self._sct = None
        self._error: Optional[str] = None

    def is_available(self) -> bool:
        import importlib.util
        if importlib.util.find_spec("mss") is None:
            self._error = "mss 或 rapidocr 不可用"
            return False
        try:
            from plugin.plugins._shared.rapidocr import (
                RapidOcrBackend as _RapidOcrBackend,
            )
            return _RapidOcrBackend is not None
        except ImportError:
            self._error = "mss 或 rapidocr 不可用"
            return False

    def _ensure(self) -> bool:
        if self._backend is not None:
            return True
        try:
            from plugin.plugins._shared.rapidocr import RapidOcrBackend
            self._backend = RapidOcrBackend(
                install_target_dir_raw=str(self.plugin.data_path("rapidocr_models")),
                engine_type="onnxruntime",
                lang_type="ch",
                model_type="mobile",
                ocr_version="PP-OCRv4",
            )
            import mss
            self._sct = mss.mss()
            return True
        except Exception as exc:
            self._error = str(exc)
            return False

    def capture(self, region: Optional[tuple] = None) -> Optional[Any]:
        """截取屏幕（region: left,top,width,height；空=全屏）。"""
        if not self._ensure():
            return None
        try:
            if region:
                monitor = {"left": region[0], "top": region[1],
                           "width": region[2], "height": region[3]}
            else:
                monitor = self._sct.monitors[1]
            shot = self._sct.grab(monitor)
            from PIL import Image
            return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            return None

    def read_text(self, region: Optional[tuple] = None) -> str:
        """识别指定区域文本；空区域全屏。"""
        if not self._ensure():
            return ""
        try:
            img = self.capture(region)
            if img is None:
                return ""
            return self._backend.extract_text(img) or ""
        except Exception:
            return ""

    def snapshot(self) -> Dict[str, Any]:
        return {"available": self.is_available(), "error": self._error}
