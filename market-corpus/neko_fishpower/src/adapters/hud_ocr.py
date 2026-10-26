"""HUD OCR 感知：屏幕截取 + 宿主 RapidOCR 识别（照 pawpilot）。"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict, Optional


class HudOcr:
    """mss 截屏 + RapidOCR 识别屏幕文本。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._backend: Optional[Any] = None
        self._sct = None
        self._error: Optional[str] = None

    def is_available(self) -> bool:
        """mss（截屏）与宿主 rapidocr（识别）都就位才算可用。

        用 importlib.util.find_spec 探测：只查模块是否可导入，
        **不真的执行 import**（CI 里 ruff 用 --ignore-noqa，`# noqa: F401` 的
        探测式 import 会被判 F401，也会在缺依赖时白白抛异常）。
        """
        if importlib.util.find_spec("mss") is None:
            self._error = "mss 或 rapidocr 不可用"
            return False
        try:
            spec = importlib.util.find_spec("plugin.plugins._shared.rapidocr")
        except (ImportError, ValueError):       # 父包不可导入时 find_spec 会抛
            spec = None
        if spec is None:
            self._error = "mss 或 rapidocr 不可用"
            return False
        return True

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
                plugin_id=getattr(self.plugin, "plugin_id", "neko_fishpower"),
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
