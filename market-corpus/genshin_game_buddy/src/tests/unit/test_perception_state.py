"""拍板 2.0.63 + 2.0.64：收紧 in_game_chars + alias 长度下限 + 桌面/IDE 黑名单 + 开关控制。

5+3 个 case：
  1. 纯游戏名（无术语）不算 in_game_chars
  2. 桌面/浏览器反特征黑名单命中即拒
  3. 真游戏内（多术语命中）算 in_game_chars
  4. screen-activate alias 长度 < 2 不命中
  5. screen-activate alias 长度 >= 2 命中
  6. (2.0.64) IDE marker（cmd.exe / resolve bridge / mcp.json）命中即拒
  7. (2.0.64) desktop_markers_enabled=False 时黑名单失效
  8. (2.0.64) desktop_markers_extra 用户自定义追加
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from conftest import make_config
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import GameRegistry
from plugin.plugins.genshin_game_buddy.term_store import TermEntry, TermLibrary


def _setup_registry(plugin: Any) -> None:
    """build_plugin() 后手动把 _registry 灌上——__init__ 里只是空 GameRegistry()，
    要等 _ensure_config_ready()（异步）才会从 config 重建。测试里不跑那条路径，
    所以直接 from_config(make_config()) 灌进去。
    """
    plugin._registry = GameRegistry.from_config(make_config())


# =====================================================================
# _has_in_game_characteristics：3 个集成测试（用 build_plugin 真实加载术语库）
# =====================================================================


def test_perception_state__pure_game_name_not_in_game(build_plugin: Any) -> None:
    """拍板 2.0.63：纯游戏名（无任何术语命中）不算 in_game_chars。
    测试用 conftest 的 GENSHIN_ENTRY 没 signals，所以 (c) 必然 False；
    "原神" 也不是任何 term 的 key/alias（已 grep 验证），所以 (a)(b) 也 False。
    """
    plugin = build_plugin()
    _setup_registry(plugin)
    assert plugin._has_in_game_characteristics("原神", "genshin") is False


def test_perception_state__desktop_marker_not_in_game(build_plugin: Any) -> None:
    """拍板 2.0.63：桌面/浏览器反特征黑名单——命中任一直接判非游戏。"""
    plugin = build_plugin()
    _setup_registry(plugin)
    assert plugin._has_in_game_characteristics("N.E.K.O 插件管理 回收站", "genshin") is False


def test_perception_state__real_game_terms_in_game(build_plugin: Any) -> None:
    """拍板 2.0.63：真游戏内（多术语命中）算 in_game_chars——
    "丝柯克"（characters.toml:20）+ "圣遗物"（systems.toml:57）都是真实 term key，
    各自 len>=2 命中，total_count=2 满足 (b)。
    """
    plugin = build_plugin()
    _setup_registry(plugin)
    assert plugin._has_in_game_characteristics("丝柯克 圣遗物", "genshin") is True


# =====================================================================
# _scan_screen_activate_terms：2 个静态方法测试（直接构造 TermLibrary）
# =====================================================================


def _make_lib(*terms: TermEntry) -> TermLibrary:
    return TermLibrary(
        game_id="genshin",
        display_name="原神",
        tone="",
        entries=tuple(terms),
        by_key={t.key: t for t in terms},
    )


def test_screen_activate__single_char_alias_does_not_match() -> None:
    """拍板 2.0.63：alias 长度 < 2 不命中——防"莹"（1字）误命中"CV: 谢莹"。
    key="荧" 本身也只有 1 字，所以 key 命中也被长度下限挡住。
    """
    term = TermEntry(
        key="荧",
        kind="角色",
        brief="",
        dimension="characters",
        aliases=("莹",),
    )
    lib = _make_lib(term)
    screen_matched, _slang = GenshinGameBuddyPlugin._scan_screen_activate_terms("CV: 谢莹", lib)
    assert "荧" not in screen_matched


def test_screen_activate__two_char_alias_does_match() -> None:
    """拍板 2.0.63：alias 长度 >= 2 命中——"公子"（2字）正常匹配达达利亚。"""
    term = TermEntry(
        key="达达利亚",
        kind="角色",
        brief="",
        dimension="characters",
        aliases=("公子",),
    )
    lib = _make_lib(term)
    screen_matched, _slang = GenshinGameBuddyPlugin._scan_screen_activate_terms("公子来了", lib)
    assert "达达利亚" in screen_matched


# =====================================================================
# 2.0.64 新增：_desktop_markers() 开关 + IDE 黑名单
# =====================================================================


def test_perception_state__ide_marker_blocks_in_game(build_plugin: Any) -> None:
    """拍板 2.0.64：IDE/编辑器 marker（cmd.exe / resolve bridge / mcp.json）命中即拒。
    即使文本里有真游戏术语，IDE marker 也应拦在最前面。
    """
    plugin = build_plugin()
    _setup_registry(plugin)
    assert plugin._has_in_game_characteristics(
        "cmd.exe resolve bridg mcp.json 丝柯克 圣遗物", "genshin"
    ) is False


def test_perception_state__desktop_markers_disabled_passes_through(build_plugin: Any) -> None:
    """拍板 2.0.64：desktop_markers_enabled=False 时黑名单失效——让 term 命中自己说话。
    默认 True；切到 False 后即使含桌面 marker 也不拦（让 term 命中自行决定）。
    用 2 个 term hit（丝柯克 + 圣遗物）保证 (b) total_count>=2 满足，不依赖 signals。
    """
    plugin = build_plugin()
    _setup_registry(plugin)

    # baseline: 桌面 marker 命中即拒（启用状态）
    assert plugin._has_in_game_characteristics(
        "N.E.K.O 插件管理 丝柯克 圣遗物", "genshin"
    ) is False

    # 关掉黑名单——desktop marker 不再拦，让 term 命中决定（2 个 term 满足 (b)）
    plugin._options = replace(plugin._options, desktop_markers_enabled=False)
    assert plugin._has_in_game_characteristics(
        "N.E.K.O 插件管理 丝柯克 圣遗物", "genshin"
    ) is True


def test_perception_state__desktop_markers_extra_user_added(build_plugin: Any) -> None:
    """拍板 2.0.64：desktop_markers_extra 用户自定义追加——命中即拒。
    内置列表生效 + 用户追加的 "Steam" 也生效。
    """
    plugin = build_plugin()
    _setup_registry(plugin)
    plugin._options = replace(
        plugin._options, desktop_markers_extra=("Steam", "Discord")
    )
    # 用户追加的 marker 命中即拒（即使内置列表没有）
    assert plugin._has_in_game_characteristics(
        "Steam 正在运行 丝柯克 圣遗物", "genshin"
    ) is False
    # 没命中（无 marker + 术语命中足够）→ True
    assert plugin._has_in_game_characteristics(
        "丝柯克 圣遗物", "genshin"
    ) is True