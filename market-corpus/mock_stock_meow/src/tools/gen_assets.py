# -*- coding: utf-8 -*-
"""P8.0 模块三：生成 ``ui/assets/`` 静态资源（群友头像 + 梗图）。

用法::

    python tools/gen_assets.py

会写出：

* ``ui/assets/avatar-<alias>.png`` —— 6 个群友的圆形头像（源文件）；
* ``ui/assets/meow-loan.png`` —— 网贷弹窗梗图（源文件）；
* ``ui/assets/avatars.ts`` —— 上面这些 PNG 的 **base64 data URI** 编译产物。

为什么面板里用的是 data URI 而不是 ``<img src="./assets/xxx.png">``：
面板跑在宿主给的 ``srcdoc`` + ``sandbox="allow-scripts"`` iframe 里，那是
**opaque origin**；相对路径会落到宿主页面（``/ui/plugins/...``）上，而宿主
**不服务插件目录** ⇒ 必然 404 ⇒ 破图。只有内联的 data URI 才保证一定显示。
PNG 原图仍然保留在仓库里当**源文件**，人可读、可替换。

这里**不含任何违禁元素**（规避同类作品的审查风险）。
头像也不用 Emoji 字形 —— PIL 的内置位图字体画不出彩色 Emoji，改用几何符号。

P9.0：**结算页的「破产猫」梗图已整个删掉**。那张图是 PIL 画的黄色猫瘫在
灰钱包上，远看就是个土豆 —— 玩家直呼辣眼睛。现在结算页的配图改成面板里
**纯 CSS 拼的扁平空钱包**（见 ``ui/panel.tsx`` 的 ``BankruptArt``），
既不用素材文件，也没有 PIL 手绘那种廉价感。
"""

from __future__ import annotations

import base64
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

ASSETS = Path(__file__).resolve().parents[1] / "ui" / "assets"
SIZE = 64
# P8.1：梗图在结算页要放大到 240×180 显示，源图太小会被拉糊。
# 这里把梗图画布整体放大 2 倍（160×120 → 320×240），坐标全是整数 ⇒ 边缘依旧锐利。
MEME_SCALE = 2
MEME_W = 160
MEME_H = 120


class ScaledDraw:
    """把 160×120 的原始坐标画到 ``MEME_SCALE`` 倍大的画布上。

    梗图的绘制代码写的是 160×120 的"手写坐标"，这里用一层薄包装把
    坐标整体放大 —— 不用改任何一行动画逻辑，改图只要改 ``MEME_SCALE``。
    """

    def __init__(self, draw: ImageDraw.ImageDraw, k: int) -> None:
        self._draw = draw
        self._k = k

    def _box(self, box):
        return [value * self._k for value in box]

    def _points(self, points):
        return [(x * self._k, y * self._k) for x, y in points]

    def _scale_width(self, kwargs):
        if "width" in kwargs and kwargs["width"]:
            kwargs["width"] = max(1, int(kwargs["width"] * self._k))
        return kwargs

    def ellipse(self, box, **kwargs):
        return self._draw.ellipse(self._box(box), **self._scale_width(kwargs))

    def rounded_rectangle(self, box, radius=0, **kwargs):
        return self._draw.rounded_rectangle(
            self._box(box), radius=radius * self._k, **self._scale_width(kwargs)
        )

    def polygon(self, points, **kwargs):
        return self._draw.polygon(self._points(points), **kwargs)

    def line(self, points, **kwargs):
        return self._draw.line(self._points(points), **self._scale_width(kwargs))

    def arc(self, box, start=0, end=360, **kwargs):
        return self._draw.arc(self._box(box), start, end, **self._scale_width(kwargs))


def circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill, outline=None, width=1):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=width)


def star_points(cx: float, cy: float, outer: float, inner: float, points: int = 5):
    result = []
    for index in range(points * 2):
        radius = outer if index % 2 == 0 else inner
        angle = math.pi / 2 + index * math.pi / points
        result.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    return result


def bolt_points(cx: float, cy: float, scale: float):
    return [
        (cx + 4 * scale, cy - 18 * scale),
        (cx - 12 * scale, cy + 2 * scale),
        (cx - 1 * scale, cy + 2 * scale),
        (cx - 5 * scale, cy + 18 * scale),
        (cx + 12 * scale, cy - 3 * scale),
        (cx + 1 * scale, cy - 3 * scale),
    ]


def draw_avatar(kind: str, bg: str, ink: str) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    circle(draw, SIZE / 2, SIZE / 2, SIZE / 2 - 1, bg)
    cx = cy = SIZE / 2
    if kind == "star":
        draw.polygon(star_points(cx, cy, 21, 9), fill=ink)
    elif kind == "bolt":
        draw.polygon(bolt_points(cx, cy, 1.15), fill=ink)
    elif kind == "dots":
        for offset in (-12, 0, 12):
            circle(draw, cx + offset, cy, 4.2, ink)
    elif kind == "book":
        draw.rounded_rectangle([cx - 15, cy - 13, cx + 15, cy + 13], radius=3, fill=ink)
        draw.rounded_rectangle([cx - 1, cy - 13, cx + 1, cy + 13], radius=1, fill=bg)
    elif kind == "moon":
        circle(draw, cx + 2, cy, 17, ink)
        circle(draw, cx + 9, cy - 3, 15, bg)
    elif kind == "bubble":
        circle(draw, cx - 4, cy + 2, 13, ink)
        circle(draw, cx + 11, cy - 8, 8, ink)
        circle(draw, cx - 12, cy - 10, 5, ink)
    return img


