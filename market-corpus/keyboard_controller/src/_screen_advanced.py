# -*- coding: utf-8 -*-
"""屏幕高级感知：像素色值检测、帧差异、多尺度模板匹配、GIF 录屏。"""

from __future__ import annotations

import os
import time
from typing import Any

from PIL import Image

# ── 像素操作 ───────────────────────────────────────────────────────


def get_pixel(img: Image.Image, x: int, y: int) -> dict[str, Any]:
    """返回 (x,y) 处像素的 RGB 值及十六进制色码。"""
    w, h = img.size
    if x < 0 or y < 0 or x >= w or y >= h:
        return {"ok": False, "error": f"坐标 ({x},{y}) 超出图像范围 ({w}x{h})"}
    r, g, b = img.getpixel((x, y))[:3]
    return {
        "ok": True,
        "x": x,
        "y": y,
        "r": r,
        "g": g,
        "b": b,
        "hex": f"#{r:02x}{g:02x}{b:02x}",
    }


def find_color(
    img: Image.Image,
    target: tuple[int, int, int],
    tolerance: int = 8,
    region: tuple[int, int, int, int] | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """在图像中搜索接近目标颜色的像素，返回坐标列表。

    target: (R, G, B) 目标颜色
    tolerance: 每个通道的容差上限（默认 8）
    region: 限定搜索区域 (x, y, w, h)
    """
    w, h = img.size
    rx, ry, rw, rh = 0, 0, w, h
    if region:
        rx, ry, rw, rh = region
        rx = max(0, min(rx, w - 1))
        ry = max(0, min(ry, h - 1))
        rw = min(rw, w - rx)
        rh = min(rh, h - ry)

    pixels = img.load()
    rt, gt, bt = target
    matches: list[dict[str, Any]] = []

    for y in range(ry, ry + rh):
        if len(matches) >= max_results:
            break
        for x in range(rx, rx + rw):
            if len(matches) >= max_results:
                break
            r, g, b = pixels[x, y][:3]
            if (abs(r - rt) <= tolerance and abs(g - gt) <= tolerance
                    and abs(b - bt) <= tolerance):
                matches.append({
                    "x": x, "y": y,
                    "r": r, "g": g, "b": b,
                    "hex": f"#{r:02x}{g:02x}{b:02x}",
                })

    return {
        "ok": True,
        "target": {"r": rt, "g": gt, "b": bt, "hex": f"#{rt:02x}{gt:02x}{bt:02x}"},
        "tolerance": tolerance,
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= max_results,
    }


# ── 帧差异检测 ─────────────────────────────────────────────────────


def frame_diff(
    before: Image.Image,
    after: Image.Image,
    threshold: int = 16,
    min_area: int = 64,
) -> dict[str, Any]:
    """比较两帧，返回发生变化的矩形区域列表。

    threshold: 像素灰度差阈值（0-255，默认 16）
    min_area: 最小变化区域面积（像素数，过滤噪点）
    """
    if before.size != after.size:
        return {"ok": False, "error": f"两帧尺寸不同：{before.size} vs {after.size}"}

    ba = before.convert("L")
    aa = after.convert("L")
    w, h = ba.size

    # 构建差异掩码
    mask = Image.new("L", (w, h), 0)
    bp = ba.load()
    ap = aa.load()
    mp = mask.load()
    for y in range(h):
        for x in range(w):
            if abs(bp[x, y] - ap[x, y]) >= threshold:
                mp[x, y] = 255

    # 连通域分析（简单扫描线合并）
    regions = _find_bounding_rects(mask, min_area)
    changed_pixels = sum(1 for y in range(h) for x in range(w) if mp[x, y])

    return {
        "ok": True,
        "changed_pixels": changed_pixels,
        "changed_pct": round(changed_pixels / (w * h) * 100, 2),
        "regions": regions,
        "count": len(regions),
        "threshold": threshold,
        "min_area": min_area,
        "size": {"width": w, "height": h},
    }


def _find_bounding_rects(mask: Image.Image, min_area: int) -> list[dict[str, int]]:
    """在二值掩码上找连通域的包围盒（简化版，不严格连通分量，用行扫描）。"""
    w, h = mask.size
    mp = mask.load()
    visited = set()
    rects: list[dict[str, int]] = []

    for y in range(h):
        for x in range(w):
            if mp[x, y] and (x, y) not in visited:
                # 泛洪填充找连通域
                stack = [(x, y)]
                visited.add((x, y))
                min_x, min_y, max_x, max_y = x, y, x, y
                area = 0
                while stack:
                    cx, cy = stack.pop()
                    area += 1
                    min_x = min(min_x, cx)
                    min_y = min(min_y, cy)
                    max_x = max(max_x, cx)
                    max_y = max(max_y, cy)
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if 0 <= nx < w and 0 <= ny < h and mp[nx, ny] and (nx, ny) not in visited:
                            visited.add((nx, ny))
                            stack.append((nx, ny))
                if area >= min_area:
                    rects.append({
                        "x": min_x, "y": min_y,
                        "width": max_x - min_x + 1,
                        "height": max_y - min_y + 1,
                        "area": area,
                        "center_x": (min_x + max_x) // 2,
                        "center_y": (min_y + max_y) // 2,
                    })
    return rects


# ── 多尺度模板匹配 ─────────────────────────────────────────────────


def multi_scale_match(
    img: Image.Image,
    template: Image.Image,
    scales: list[float] | None = None,
    min_score: float = 0.75,
) -> dict[str, Any]:
    """在多个缩放级别上匹配模板，返回最佳匹配。

    scales: 模板缩放比例列表（默认 [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]）。
    复用 _template_match.find_template（FFT NCC，纯色模板/区域也能正确匹配）。
    """
    from . import _template_match as tm

    if scales is None:
        scales = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

    best = None
    best_score = min_score

    for scale in scales:
        tw = max(1, int(round(template.width * scale)))
        th = max(1, int(round(template.height * scale)))
        if tw < 4 or th < 4 or tw > img.width or th > img.height:
            continue
        scaled = template.resize((tw, th), Image.LANCZOS).convert("L")
        # find_template 返回 {x,y,w,h,score}，其中 x/y 是中心坐标
        matches = tm.find_template(img, scaled, min_score=min_score, top_k=1)
        if not matches:
            continue
        m = matches[0]
        if m["score"] > best_score:
            best = {
                "score": m["score"],
                "x": int(m["x"] - tw / 2),
                "y": int(m["y"] - th / 2),
                "center_x": int(m["x"]),
                "center_y": int(m["y"]),
                "width": tw,
                "height": th,
            }
            best_score = m["score"]

    if best is None:
        return {"ok": False, "status": "no_match", "min_score": min_score, "scales_tried": scales}

    return {
        "ok": True,
        "status": "ok",
        **best,
        "scale": round(best["width"] / max(template.width, 1), 4),
        "scales_tried": scales,
    }


# ── GIF 录屏 ───────────────────────────────────────────────────────


class _GifRecorder:
    """GIF 屏幕录制器，hold 帧直到 close。"""

    def __init__(self):
        self.frames: list[Image.Image] = []
        self._started: float | None = None
        self.fps: float = 10.0
        self.region: tuple[int, int, int, int] | None = None

    def start(self, fps: float = 10.0, region: tuple[int, int, int, int] | None = None):
        self.frames.clear()
        self._started = time.time()
        self.fps = fps
        self.region = region

    def add(self, frame: Image.Image):
        if self.region:
            frame = frame.crop(self.region)
        self.frames.append(frame.copy())

    def save(self, path: str) -> dict[str, Any]:
        if not self.frames:
            return {"ok": False, "error": "没有录到帧"}
        # fps<=0 会算出负/除零 duration，PIL 直接抛异常；钳到安全区间。
        fps = min(max(float(self.fps or 2.0), 0.5), 20.0)
        duration_ms = int(1000 / fps)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.frames[0].save(
            path,
            save_all=True,
            append_images=self.frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=True,
        )
        size = os.path.getsize(path)
        return {
            "ok": True,
            "path": path,
            "frames": len(self.frames),
            "fps": fps,
            "duration_s": round(len(self.frames) / fps, 1),
            "size_bytes": size,
            "size_kb": round(size / 1024, 1),
        }

    def stop(self):
        self._started = None
        # 帧列表不再需要：整段 PIL Image 可达数十 MB，save 后立即归还。
        self.frames.clear()

    @property
    def is_recording(self) -> bool:
        return self._started is not None

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def elapsed(self) -> float:
        if self._started is None:
            return 0
        return time.time() - self._started