"""A-22 / A-29 ~ A-32：注册表解析、别名归一化与模糊匹配。

每个用例都断言"改了配置/输入，行为就变"，而不是"文本里存在某个键"。
"""

from __future__ import annotations

import pytest
from conftest import GENSHIN_ENTRY, WUWA_ENTRY, make_config
from plugin.plugins.genshin_game_buddy.game_registry import (
    GameRegistry,
    PluginOptions,
    as_section,
    is_safe_relative_dir,
    normalize_key,
)

# =============================================================================
# normalize_key
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("原神", "原神"),
        ("《原神》", "原神"),
        ("  Genshin   Impact ", "genshinimpact"),
        ("GENSHIN", "genshin"),
        ("Ｇｅｎｓｈｉｎ", "genshin"),  # 全角 → NFKC
        ("「鸣潮」", "鸣潮"),
        ("", ""),
        (None, ""),
        (123, ""),
    ],
)
def test_normalize_key__canonicalizes_forms(raw: object, expected: str) -> None:
    assert normalize_key(raw) == expected


@pytest.mark.unit
def test_normalize_key__is_idempotent() -> None:
    once = normalize_key("《Genshin  Impact》")
    assert normalize_key(once) == once


# =============================================================================
# A-22 / 可调项
# =============================================================================


@pytest.mark.unit
def test_options__defaults_match_manifest() -> None:
    options = PluginOptions()
    assert options.default_game == ""
    assert options.auto_restore_last_game is True
    assert options.context_inject_max_terms == 40
    assert options.max_context_chars == 800
    assert options.lookup_max_matches == 3
    assert options.lookup_max_chars_per_term == 200
    assert options.reinject_every_n_messages == 0
    # 拍板 2.0.64 + 2.0.65 + 2.0.66 + 2.0.67：11 个新可调项默认值
    assert options.ocr_profile == "auto"
    assert options.ocr_perceive_interval_seconds == 15
    assert options.ocr_worker_threads == 1
    assert options.screen_activation_limit == 12
    assert options.query_activation_ttl_seconds == 300
    assert options.scene_prompt_reinject_seconds == 0
    # 拍板 2.0.70：S1 change_driven_enabled 默认改 false——S1 真机连续 worker 卡死，默认关回到 2.0.66 稳定行为
    assert options.change_driven_enabled is False
    assert options.change_detect_threshold == 8
    assert options.scene_switch_cooldown_seconds == 30
    assert options.desktop_markers_enabled is True
    assert options.desktop_markers_extra == ()


@pytest.mark.unit
def test_options__every_key_is_read_and_changes_behaviour() -> None:
    options = PluginOptions.from_section(
        {
            "default_game": "genshin",
            "auto_restore_last_game": False,
            "context_inject_max_terms": 2,
            "max_context_chars": 100,
            "lookup_max_matches": 1,
            "lookup_max_chars_per_term": 50,
            "reinject_every_n_messages": 3,
            "ocr_profile": "performance",
            "ocr_perceive_interval_seconds": 60,
            "ocr_worker_threads": 2,
            "screen_activation_limit": 5,
            "query_activation_ttl_seconds": 120,
            "scene_prompt_reinject_seconds": 600,
            "change_driven_enabled": False,
            "change_detect_threshold": 4,
            "scene_switch_cooldown_seconds": 60,
            "desktop_markers_enabled": False,
            "desktop_markers_extra": ["Steam", "Discord"],
        }
    )
    assert options.default_game == "genshin"
    assert options.auto_restore_last_game is False
    assert options.context_inject_max_terms == 2
    assert options.max_context_chars == 100
    assert options.lookup_max_matches == 1
    assert options.lookup_max_chars_per_term == 50
    assert options.reinject_every_n_messages == 3
    assert options.ocr_profile == "performance"
    assert options.ocr_perceive_interval_seconds == 60
    assert options.ocr_worker_threads == 2
    assert options.screen_activation_limit == 5
    assert options.query_activation_ttl_seconds == 120
    assert options.scene_prompt_reinject_seconds == 600
    # 拍板 2.0.67：3 个新字段生效
    assert options.change_driven_enabled is False
    assert options.change_detect_threshold == 4
    assert options.scene_switch_cooldown_seconds == 60
    assert options.desktop_markers_enabled is False
    assert options.desktop_markers_extra == ("Steam", "Discord")


