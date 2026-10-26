"""OCR 引擎封装（Windows 系统 OCR）。"""
from __future__ import annotations

import base64
import io
import re

# Windows OCR 会在汉字之间插空格，这个正则把它们去掉（保留中英之间的空格）
_HAN_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])")


def _normalize_han_spacing(text: str) -> str:
    return _HAN_SPACE_RE.sub("", text)


class OcrEngine:
    """Windows 系统 OCR 封装（WinRT）。"""

    def __init__(self, plugin_id: str, logger=None) -> None:
        self._plugin_id = plugin_id
        self._logger = logger
        self._engine = None
        self._language_tag: str | None = None

    def _ensure_engine(self):
        if self._engine is not None:
            return self._engine
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine as WinOcrEngine

        engine = WinOcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            try:
                engine = WinOcrEngine.try_create_from_language(Language("zh-Hans-CN"))
            except Exception:
                engine = None
        if engine is None:
            raise RuntimeError("Windows OCR engine unavailable")
        self._engine = engine
        try:
            self._language_tag = engine.recognizer_language.language_tag
        except Exception:
            self._language_tag = None
        if self._logger is not None:
            self._logger.info("OCR engine ready: lang={}", self._language_tag)
        return self._engine

    @property
    def language_tag(self) -> str | None:
        return self._language_tag

    def is_available(self) -> bool:
        try:
            self._ensure_engine()
            return True
        except Exception:
            return False

    async def _pil_to_software_bitmap(self, pil_img):
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        return await decoder.get_software_bitmap_async()

    async def extract_text_from_base64(self, image_base64: str) -> str:
        if not image_base64:
            return ""
        try:
            from PIL import Image
        except ImportError:
            return ""
        try:
            import time
            t0 = time.monotonic()

            raw = base64.b64decode(image_base64)
            t1 = time.monotonic()

            image = Image.open(io.BytesIO(raw))
            t2 = time.monotonic()

            engine = self._ensure_engine()
            t3 = time.monotonic()

            sb = await self._pil_to_software_bitmap(image)
            t4 = time.monotonic()

            result = await engine.recognize_async(sb)
            t5 = time.monotonic()

            text = "\n".join(line.text for line in result.lines)
            text = _normalize_han_spacing(text)

            if self._logger is not None:
                self._logger.info(
                    "OCR_TIMING decode={:.3f}s open={:.3f}s ensure={:.3f}s "
                    "convert={:.3f}s recognize={:.3f}s total={:.3f}s size={}x{} chars={}",
                    t1 - t0, t2 - t1, t3 - t2, t4 - t3, t5 - t4, t5 - t0,
                    image.width, image.height, len(text),
                )

            return text
        except Exception as exc:
            # 临时诊断（2.0.18）：暴露被 except 吞掉的异常，便于定位 OCR 静默失败。
            # 行为不变（仍 return ""），仅加 warning。定位完成后可撤。
            img_w = 0
            img_h = 0
            try:
                img_w = image.width
                img_h = image.height
            except (NameError, AttributeError):
                pass
            if self._logger is not None:
                self._logger.warning(
                    "OCR failed: {}: {} (lang={}, engine={}, size={}x{})",
                    type(exc).__name__, exc,
                    self._language_tag,
                    "set" if self._engine else "none",
                    img_w, img_h,
                )
            return ""

    def warmup(self) -> None:
        try:
            self._ensure_engine()
        except Exception:
            return

    async def warmup_async(self) -> None:
        import asyncio
        await asyncio.to_thread(self.warmup)

    def close(self) -> None:
        self._engine = None