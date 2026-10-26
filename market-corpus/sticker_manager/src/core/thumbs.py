"""表情包缩略图（v0.17.2）：渲染尺与降级路径。

**为什么需要**：实机账（2026-09-22）——面板表情墙一格一图，而 `preview` 回的是
**原图字节**分段。官方区 190 张压后 gif 共 34.7MB，base64 后约 46MB，全部走宿主
ZeroMQ 控制通道，而面板调度器同时只跑 2 张。后果是两件同一根因的病：
排在后面的格子等过面板默认 30s ⇒ 主人看到的「加载图片超时」；
每张图一行宿主 SDK 的 `TRIGGER entry='preview'` ⇒ 主人看到的「日志刷屏」
（当天 363 行里 313 行是 preview，20:49:54~57 四秒 180 次）。

格子只有 128~176px，为此传一张 180KB 的原图是纯粹的浪费，所以这里生成
**256px / JPEG 质量 78 / 单张 ≤64KiB** 的缩略图，按内容指纹落盘（`Library.thumb_for`）。

**动图会掉动画**（PIL 取第 0 帧）——主人 2026-09-22 拍板接受：格子是索引不是展品，
点开的聚焦卡仍取原图、仍带动画。

**运行环境说明**：宿主用自己的解释器起插件子进程（`plugin/server/application/plugins/
metadata_scanner.py:72` 的 `sys.executable`），那边装有 Pillow，所以真机上这条路是通的；
**本仓的 `.venv` 里没有 Pillow**，因此 PIL 只能在函数内懒 import（模块级 import 会当场
打断插件导入链与测试收集）。也因为这样，真 PIL 那段不能写成"没有 PIL 就 skip"的测试——
那是一道永远不会变红的门（陷阱 5 的教训）。编排逻辑靠 `render=` 注入来钉，
真实渲染另跑一次性实测，结果记 CHANGELOG。
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Callable

# 缩略图尺：256px 长边 = 格子最大宽（176px）的约 1.4 倍，留高清屏余量。
THUMB_EDGE = 256
THUMB_QUALITY = 78
# 单张字节上限：超过就降一档再试，还超就回退原图（不硬塞大响应进通道）。
THUMB_MAX_BYTES = 64 * 1024
# 降档参数（第一次超字节时用）。
THUMB_FALLBACK_EDGE = 192
THUMB_FALLBACK_QUALITY = 65
# 解码前的像素总数上限：与宿主图片通道同尺（`sdk/shared/core/images.py` 的 16MP 量级），
# 防"缩略图接口变成解压炸弹引信"。库里的图是主人自己收的，但仍不该无脑解码。
THUMB_MAX_PIXELS = 26_000_000
# 文件名里的尺寸档：将来若要第二种尺寸（比如列表行内小图），换号即可，旧缓存自然失效。
THUMB_CACHE_VERSION = "v1"


def thumb_filename(digest: str, edge: int = THUMB_EDGE) -> str:
    """按**内容指纹**命名，不按 sticker id：同图多条（重复入库被拒后不该出现，但换 id 重建会）
    共用一份缓存，且内容变了天然要重算——不需要失效逻辑。
    """
    return f"{digest or 'no-digest'}-{THUMB_CACHE_VERSION}-{edge}.jpg"


def render_thumb(data: bytes, *, edge: int, quality: int) -> bytes:
    """唯一的 PIL 触点：把字节压成小 JPEG。任何异常都抛给 `build_thumb` 处置。"""
    from PIL import Image, ImageOps  # 懒 import：见模块 docstring 的运行环境说明

    with Image.open(BytesIO(data)) as image:
        # 只按第 0 帧出图（gif/webp 动图 = 静态首帧，本轮拍板接受的取舍）。
        image.load()
        if image.width * image.height > THUMB_MAX_PIXELS:
            raise ValueError("thumb_source_too_large")
        # 手机照片的 EXIF 方向必须在这里归一，否则缩略图会横躺（原图路径由宿主显示端处理过）。
        sized = ImageOps.exif_transpose(image) or image
        sized.thumbnail((edge, edge))
        flat = _to_rgb(sized)
        out = BytesIO()
        flat.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def _to_rgb(image: Any) -> Any:
    """透明/调色板模式先摊到白底再转 RGB——直接 `convert("RGB")` 会把透明区变黑块。"""
    from PIL import Image

    if image.mode in ("RGBA", "LA", "PA"):
        base = image.convert("RGBA")
        canvas = Image.new("RGB", base.size, (255, 255, 255))
        canvas.paste(base, mask=base.split()[-1])
        return canvas
    return image.convert("RGB")


def build_thumb(
    data: bytes,
    *,
    render: Callable[..., bytes] = render_thumb,
    edge: int = THUMB_EDGE,
    quality: int = THUMB_QUALITY,
    max_bytes: int = THUMB_MAX_BYTES,
) -> bytes | None:
    """编排：渲一次 → 超字节就降一档再试 → 坏图/无 PIL 一律回 `None`。

    回 `None` 不等于"报错"：调用方（`preview` 入口）据此**透明降级回原图分段**，
    格子不许因为缩略图坏了就空白——那是把性能优化做成功能回归。
    """
    if not data:
        return None
    for try_edge, try_quality in ((edge, quality), (THUMB_FALLBACK_EDGE, THUMB_FALLBACK_QUALITY)):
        try:
            blob = render(data, edge=try_edge, quality=try_quality)
        except Exception:  # noqa: BLE001 - 坏图/解码器缺席/像素超限，一律降级不抛
            return None
        if blob and len(blob) <= max_bytes:
            return blob
    return None
