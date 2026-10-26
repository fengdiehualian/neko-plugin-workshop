# -*- coding: utf-8 -*-
"""渲染桥接: 插件把图片渲染能力以"数据进、图片出"的方式暴露给游戏。

**游戏不参与渲染**——不写 HTML/CSS、不碰 PIL、不碰浏览器、不管主题:

    # 游戏里(只给数据)
    pages = await self.render_help(topic="纳戒")            # 本游戏帮助图
    png   = await self.render_page("今日战绩", blocks=[...])  # 任意版式页面
    await self.send_page("今日战绩", blocks=[...])            # 渲染 + 推送一条龙

桥接内部统一走 :class:`HelpRenderer`(官方 UI Kit 视觉 + 主题 + 宿主浏览器),
渲染失败返回空, 由调用方降级(游戏无需关心)。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .contract import HelpDoc, normalize_help, resolve_topic
from .renderer import HelpRenderer

log = logging.getLogger(__name__)


class RenderBridge:
    """游戏可用的图片渲染入口(由插件主体构造并注入到游戏基类)。"""

    def __init__(self, renderer: Optional[HelpRenderer] = None,
                 config_manager: Any = None, push_sender: Any = None) -> None:
        self._renderer = renderer
        self._cfg_mgr = config_manager
        self._push = push_sender

    # ── 主题 ──────────────────────────────────────────────
    def theme(self) -> str:
        """当前帮助/图片主题(light/dark), 来自插件主配置, 缺省官方亮色。"""
        try:
            cfg = (self._cfg_mgr.load_main_config() or {}) if self._cfg_mgr else {}
        except Exception:
            cfg = {}
        section = cfg.get("help") if isinstance(cfg.get("help"), dict) else {}
        name = str(section.get("theme") or cfg.get("help_theme") or "light").lower()
        return name if name in ("light", "dark") else "light"

    @property
    def ready(self) -> bool:
        return self._renderer is not None

    # ── 帮助图 ────────────────────────────────────────────
    async def help(self, game_id: str, game_name: str = "", raw_help: Any = None,
                   topic: str = "", theme: str = "") -> List[bytes]:
        """渲染某游戏的帮助图(支持功能主题); 无浏览器/失败返回空列表。"""
        if not self.ready:
            return []
        doc = normalize_help(raw_help or {}, game_id, game_name)
        page = resolve_topic(doc, topic)
        return await self._renderer.render(doc, page, theme=theme or self.theme())

    # ── 通用页面(游戏自定义内容, 仍由插件渲染) ─────────────
    async def page(self, title: str, subtitle: str = "", blocks: Any = None,
                   rows: Any = None, commands: Any = None, chips: Any = None,
                   tip: str = "", theme: str = "", game_id: str = "") -> List[bytes]:
        """渲染一个自定义页面(官方组件 + 版式块)。游戏只给数据。

        blocks 支持 chips/table/steps/flow/slots/bag/text;
        rows 是简单两列表格; commands 是 [[指令, 说明], ...] 或对象形式。
        """
        if not self.ready:
            return []
        spec: Dict[str, Any] = {
            "game_id": game_id, "title": title, "subtitle": subtitle,
            "blocks": list(blocks or []), "rows": list(rows or []),
            "commands": list(commands or []), "chips": list(chips or []),
            "tip": tip, "theme": theme,
        }
        return await self._renderer.render_custom(spec, theme=theme or self.theme())

    # ── 渲染 + 推送(游戏连推送都不用管) ────────────────────
    async def send(self, title: str, subtitle: str = "", blocks: Any = None,
                   rows: Any = None, commands: Any = None, chips: Any = None,
                   tip: str = "", text: str = "", theme: str = "",
                   game_id: str = "") -> Dict[str, Any]:
        """渲染并推送一条图片消息。返回 {ok, pages, summary}。"""
        pages = await self.page(title, subtitle=subtitle, blocks=blocks, rows=rows,
                                commands=commands, chips=chips, tip=tip,
                                theme=theme, game_id=game_id)
        if not pages:
            return {"ok": False, "pages": 0, "summary": "图片渲染不可用喵"}
        if self._push:
            for i, png in enumerate(pages):
                cap = title if len(pages) == 1 else f"{title} ({i + 1}/{len(pages)})"
                await self._push.help_doc(cap, png, text if i == 0 else None)
        return {"ok": True, "pages": len(pages), "summary": f"已发送「{title}」"}

    async def send_help(self, game_id: str, game_name: str = "", raw_help: Any = None,
                        topic: str = "", text: str = "") -> Dict[str, Any]:
        """渲染并推送某游戏的帮助图(带主题)。返回 {ok, pages, summary}。"""
        pages = await self.help(game_id, game_name=game_name, raw_help=raw_help,
                                topic=topic)
        if not pages:
            return {"ok": False, "pages": 0, "summary": "帮助图渲染不可用喵"}
        label = game_name or game_id
        if self._push:
            for i, png in enumerate(pages):
                cap = f"{label} 帮助" + (f" ({i + 1}/{len(pages)})" if len(pages) > 1 else "")
                await self._push.help_doc(cap, png, text if i == 0 else None)
        return {"ok": True, "pages": len(pages), "summary": f"已发送 {label} 的帮助喵"}


__all__ = ["RenderBridge", "HelpDoc"]
