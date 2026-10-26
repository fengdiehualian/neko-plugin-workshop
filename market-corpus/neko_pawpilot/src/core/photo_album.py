"""美景自动拍照：检测到美景场景 → 截屏存相册 + 推送。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# 美景场景关键词（画面畅聊增强）
SCENE_PHOTO_KEYWORDS = {
    "sunset": ("日落", "sunset"),
    "sunrise": ("日出", "sunrise"),
    "mountain": ("山", "mountain", "阿尔卑斯"),
    "sea": ("海", "coast", "海边"),
    "snow": ("雪", "snow"),
    "city_night": ("夜景", "city night"),
    "forest": ("森林", "forest"),
}


class PhotoAlbum:
    """美景自动拍照：存相册 + 可选推送。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._photos: List[Dict[str, Any]] = []
        self._last_shot = 0.0
        self.last_reason = ""             # 上一次没拍成的原因（面板要说清楚）

    def detect(self, ocr_text: str) -> Optional[str]:
        """OCR 文本里找美景关键词，返回场景名。"""
        if not ocr_text:
            return None
        text = ocr_text.lower()
        for scene, keywords in SCENE_PHOTO_KEYWORDS.items():
            if any(k in text for k in keywords):
                return scene
        return None

    def can_shoot(self, cooldown: float = 600.0) -> bool:
        return (time.time() - self._last_shot) >= cooldown

    async def shoot(self, scene: str, ocr: Any,
                    force: bool = False,
                    allow_background: bool = False) -> Optional[str]:
        """截屏存相册并返回照片记录；自动拍有 10 分钟冷却，明确要求可 force。

        截图一律走 `ocr.capture_game()`：只截游戏窗口。以前截整屏，
        截出来的是面板+桌面，跟"美景"毫无关系。
        """
        now = time.time()
        if not force and now - self._last_shot < 600:
            self.last_reason = "cooldown"
            return None
        self._last_shot = now
        try:
            grab = getattr(ocr, "capture_game", None)
            if callable(grab):
                img, note = await __import__("asyncio").to_thread(
                    grab, allow_background)
            else:
                img, note = await __import__("asyncio").to_thread(ocr.capture), ""
            if img is None:
                self.last_reason = note or "抓不到画面"
                return None
            photos_dir = self.plugin.data_path("photos")
            photos_dir.mkdir(parents=True, exist_ok=True)
            name = f"{int(now)}_{scene}.png"
            path = photos_dir / name
            await __import__("asyncio").to_thread(img.save, str(path))
            record = {"name": name, "scene": scene, "ts": int(now), "path": str(path)}
            self._photos.insert(0, record)
            self._photos = self._photos[:50]
            self.last_reason = ""
            return record
        except Exception as exc:            # noqa: BLE001
            self.last_reason = f"{type(exc).__name__}: {exc}"
            return None

    def list_photos(self, top: int = 10) -> List[Dict[str, Any]]:
        return self._photos[:top]

    def snapshot(self) -> Dict[str, Any]:
        return {"count": len(self._photos), "recent": self.list_photos(5)}
