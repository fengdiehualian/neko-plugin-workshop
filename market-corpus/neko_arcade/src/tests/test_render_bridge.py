# -*- coding: utf-8 -*-
"""渲染桥接测试: 插件提供渲染, 游戏只给数据。

覆盖:
- RenderBridge.help/page/send 的数据契约与推送行为
- GameAdapter(游戏侧) 的 render_help / render_page / send_page
- 桥接不可用时的安全降级(游戏不该因此崩)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugin.plugins.neko_arcade.core.contracts import GameAdapter
from plugin.plugins.neko_arcade.core.help import RenderBridge
from plugin.plugins.neko_arcade.core.help.contract import HelpDoc, Page


class FakeHelpRenderer:
    """替代 HelpRenderer: 记录调用参数, 返回固定 PNG。"""

    def __init__(self, fail: bool = False):
        self.render_calls: List[Tuple[HelpDoc, Page, str]] = []
        self.custom_calls: List[Dict[str, Any]] = []
        self.fail = fail

    async def render(self, doc: HelpDoc, page: Page, theme: str = "light",
                     use_cache: bool = True) -> List[bytes]:
        self.render_calls.append((doc, page, theme))
        return [] if self.fail else [b"\x89PNG-help"]

    async def render_custom(self, spec: Dict[str, Any], theme: str = "",
                            use_cache: bool = True) -> List[bytes]:
        self.custom_calls.append(spec)
        return [] if self.fail else [b"\x89PNG-page"]


class FakePush:
    def __init__(self):
        self.docs: List[Tuple[str, bytes, Optional[str]]] = []
        self.texts: List[str] = []

    async def help_doc(self, title: str, image_bytes: bytes,
                       text: Optional[str] = None) -> None:
        self.docs.append((title, image_bytes, text))

    async def text(self, msg: str, **_: Any) -> None:
        self.texts.append(msg)


class FakeCfgMgr:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg

    def load_main_config(self) -> Dict[str, Any]:
        return self.cfg


RAW = {
    "text": "诸天修仙",
    "groups": [
        {"id": "bag", "name": "装备与道具", "icon": "bag", "aliases": ["纳戒"],
         "commands": [["我的纳戒", "查看背包"]]},
    ],
}


class DemoGame(GameAdapter):
    id = "demo"
    name = "演示游戏"

    async def handle_action(self, user_id: str, cmd: str, args=None) -> Dict[str, Any]:
        return {}


def _run(coro):
    return asyncio.run(coro)


# ── 桥接自身 ────────────────────────────────────────────
def test_bridge_help_resolves_topic_and_theme() -> None:
    r = FakeHelpRenderer()
    bridge = RenderBridge(r, config_manager=FakeCfgMgr({"help": {"theme": "dark"}}))
    pages = _run(bridge.help("demo", game_name="演示游戏", raw_help=RAW, topic="纳戒"))
    assert pages == [b"\x89PNG-help"]
    doc, page, theme = r.render_calls[0]
    assert doc.game_id == "demo"
    assert page.kind == "group" and page.group.name == "装备与道具"
    assert theme == "dark"                      # 主题由插件侧决定, 游戏不用管


def test_bridge_page_passes_data_only() -> None:
    r = FakeHelpRenderer()
    bridge = RenderBridge(r, config_manager=FakeCfgMgr({}))
    blocks = [{"type": "slots", "items": [{"icon": "sword", "name": "武器"}]}]
    pages = _run(bridge.page("今日战绩", subtitle="第 3 天", blocks=blocks,
                             commands=[["签到", "每日签到"]], tip="加油喵"))
    assert pages == [b"\x89PNG-page"]
    spec = r.custom_calls[0]
    assert spec["title"] == "今日战绩" and spec["subtitle"] == "第 3 天"
    assert spec["blocks"] == blocks
    assert spec["commands"] == [["签到", "每日签到"]]
    assert spec["tip"] == "加油喵"
    assert "html" not in spec and "css" not in spec     # 游戏不该传样式


def test_bridge_send_renders_and_pushes() -> None:
    r, push = FakeHelpRenderer(), FakePush()
    bridge = RenderBridge(r, config_manager=FakeCfgMgr({}), push_sender=push)
    out = _run(bridge.send("面板", commands=[["a", "b"]], text="配文"))
    assert out["ok"] and out["pages"] == 1
    assert push.docs and push.docs[0][0] == "面板"
    assert push.docs[0][2] == "配文"


def test_bridge_send_help_uses_game_name_in_caption() -> None:
    r, push = FakeHelpRenderer(), FakePush()
    bridge = RenderBridge(r, config_manager=FakeCfgMgr({}), push_sender=push)
    out = _run(bridge.send_help("demo", game_name="演示游戏", raw_help=RAW))
    assert out["ok"] and push.docs[0][0] == "演示游戏 帮助"


def test_bridge_without_renderer_is_safe() -> None:
    bridge = RenderBridge(None)
    assert not bridge.ready
    assert _run(bridge.help("demo")) == []
    assert _run(bridge.page("x")) == []
    assert _run(bridge.send("x"))["ok"] is False


def test_bridge_theme_falls_back_to_light() -> None:
    assert RenderBridge(None, config_manager=FakeCfgMgr({})).theme() == "light"
    assert RenderBridge(None, config_manager=FakeCfgMgr(
        {"help": {"theme": "DARK"}})).theme() == "dark"
    assert RenderBridge(None, config_manager=FakeCfgMgr(
        {"help": {"theme": "花里胡哨"}})).theme() == "light"


def test_bridge_render_failure_returns_empty() -> None:
    bridge = RenderBridge(FakeHelpRenderer(fail=True), config_manager=FakeCfgMgr({}))
    assert _run(bridge.help("demo", raw_help=RAW)) == []
    assert _run(bridge.page("x")) == []


# ── 游戏侧(只给数据) ────────────────────────────────────
def _game(bridge: Any = None, raw: Dict[str, Any] = None) -> DemoGame:
    g = DemoGame(plugin=None)
    g._help_data = raw or {}
    g.bind_services(render=bridge)
    return g


def test_game_render_help_uses_its_own_help_json() -> None:
    r = FakeHelpRenderer()
    game = _game(RenderBridge(r, config_manager=FakeCfgMgr({})), raw=RAW)
    pages = _run(game.render_help(topic="纳戒"))
    assert pages == [b"\x89PNG-help"]
    doc = r.render_calls[0][0]
    assert doc.game_name == "演示游戏" and doc.game_name != ""
    assert doc.groups and doc.groups[0].name == "装备与道具"


def test_game_render_page_returns_single_image() -> None:
    r = FakeHelpRenderer()
    game = _game(RenderBridge(r, config_manager=FakeCfgMgr({})))
    png = _run(game.render_page("结算", rows=[["得分", "100"]]))
    assert png == b"\x89PNG-page"
    assert r.custom_calls[0]["rows"] == [["得分", "100"]]


def test_game_send_page_pushes_once() -> None:
    push = FakePush()
    game = _game(RenderBridge(FakeHelpRenderer(), config_manager=FakeCfgMgr({}),
                              push_sender=push))
    out = _run(game.send_page("面板", commands=[["a", "b"]]))
    assert out["ok"] and len(push.docs) == 1


def test_game_render_without_bridge_degrades_safely() -> None:
    """桥接不可用时游戏拿到空结果, 不应抛异常(游戏可降级为文本)。"""
    game = _game(None)
    assert _run(game.render_help()) == []
    assert _run(game.render_page("x")) is None
    assert _run(game.send_page("x"))["ok"] is False


def test_bind_services_keeps_backward_compatible_signature() -> None:
    """老的 bind_services(push, img, tts, llm, photo) 调用方式必须继续可用。"""
    g = DemoGame(plugin=None)
    g.bind_services(object(), object(), object(), object(), object())
    assert g._render is None and g._push is not None


def test_games_do_not_render_images_themselves() -> None:
    """「游戏适配插件」硬约束: 游戏侧不得自己渲染图片。

    游戏只能给数据 / 调桥接(render_help / render_page / send_page / render_card),
    不得 import PIL、不得起浏览器、不得自带 HTML 模板。
    """
    import re
    root = Path(__file__).resolve().parents[1] / "games"
    banned = re.compile(r"(from\s+PIL|import\s+PIL|ImageDraw|playwright|render_html"
                        r"|<!doctype|<html|<div\s+class=)")
    offenders = []
    for py in root.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{py.relative_to(root.parent)}:{i}: {line.strip()[:70]}")
    assert not offenders, "游戏侧出现自绘代码:\n" + "\n".join(offenders)
