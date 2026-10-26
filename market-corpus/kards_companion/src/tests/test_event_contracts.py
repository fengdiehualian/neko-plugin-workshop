"""Contract tests for runtime/contracts.py (shared event semantics)."""

from __future__ import annotations

import json

import pytest
from kards_companion.runtime.contracts import (
    CATEGORY_BATTLE,
    CATEGORY_CHATTER,
    CATEGORY_CRITICAL,
    CATEGORY_OPENING,
    CATEGORY_PRESSURE,
    CATEGORY_RECOVERY,
    CATEGORY_STATE,
    CATEGORY_TERMINAL,
    CRITICAL,
    EDGE_ENTER,
    EDGE_RECOVERY,
    EDGE_STEADY,
    EDGE_UPGRADE,
    ENDED,
    IN_MATCH,
    LEVEL_CRITICAL,
    LEVEL_NOTICE,
    LEVEL_TERMINAL,
    LEVEL_WARNING,
    OPENING,
    OUT_OF_BATTLE,
    PRESSURE,
    category_allowed,
    event_edge,
    event_identity,
    event_level,
    event_max_age_seconds,
    event_spec,
    normalize_event,
)
from kards_companion.state.model import (
    E_HQ_CRITICAL,
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_MATCH_START,
    E_ORDER_CARD,
    E_TURN_SWITCH,
    E_UNIT_ADVANCED,
    E_UNIT_DIED,
    E_UNIT_PLAYED,
    SIDE_ENEMY,
    SIDE_MINE,
    GameEvent,
)


def event(kind: str, **details) -> GameEvent:
    return GameEvent(kind=kind, priority=5, summary="x", ts=100.0, details=details)


# ----------------------------------------------------------------- spec

def test_canonical_spec_catalog_values() -> None:
    start = event_spec(event(E_MATCH_START))
    assert (start.category, start.priority, start.max_age_seconds) == (
        CATEGORY_OPENING, 5, 4.0)
    assert start.once_per_match is True and start.preempt is False

    turn = event_spec(event(E_TURN_SWITCH))
    assert (turn.category, turn.priority, turn.max_age_seconds) == (
        CATEGORY_STATE, 4, 6.0)

    end = event_spec(event(E_MATCH_END))
    assert (end.category, end.priority, end.max_age_seconds) == (
        CATEGORY_TERMINAL, 9, 8.0)
    assert end.terminal is True and end.once_per_match is True
    assert end.preempt is False

    chatter = event_spec(event("chatter"))
    assert chatter.category == CATEGORY_CHATTER and chatter.preempt is False


def test_spec_side_overrides_and_only_confirmed_own_critical_preempts() -> None:
    mine_damage = event_spec(event(E_HQ_DAMAGE, side=SIDE_MINE))
    enemy_damage = event_spec(event(E_HQ_DAMAGE, side=SIDE_ENEMY))
    unknown_damage = event_spec(event(E_HQ_DAMAGE))
    assert (mine_damage.category, mine_damage.priority) == (CATEGORY_PRESSURE, 7)
    assert (enemy_damage.category, enemy_damage.priority) == (CATEGORY_BATTLE, 5)
    assert unknown_damage.priority == 6
    assert not mine_damage.preempt and not enemy_damage.preempt

    mine_crisis = event_spec(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=4, band=1))
    assert (mine_crisis.category, mine_crisis.priority) == (CATEGORY_CRITICAL, 8)
    assert mine_crisis.preempt is True and mine_crisis.wants_recovery is True

    enemy_crisis = event_spec(event(E_HQ_CRITICAL, side=SIDE_ENEMY, hp=4, band=1))
    assert (enemy_crisis.category, enemy_crisis.priority) == (CATEGORY_BATTLE, 6)
    assert enemy_crisis.preempt is False and enemy_crisis.wants_recovery is False

    unknown_crisis = event_spec(event(E_HQ_CRITICAL, hp=4, band=1))
    assert (unknown_crisis.category, unknown_crisis.priority) == (CATEGORY_PRESSURE, 6)
    assert unknown_crisis.preempt is False

    recovery = event_spec(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=9, band=0,
                                edge=EDGE_RECOVERY))
    assert (recovery.category, recovery.priority) == (CATEGORY_RECOVERY, 4)
    assert recovery.preempt is False and recovery.wants_recovery is False


