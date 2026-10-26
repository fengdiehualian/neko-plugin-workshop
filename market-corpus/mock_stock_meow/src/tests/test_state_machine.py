"""阶段状态机单测：收割触发是核心机制，必须有守卫。"""

from __future__ import annotations

from engine.config import DifficultyConfig, HeatConfig
from engine.models import Player
from engine.state_machine import (
    PHASE_HONEYMOON,
    PHASE_SLAUGHTER,
    PHASE_TRAP,
    evaluate_transition,
    jump_severity,
    phase_profile,
)

HEAT = HeatConfig()


def _player(trade_count: int = 0, heat_history: list[float] | None = None) -> Player:
    player = Player(id="p1", name="玩家", cash=100.0, mode="classic_advanced")
    player.trade_count = trade_count
    player.heat_history = list(heat_history or [])
    return player


def _difficulty(**overrides) -> DifficultyConfig:
    base = {
        "mode": "classic_advanced",
        "harvest_trigger": "heat_peak",
        "fixed_harvest_trades": 30,
    }
    base.update(overrides)
    return DifficultyConfig(**base)


def test_phase_profile_returns_configured_parameters():
    difficulty = _difficulty()
    assert phase_profile(PHASE_HONEYMOON, difficulty) == (
        difficulty.honeymoon_mu,
        difficulty.honeymoon_sigma,
    )
    assert phase_profile(PHASE_TRAP, difficulty) == (
        difficulty.trap_mu,
        difficulty.trap_sigma,
    )
    assert phase_profile(PHASE_SLAUGHTER, difficulty) == (
        difficulty.slaughter_mu,
        difficulty.slaughter_sigma,
    )


def test_heat_peak_moves_honeymoon_to_trap():
    difficulty = _difficulty()
    player = _player(heat_history=[0.0, 50.0, 40.0])
    assert (
        evaluate_transition(
            PHASE_HONEYMOON, difficulty=difficulty, heat_config=HEAT, player=player
        )
        == PHASE_TRAP
    )


def test_no_peak_keeps_honeymoon():
    difficulty = _difficulty()
    player = _player(heat_history=[0.0, 10.0, 5.0])
    assert (
        evaluate_transition(
            PHASE_HONEYMOON, difficulty=difficulty, heat_config=HEAT, player=player
        )
        == PHASE_HONEYMOON
    )


def test_heat_peak_moves_trap_to_slaughter():
    difficulty = _difficulty()
    player = _player(heat_history=[0.0, 60.0, 50.0])
    assert (
        evaluate_transition(
            PHASE_TRAP, difficulty=difficulty, heat_config=HEAT, player=player
        )
        == PHASE_SLAUGHTER
    )


def test_slaughter_is_terminal():
    difficulty = _difficulty()
    player = _player(heat_history=[0.0, 60.0, 50.0])
    assert (
        evaluate_transition(
            PHASE_SLAUGHTER, difficulty=difficulty, heat_config=HEAT, player=player
        )
        == PHASE_SLAUGHTER
    )


def test_fixed_trades_trigger_for_arcade_mode():
    difficulty = _difficulty(harvest_trigger="fixed_trades", fixed_harvest_trades=30)
    assert (
        evaluate_transition(
            PHASE_HONEYMOON,
            difficulty=difficulty,
            heat_config=HEAT,
            player=_player(trade_count=29),
        )
        == PHASE_HONEYMOON
    )
    assert (
        evaluate_transition(
            PHASE_HONEYMOON,
            difficulty=difficulty,
            heat_config=HEAT,
            player=_player(trade_count=30),
        )
        == PHASE_TRAP
    )


def test_prey_index_trigger_for_hell_mode():
    difficulty = _difficulty(
        harvest_trigger="prey_index", prey_index_threshold=60.0
    )
    assert (
        evaluate_transition(
            PHASE_HONEYMOON,
            difficulty=difficulty,
            heat_config=HEAT,
            player=_player(),
            prey_index=59.0,
        )
        == PHASE_HONEYMOON
    )
    assert (
        evaluate_transition(
            PHASE_HONEYMOON,
            difficulty=difficulty,
            heat_config=HEAT,
            player=_player(),
            prey_index=60.0,
        )
        == PHASE_TRAP
    )


def test_jump_severity_scales_with_concentration():
    assert jump_severity(0.0) == 0.20
    assert jump_severity(1.0) == 0.80
    assert jump_severity(0.5) == 0.50
    # 非收割阶段的跳跃减半
    assert jump_severity(1.0, phase=PHASE_TRAP) == 0.40


def test_jump_severity_is_clamped():
    assert jump_severity(-1.0) == 0.20
    assert jump_severity(5.0) == 0.80
