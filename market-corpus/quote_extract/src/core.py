"""图库核心逻辑：PROPFIND 解析、普通/隐藏分池、带概率抽取。

纯标准库实现，不依赖 N.E.K.O SDK，也不做网络请求；网络部分见 fetchers.py。
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
_HISTORY_CAP = 200


def parse_propfind(data: bytes) -> list[str]:
    """解析 WebDAV PROPFIND 响应，返回全部 href 路径（未解码）。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    hrefs: list[str] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() == "href" and el.text:
            hrefs.append(el.text.strip())
    return hrefs


def is_image(path: str) -> bool:
    return path.split("?", 1)[0].lower().endswith(IMAGE_EXTS)


def mime_for(path: str) -> str:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _MIME.get(ext, "application/octet-stream")


def choose_pool(normal: list[str], hidden: list[str], hidden_rate: float,
                rng: random.Random | None = None) -> tuple[list[str], bool]:
    """按掉率选择本次抽取的池子；返回 (池子, 是否隐藏款)。

    隐藏池为空时永远抽普通款；普通池为空时回退隐藏款。
    """
    rng = rng or random.Random()
    if hidden and rng.random() < hidden_rate:
        return hidden, True
    if normal:
        return normal, False
    if hidden:
        return hidden, True
    return [], False


def pick(paths: list[str], history: list[str], no_repeat: int,
         rng: random.Random | None = None) -> str | None:
    """从池子里随机抽一张；no_repeat > 0 时近期抽过的不重复。"""
    if not paths:
        return None
    rng = rng or random.Random()
    if no_repeat > 0 and len(paths) > 1:
        recent = set(history[-no_repeat:])
        fresh = [p for p in paths if p not in recent]
        paths = fresh or paths
    chosen = rng.choice(paths)
    history.append(chosen)
    if len(history) > _HISTORY_CAP:
        del history[:-_HISTORY_CAP]
    return chosen
