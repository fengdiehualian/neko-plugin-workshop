"""永远的陪伴 —— 面板外观（1.2.0 图库 + 可调背景）的纯逻辑。

数据进数据出、零 SDK 依赖：data URL 校验、外观参数归一（clamp）、
图库索引增删、旧版单图背景（panel_bg）→ 图库一次性迁移。
Store 读写与入口编排都在 mixins/panel.py。

归一纪律（装饰性参数宽容处理）：数值项坏值回退默认、越界夹取到域内，
枚举项非法值回退默认，绝不因单个坏参数拒绝整包保存。
"""

from __future__ import annotations

from typing import Any

from .state import (
    _GALLERY_IMG_PREFIX,
    _GALLERY_MAX_ITEMS,
    _GALLERY_THUMB_MAX_CHARS,
    _LEGACY_BG_ID,
    _PANEL_BG_DEFAULT_DIM,
    _PANEL_BG_MAX_CHARS,
    _PANEL_BG_MIMES,
)

JsonObject = dict[str, Any]

# ---- 外观参数：范围 / 枚举 / 默认 ----
# 默认值刻意复刻 1.1.x 的观感：不带图库参数时面板与旧版完全一致
# （fill=cover / position=center 即旧 CSS；glass=16px 即 .tm-has-bg 硬编码值；
#  card_alpha / text_weight = 100 表示不改动宿主原色；dim 默认沿用 0.3）
APPEARANCE_FILLS = ("cover", "contain", "repeat", "stretch")
APPEARANCE_POSITIONS = (
    "left top", "center top", "right top",
    "left center", "center", "right center",
    "left bottom", "center bottom", "right bottom",
)
# (最小, 最大, 默认)：blur/glass 单位 px；brightness/saturate/contrast 单位 %；
# card_alpha=卡片底色不透明度 %（100=宿主原样）；text_weight=文字浓度 %
# （100=实色无描边，越低越淡并自动加深文字投影保可读）
_APPEARANCE_RANGES: dict[str, tuple[float, float, float]] = {
    "blur": (0.0, 30.0, 0.0),
    "dim": (0.0, 0.85, _PANEL_BG_DEFAULT_DIM),
    "brightness": (30.0, 150.0, 100.0),
    "saturate": (0.0, 200.0, 100.0),
    "contrast": (50.0, 200.0, 100.0),
    "glass": (0.0, 40.0, 16.0),
    "card_alpha": (0.0, 100.0, 100.0),
    "text_weight": (40.0, 100.0, 100.0),
}


def appearance_defaults() -> JsonObject:
    """默认外观参数（bg_id="" 即不用任何图库图）。"""
    out: JsonObject = {"bg_id": "", "fill": "cover", "position": "center"}
    for name, (_lo, _hi, default) in _APPEARANCE_RANGES.items():
        out[name] = default
    return out


def clamp_appearance(raw: Any) -> JsonObject:
    """任意来源的参数 dict → 归一 dict：坏值回退默认，越界夹取，枚举非法回退默认。"""
    src = raw if isinstance(raw, dict) else {}
    out = appearance_defaults()
    bg = str(src.get("bg_id") or "").strip()
    out["bg_id"] = bg if bg and len(bg) <= 64 else ""
    fill = str(src.get("fill") or "").strip().lower()
    out["fill"] = fill if fill in APPEARANCE_FILLS else "cover"
    pos = " ".join(str(src.get("position") or "").strip().lower().split())
    out["position"] = pos if pos in APPEARANCE_POSITIONS else "center"
    for name, (lo, hi, default) in _APPEARANCE_RANGES.items():
        try:
            value = float(src.get(name))
        except (TypeError, ValueError):
            out[name] = default
            continue
        out[name] = min(hi, max(lo, value))
    return out


# ---- data URL 图片校验 ----


