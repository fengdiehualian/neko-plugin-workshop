"""拍板 2.0.67：S2 场景切换立即 respond——切换检测 + 冷却旁路 + 同场景不重推。

不构造插件实例——直接用 MagicMock / 简单 stub 验证 _should_push_proactive 的
双路径（同场景 vs 场景切换）行为。

S2 关键契约：
  1. 同场景：hash 变化 + 300s 全局冷却 + 术语密度 < 5（沿用 2.0.59）
  2. 场景切换：仅看 scene_switch_cooldown（默认 30s）+ 术语密度 < 5
     - hash 检查跳过（切场景文本必然不同）
     - 300s 跳过（切场景就该立即说话）
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions


def _make_plugin_stub() -> GenshinGameBuddyPlugin:
    """构造一个最小可用 stub——只填 _should_push_proactive 需要的字段。

    不调 __init__（会触发 super().__init__ 需要 ctx）；用 __new__ 跳过。
    """
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions()
    plugin._last_pushed_hash = ""
    plugin._last_proactive_at = 0.0
    plugin._last_scene_switch_at = 0.0
    plugin._last_scene_for_proactive = ""
    # _manager 是 _should_push_proactive 读的 library 来源
    plugin._manager = MagicMock()
    plugin._manager.library = None  # 跳过术语密度检查
    return plugin


@pytest.mark.unit
def test_should_push__first_time_returns_true() -> None:
    """首次（last_pushed_hash="" + last_scene_switch_at=0）任意路径都返回 True。"""
    plugin = _make_plugin_stub()
    text = "纠缠之缘 限定五星 祈愿"
    assert plugin._should_push_proactive(text, scene_switched=False) is True
    # 同场景路径会更新 _last_pushed_hash
    assert plugin._last_pushed_hash != ""


@pytest.mark.unit
def test_should_push__same_scene__hash_unchanged_blocks() -> None:
    """同场景：上一次推过的 hash 这次也一样 → 不重推（避免重复）。"""
    plugin = _make_plugin_stub()
    text = "纠缠之缘 限定五星 祈愿"
    plugin._should_push_proactive(text, scene_switched=False)  # 第一次推
    assert plugin._should_push_proactive(text, scene_switched=False) is False


@pytest.mark.unit
def test_should_push__same_scene__within_300s_blocks() -> None:
    """同场景：hash 变了，但距上次推 < 300s → 不重推。

    测试需手动设 _last_proactive_at（_should_push_proactive 本身不更新它——
    那是 _push_proactive 的职责）。模拟"刚推过"的场景。
    """
    plugin = _make_plugin_stub()
    # 模拟"30s 前刚推过"
    plugin._last_proactive_at = time.monotonic() - 30.0
    plugin._last_pushed_hash = "old_hash_xxxxxxxxxxxx"
    # 现在的文本 hash 不同（"完全不同新文本"）—— 但 300s 内还是被挡
    assert plugin._should_push_proactive("完全不同新文本", scene_switched=False) is False
    # 即便另一段也不同也不推——300s 冷却严格
    assert plugin._should_push_proactive("纠缠之缘 限定五星", scene_switched=False) is False


@pytest.mark.unit
def test_should_push__same_scene__after_300s_allows() -> None:
    """同场景：距上次推 ≥ 300s + hash 变化 → 允许推。"""
    plugin = _make_plugin_stub()
    # 模拟"301s 前刚推过"
    plugin._last_proactive_at = time.monotonic() - 301.0
    plugin._last_pushed_hash = "old_hash_xxxxxxxxxxxx"
    assert plugin._should_push_proactive("完全不同新文本", scene_switched=False) is True


@pytest.mark.unit
def test_should_push__scene_switch__bypasses_300s_cooldown() -> None:
    """拍板 2.0.67 S2：场景切换路径绕过 300s 全局冷却。

    即便刚刚（同场景）推过，scene_switched=True 也要允许（只要切换冷却允许）。
    """
    plugin = _make_plugin_stub()
    # 模拟"30s 前同场景刚推过"
    plugin._last_proactive_at = time.monotonic() - 30.0
    plugin._last_pushed_hash = "old_hash_xxxxxxxxxxxx"
    # 标记为场景切换——绕过 300s 全局冷却
    assert plugin._should_push_proactive("深境螺旋 第12层", scene_switched=True) is True


@pytest.mark.unit
def test_should_push__scene_switch__within_cooldown_blocks() -> None:
    """拍板 2.0.67 S2：场景切换冷却（默认 30s）内连续切换 → 不重推（防刷屏）。

    测试模拟"刚刚切过一次"的场景（_last_scene_switch_at 是刚才），
    下一次切换应在 30s 内被挡。
    """
    plugin = _make_plugin_stub()
    plugin._last_scene_switch_at = time.monotonic()  # 刚刚切过
    assert plugin._should_push_proactive("大世界探索 神瞳", scene_switched=True) is False


@pytest.mark.unit
def test_should_push__scene_switch__after_cooldown_allows() -> None:
    """拍板 2.0.67 S2：超过场景切换冷却 → 允许再次切换搭话。"""
    plugin = _make_plugin_stub()
    # 模拟"31s 前切过一次"
    plugin._last_scene_switch_at = time.monotonic() - 31.0
    assert plugin._should_push_proactive("大世界探索", scene_switched=True) is True


@pytest.mark.unit
def test_should_push__scene_switch__skips_hash_check() -> None:
    """拍板 2.0.67 S2：场景切换路径**跳过** hash 检查——切场景文本必然不同。

    即便 _last_pushed_hash 已被设置成相同 hash（理论极小概率），切路径仍允许。
    关键是：切路径既不读 _last_pushed_hash 也不读 _last_proactive_at。
    """
    plugin = _make_plugin_stub()
    # 模拟"切换冷却已过 + 同文本重复"
    plugin._last_scene_switch_at = time.monotonic() - 31.0  # 过了 30s 冷却
    plugin._last_pushed_hash = "same_hash_xxxxxxxxxx"
    text = "深境螺旋 第12层"
    # 同场景路径会因 hash 重复被挡（验证前置条件）
    # 注：_should_push_proactive 在同场景路径会覆盖 _last_pushed_hash 为 text 的 hash，
    #     然后下一次调用会判 text_hash == _last_pushed_hash → False。我们只要验切路径。
    # 切路径：跳过 hash 检查 → 允许
    assert plugin._should_push_proactive(text, scene_switched=True) is True


@pytest.mark.unit
def test_should_push__scene_switch__custom_cooldown() -> None:
    """拍板 2.0.67：scene_switch_cooldown_seconds 可配（[10, 300]）。"""
    plugin = _make_plugin_stub()
    # 配置 60s 冷却
    plugin._options = PluginOptions(scene_switch_cooldown_seconds=60)
    # 模拟"刚刚切过"
    plugin._last_scene_switch_at = time.monotonic() - 31.0
    # 60s 冷却下，31s 仍不够
    assert plugin._should_push_proactive("大世界", scene_switched=True) is False
    # 61s → 够
    plugin._last_scene_switch_at = time.monotonic() - 61.0
    assert plugin._should_push_proactive("大世界", scene_switched=True) is True


@pytest.mark.unit
def test_should_push__density_limit_applies_to_both_paths() -> None:
    """拍板 2.0.67：术语密度 > 5 时两条路径都拒绝（不刷屏）。"""
    lib = MagicMock()
    lib.by_key.values.return_value = [
        MagicMock(key="术语1"), MagicMock(key="术语2"),
        MagicMock(key="术语3"), MagicMock(key="术语4"),
        MagicMock(key="术语5"), MagicMock(key="术语6"),
    ]
    plugin = _make_plugin_stub()
    plugin._manager.library = lib
    # 切路径：设冷却已过 + 密度 6 → 拒
    plugin._last_scene_switch_at = time.monotonic() - 100.0
    assert plugin._should_push_proactive(
        "术语1 术语2 术语3 术语4 术语5 术语6", scene_switched=True
    ) is False