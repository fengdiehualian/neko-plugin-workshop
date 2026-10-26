"""TTS 适配：语音由主项目自动播放，本插件只保证文本是 TTS-friendly 短句。"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("neko_arcade.tts")

MAX_TTS_CHARS = 60


class TTSClient:
    """语音输出辅助：确保猫娘的话适合主项目 TTS 播放。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def is_tts_friendly(self, text: str) -> bool:
        """是否适合 TTS 播放：短句、无长数字、无代码符号。"""
        if not text or len(text) > MAX_TTS_CHARS:
            return False
        if re.search(r"[{}=_\[\]|\\`]", text):
            return False
        return True

    def tts_line(self, text: str) -> str:
        """截取为适合 TTS 的一行（按标点断句取第一句）。"""
        if self.is_tts_friendly(text):
            return text
        for sep in ("！", "。", "？", "！？", "……"):
            idx = text.find(sep)
            if 0 < idx < MAX_TTS_CHARS:
                return text[: idx + 1]
        return text[:MAX_TTS_CHARS]

    def note_tts_line(self, text: str) -> None:
        """记录即将由主项目 TTS 播放的一行（供日志排查）。"""
        line = self.tts_line(text)
        log.debug("TTS 播放: %s", line)