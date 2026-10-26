# -*- coding: utf-8 -*-
"""帮助主题: 官方 UI Kit 的设计 token + 插件自有素材解析。

token 逐字取自官方 UI Kit 样式表
``frontend/plugin-manager/src/components/plugin/hosted/ui-kit/styles.css``:
亮色 ``:root`` / 暗色 ``prefers-color-scheme: dark``。帮助图因此与官方托管 UI 同款长相。

素材(全部来自插件自身, 离线可用, 渲染时以 data URI 内联):
- ``assets/icon.jpg``            猫娘图标 → 头像
- ``assets/media/yui竖.webp``     汉服猫娘云海 → 整页背景
- ``static/img/yui-hero.webp``   透明抠图 → 右下角立绘
- ``static/img/logo-icon.png``   插件徽章 → 页脚品牌
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
from typing import Dict, Optional

log = logging.getLogger(__name__)

# ── 官方 UI Kit token(照抄) ─────────────────────────────────────────
TOKENS_LIGHT = {
    "radius-sm": "8px", "radius-md": "12px", "radius-lg": "16px", "radius-xl": "20px",
    "bg": "#f7f9fc",
    "surface": "rgba(255,255,255,0.90)",
    "surface-strong": "rgba(255,255,255,0.98)",
    "text": "#1f2937", "muted": "#667085",
    "border": "rgba(148,163,184,0.36)",
    "primary": "#409eff", "success": "#67c23a", "warning": "#e6a23c",
    "danger": "#f56c6c", "info": "#14b8a6",
    "shadow-soft": "0 12px 36px rgba(15,23,42,0.12)",
}

TOKENS_DARK = {
    "radius-sm": "8px", "radius-md": "12px", "radius-lg": "16px", "radius-xl": "20px",
    "bg": "#0f172a",
    "surface": "rgba(15,23,42,0.78)",
    "surface-strong": "rgba(17,24,39,0.94)",
    "text": "#e5e7eb", "muted": "#94a3b8",
    "border": "rgba(148,163,184,0.22)",
    "primary": "#409eff", "success": "#67c23a", "warning": "#e6a23c",
    "danger": "#f56c6c", "info": "#14b8a6",
    "shadow-soft": "0 18px 48px rgba(0,0,0,0.22)",
}

THEMES = {"light": TOKENS_LIGHT, "dark": TOKENS_DARK}

# 暗色主题下背景图要压得更重, 否则浅色云海会把卡片对比度拉平
VEIL = {
    "light": ("linear-gradient(104deg, rgba(247,249,252,0.90) 0%, "
              "rgba(247,249,252,0.80) 38%, rgba(247,249,252,0.46) 62%, "
              "rgba(247,249,252,0.18) 88%, rgba(247,249,252,0.06) 100%)"),
    "dark": ("linear-gradient(104deg, rgba(15,23,42,0.94) 0%, "
             "rgba(15,23,42,0.86) 38%, rgba(15,23,42,0.62) 62%, "
             "rgba(15,23,42,0.34) 88%, rgba(15,23,42,0.18) 100%)"),
}

# 语义图标名 → 各主题的中性兜底(emoji)。主题可以覆盖成自己的图形。
ICON_FALLBACK: Dict[str, str] = {
    "bag": "🎒", "sword": "⚔️", "sect": "🏯", "pet": "🐾", "furnace": "⚗️",
    "coin": "🪙", "meditate": "🌱", "daily": "📅", "heart": "💞", "star": "🌌",
    "flame": "😈", "weapon": "🗡️", "armor": "🛡️", "trinket": "💍", "herb": "🌿",
    "pill": "💊", "book": "📖", "gem": "💎", "misc": "🎲", "help": "💡",
}


def theme_tokens(name: str = "light") -> str:
    """把 token 拼成 CSS 变量块。未知主题名一律回退亮色。"""
    tokens = THEMES.get((name or "").strip().lower(), TOKENS_LIGHT)
    return "\n".join(f"  --{k}:{v};" for k, v in tokens.items())


def theme_veil(name: str = "light") -> str:
    return VEIL.get((name or "").strip().lower(), VEIL["light"])


def icon_glyph(name: str, override: Optional[Dict[str, str]] = None) -> str:
    """语义图标名 → 展示字形。主题可通过 override 换成自己的图形。"""
    key = (name or "").strip().lower()
    if override and key in override:
        return override[key]
    return ICON_FALLBACK.get(key, "🎯")


class AssetResolver:
    """把插件素材解析成可内联的 data URI(带缓存与降采样)。"""

    #: 素材在"外部素材库"里的 id —— 包体里不再带图片，首次运行时按这些 id
    #: 从素材源(Gitee 主源/GitHub 兜底)下载补齐到缓存；本地已有则直接用本地。
    #: 见 adapters/asset_store.py 与 core/help/renderer.py::_prepare_assets()
    ASSET_IDS: Dict[str, str] = {
        "avatar": "ui/icon.webp",           # 猫娘图标 → 头像
        "bg": "ui/media/yui竖.webp",         # 整页背景
        "mascot": "panel/yui-hero.webp",     # 右下角立绘
        "brand": "panel/logo-icon.webp",     # 页脚徽章
    }

    SPEC = {
        # key: (相对路径, 最大宽度, JPEG 质量 or None, 是否强制丢 alpha)
        "avatar": ("assets/icon.jpg", 256, None, False),           # 猫娘图标 → 头像
        "bg": ("assets/media/yui竖.webp", 820, 86, True),          # 整页背景(丢 alpha 转 JPEG)
        "mascot": ("static/img/yui-hero.webp", 320, None, False),  # 透明抠图 → 保留 alpha
        "brand": ("static/img/logo-icon.png", 64, None, False),   # 页脚徽章
    }

    def __init__(self, code_dir: str, cache_dir: str = ""):
        self.code_dir = code_dir or ""
        self.cache_dir = cache_dir or ""
        self._memo: Dict[str, str] = {}
        #: 由 renderer 解析后注入的"素材已就位路径"（优先于 code_dir 里的相对路径）。
        #: 正常来自 AssetStore：本地有就用本地，没有就首次运行从 Gitee 下载到缓存。
        self._paths: Dict[str, str] = {}
        if self.cache_dir:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
            except OSError as exc:      # 只读环境不致命: 退化为每次现算
                log.debug("帮助素材缓存目录不可写: %s", exc)
                self.cache_dir = ""

    def set_paths(self, mapping: Dict[str, str]) -> None:
        """注入素材的实际文件路径（AssetStore 解析结果）。路径变了要清 memo。"""
        fresh = {k: v for k, v in (mapping or {}).items() if v}
        if not fresh:
            return
        self._paths.update(fresh)
        self._memo.clear()

    # ── 对外 ──────────────────────────────────────────────
    def uri(self, key: str) -> str:
        """返回 data URI; 素材缺失返回空串(渲染层会优雅降级)。"""
        if key in self._memo:
            return self._memo[key]
        spec = self.SPEC.get(key)
        uri = ""
        if spec:
            located = self._paths.get(key) or spec[0]
            path = located if os.path.isabs(located) \
                else os.path.join(self.code_dir, str(located).replace("/", os.sep))
            if os.path.isfile(path):
                uri = self._encode(path, key, spec[1], spec[2],
                                   spec[3] if len(spec) > 3 else False) or ""
            else:
                log.debug("帮助素材缺失: %s", path)
        self._memo[key] = uri
        return uri

    # ── 内部 ──────────────────────────────────────────────
    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"help-asset-{key}.txt") if self.cache_dir else ""

    def _encode(self, path: str, key: str, max_width: Optional[int],
                quality: Optional[int], force_rgb: bool = False) -> str:
        stamp = self._stamp(path) + (":rgb" if force_rgb else "")
        cache = self._cache_path(key)
        if cache and os.path.isfile(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    head, _, body = fh.read().partition("\n")
                if head.strip() == stamp and body.strip():
                    return body.strip()
            except OSError:
                pass
        uri = self._render_uri(path, max_width, quality, force_rgb)
        if cache and uri:
            try:
                with open(cache, "w", encoding="utf-8") as fh:
                    fh.write(stamp + "\n" + uri)
            except OSError:
                pass
        return uri

    @staticmethod
    def _stamp(path: str) -> str:
        try:
            st = os.stat(path)
            return hashlib.md5(f"{path}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()
        except OSError:
            return "na"

    @staticmethod
    def _render_uri(path: str, max_width: Optional[int], quality: Optional[int],
                    force_rgb: bool = False) -> str:
        raw: Optional[bytes] = None
        mime = "image/png"
        try:
            from PIL import Image
        except ImportError:
            Image = None  # type: ignore[assignment]
        if Image is not None and (max_width or force_rgb):
            try:
                with Image.open(path) as im:
                    if max_width and im.width > max_width:
                        ratio = max_width / float(im.width)
                        im = im.resize((max_width, max(1, int(im.height * ratio))),
                                       Image.LANCZOS)
                    buf = io.BytesIO()
                    if quality and (force_rgb or im.mode not in ("RGBA", "LA", "P")):
                        im.convert("RGB").save(buf, format="JPEG", quality=quality,
                                               optimize=True)
                        mime = "image/jpeg"
                    else:
                        im.convert("RGBA").save(buf, format="PNG", optimize=True)
                    raw = buf.getvalue()
            except Exception as exc:       # 任何解码/缩放问题都退回原文件
                log.debug("帮助素材处理失败, 用原图: %s", exc)
                raw = None
        if raw is None:
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
            except OSError:
                return ""
            mime = "image/webp" if path.lower().endswith(".webp") else "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