def test_spec_unknown_kind_is_conservative() -> None:
    spec = event_spec(GameEvent("custom", 42, "x"))
    assert spec.category == CATEGORY_STATE
    assert 1 <= spec.priority <= 9
    assert spec.max_age_seconds == 0.0


# ----------------------------------------------------------------- level

def test_level_only_confirmed_own_crisis_is_critical() -> None:
    assert event_level(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=4)) == LEVEL_CRITICAL
    assert event_level(event(E_HQ_CRITICAL, side=SIDE_ENEMY, hp=4)) == LEVEL_WARNING
    assert event_level(event(E_HQ_CRITICAL, hp=4)) == LEVEL_WARNING
    assert event_level(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=4,
                             edge=EDGE_RECOVERY)) == LEVEL_NOTICE
    assert event_level(event(E_HQ_DAMAGE, side=SIDE_MINE)) == LEVEL_WARNING
    assert event_level(event(E_HQ_DAMAGE, side=SIDE_ENEMY)) == LEVEL_NOTICE
    assert event_level(event(E_MATCH_END)) == LEVEL_TERMINAL
    assert event_level(event(E_UNIT_DIED, side=SIDE_MINE)) == LEVEL_WARNING
    assert event_level(event(E_UNIT_ADVANCED, side=SIDE_ENEMY)) == LEVEL_WARNING
    assert event_level(event(E_MATCH_START)) == "info"


def test_explicit_level_is_overridden_for_non_own_crisis() -> None:
    assert event_level(event(E_HQ_CRITICAL, side=SIDE_ENEMY,
                             level="critical")) == LEVEL_WARNING
    assert event_level(event(E_HQ_CRITICAL, side=SIDE_MINE,
                             level="critical")) == LEVEL_CRITICAL


# ----------------------------------------------------------------- edge

def test_edge_enter_upgrade_recovery_steady() -> None:
    assert event_edge(event(E_MATCH_START)) == EDGE_ENTER
    assert event_edge(event(E_UNIT_PLAYED)) == EDGE_ENTER
    assert event_edge(event(E_UNIT_ADVANCED)) == EDGE_UPGRADE
    assert event_edge(event(E_UNIT_DIED)) == EDGE_STEADY
    assert event_edge(event(E_HQ_DAMAGE, side=SIDE_MINE)) == EDGE_STEADY
    assert event_edge(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=4, band=1)) == EDGE_ENTER
    assert event_edge(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=2, band=2)) == EDGE_UPGRADE
    assert event_edge(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=8, band=0)) == EDGE_RECOVERY
    assert event_edge(event(E_HQ_CRITICAL, hp=4)) == EDGE_STEADY  # side unknown
    assert event_edge(event(E_HQ_CRITICAL, side=SIDE_MINE, hp=4,
                            edge="upgrade")) == EDGE_UPGRADE


# ----------------------------------------------------------------- identity

def test_identity_is_ten_segments_with_stable_entity() -> None:
    identity = event_identity(event(
        E_HQ_CRITICAL,
        session_id="sess",
        game=2,
        side=SIDE_MINE,
        entity="hq",
        turn=5,
        band=2,
        occurrence=3,
        episode=7,
    ))
    assert identity.split("|") == [
        "sess", "2", E_HQ_CRITICAL, SIDE_MINE, "hq", "5", "2",
        EDGE_UPGRADE, "3", "7",
    ]


def test_identity_prefers_stable_entity_and_uses_defaults() -> None:
    identity = event_identity(event(E_UNIT_PLAYED, unit_id=9, instance_id=1))
    parts = identity.split("|")
    assert len(parts) == 10
    assert parts[4] == "9"
    assert parts[0] == "-" and parts[1] == "0"
    assert event_identity(event(E_MATCH_START)) == \
        f"-|0|{E_MATCH_START}|-|-|-|-|{EDGE_ENTER}|-|-"


# ----------------------------------------------------------------- TTL