@pytest.mark.unit
def test_options__bad_types_fall_back_and_out_of_range_is_clamped() -> None:
    options = PluginOptions.from_section(
        {
            "default_game": 42,
            "auto_restore_last_game": "yes",
            "context_inject_max_terms": 999,
            "max_context_chars": 1,
            "lookup_max_matches": 0,
            "lookup_max_chars_per_term": 10_000,
            "reinject_every_n_messages": -5,
            "ocr_perceive_interval_seconds": 9999,    # → 300 (high clamp)
            "ocr_worker_threads": 5,              # → 2 (high clamp)
            "ocr_profile": "totally_invalid",     # → "auto"（非法值 fallback）
            "screen_activation_limit": 0,             # → 1 (low clamp)
            "query_activation_ttl_seconds": 5,        # → 10 (low clamp)
            "scene_prompt_reinject_seconds": -100,    # → 0 (low clamp)
            "desktop_markers_enabled": "no",          # → True default（非 bool）
            "desktop_markers_extra": 42,            # → ()（非 str 非 list）
        }
    )
    assert options.default_game == ""
    assert options.auto_restore_last_game is True  # 非布尔 → 默认值，不猜
    assert options.context_inject_max_terms == 50
    assert options.max_context_chars == 16
    assert options.lookup_max_matches == 1
    assert options.lookup_max_chars_per_term == 2000
    assert options.reinject_every_n_messages == 0
    assert options.ocr_perceive_interval_seconds == 300
    assert options.ocr_worker_threads == 2
    assert options.ocr_profile == "auto"
    assert options.screen_activation_limit == 1
    assert options.query_activation_ttl_seconds == 10
    assert options.scene_prompt_reinject_seconds == 0
    assert options.desktop_markers_enabled is True
    assert options.desktop_markers_extra == ()


@pytest.mark.unit
def test_options__ocr_worker_threads_boundary() -> None:
    """拍板 2.0.65：worker 数只允许 1 或 2，clamp 严格。"""
    # 边界值
    assert PluginOptions.from_section({"ocr_worker_threads": 1}).ocr_worker_threads == 1
    assert PluginOptions.from_section({"ocr_worker_threads": 2}).ocr_worker_threads == 2
    # clamp：低于 1 拉到 1；高于 2 降到 2
    assert PluginOptions.from_section({"ocr_worker_threads": 0}).ocr_worker_threads == 1
    assert PluginOptions.from_section({"ocr_worker_threads": 3}).ocr_worker_threads == 2
    assert PluginOptions.from_section({"ocr_worker_threads": -1}).ocr_worker_threads == 1
    assert PluginOptions.from_section({"ocr_worker_threads": 100}).ocr_worker_threads == 2
    # 非法类型 → 默认 1
    assert PluginOptions.from_section({"ocr_worker_threads": "two"}).ocr_worker_threads == 1
    assert PluginOptions.from_section({"ocr_worker_threads": 1.5}).ocr_worker_threads == 1


@pytest.mark.unit
def test_options__ocr_perceive_interval_seconds_low_clamp_is_3() -> None:
    """拍板 2.0.65：interval 下限从 5 降到 3（tick 装饰器固定 3s 跑一次）。"""
    assert PluginOptions.from_section({"ocr_perceive_interval_seconds": 3}).ocr_perceive_interval_seconds == 3
    # 2 还是被 clamp 到 3（不能再低）
    assert PluginOptions.from_section({"ocr_perceive_interval_seconds": 2}).ocr_perceive_interval_seconds == 3
    assert PluginOptions.from_section({"ocr_perceive_interval_seconds": 0}).ocr_perceive_interval_seconds == 3


@pytest.mark.unit
def test_options__ocr_profile__valid_values_accepted() -> None:
    """拍板 2.0.66：ocr_profile 接受 5 个合法值。"""
    for v in ("auto", "eco", "balanced", "performance", "custom"):
        assert PluginOptions.from_section({"ocr_profile": v}).ocr_profile == v


