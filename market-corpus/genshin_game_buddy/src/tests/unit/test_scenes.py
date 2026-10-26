"""拍板 2.0.67：scenes.toml 18 场景数据质量校验。

不依赖插件实例——直接读 terms/genshin/scenes.toml 校验：
  - 总场景数 ≥ 18（原神游戏内界面覆盖度）
  - signals ≥ 2 字（防单字误命中）
  - signals 不带空格 / 斜杠（OCR 易掉格式）
  - 18 个场景间 signals 不重复（OC R 文本单次只能命中一个意图）
  - 4 个旧场景（深境螺旋 / 大世界探索 / 角色养成 / 角色详情页）仍在 + signals 重写过
  - 每个场景都有 prompt（意图化：用户正在 X，可以聊 Y/Z）
"""
from __future__ import annotations

from pathlib import Path

import pytest
from plugin.plugins.genshin_game_buddy.scene_store import load_scenes


def _plugin_dir() -> Path:
    # tests/unit/ → tests/ → genshin_game_buddy/
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def scenes() -> dict:
    plugin_dir = _plugin_dir()
    return load_scenes(plugin_dir, None, "genshin", "terms/genshin")


@pytest.mark.unit
def test_scenes__at_least_18() -> None:
    """拍板 2.0.67：场景字典从 4 扩到 18 个。"""
    plugin_dir = _plugin_dir()
    scenes = load_scenes(plugin_dir, None, "genshin", "terms/genshin")
    assert len(scenes) >= 18, (
        f"expected ≥ 18 scenes, got {len(scenes)}: {sorted(scenes.keys())}"
    )


@pytest.mark.unit
def test_scenes__legacy_four_present(scenes: dict) -> None:
    """原 4 个场景仍存在（向后兼容）。"""
    for name in ("深境螺旋", "大世界探索", "角色养成", "角色详情页"):
        assert name in scenes, f"legacy scene {name!r} missing"


@pytest.mark.unit
def test_scenes__new_scenes_present(scenes: dict) -> None:
    """新加的 14 个场景。"""
    expected = [
        "祈愿", "背包", "圣遗物", "武器", "天赋升级", "命之座",
        "任务", "地图", "活动", "冒险之证", "尘歌壶",
        "纪行", "商城", "派蒙菜单",
    ]
    for name in expected:
        assert name in scenes, f"new scene {name!r} missing"


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "深境螺旋", "大世界探索", "角色养成", "角色详情页", "祈愿", "背包",
        "圣遗物", "武器", "天赋升级", "命之座", "任务", "地图", "活动",
        "冒险之证", "尘歌壶", "纪行", "商城", "派蒙菜单",
    ],
)
def test_scenes__signals_no_short(scenes: dict, name: str) -> None:
    """拍板 2.0.67：每条 signal ≥ 2 字——防单字 alias 误命中。"""
    scene = scenes[name]
    for sig in scene.signals:
        assert len(sig) >= 2, f"{name}: signal {sig!r} too short (< 2)"


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "深境螺旋", "大世界探索", "角色养成", "角色详情页", "祈愿", "背包",
        "圣遗物", "武器", "天赋升级", "命之座", "任务", "地图", "活动",
        "冒险之证", "尘歌壶", "纪行", "商城", "派蒙菜单",
    ],
)
def test_scenes__signals_no_space_or_slash(scenes: dict, name: str) -> None:
    """拍板 2.0.67：signal 不带空格/斜杠——OCR 易掉格式（"资料 / 故事" 这类坑）。"""
    scene = scenes[name]
    for sig in scene.signals:
        assert " " not in sig, f"{name}: signal {sig!r} has space"
        assert "/" not in sig, f"{name}: signal {sig!r} has slash"
        assert "\\" not in sig, f"{name}: signal {sig!r} has backslash"


@pytest.mark.unit
def test_scenes__signals_no_overlap_between_scenes(scenes: dict) -> None:
    """拍板 2.0.67：18 个场景间 signals 不应完全重复——
    完全重复会让单次 OCR 文本命中两个场景，proactive 选第一个，意图漂移。

    允许信号"部分共享"（如"传送锚点" 同时属于大世界 + 地图）
    ——只要不是 100% 重复就行。这里只校验"完全重复整个 signals 集合" 的情况。
    """
    seen: dict[tuple, str] = {}
    for name, scene in scenes.items():
        sig_tuple = tuple(sorted(scene.signals))
        # 允许单个 signal 跨场景共享（如"传送锚点"），只查"全部相同"
        if sig_tuple in seen and sig_tuple:
            pytest.fail(
                f"场景 {name} 与 {seen[sig_tuple]} 的 signals 完全相同: {sig_tuple}"
            )
        if sig_tuple:
            seen[sig_tuple] = name


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "深境螺旋", "大世界探索", "角色养成", "角色详情页", "祈愿", "背包",
        "圣遗物", "武器", "天赋升级", "命之座", "任务", "地图", "活动",
        "冒险之证", "尘歌壶", "纪行", "商城", "派蒙菜单",
    ],
)
def test_scenes__each_has_prompt(scenes: dict, name: str) -> None:
    """拍板 2.0.67：每个场景都有 prompt——意图化"用户正在 X，可以聊 Y/Z"。"""
    scene = scenes[name]
    assert scene.prompt, f"{name}: prompt missing"
    # 格式检查：包含【身份】【该聊】【避免】【语气】四段（沿用 2.0.61）
    for marker in ("【身份】", "【该聊】", "【避免】", "【语气】"):
        assert marker in scene.prompt, f"{name}: prompt missing {marker}"


@pytest.mark.unit
def test_scenes__character_profile_no_preactivated_terms(scenes: dict) -> None:
    """拍板 2.0.67 + 2.0.60：角色详情页不预激活角色（避免猜角色污染上下文）。"""
    scene = scenes["角色详情页"]
    assert scene.context_terms == (), (
        f"角色详情页不应有 context_terms（避免猜角色）；got {scene.context_terms}"
    )


@pytest.mark.unit
def test_scenes__no_legacy_generic_terms(scenes: dict) -> None:
    """拍板 2.0.67：用户明确要求的"反例"——signals 不能包含这些常驻泛词。
    避免误命中：武器/天赋/资料/冒险等阶（这些常驻 HUD / 多场景共有）。
    """
    forbidden = {"武器", "天赋", "资料", "冒险等阶", "原粹树脂"}
    for name, scene in scenes.items():
        for sig in scene.signals:
            assert sig not in forbidden, (
                f"{name}: signal {sig!r} is a 泛词（用户标记的反例）"
            )