def draw_loan_paw() -> Image.Image:
    """网贷弹窗梗图：一只猫爪按着合同。"""
    img = Image.new("RGBA", (MEME_W * MEME_SCALE, MEME_H * MEME_SCALE), (0, 0, 0, 0))
    draw = ScaledDraw(ImageDraw.Draw(img), MEME_SCALE)
    draw.rounded_rectangle([26, 24, 122, 104], radius=6, fill=(255, 255, 255, 255),
                           outline=(203, 213, 225, 255), width=2)
    for index in range(5):
        y = 38 + index * 12
        draw.rounded_rectangle([36, y, 36 + (74 if index % 2 else 56), y + 4], radius=2,
                               fill=(226, 232, 240, 255))
    circle(draw, 116, 40, 8, (255, 143, 163, 255))
    for dx, dy in ((-16, -14), (-6, -20), (6, -18), (15, -10)):
        circle(draw, 116 + dx, 40 + dy, 5.5, (255, 143, 163, 255))
    circle(draw, 118, 78, 20, (255, 107, 129, 255))
    circle(draw, 118, 78, 15, (255, 255, 255, 255))
    return img


AVATARS = [
    ("星辰", "star", (219, 234, 254, 255), (37, 99, 235, 255)),
    ("Wehos", "bolt", (255, 228, 230, 255), (225, 29, 72, 255)),
    ("老王", "dots", (254, 243, 199, 255), (217, 119, 6, 255)),
    ("小明", "book", (209, 250, 229, 255), (5, 150, 105, 255)),
    ("熬夜怪", "moon", (224, 231, 255, 255), (67, 56, 202, 255)),
    ("潜水员", "bubble", (207, 250, 254, 255), (14, 116, 144, 255)),
]

FALLBACK_EMOJI = {
    "星辰": "🌟",
    "Wehos": "⚡",
    "老王": "🕵️",
    "小明": "📚",
    "熬夜怪": "🌙",
    "潜水员": "🫧",
}


def save(img: Image.Image, name: str) -> Path:
    path = ASSETS / name
    img.save(path, format="PNG", optimize=True)
    print(f"  {name}: {path.stat().st_size} bytes")
    return path


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    print("--- 生成头像 ---")
    entries = []
    for alias, kind, bg, ink in AVATARS:
        file_name = f"avatar-{alias}.png"
        path = save(draw_avatar(kind, bg, ink), file_name)
        entries.append((alias, data_uri(path)))

    print("--- 生成梗图 ---")
    save(draw_loan_paw(), "meow-loan.png")

    print("--- 写 ui/assets/avatars.ts ---")
    lines = [
        "/**",
        " * P8.0 模块三：群友头像与梗图的**内联资源**（编译产物，勿手改）。",
        " *",
        " * 为什么是 data URI 而不是 ``<img src=\"./assets/xxx.png\">``：",
        " * 面板跑在宿主给的 ``srcdoc`` + ``sandbox=\"allow-scripts\"`` iframe 里，",
        " * 那是 **opaque origin**；相对路径会落到宿主页面（``/ui/plugins/...``）上，",
        " * 宿主**不服务插件目录** ⇒ 必然 404 ⇒ 破图。",
        " * 只有内联的 data URI 才保证一定显示。",
        " *",
        " * ``ui/assets/*.png`` 是**源文件**（人可读、可替换），本文件是它的编译产物。",
        " * 重新生成：``python tools/gen_assets.py``。",
        " *",
        " * 这里**不含任何违禁元素**。",
        " */",
        "",
        "export type AvatarEntry = {",
        "  /** 内联 PNG（data URI）。 */",
        "  src: string",
        "  /** 加载失败时的兜底 Emoji（img 挂了也不破图）。 */",
        "  fallback: string",
        "}",
        "",
        "// 注意：这里**不能**写 ``Record<string, AvatarEntry>`` —— 泛型参数里的",
        "// 逗号会被宿主的 hosted-TSX 链接器当成「一个 export const 里的多个声明」，",
        "// 直接判定为不支持。改用**索引签名**（没有逗号）。",
        "export const AVATARS: { [alias: string]: AvatarEntry } = {",
    ]
    for alias, uri in entries:
        lines.append(f"  {json.dumps(alias, ensure_ascii=False)}: {{")
        lines.append(f"    src: {json.dumps(uri)},")
        lines.append(f"    fallback: {json.dumps(FALLBACK_EMOJI[alias], ensure_ascii=False)},")
        lines.append("  },")
    lines.append("}")
    lines.append("")
    lines.append("/** 网贷弹窗梗图（猫爪按着合同）。 */")
    lines.append(f"export const MEOW_LOAN = {json.dumps(data_uri(ASSETS / 'meow-loan.png'))}")
    lines.append("")
    (ASSETS / "avatars.ts").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"  avatars.ts: {(ASSETS / 'avatars.ts').stat().st_size} bytes")


if __name__ == "__main__":
    main()
