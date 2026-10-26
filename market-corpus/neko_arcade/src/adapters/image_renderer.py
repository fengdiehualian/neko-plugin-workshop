"""图片渲染：猫娘表情头像 + 结果卡片 + 帮助文档图（纯 PIL 绘制）。

帮助文档图另有一条浏览器(Playwright/Chromium)渲染通道 render_help_html，
排版更精细；无浏览器环境回退本文件的 PIL 版 render_help。

浏览器解析**优先走宿主内置**(见 _host_browser_hint / _find_chromium):
1. 宿主自己的查找函数 ``brain.browser_use_adapter._find_chrome_path``(插件运行在宿主进程内,
   与宿主浏览器完全同源)
2. ``browser_use`` 自带的系统浏览器查找
3. 宿主 Launcher 设置的 ``PLAYWRIGHT_BROWSERS_PATH`` 与应用自带的 playwright_browsers
4. 系统 ms-playwright 缓存 / 系统已装 Chrome / Edge
"""

from __future__ import annotations

import glob
import html as _html
import importlib
import importlib.util
import io
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("neko_arcade.image")


def _esc(text: Any) -> str:
    """HTML 转义(帮助图里的指令/描述可能含 & < > 等字符)。"""
    return _html.escape(str(text), quote=True)


RARITY_COLORS = {
    "common": "#8a7e72", "uncommon": "#4a9e5f", "rare": "#3a7bd5",
    "epic": "#9b59b6", "legendary": "#d4a84b", "？": "#e74c3c",
}

MOOD_COLORS = {
    "excitement": "#ffd9a8", "curiosity": "#ffe9c8", "proud": "#ffd9a8",
    "upset": "#dcd2e8", "sleepy": "#e8e4da", "calm": "#ffe9c8",
}

CARD_BG = (250, 247, 240)
CARD_BORDER = (214, 200, 182)
INK = (61, 51, 40)
INK2 = (138, 122, 102)

BRAND_FOOTER = "N.E.K.O 猫娘小游戏"
_CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
]


def _load_cjk_font(size: int):
    """加载支持中文的字体（PIL 默认字体不含 CJK，会渲染成方框）。"""
    try:
        from PIL import ImageFont
        for path in _CJK_FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()
    except Exception:
        return None