@pytest.mark.unit
def test_options__ocr_profile__invalid_falls_back_to_auto() -> None:
    """拍板 2.0.66：ocr_profile 非法值（非枚举 / 非字符串 / 空）→ "auto"。"""
    assert PluginOptions.from_section({"ocr_profile": "TURBO"}).ocr_profile == "auto"
    assert PluginOptions.from_section({"ocr_profile": ""}).ocr_profile == "auto"
    assert PluginOptions.from_section({"ocr_profile": 42}).ocr_profile == "auto"
    assert PluginOptions.from_section({"ocr_profile": None}).ocr_profile == "auto"
    assert PluginOptions.from_section({}).ocr_profile == "auto"


@pytest.mark.unit
def test_options__ocr_profile__case_insensitive() -> None:
    """拍板 2.0.66：ocr_profile 大小写不敏感。"""
    assert PluginOptions.from_section({"ocr_profile": "PERFORMANCE"}).ocr_profile == "performance"
    assert PluginOptions.from_section({"ocr_profile": "Custom"}).ocr_profile == "custom"
    assert PluginOptions.from_section({"ocr_profile": "AUTO"}).ocr_profile == "auto"


@pytest.mark.unit
def test_options__ocr_profile_defaults_constant() -> None:
    """拍板 2.0.66：3 档预设默认值稳定（面板 UI 和 Python 都依赖）。"""
    from plugin.plugins.genshin_game_buddy.game_registry import OCR_PROFILE_DEFAULTS
    assert OCR_PROFILE_DEFAULTS == {
        "eco":         {"interval": 30, "threads": 1},
        "balanced":    {"interval": 15, "threads": 1},
        "performance": {"interval": 8,  "threads": 2},
    }


@pytest.mark.unit
def test_options__ocr_profile_profiles_constant() -> None:
    """拍板 2.0.66：5 个合法档位枚举稳定。"""
    from plugin.plugins.genshin_game_buddy.game_registry import OCR_PROFILES
    assert OCR_PROFILES == ("auto", "eco", "balanced", "performance", "custom")


@pytest.mark.unit
@pytest.mark.parametrize("raw_value,expected", [
    (1, 1),
    (8, 8),
    (64, 64),
    (0, 1),         # 下界 clamp 到 1
    (65, 64),       # 上界 clamp 到 64
    (-5, 1),
    ("8", 8),       # 字符串 → 8（默认；_clamp_int 不认字符串）
    (3.7, 8),       # float → fallback 默认 8
    (None, 8),      # None → fallback 默认 8
    (True, 8),      # bool → fallback 默认 8（_clamp_int 拒 bool）
])
def test_options__change_detect_threshold_clamp(raw_value, expected) -> None:
    """拍板 2.0.67：change_detect_threshold clamp 到 [1, 64]；非法值 fallback 8。"""
    options = PluginOptions.from_section({"change_detect_threshold": raw_value})
    assert options.change_detect_threshold == expected


@pytest.mark.unit
@pytest.mark.parametrize("raw_value,expected", [
    (10, 10),
    (30, 30),
    (300, 300),
    (9, 10),        # 下界
    (301, 300),     # 上界
    (0, 10),
    (-100, 10),
])
def test_options__scene_switch_cooldown_clamp(raw_value, expected) -> None:
    """拍板 2.0.67：scene_switch_cooldown_seconds clamp 到 [10, 300]。"""
    options = PluginOptions.from_section({"scene_switch_cooldown_seconds": raw_value})
    assert options.scene_switch_cooldown_seconds == expected


@pytest.mark.unit
@pytest.mark.parametrize("raw_value,expected", [
    (True, True),
    (False, False),
    ("true", False),   # 非 bool → fallback False（2.0.70 新默认）
    ("false", False),  # 同上
    (None, False),
    (1, False),        # 注意：1 不是 bool（isinstance(1, bool) is False）→ fallback False
    (0, False),
])
def test_options__change_driven_enabled_default(raw_value, expected) -> None:
    """拍板 2.0.70：change_driven_enabled 非 bool → fallback 默认 False（S1 默认关）。"""
    options = PluginOptions.from_section({"change_driven_enabled": raw_value})
    assert options.change_driven_enabled is expected


@pytest.mark.unit
def test_options__scene_switch_cooldown_default_when_missing() -> None:
    """拍板 2.0.67：scene_switch_cooldown_seconds 缺省 → 30。"""
    options = PluginOptions.from_section({})
    assert options.scene_switch_cooldown_seconds == 30