class ImageDataUrlError(ValueError):
    """图片 data URL 校验失败：携带稳定 reason code。

    i18n 契约（1.3.0 第九轮）：面板入口把 `exc.code` 原样放进
    `Err(SdkError(code))`，前端按 `panel.errors.<camelCase(code)>` 翻译——
    异常消息本身只进日志，绝不直出用户可见 toast。子类自 ValueError，
    旧 `except ValueError` 调用方与测试不受影响。
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def parse_image_data_url(data_url: Any, max_chars: int = _PANEL_BG_MAX_CHARS) -> tuple[str, int]:
    """图片 data URL 校验（原图与缩略图共用）。

    返回 (mime, 字符数)；非法时抛 ImageDataUrlError（带稳定码），入口层翻译成 Err 给面板。
    """
    text = str(data_url or "").strip()
    if not text.startswith("data:"):
        raise ImageDataUrlError("image_not_data_url", "image must be a data: URL")
    if len(text) > max_chars:
        raise ImageDataUrlError("image_too_large", "image too large")
    header = text.split(",", 1)[0]
    mime = header[len("data:"):].split(";", 1)[0].strip().lower()
    if mime not in _PANEL_BG_MIMES:
        raise ImageDataUrlError("image_type_unsupported", f"unsupported image type: {mime or 'unknown'}")
    if "," not in text:
        raise ImageDataUrlError("image_malformed", "malformed data: URL")
    return mime, len(text)


# ---- 图库索引纯操作 ----


def gallery_new_index() -> JsonObject:
    return {"items": [], "next": 1}


def gallery_normalize_index(raw: Any) -> JsonObject:
    """索引记录宽容归一：坏结构回退空索引，条目缺字段补默认、脏字段剔除。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return gallery_new_index()
    items: list[JsonObject] = []
    for item in raw["items"]:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("id") or "").strip()
        if not gid or len(gid) > 64:
            continue
        thumb = str(item.get("thumb") or "")
        if len(thumb) > _GALLERY_THUMB_MAX_CHARS:
            thumb = ""
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        items.append({
            "id": gid,
            "name": str(item.get("name") or "")[:80],
            "mime": str(item.get("mime") or "")[:40],
            "size": max(0, size),
            "added_at": str(item.get("added_at") or "")[:32],
            "thumb": thumb,
        })
    try:
        nxt = int(raw.get("next") or 1)
    except (TypeError, ValueError):
        nxt = 1
    return {"items": items, "next": max(nxt, len(items) + 1)}


def gallery_next_id(index: JsonObject) -> str:
    """取下一个条目 id（g1、g2…），并把索引的 next 推进。"""
    gid = f"g{int(index.get('next') or 1)}"
    index["next"] = int(index.get("next") or 1) + 1
    return gid


def gallery_img_key(item_id: str) -> str:
    return _GALLERY_IMG_PREFIX + item_id


def gallery_find(index: JsonObject, item_id: str) -> JsonObject | None:
    for item in index.get("items") or []:
        if item.get("id") == item_id:
            return item
    return None


def gallery_add_item(index: JsonObject, item: JsonObject) -> tuple[JsonObject, bool]:
    """向索引追加条目；超上限时拒绝（返回 (index, False) 由入口层报错）。"""
    items = index.setdefault("items", [])
    if len(items) >= _GALLERY_MAX_ITEMS:
        return index, False
    items.append(item)
    return index, True


def gallery_remove_item(index: JsonObject, item_id: str) -> bool:
    """从索引移除条目；返回是否删掉了东西。"""
    items = index.get("items") or []
    kept = [item for item in items if item.get("id") != item_id]
    changed = len(kept) != len(items)
    index["items"] = kept
    return changed


# ---- 旧版单图背景迁移 ----


def legacy_to_gallery(
    legacy: Any,
) -> tuple[str, JsonObject, JsonObject, JsonObject] | None:
    """panel_bg 旧记录 → (item_id, 索引追加条目, 图片记录, 初版外观参数)。

    非法/空记录返回 None（按全新用户给默认外观）。name 留空由面板显示
    兜底文案；thumb 留空由面板生成后经 gallery_set_thumb 回填。
    """
    if not isinstance(legacy, dict):
        return None
    data_url = str(legacy.get("data_url") or "").strip()
    if not data_url:
        return None
    try:
        mime, size = parse_image_data_url(data_url)
    except ValueError:
        return None
    item = {
        "id": _LEGACY_BG_ID,
        "name": "",
        "mime": mime,
        "size": size,
        "added_at": "",
        "thumb": "",
    }
    image = {"data_url": data_url, "mime": mime, "size": size, "added_at": ""}
    try:
        legacy_dim = float(legacy.get("dim"))
    except (TypeError, ValueError):
        legacy_dim = _PANEL_BG_DEFAULT_DIM
    appearance = clamp_appearance({"bg_id": _LEGACY_BG_ID, "dim": legacy_dim})
    return _LEGACY_BG_ID, item, image, appearance
