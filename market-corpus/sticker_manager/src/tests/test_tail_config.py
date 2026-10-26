"""Reply-tail display buffer defaults and lenient configuration parsing."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings

ROOT = Path(__file__).resolve().parents[1]


def _settings(value: object) -> StickerManagerSettings:
    return StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"reply_tail_display_buffer_sec": value}}}
    )


def test_tail_buffer_defaults_are_synchronized():
    assert SendSettings().reply_tail_display_buffer_sec == 2.0
    assert StickerManagerSettings.defaults().send.reply_tail_display_buffer_sec == 2.0
    for name in ("plugin.toml", "config.example.toml"):
        config = tomllib.loads((ROOT / name).read_text(encoding="utf-8"))
        assert config["sticker_manager"]["send"]["reply_tail_display_buffer_sec"] == 2.0


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"sticker_manager": {}},
        {"sticker_manager": {"send": {}}},
        {"sticker_manager": {"send": None}},
        {"sticker_manager": {"send": "invalid"}},
    ],
)
def test_existing_configuration_inherits_display_buffer(config):
    assert StickerManagerSettings.from_config(config).send.reply_tail_display_buffer_sec == 2.0
    assert StickerManagerSettings.from_config(config).send.defer_tool_sends is False


@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_invalid_tool_deferral_defaults_to_immediate(value):
    settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"defer_tool_sends": value}}}
    )
    assert settings.send.defer_tool_sends is False


@pytest.mark.parametrize("value", [True, False])
def test_explicit_tool_deferral_override(value):
    settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"defer_tool_sends": value}}}
    )
    assert settings.send.defer_tool_sends is value


def test_existing_eager_configuration_keeps_its_gates_and_inherits_shorter_buffer():
    settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"eagerness": "eager", "cooldown_sec": 20.0}}}
    ).send
    assert settings.reply_tail_display_buffer_sec == 2.0
    assert settings.eagerness == "eager"
    assert settings.cooldown_sec == 20.0
    assert settings.defer_tool_sends is False


def test_explicit_seven_second_override_is_preserved():
    assert _settings(7.0).send.reply_tail_display_buffer_sec == 7.0


@pytest.mark.parametrize(
    "value",
    [None, True, False, "7", "invalid", [], {}, float("nan"), float("inf"), -float("inf")],
)
def test_invalid_tail_buffer_uses_default(value):
    assert _settings(value).send.reply_tail_display_buffer_sec == 2.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (0.0, 0.0),
        (2.5, 2.5),
        (7, 7.0),
        (30, 30.0),
        (30.0, 30.0),
        (-1, 0.0),
        (31, 30.0),
        (-10**400, 0.0),
        (10**400, 30.0),
    ],
)
def test_tail_buffer_clamps_to_supported_range(value, expected):
    assert _settings(value).send.reply_tail_display_buffer_sec == expected


def test_tail_buffer_does_not_change_existing_send_gates():
    baseline = StickerManagerSettings.defaults().send
    configured = _settings(30).send
    assert configured.cooldown_sec == baseline.cooldown_sec
    assert configured.probability == baseline.probability
    assert configured.probability_reuse_sec == baseline.probability_reuse_sec
    assert configured.recent_dedup_count == baseline.recent_dedup_count
    assert configured.eagerness == baseline.eagerness