@pytest.mark.unit
def test_options__change_driven_enabled_default_when_missing() -> None:
    """拍板 2.0.70：change_driven_enabled 缺省 → False（S1 默认关）。"""
    options = PluginOptions.from_section({})
    assert options.change_driven_enabled is False


@pytest.mark.unit
def test_options__change_detect_threshold_default_when_missing() -> None:
    """拍板 2.0.67：change_detect_threshold 缺省 → 8。"""
    options = PluginOptions.from_section({})
    assert options.change_detect_threshold == 8


@pytest.mark.unit
def test_options__desktop_markers_extra_normalizes_to_tuple() -> None:
    """拍板 2.0.64：extra 是 list[str] 配置项，过滤空串/非字符串，归一为 tuple。"""
    options = PluginOptions.from_section(
        {"desktop_markers_extra": ["  Steam  ", "", "Discord", 123, None, "VS Code"]}
    )
    assert options.desktop_markers_extra == ("Steam", "Discord", "VS Code")


@pytest.mark.unit
def test_options__bool_is_not_accepted_as_int() -> None:
    # bool 是 int 的子类；不显式挡掉，True 会变成 1 条术语上限。
    options = PluginOptions.from_section({"context_inject_max_terms": True})
    assert options.context_inject_max_terms == 40


@pytest.mark.unit
def test_as_section__non_mapping_is_empty() -> None:
    assert as_section(None, "games") == {}
    assert as_section({"games": 5}, "games") == {}
    assert as_section({"games": {"a": 1}}, "games") == {"a": 1}


# =============================================================================
# A-29 / A-30 / A-31 / A-32：注册表解析
# =============================================================================


@pytest.mark.unit
def test_registry__display_name__required_and_used_in_block() -> None:
    broken = {"genshin": {k: v for k, v in GENSHIN_ENTRY.items() if k != "display_name"}}
    registry = GameRegistry.from_config(make_config(games=broken))
    assert registry.games == ()
    assert any("display_name is required" in issue for issue in registry.issues)


@pytest.mark.unit
def test_registry__aliases__required_and_every_form_resolves_to_same_id() -> None:
    missing = {"genshin": {k: v for k, v in GENSHIN_ENTRY.items() if k != "aliases"}}
    registry = GameRegistry.from_config(make_config(games=missing))
    assert registry.games == ()
    assert any("aliases is required" in issue for issue in registry.issues)

    registry = GameRegistry.from_config(make_config(games={"genshin": dict(GENSHIN_ENTRY)}))
    for alias in ["原神", "Genshin", "Genshin Impact", "genshin", "GENSHIN", "《原神》"]:
        match = registry.resolve(alias)
        assert match.found, alias
        assert match.game is not None and match.game.game_id == "genshin"


@pytest.mark.unit
def test_registry__terms_dir__default_is_used_when_absent() -> None:
    entry = {k: v for k, v in GENSHIN_ENTRY.items() if k != "terms_dir"}
    registry = GameRegistry.from_config(make_config(games={"genshin": entry}))
    assert registry.get("genshin").terms_dir == "terms/genshin"


@pytest.mark.unit
def test_registry__terms_dir__explicit_override_wins() -> None:
    entry = dict(GENSHIN_ENTRY)
    entry["terms_dir"] = "terms/custom_genshin"
    registry = GameRegistry.from_config(make_config(games={"genshin": entry}))
    assert registry.get("genshin").terms_dir == "terms/custom_genshin"


@pytest.mark.unit
@pytest.mark.parametrize("bad_dir", ["/etc/passwd", "C:/windows", "../escape", "terms/../../x", ""])
def test_registry__terms_dir__unsafe_path_is_rejected_not_followed(bad_dir: str) -> None:
    entry = dict(GENSHIN_ENTRY)
    entry["terms_dir"] = bad_dir
    registry = GameRegistry.from_config(make_config(games={"genshin": entry}))
    game = registry.get("genshin")
    if bad_dir == "":
        # 空字符串等同"没写"，用默认模板，不算问题
        assert game is not None and game.terms_dir == "terms/genshin"
    else:
        assert game is not None and game.terms_dir == "terms/genshin"
        assert any("relative path" in issue for issue in registry.issues)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("terms/genshin", True),
        ("terms\\genshin", True),
        ("a/b/c", True),
        ("/abs", False),
        ("C:/x", False),
        ("../up", False),
        ("", False),
        (".", False),
    ],
)
def test_is_safe_relative_dir(value: str, expected: bool) -> None:
    assert is_safe_relative_dir(value) is expected