def test_ttl_strict_min_of_spec_and_configured() -> None:
    start = event(E_MATCH_START)
    end = event(E_MATCH_END)
    assert event_max_age_seconds(start) == 4.0
    assert event_max_age_seconds(start, 12.0) == 4.0
    assert event_max_age_seconds(start, 2.0) == 2.0
    assert event_max_age_seconds(start, 0) == 4.0
    assert event_max_age_seconds(end, 100.0) == 8.0  # no terminal extension
    assert event_max_age_seconds(end, 3.0) == 3.0


# ----------------------------------------------------------------- category gate

def test_category_allowed_matrix() -> None:
    assert category_allowed(OUT_OF_BATTLE, CATEGORY_TERMINAL)
    assert not category_allowed(OUT_OF_BATTLE, CATEGORY_OPENING)
    assert not category_allowed(OUT_OF_BATTLE, CATEGORY_BATTLE)

    assert category_allowed(OPENING, CATEGORY_OPENING)
    assert category_allowed(OPENING, CATEGORY_CRITICAL)
    assert category_allowed(OPENING, CATEGORY_TERMINAL)
    assert not category_allowed(OPENING, CATEGORY_CHATTER)

    assert category_allowed(IN_MATCH, CATEGORY_BATTLE)
    assert category_allowed(IN_MATCH, CATEGORY_CHATTER)

    assert category_allowed(PRESSURE, CATEGORY_BATTLE)
    assert category_allowed(PRESSURE, CATEGORY_CRITICAL)
    assert not category_allowed(PRESSURE, CATEGORY_OPENING)

    assert category_allowed(CRITICAL, CATEGORY_CRITICAL)
    assert category_allowed(CRITICAL, CATEGORY_RECOVERY)
    assert not category_allowed(CRITICAL, CATEGORY_BATTLE)

    assert category_allowed(ENDED, CATEGORY_TERMINAL)
    assert not category_allowed(ENDED, CATEGORY_STATE)
    assert not category_allowed("nonsense", CATEGORY_BATTLE)
    assert category_allowed("nonsense", CATEGORY_TERMINAL)


# ----------------------------------------------------------------- normalize

def test_normalize_event_is_canonical_non_mutating_and_idempotent() -> None:
    raw = GameEvent(
        kind=E_HQ_CRITICAL,
        priority=1,
        summary="x",
        ts=123.0,
        details={"side": SIDE_MINE, "hp": 4, "band": 1, "session_id": "s",
                 "game": 2, "entity": "hq", "turn": 3, "occurrence": 1},
    )
    snapshot = dict(raw.details)

    normalized = normalize_event(raw)
    assert raw.priority == 1 and raw.details == snapshot
    assert "identity" not in raw.details
    assert normalized.priority == 8
    assert normalized.details["category"] == CATEGORY_CRITICAL
    assert normalized.details["level"] == LEVEL_CRITICAL
    assert normalized.details["edge"] == EDGE_ENTER
    assert normalized.details["max_age_seconds"] == 6.0
    assert normalized.details["identity"] == event_identity(
        GameEvent(raw.kind, 8, raw.summary, ts=raw.ts, details=normalized.details)
    )

    again = normalize_event(normalized)
    assert again == normalized
    assert json.dumps(again.to_dict(), ensure_ascii=True)


def test_normalize_event_session_override_and_recovery_priority() -> None:
    raw = event(E_HQ_CRITICAL, side=SIDE_MINE, hp=9, band=0,
                edge=EDGE_RECOVERY, session_id="old")
    normalized = normalize_event(raw, session_id="new")
    assert normalized.priority == 4
    assert normalized.details["session_id"] == "new"
    assert normalized.details["category"] == CATEGORY_RECOVERY
    assert normalized.details["level"] == LEVEL_NOTICE


@pytest.mark.parametrize(
    "built",
    [
        event(E_MATCH_START),
        event(E_HQ_DAMAGE, side=SIDE_MINE, loss=6, hp=14),
        event(E_UNIT_PLAYED, side=SIDE_ENEMY, entity="7"),
        event(E_ORDER_CARD, side=SIDE_ENEMY, entity="card", confidence=0.9),
        event(E_MATCH_END, result="won"),
    ],
)
def test_normalize_is_idempotent_for_every_catalog_kind(built: GameEvent) -> None:
    once = normalize_event(built)
    assert normalize_event(once) == once
