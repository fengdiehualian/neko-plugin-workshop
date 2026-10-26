# -*- coding: utf-8 -*-
"""统一帮助系统(插件端)。

游戏只提供 ``data/config/<game>/help.json``; 分组/别名/语义图标/版式块都是**数据**,
配色与排版由插件主题统一解决(默认官方 UI Kit 风格)。

    from core.help import HelpRenderer, normalize_help, resolve_topic, split_help_intent

    doc = normalize_help(raw, game_id, game_name)
    page = resolve_topic(doc, topic)          # 三层寻址: 游戏 → 分组 → 指令
    pages = await renderer.render(doc, page)  # [PNG bytes]
"""
from .bridge import RenderBridge  # noqa: F401
from .contract import (  # noqa: F401
    HELP_INTENT_WORDS,
    Command,
    Group,
    HelpDoc,
    Page,
    auto_groups,
    normalize_help,
    resolve_topic,
    split_help_intent,
)
from .renderer import HelpRenderer  # noqa: F401
from .themes import AssetResolver, icon_glyph, theme_tokens, theme_veil  # noqa: F401

__all__ = [
    "HELP_INTENT_WORDS",
    "AssetResolver",
    "Command",
    "Group",
    "HelpDoc",
    "HelpRenderer",
    "Page",
    "RenderBridge",
    "auto_groups",
    "icon_glyph",
    "normalize_help",
    "resolve_topic",
    "split_help_intent",
    "theme_tokens",
    "theme_veil",
]