class ImageRenderer:
    """用 PIL 绘制猫娘表情、结果卡片、帮助图。"""

    def __init__(self) -> None:
        self._pil_ok = self._check_pil()
        # 聊天窗图片气泡 max-width≈280px；2x DPR 渲染后缩回去仍清晰。
        try:
            self.device_scale = float(os.environ.get("NEKO_ARCADE_IMAGE_SCALE") or 2.0)
        except (TypeError, ValueError):
            self.device_scale = 2.0
        self.device_scale = max(1.0, min(3.0, self.device_scale))

    @staticmethod
    def _check_pil() -> bool:
        return importlib.util.find_spec("PIL") is not None

    # ══════════════════════════════════════════
    # 猫娘表情头像
    # ══════════════════════════════════════════

    def render_neko_avatar(self, mood: str = "calm", size: int = 128) -> Optional[bytes]:
        """绘制一只带表情的猫娘头像（PNG 字节）。"""
        if not self._pil_ok:
            return None
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            cx, cy = size / 2, size / 2
            r = size * 0.32

            # 发色
            hair = (255, 214, 150)
            # 猫耳（左）
            ear = [(cx - r * 1.1, cy - r * 0.9), (cx - r * 0.75, cy - r * 1.9), (cx - r * 0.15, cy - r * 1.0)]
            d.polygon(ear, fill=hair)
            # 猫耳（右）
            ear2 = [(cx + r * 1.1, cy - r * 0.9), (cx + r * 0.75, cy - r * 1.9), (cx + r * 0.15, cy - r * 1.0)]
            d.polygon(ear2, fill=hair)
            # 耳内粉
            inner = [(cx - r * 0.98, cy - r * 1.0), (cx - r * 0.72, cy - r * 1.65), (cx - r * 0.32, cy - r * 0.98)]
            d.polygon(inner, fill=(255, 200, 200))
            inner2 = [(cx + r * 0.98, cy - r * 1.0), (cx + r * 0.72, cy - r * 1.65), (cx + r * 0.32, cy - r * 0.98)]
            d.polygon(inner2, fill=(255, 200, 200))

            # 脸
            d.ellipse([cx - r, cy - r * 0.85, cx + r, cy + r * 1.15], fill=(255, 246, 235))
            # 腮红
            blush = (255, 200, 200)
            d.ellipse([cx - r * 0.9, cy + r * 0.35, cx - r * 0.45, cy + r * 0.62], fill=blush)
            d.ellipse([cx + r * 0.45, cy + r * 0.35, cx + r * 0.9, cy + r * 0.62], fill=blush)

            # 表情
            mood = (mood or "calm").lower()
            if mood in ("excitement", "proud", "curiosity"):
                # 兴奋/得意/好奇：大眼睛 + 星星
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.12, r * 0.16, "open")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.12, r * 0.16, "open")
                if mood == "excitement":
                    self._draw_sparkle(d, cx + r * 0.75, cy - r * 0.7, r * 0.1)
                    self._draw_sparkle(d, cx - r * 0.75, cy - r * 0.6, r * 0.07)
            elif mood == "upset":
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.1, r * 0.13, "droop")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.1, r * 0.13, "droop")
                # 泪珠
                d.ellipse([cx - r * 0.5, cy + r * 0.1, cx - r * 0.32, cy + r * 0.28], fill=(150, 200, 255))
            elif mood == "sleepy":
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.1, r * 0.13, "closed")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.1, r * 0.13, "closed")
                # zzz
                self._draw_zzz(d, cx + r * 0.7, cy - r * 0.9, r * 0.08)
            else:
                # calm 微笑弯眼
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.08, r * 0.13, "smile")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.08, r * 0.13, "smile")

            # 嘴巴
            if mood in ("excitement", "proud"):
                d.arc([cx - r * 0.3, cy + r * 0.3, cx + r * 0.3, cy + r * 0.72], 20, 160, fill=INK, width=max(2, int(size * 0.02)))
            elif mood == "upset":
                d.arc([cx - r * 0.3, cy + r * 0.4, cx + r * 0.3, cy + r * 0.78], 200, 340, fill=INK, width=max(2, int(size * 0.02)))
            elif mood == "sleepy":
                d.ellipse([cx - r * 0.12, cy + r * 0.45, cx + r * 0.12, cy + r * 0.6], fill=INK)
            else:
                d.arc([cx - r * 0.28, cy + r * 0.28, cx + r * 0.28, cy + r * 0.7], 20, 160, fill=INK, width=max(2, int(size * 0.02)))

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("绘制猫娘头像失败: %s", exc)
            return None

    def _draw_eye(self, d, x: float, y: float, r: float, style: str) -> None:
        if style == "open":
            d.ellipse([x - r, y - r, x + r, y + r], outline=INK, width=max(1, int(r * 0.4)))
            d.ellipse([x - r * 0.35, y - r * 0.3, x + r * 0.35, y + r * 0.35], fill=INK)
            d.ellipse([x - r * 0.15, y - r * 0.5, x + r * 0.1, y - r * 0.1], fill=(255, 255, 255))
        elif style == "smile":
            d.arc([x - r, y - r, x + r, y + r * 0.6], 30, 150, fill=INK, width=max(1, int(r * 0.4)))
        elif style == "closed":
            d.line([x - r, y, x + r, y], fill=INK, width=max(1, int(r * 0.35)))
        elif style == "droop":
            d.line([x - r, y + r * 0.4, x + r, y - r * 0.2], fill=INK, width=max(1, int(r * 0.35)))

    def _draw_sparkle(self, d, x: float, y: float, r: float) -> None:
        d.polygon([(x, y - r * 2), (x + r * 0.4, y - r * 0.3), (x + r * 2, y),
                   (x + r * 0.4, y + r * 0.3), (x, y + r * 2),
                   (x - r * 0.4, y + r * 0.3), (x - r * 2, y),
                   (x - r * 0.4, y - r * 0.3)], fill=(255, 215, 130))

    def _draw_zzz(self, d, x: float, y: float, r: float) -> None:
        d.text((x, y), "z", fill=(150, 140, 160))
        d.text((x + r * 0.9, y - r * 0.7), "z", fill=(170, 160, 180))
        d.text((x + r * 1.7, y - r * 1.4), "z", fill=(190, 180, 200))

    # ══════════════════════════════════════════
    # 结果卡片（带猫娘表情）
    # ══════════════════════════════════════════

    async def render_card(self, game_name: str, title: str,
                          lines: List[Tuple[str, str]],
                          subtitle: str = "", mood: str = "calm") -> Optional[bytes]:
        """渲染一张精美结果卡片(渐变背景, 无猫娘头像)。"""
        if not self._pil_ok:
            return None
        try:
            from PIL import Image, ImageDraw
            font = _load_cjk_font(14)
            font_title = _load_cjk_font(19)
            font_sub = _load_cjk_font(12)
            w, h = 460, 64 + len(lines) * 34 + (40 if subtitle else 12)
            header_top, header_bot = (96, 74, 60), (158, 118, 84)
            body_top, body_bot = (252, 249, 243), (244, 237, 225)

            img = Image.new("RGB", (w, h), body_top)
            grad = self._v_gradient(w, h, body_top, body_bot)
            img.paste(grad, (0, 0))
            draw = ImageDraw.Draw(img)
            # 顶部渐变标题条
            header = self._v_gradient(w, 52, header_top, header_bot)
            img.paste(header, (0, 0))
            draw = ImageDraw.Draw(img)
            try:
                tw = draw.textlength(title, font=font_title)
            except Exception:
                tw = len(title) * 19
            draw.text(((w - tw) // 2, 10), title, fill=(255, 250, 240), font=font_title)
            draw.line([(0, 51), (w, 51)], fill=(120, 88, 62), width=1)
            # 内容区
            y = 64
            for text, rarity in lines:
                color = RARITY_COLORS.get(rarity, INK)
                # 稀有度色点 + 文字
                draw.ellipse([16, y + 6, 24, y + 14], fill=color)
                draw.text((32, y), text, fill=INK, font=font)
                y += 34
            if subtitle:
                draw.text((18, y), subtitle, fill=INK2, font=font_sub)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("渲染卡片失败: %s", exc)
            return None

    async def render_help(self, game_name: str, commands: List[Tuple[str, str]],
                          footer: str = "") -> Optional[List[bytes]]:
        """渲染精美帮助文档图(渐变背景 + 圆角指令标签, 无猫娘头像)。

        指令过多时自动分页 + 两列紧凑布局, 单页高度 ≤ ~600px,
        避免聊天窗口截断。返回每页的 PNG bytes 列表。
        """
        if not self._pil_ok:
            return None
        try:
            from PIL import Image, ImageDraw
            font_title = _load_cjk_font(18)
            font_cmd = _load_cjk_font(13)
            font = _load_cjk_font(12)
            font_footer = _load_cjk_font(11)
            brand = f"{BRAND_FOOTER} × {game_name}"
            cmds = list(commands or [])
            n = len(cmds)

            # 聊天窗友好: 单列, 指令在上、说明在下; 单页 ~12 条
            per_page = 12
            pages = [cmds[i:i + per_page] for i in range(0, n, per_page)] or [[]]

            W = 340
            HEADER_H = 56
            line_h = 46

            # 顶部标题条 + 页脚渐变色
            header_top, header_bot = (96, 74, 60), (158, 118, 84)
            body_top, body_bot = (252, 249, 243), (244, 237, 225)

            results: List[bytes] = []
            for pi, page in enumerate(pages):
                rows = max(1, len(page))
                extra = (30 if footer else 0) + 34
                h = HEADER_H + 16 + rows * line_h + extra
                img = Image.new("RGB", (W, h), body_top)
                # 背景纵向渐变
                grad = self._v_gradient(W, h, body_top, body_bot)
                img.paste(grad, (0, 0))
                draw = ImageDraw.Draw(img)

                # 顶部标题条(渐变)
                header = self._v_gradient(W, HEADER_H, header_top, header_bot)
                img.paste(header, (0, 0))
                draw = ImageDraw.Draw(img)
                title = f"{game_name} · 玩法帮助"
                if len(pages) > 1:
                    title += f"  ({pi + 1}/{len(pages)})"
                try:
                    tw = draw.textlength(title, font=font_title)
                except Exception:
                    tw = len(title) * 20
                draw.text(((W - tw) // 2, (HEADER_H - 18) // 2), title,
                          fill=(255, 250, 240), font=font_title)
                # 标题条下沿细高光线
                draw.line([(0, HEADER_H - 1), (W, HEADER_H - 1)], fill=(120, 88, 62), width=1)

                # 指令两列: 指令名圆角标签 + 描述
                y = HEADER_H + 12
                for cmd, desc in page:
                    x = 18
                    # 指令名标签(圆角浅金底)
                    cmd_s = str(cmd)
                    try:
                        cw = draw.textlength(cmd_s, font=font_cmd) + 14
                    except Exception:
                        cw = len(cmd_s) * 13 + 14
                    draw.rounded_rectangle(
                        [x, y, x + cw, y + line_h - 18],
                        radius=9, fill=(241, 201, 148), outline=(224, 178, 118), width=1)
                    draw.text((x + 7, y + 2), cmd_s, fill=(110, 74, 42), font=font_cmd)
                    # 描述文字(下一行, 超宽截断)
                    desc_s = str(desc)
                    try:
                        maxw = W - 36
                        if draw.textlength(desc_s, font=font) > maxw:
                            while desc_s and draw.textlength(desc_s + "…", font=font) > maxw:
                                desc_s = desc_s[:-1]
                            desc_s += "…"
                    except Exception:
                        pass
                    draw.text((x + 2, y + 24), desc_s, fill=(70, 58, 46), font=font)
                    y += line_h
                if footer:
                    draw.text((28, y), footer, fill=(138, 122, 102), font=font)
                    y += 30
                # 底部居中品牌落款
                try:
                    bbox = draw.textbbox((0, 0), brand, font=font_footer)
                    tw2 = bbox[2] - bbox[0]
                except Exception:
                    tw2 = len(brand) * 11
                draw.text(((W - tw2) // 2, h - 26), brand, fill=(150, 132, 112), font=font_footer)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                results.append(buf.read())
            return results
        except Exception as exc:
            log.warning("渲染帮助失败: %s", exc)
            return None

    @staticmethod
    def _v_gradient(w: int, h: int, top: Tuple[int, int, int],
                    bottom: Tuple[int, int, int]):
        """生成 w×h 纵向渐变 RGB 图(top→bottom)。"""
        from PIL import Image
        base = Image.new("RGB", (1, h))
        for y in range(h):
            t = y / max(1, h - 1)
            c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            base.putpixel((0, y), c)
        return base.resize((w, h))

    # ══════════════════════════════════════════
    # HTML → PNG（Playwright，复用 N.E.K.O 内置 Chromium）
    # ══════════════════════════════════════════

    HELP_HTML_CSS = """
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body { background:#FCF9F3; }
    body { width:340px; font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif; }
    .card { width:340px; background:linear-gradient(180deg,#FCF9F3 0%,#F4EDE1 100%); }
    .hd { background:linear-gradient(135deg,#604A3C 0%,#9E7654 100%); color:#FFFAF0;
          font-size:17px; font-weight:600; letter-spacing:1px; text-align:center;
          padding:16px 8px; }
    .hd .pg { font-size:14px; font-weight:400; opacity:.85; margin-left:8px; }
    .grid { display:grid; grid-template-columns:1fr; gap:8px; padding:14px 16px 6px; }
    .row { display:flex; flex-direction:column; align-items:flex-start; gap:3px; min-height:25px; }
    .k { flex:0 0 auto; background:#F1C994; border:1px solid #E0B276; color:#6E4A2A;
         border-radius:9px; padding:2px 8px; font-size:13px; white-space:nowrap; }
    .v { color:#463A2E; font-size:12px; overflow:hidden; text-overflow:ellipsis;
         white-space:nowrap; }
    .ft { color:#8A7A66; font-size:12px; line-height:1.7; padding:10px 16px 0; }
    .brand { color:#968470; font-size:11px; text-align:center; padding:14px 0 16px; }
    """

    def build_help_html(self, game_name: str, commands: List[Tuple[str, str]],
                        footer: str = "", page: int = 1, pages: int = 1) -> str:
        """构造帮助文档图的 HTML(与 render_help_html 实际渲染的内容一致)。

        单独抽出来便于本地预览/测试(浏览器截图或用例断言)。
        """
        brand = f"{BRAND_FOOTER} × {game_name}"
        title = f"{game_name} · 玩法帮助"
        pgtag = f'<span class="pg">({page}/{pages})</span>' if pages > 1 else ""
        rows = "".join(
            f'<div class="row"><span class="k">{_esc(c)}</span>'
            f'<span class="v">{_esc(d)}</span></div>' for c, d in commands)
        ft = f'<div class="ft">{_esc(footer)}</div>' if footer and page == 1 else ""
        return (
            f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>{self.HELP_HTML_CSS}</style></head><body>'
            f'<div class="card"><div class="hd">{_esc(title)}{pgtag}</div>'
            f'<div class="grid">{rows}</div>{ft}'
            f'<div class="brand">{_esc(brand)}</div></div></body></html>'
        )

    async def render_help_html(self, game_name: str, commands: List[Tuple[str, str]],
                               footer: str = "", per_page: int = 34) -> Optional[List[bytes]]:
        """用内置浏览器(Playwright/Chromium)渲染帮助文档图。

        与 PIL 版 render_help 同契约: 返回每页 PNG bytes; 环境无浏览器时返回 None,
        调用方回退 PIL 版。HTML/CSS 排版比 PIL 绘制更精细(阴影/圆角/字距/自动换行)。
        """
        cmds = [(str(c[0]), str(c[1])) for c in (commands or [])
                if isinstance(c, (list, tuple)) and len(c) >= 2]
        if not cmds:
            return None
        chunks = [cmds[i:i + per_page] for i in range(0, len(cmds), per_page)] or [cmds]
        pages: List[bytes] = []
        for pi, chunk in enumerate(chunks):
            html = self.build_help_html(game_name, chunk, footer,
                                        page=pi + 1, pages=len(chunks))
            png = await self.render_html(html, width=340, height=600, selector=".card")
            if not png:
                return None
            pages.append(png)
        return pages

    async def render_html(self, html: str, css: str = "", width: int = 720,
                          height: int = 600, game_name: str = "",
                          selector: str = "body", brand_footer: bool = False) -> Optional[bytes]:
        """把 HTML 模板渲染成 PNG（插件端渲染方案，供游戏接入）。

        - 优先用 Playwright(Chromium)，复用 N.E.K.O 内置浏览器；
        - 渲染失败或环境无 Playwright 时返回 None，调用方自行降级；
        - brand_footer=True 时在底部叠加统一品牌落款。
        """
        png = await self._render_html_playwright(html, css, width, height, selector)
        if not png:
            return None
        if brand_footer:
            png = self._append_footer(png, game_name)
        return png

    async def _render_html_playwright(self, html: str, css: str, width: int,
                                      height: int, selector: str) -> Optional[bytes]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.info("Playwright 不可用，跳过 HTML 渲染")
            return None
        try:
            if css:
                html = f"<style>{css}</style>{html}"
            async with async_playwright() as p:
                # ① 宿主原生: 不指定 executable_path, 让宿主自己的 Playwright
                #    按 PLAYWRIGHT_BROWSERS_PATH(Launcher 冻结时注入)找浏览器
                # ② 宿主查找函数/自带目录/系统兜底给出的显式路径
                browser = None
                errors: List[str] = []
                for kwargs in self._launch_kwargs_chain():
                    try:
                        browser = await p.chromium.launch(**kwargs)
                        break
                    except Exception as exc:
                        errors.append(f"{kwargs.get('executable_path', '宿主自带')}: "
                                      f"{str(exc)[:110]}")
                if browser is None:
                    log.warning("Playwright 启动失败(宿主/兜底都不可用): %s",
                                " | ".join(errors))
                    return None
                try:
                    page = await browser.new_page(
                        viewport={"width": width, "height": height},
                        device_scale_factor=self.device_scale)
                    await page.set_content(html, wait_until="networkidle")
                    el = await page.query_selector(selector)
                    if el:
                        return await el.screenshot()
                    return await page.screenshot(full_page=True)
                finally:
                    await browser.close()
        except Exception as exc:
            log.warning("Playwright HTML 渲染失败: %s", exc)
            return None

    @classmethod
    def _launch_kwargs_chain(cls) -> List[Dict[str, Any]]:
        """启动参数链: 宿主浏览器环境优先, 显式路径兜底。

        顺序即"尽量走宿主":
        1. ``{"headless": True}`` —— Playwright 用宿主 Launcher 注入的浏览器目录
        2. ``executable_path = _find_chromium()`` —— 宿主查找函数 / 自带目录 / 系统浏览器
        """
        chain: List[Dict[str, Any]] = [{"headless": True}]
        exe = cls._find_chromium()
        if exe:
            chain.append({"headless": True, "executable_path": exe})
        return chain

    @staticmethod
    def _browser_roots() -> List[str]:
        """收集所有可能的 Playwright 浏览器根目录(按优先级)。

        应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe
        (实测 0.9.0.2 安装包即如此)，因此同时扫描系统 Playwright 缓存
        (%LOCALAPPDATA%\\ms-playwright 等)，找到能跑的那个。
        """
        roots: List[str] = []
        env_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if env_dir:
            roots.append(env_dir)
        here = os.path.dirname(os.path.abspath(__file__))
        starts = [os.path.dirname(os.path.abspath(sys.argv[0] or "")), here, os.getcwd()]
        for start in starts:
            cur = start
            for _ in range(5):  # 应用布局: <app>/resources/bin/playwright_browsers
                cur = os.path.dirname(cur)
                if not cur or cur == os.path.dirname(cur):
                    break
                roots.append(os.path.join(cur, "playwright_browsers"))
        local = os.environ.get("LOCALAPPDATA") or ""
        roaming = os.environ.get("APPDATA") or ""
        home = os.path.expanduser("~")
        roots += [
            os.path.join(local, "ms-playwright") if local else "",
            os.path.join(roaming, "ms-playwright") if roaming else "",
            os.path.join(home, ".cache", "ms-playwright"),
            os.path.join(home, "Library", "Caches", "ms-playwright"),
        ]
        seen: List[str] = []
        for r in roots:
            if r and r not in seen:
                seen.append(r)
        return seen

    @staticmethod
    def _system_browser_candidates() -> List[str]:
        """系统上已装的 Chrome / Edge / Chromium 可执行文件候选。

        与宿主 brain/browser_use_adapter._find_chrome_path 的第二级回退对齐:
        内置 Playwright Chromium 不可用时, 退到系统已装浏览器。
        """
        env = os.environ
        if sys.platform == "win32":
            bases = [env.get("ProgramFiles", r"C:\Program Files"),
                     env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     env.get("LOCALAPPDATA", "")]
            rels = [
                r"Google\Chrome\Application\chrome.exe",
                r"Google\Chrome Beta\Application\chrome.exe",
                r"Microsoft\Edge\Application\msedge.exe",
                r"Chromium\Application\chrome.exe",
            ]
            return [os.path.join(b, r) for b in bases if b for r in rels]
        if sys.platform == "darwin":
            apps = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
            return apps
        return ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium", "/usr/bin/chromium-browser",
                "/usr/bin/microsoft-edge", "/snap/bin/chromium"]

    @classmethod
    def _host_browser_hint(cls) -> Optional[str]:
        """优先向宿主要浏览器(宿主内置)。

        插件运行在宿主进程内, 因此可以直接复用宿主自己的浏览器定位逻辑,
        避免"插件另装一个浏览器"或版本与宿主不一致。
        """
        # 1) 宿主 brain 的查找器: 与宿主自带 Chromium 完全同源
        try:
            mod = importlib.import_module("brain.browser_use_adapter")
        except Exception:
            mod = None
        if mod is not None:
            for attr in ("_find_chrome_path", "_find_bundled_chromium"):
                finder = getattr(mod, attr, None)
                if callable(finder):
                    try:
                        exe = finder()
                    except Exception:
                        continue
                    if exe and os.path.isfile(exe):
                        log.info("帮助图使用宿主内置浏览器: %s", exe)
                        return exe
        # 2) browser_use 自带的系统浏览器查找(宿主同一依赖)
        try:
            from browser_use.browser.watchdogs.local_browser_watchdog import (
                LocalBrowserWatchdog,
            )
            exe = LocalBrowserWatchdog._find_installed_browser_path()
            if exe and os.path.isfile(exe):
                log.info("帮助图使用宿主依赖定位的浏览器: %s", exe)
                return exe
        except Exception:
            pass
        # 3) 宿主 Launcher 注入的 Playwright 浏览器目录
        env_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if env_dir and os.path.isdir(env_dir):
            for pat in (("chromium-*", "chrome-win64", "chrome.exe"),
                        ("chromium-*", "chrome-win", "chrome.exe"),
                        ("chromium-*", "chrome-linux*", "chrome"),
                        ("chromium-*", "chrome-mac", "Chromium.app", "Contents",
                         "MacOS", "Chromium")):
                matches = [p for p in glob.glob(os.path.join(env_dir, *pat))
                           if os.path.isfile(p)]
                if matches:
                    matches.sort()
                    log.info("帮助图使用宿主 PLAYWRIGHT_BROWSERS_PATH: %s", matches[-1])
                    return matches[-1]
        return None

    @classmethod
    def _find_chromium(cls) -> Optional[str]:
        """定位可用的 Chromium 可执行文件。

        优先级(**宿主内置优先**, 与宿主 brain/browser_use_adapter 一致 + 额外兜底):
        1. 宿主自己的查找函数 / browser_use 依赖 / 宿主注入的 PLAYWRIGHT_BROWSERS_PATH
        2. Playwright 浏览器目录(应用自带 playwright_browsers / 系统 ms-playwright 缓存)
        3. 系统已装的 Chrome / Edge / Chromium
        应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe
        (实测 0.9.0.2 安装包如此, 且宿主启动检查只看目录非空 → 不会自动修复),
        因此必须逐个验证文件真实存在, 并保留系统回退。
        """
        host = cls._host_browser_hint()
        if host:
            return host
        patterns = (
            ("chromium-*", "chrome-win64", "chrome.exe"),
            ("chromium-*", "chrome-win", "chrome.exe"),
            ("chromium_headless_shell-*", "chrome-headless-shell-win64", "chrome-headless-shell.exe"),
            ("chromium-*", "chrome-linux", "chrome"),
            ("chromium_headless_shell-*", "chrome-headless-shell-linux64", "chrome-headless-shell"),
            ("chromium-*", "chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
        )
        for browsers_dir in cls._browser_roots():
            if not os.path.isdir(browsers_dir):
                continue
            for pat in patterns:
                matches = [p for p in glob.glob(os.path.join(browsers_dir, *pat))
                           if os.path.isfile(p)]
                if matches:
                    matches.sort()
                    return matches[-1]  # 版本号最大的
        for candidate in cls._system_browser_candidates():
            if os.path.isfile(candidate):
                return candidate
        return None

    def _append_footer(self, png_bytes: bytes, game_name: str) -> Optional[bytes]:
        """在 PNG 底部叠加统一品牌落款条。"""
        if not self._pil_ok:
            return png_bytes
        try:
            from PIL import Image, ImageDraw
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            w, h = img.size
            bar_h = 32
            canvas = Image.new("RGB", (w, h + bar_h), CARD_BG)
            canvas.paste(img, (0, 0))
            d = ImageDraw.Draw(canvas)
            font = _load_cjk_font(13)
            brand = f"{BRAND_FOOTER} × {game_name}" if game_name else BRAND_FOOTER
            try:
                bbox = d.textbbox((0, 0), brand, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            except Exception:
                tw, th = len(brand) * 12, 16
            d.text(((w - tw) / 2, h + (bar_h - th) / 2), brand, fill=INK2, font=font)
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("叠加品牌落款失败: %s", exc)
            return png_bytes

    def _get_avatar(self, mood: str, size: int) -> Optional[Any]:
        """获取猫娘表情头像（PIL Image）。"""
        if not self._pil_ok:
            return None
        from PIL import Image
        data = self.render_neko_avatar(mood, size)
        if not data:
            return None
        buf = io.BytesIO(data)
        buf.seek(0)
        return Image.open(buf).convert("RGBA")