@pytest.mark.unit
def test_registry__enabled_false__excluded_from_list_and_declaration() -> None:
    entry = dict(GENSHIN_ENTRY)
    entry["enabled"] = False
    registry = GameRegistry.from_config(make_config(games={"genshin": entry, "wuthering_waves": dict(WUWA_ENTRY)}))

    assert [game.game_id for game in registry.enabled_games()] == ["wuthering_waves"]
    assert "原神" not in registry.available_names()

    match = registry.resolve("原神")
    assert match.found is True
    assert match.usable is False  # 命中了，但被下架 → 调用方必须报 game_disabled


@pytest.mark.unit
def test_registry__invalid_game_id_is_rejected_with_issue() -> None:
    registry = GameRegistry.from_config(make_config(games={"Genshin-Impact": dict(GENSHIN_ENTRY)}))
    assert registry.games == ()
    assert any("id must match" in issue for issue in registry.issues)


@pytest.mark.unit
def test_registry__non_table_entry_is_rejected_without_crash() -> None:
    registry = GameRegistry.from_config(make_config(games={"genshin": "原神"}))
    assert registry.games == ()
    assert any("must be a table" in issue for issue in registry.issues)


@pytest.mark.unit
def test_registry__from_config_tolerates_garbage() -> None:
    assert GameRegistry.from_config(None).games == ()
    assert GameRegistry.from_config({"games": None}).games == ()
    assert GameRegistry.from_config("nonsense").games == ()  # type: ignore[arg-type]


# =============================================================================
# resolve：模糊匹配
# =============================================================================


@pytest.fixture
def registry() -> GameRegistry:
    return GameRegistry.from_config(
        make_config(games={"genshin": dict(GENSHIN_ENTRY), "wuthering_waves": dict(WUWA_ENTRY)})
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected_game_id", "expected_reason"),
    [
        ("genshin", "genshin", "exact_id"),
        ("原神", "genshin", "exact_alias"),
        ("我在玩原神", "genshin", "partial"),
        ("最近在肝鸣潮，好累", "wuthering_waves", "partial"),
        ("wuwa", "wuthering_waves", "exact_alias"),
        ("Wuthering Waves", "wuthering_waves", "exact_alias"),
    ],
)
def test_registry__resolve(
    registry: GameRegistry, text: str, expected_game_id: str, expected_reason: str
) -> None:
    match = registry.resolve(text)
    assert match.reason == expected_reason
    assert match.game is not None and match.game.game_id == expected_game_id


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "   ", "塞尔达", None, 123, "原"])
def test_registry__resolve__misses(registry: GameRegistry, text: object) -> None:
    match = registry.resolve(text)
    assert match.found is False
    assert match.reason == "not_found"


@pytest.mark.unit
def test_registry__resolve__single_char_never_partial_matches(registry: GameRegistry) -> None:
    # "原" 是 "原神" 的子串，但 1 个字符太容易误命中，必须要求 >= 2。
    assert registry.resolve("原").found is False
    assert registry.resolve("原神").found is True


@pytest.mark.unit
def test_registry__resolve__longest_alias_wins() -> None:
    games = {
        "a": {"display_name": "甲", "aliases": ["星"], "terms_dir": "terms/a"},
        "b": {"display_name": "乙", "aliases": ["星穹铁道"], "terms_dir": "terms/b"},
    }
    registry = GameRegistry.from_config(make_config(games=games))
    match = registry.resolve("星穹铁道真好玩")
    assert match.game is not None and match.game.game_id == "b"
    assert match.reason == "partial"


@pytest.mark.unit
def test_registry__get__only_matches_exact_id() -> None:
    registry = GameRegistry.from_config(make_config(games={"genshin": dict(GENSHIN_ENTRY)}))
    assert registry.get("genshin") is not None
    assert registry.get("原神") is None
    assert registry.get("") is None
