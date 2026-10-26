# -*- coding: utf-8 -*-
"""统一帮助渲染器测试: 页面组装(官方 UI Kit 视觉) + 多页 + 缓存 + 降级。

不依赖真实浏览器: 用一个假的图片渲染器记录收到的 HTML。
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import List, Tuple

from plugin.plugins.neko_arcade.core.help import (
    HelpRenderer,
    normalize_help,
    resolve_topic,
)
from plugin.plugins.neko_arcade.core.help.themes import THEMES, icon_glyph, theme_tokens

RAW = {
    "text": "诸天修仙 —— 猫娘陪你修仙。",
    "commands": [["踏入仙途", "创建角色"], ["修炼", "获得修为"]],
    "title": "诸天修仙",
    "subtitle": "猫娘陪你修仙",
    "groups": [
        {"id": "bag", "name": "装备与道具", "icon": "bag", "aliases": ["纳戒"],
         "blocks": [
             {"type": "slots", "title": "装备栏", "items": [
                 {"icon": "sword", "name": "武器", "sub": "剑/刀/枪"}]},
             {"type": "bag", "title": "纳戒背包", "cols": 8, "cells": 16, "filled": [0],
              "icons": ["pill"]},
         ],
         "commands": [["我的纳戒", "查看背包"], {"cmd": "装备 X", "desc": "穿戴装备",
                                             "params": ["装备名"]}]},
        {"id": "battle", "name": "战斗挑战", "icon": "sword", "aliases": ["战斗"],
         "blocks": [{"type": "table", "title": "冷却", "rows": [["打劫", "CD 60s"]]}],
         "commands": [["打劫", "打劫散修"]]},
        {"id": "life", "name": "生活职业", "icon": "furnace", "aliases": ["炼丹"],
         "blocks": [{"type": "flow", "title": "丹药线", "items": [
             {"icon": "herb", "name": "采药", "sub": "草药"},
             {"icon": "pill", "name": "炼丹", "sub": "成品"}]}],
         "commands": [["采药", "采药(CD 120s)"]]},
    ],
}


class FakeImg:
    """记录 render_html 收到的 HTML, 返回固定 PNG 字节。"""

    def __init__(self, fail: bool = False):
        self.calls: List[Tuple[str, str, int]] = []
        self.fail = fail

    async def render_html(self, html: str, css: str = "", width: int = 720,
                          height: int = 600, game_name: str = "",
                          selector: str = "body", brand_footer: bool = False):
        self.calls.append((html, selector, width))
        return None if self.fail else b"\x89PNG-fake"


def _renderer(tmp: str = "", img=None) -> HelpRenderer:
    return HelpRenderer(img or FakeImg(), code_dir=tmp, cache_dir=tmp)


def _doc():
    return normalize_help(RAW, "xiuxian", "诸天修仙")


# ── 视觉: 必须复用官方 token 与类名 ─────────────────────
def test_html_uses_official_tokens_and_classes() -> None:
    r = _renderer()
    html = r.build_html(_doc(), resolve_topic(_doc(), ""), theme="light")
    assert "--bg:#f7f9fc" in html                 # 官方亮色 token
    assert "--primary:#409eff" in html            # 官方主色
    assert "--radius-md:12px" in html
    for cls in ('class="page"', 'class="card"', 'class="badge"', 'class="tip"',
                'class="stat"', 'class="chips"'):
        assert cls in html, cls


def test_dark_theme_switches_tokens() -> None:
    r = _renderer()
    html = r.build_html(_doc(), resolve_topic(_doc(), ""), theme="dark")
    assert "--bg:#0f172a" in html and "--text:#e5e7eb" in html
    assert THEMES["dark"]["bg"] == "#0f172a"


def test_theme_tokens_helper() -> None:
    assert "--primary:#409eff" in theme_tokens("light")
    assert "--bg:#0f172a" in theme_tokens("dark")
    assert theme_tokens("不存在的主题") == theme_tokens("light")   # 未知回退亮色
    assert icon_glyph("sword") and icon_glyph("未知语义名")


# ── 页面类型 ────────────────────────────────────────────
def test_catalog_page_lists_all_groups() -> None:
    doc = _doc()
    html = _renderer().build_html(doc, resolve_topic(doc, ""))
    assert "功能分组" in html and "指令总数" in html
    for name in ("装备与道具", "战斗挑战", "生活职业"):
        assert name in html


def test_group_page_renders_blocks_and_command_table() -> None:
    doc = _doc()
    page = resolve_topic(doc, "纳戒")
    html = _renderer().build_html(doc, page)
    assert "装备栏" in html and "纳戒背包" in html     # slots / bag 块
    assert 'class="slot"' in html and 'class="cell on"' in html
    assert "我的纳戒" in html and "装备 X" in html     # 指令表
    assert "同一分类" not in html                      # 分组页不出"相关指令"


def test_flow_and_table_blocks() -> None:
    doc = _doc()
    life = _renderer().build_html(doc, resolve_topic(doc, "炼丹"))
    assert 'class="step"' in life and "丹药线" in life
    battle = _renderer().build_html(doc, resolve_topic(doc, "战斗"))
    assert "<table" in battle and "CD 60s" in battle


def test_command_page_shows_usage_related_and_tip() -> None:
    doc = _doc()
    page = resolve_topic(doc, "装备 X")
    html = _renderer().build_html(doc, page)
    assert "用法" in html and "装备名" in html         # 参数表
    assert "同一分类的其他指令" in html                # 关联指令
    assert "直接发" in html                            # 用法提示


def test_miss_hint_on_catalog() -> None:
    doc = _doc()
    page = resolve_topic(doc, "不存在")
    html = _renderer().build_html(doc, page)
    assert "没找到" in html and "你是想看" in html


def test_help_follows_command_hierarchy_single_page() -> None:
    """指令分级: 一级目录一页给全, 分组页一页; 不能一次刷一堆图。"""
    r = _renderer()
    doc = _doc()
    catalog = r._build_pages(doc, resolve_topic(doc, ""))
    assert len(catalog) == 1, f"目录页应单页, got {len(catalog)}"
    group = r._build_pages(doc, resolve_topic(doc, "纳戒"))
    assert len(group) == 1, f"分组页应单页, got {len(group)}"


def test_flat_page_for_old_format() -> None:
    doc = normalize_help({"text": "老帮助", "commands": [["开始", "开始游戏"]]},
                         "fishing", "钓鱼")
    html = _renderer().build_html(doc, resolve_topic(doc, ""))
    assert "开始" in html and "<table" in html


def test_escaping_in_help_text() -> None:
    doc = normalize_help({"commands": [["<script>x</script>", "a & b"]]}, "g", "G")
    html = _renderer().build_html(doc, resolve_topic(doc, ""))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html and "a &amp; b" in html


# ── 渲染桥接底座: 自定义页面(游戏只给数据) ───────────────
SPEC = {
    "title": "今日战绩", "subtitle": "第 3 天", "role": "喵喵陪你玩",
    "blocks": [{"type": "table", "title": "冷却", "rows": [["打劫", "CD 60s"]]}],
    "commands": [["签到", "每日签到"], {"cmd": "修炼", "desc": "获得修为"}],
    "rows": [["得分", "100"]],
    "chips": ["连击", "满勤"],
    "tip": "加油喵",
}


def test_custom_page_assembles_every_data_shape() -> None:
    """桥接传入的数据(块/表格/指令/胶囊/提示)都要落到官方组件上。"""
    html = _renderer()._spec_page(SPEC)
    for probe in ("今日战绩", "第 3 天", "CD 60s", "签到", "每日签到", "修炼",
                  "获得修为", "得分", "连击", "加油喵"):
        assert probe in html, probe
    assert 'class="table"' in html and 'class="chip"' in html and 'class="tip"' in html


def test_cards_and_stats_blocks() -> None:
    """cards(天赋/成就) 与 stats(属性进度条) 是游戏侧"自带图"迁到插件的表达方式。"""
    html = _renderer()._spec_page({
        "title": "人生总结",
        "blocks": [
            {"type": "cards", "title": "天赋", "cols": 3, "items": [
                {"icon": "heart", "name": "天生丽质", "desc": "颜值 +2"}]},
            {"type": "stats", "title": "属性", "items": [
                {"icon": "book", "label": "智力", "value": 8, "max": 10,
                 "note": "天才"}]},
        ],
    })
    assert "天生丽质" in html and "颜值 +2" in html
    assert 'class="cardgrid"' in html and "repeat(3,1fr)" in html
    assert "智力" in html and 'class="bar"' in html and "width:80%" in html
    assert "天才" in html


def test_stats_block_clamps_percent() -> None:
    """数值超上限/为 0 都不能把进度条画爆或画负。"""
    html = _renderer()._spec_page({
        "title": "t", "blocks": [{"type": "stats", "items": [
            {"label": "a", "value": 999, "max": 10},
            {"label": "b", "value": 0, "max": 10},
            {"label": "c", "value": 5, "max": 0},
        ]}]})
    assert "width:100%" in html and "width:0%" in html


def test_table_block_headers_are_configurable() -> None:
    """表格表头随内容走: 人生轨迹应是"年龄/事件", 不是"指令/说明"。"""
    html = _renderer()._spec_page({
        "title": "t", "blocks": [{"type": "table", "title": "人生轨迹",
                                  "headers": ["年龄", "事件"],
                                  "rows": [["0岁", "出生"]]}]})
    assert "年龄" in html and "事件" in html and "指令" not in html


def test_render_custom_goes_through_renderer_and_cache() -> None:
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_help_cache_tmp2")
    os.makedirs(tmp, exist_ok=True)
    try:
        img = FakeImg()
        r = _renderer(tmp=tmp, img=img)
        pages = asyncio.run(r.render_custom(SPEC, theme="light"))
        assert pages == [b"\x89PNG-fake"]
        assert len(img.calls) == 1
        assert "今日战绩" in img.calls[0][0]
        asyncio.run(r.render_custom(SPEC, theme="light"))
        assert len(img.calls) == 1              # 命中缓存
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 多页 / 缓存 / 降级 ──────────────────────────────────
def test_long_group_paginates() -> None:
    many = [{"id": "big", "name": "大分组", "icon": "star",
             "commands": [[f"指令{i}", f"说明{i}"] for i in range(50)]}]
    doc = normalize_help({"groups": many}, "g", "G")

    async def run() -> int:
        img = FakeImg()
        pages = await _renderer(img=img).render(doc, resolve_topic(doc, "大分组"))
        return len(pages)

    assert asyncio.run(run()) > 1


def test_render_returns_empty_without_browser() -> None:
    doc = _doc()

    async def run() -> list:
        return await _renderer(img=FakeImg(fail=True)).render(doc, resolve_topic(doc, ""))

    assert asyncio.run(run()) == []          # 调用方据此继续降级


def test_render_caches_by_html_hash() -> None:
    doc = _doc()
    # 不用 tempfile: 受限环境下系统临时目录可能不可写, 用仓库内目录并在结束时清理
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_help_cache_tmp")
    os.makedirs(tmp, exist_ok=True)
    try:
        img = FakeImg()
        r = _renderer(tmp=tmp, img=img)

        async def run() -> list:
            return await r.render(doc, resolve_topic(doc, ""))

        first = asyncio.run(run())
        assert first and len(img.calls) == 1
        second = asyncio.run(run())
        assert second == first
        assert len(img.calls) == 1           # 命中缓存 → 不再起浏览器
        assert any(n.startswith("help-") and n.endswith(".png")
                   for n in os.listdir(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
