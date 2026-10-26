"""视频画面取样（让她"看到"高光点到底发生了什么）。

## 为什么需要

实测（2026-10-10，用户反馈）：只喂弹幕时她只会说"这里有弹幕""大家反应好大"
——**像弹幕播报机，不像陪你一起看的人**。人机感的根源是：**她看不到画面**。

## 数据来源：B站官方"进度条预览图"

`/x/player/videoshot` 返回的就是 B站进度条悬停时显示的那张缩略图网格：

    {image: ["//bimp.hdslb.com/...-0001.jpg", ...], index: [0, 0, 5, 10, 15, ...]}

- 每张大图是 **10×10 网格**，每格 480×270（实测 4800×2700）
- `index[i]` = 第 i 格对应的视频秒数（每 5 秒一格）
- 所以"第 235 秒的画面"= 从网格里裁出第 i 格

**这就是免费、稳定、无需截屏权限的"视频画面"**。实测裁出的正是那一刻的画面
（100s→OW 的哈姆球，235s→"自从我吃了蜜汁紫米汤圆"那帧）。

## 隐私与体积

- 只下载官方预览图，不截用户的屏幕
- 裁出的一格压到 ~100KB（JPEG q80），和官方 minecraft 插件同一量级
"""

from __future__ import annotations

import io
import json
import ssl
import urllib.parse
import urllib.request

__all__ = ["fetch_shot_grid", "crop_frame_at", "cell_index_for_seconds"]

_CTX = ssl.create_default_context()
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# B站进度条预览图的固定网格（实测 4800x2700 = 10x10 格，每格 480x270）
_GRID = 10
_FRAME_SECONDS = 5.0


def fetch_shot_grid(bvid: str, *, timeout: int = 15) -> dict | None:
    """拉进度条预览图网格信息。

    返回 `{"image": [url, ...], "index": [秒, ...]}`；失败返回 None。
    """
    if not bvid:
        return None
    url = (
        "https://api.bilibili.com/x/player/videoshot"
        f"?bvid={urllib.parse.quote(bvid)}&index=1"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    if d.get("code") != 0:
        return None
    data = d.get("data") or {}
    images = [str(x) for x in (data.get("image") or []) if x]
    index = data.get("index") or []
    if not images or not index:
        return None
    return {"image": images, "index": [float(x) for x in index]}


def cell_index_for_seconds(index: list[float], seconds: float) -> int:
    """找最接近 `seconds` 的那一格在网格里的序号（0-based）。"""
    if not index:
        return 0
    best_i, best_d = 0, None
    for i, t in enumerate(index):
        d = abs(float(t) - float(seconds))
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    return best_i


def crop_frame_at(
    grid: dict,
    seconds: float,
    *,
    max_edge: int = 720,
    quality: int = 80,
) -> bytes | None:
    """裁出 `seconds` 时刻的那一格画面，返回 JPEG bytes（失败 None）。

    ★ 需要 Pillow（宿主已打包，见 resources/bin/PIL）。导入失败则返回 None，
      调用方应降级为"只有文字"（不影响主流程）。
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - 没装 Pillow 就降级
        return None

    images = grid.get("image") or []
    index = grid.get("index") or []
    if not images or not index:
        return None

    cell = cell_index_for_seconds(index, seconds)
    # 大图下标：每张大图装 10x10 格
    per_sheet = _GRID * _GRID
    sheet_no = cell // per_sheet
    if sheet_no >= len(images):
        sheet_no = len(images) - 1
    within = cell - sheet_no * per_sheet
    row, col = within // _GRID, within % _GRID

    url = images[sheet_no]
    if url.startswith("//"):
        url = "https:" + url
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
        })
        with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
            raw = r.read()
        im = Image.open(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return None

    try:
        w, h = im.size
        cw, ch = w // _GRID, h // _GRID
        if cw <= 0 or ch <= 0:
            return None
        box = (col * cw, row * ch, (col + 1) * cw, (row + 1) * ch)
        cell_im = im.crop(box)
        # 缩到合理大小（推给模型的图不必是原始 480x270 以上的大图）
        cell_im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        if cell_im.mode not in ("RGB", "L"):
            cell_im = cell_im.convert("RGB")
        buf = io.BytesIO()
        cell_im.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
