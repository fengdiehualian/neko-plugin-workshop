"""Unified situation contracts (``kards_situation_v1``).

These tests intentionally cross the real seams:

* pure builder: capabilities, TTL, unknown-side buckets, retained units,
  unsupported unit models (never an empty board), semantic fingerprints;
* main wiring: UI detections -> ``MatchState.situation`` -> read text,
  respond prompt and the LLM tool all consume the **same** frozen snapshot;
* source freshness: capture vs UI heartbeat timestamps, static-screen
  2-frame confirmation, failed/stale UI invalidating match context;
* lifecycle: read-context enter/expire is gated by consent/dry-run and local
  idempotence, card watermarks reset per session.

No host, game window, real model weights or private video are used.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from kards_companion.runtime.health import CaptureHealthSnapshot
from kards_companion.runtime.situation import (
    SITUATION_SCHEMA,
    build_situation,
    situation_context_text,
    situation_facts,
    situation_payload,
)
from kards_companion.state.model import (
    E_CHATTER,
    SCENE_MATCH,
    SCENE_UNKNOWN,
    BoardUnit,
    GameEvent,
    MatchState,
    semantic_value,
)
from test_api_contract import (
    MAIN,
    PLUGIN_CLASS,
    FakeCapture,
    FakeCardClassifier,
    FakeCardResult,
    FakeClassifier,
    FakeConfig,
    FakeLogger,
    FakeUiDetector,
    _detection,
    trusted_health,
)

NOW = 100.0
SESSION = "session-a"

FAKE_MODELS = {
    "ui_det": {
        "state": "loaded",
        "loaded": True,
        "supported_classes": [
            "unit_board", "card_hand", "played_card", "frontline",
            "end_turn_button",
        ],
        "unsupported_classes": [
            "hq", "end_turn_button_active", "screen_victory", "screen_defeat",
        ],
        "diagnostics": "",
    },
    "card_cls": {"state": "loaded", "loaded": True},
    "trusted_card_count": 2,
}

LIMITED_MODELS = {
    "ui_det": {
        "state": "loaded",
        "loaded": True,
        "supported_classes": ["card_back", "end_turn_button"],
        "unsupported_classes": [
            "unit_board", "card_hand", "played_card", "frontline",
            "hq", "end_turn_button_active", "screen_victory", "screen_defeat",
        ],
        "diagnostics": "",
    },
    "card_cls": {"state": "loaded", "loaded": True},
    "trusted_card_count": 0,
}


def _unit(uid: int, side: str, *, observed: bool, name: str = "") -> BoardUnit:
    return BoardUnit(
        instance_id=uid, side=side, lane="front", slot=uid,
        bbox=(0.1, 0.2, 0.3, 0.4), observed=observed, trajectory=True,
        track_confirmed=True, last_seen_frame=7, observed_at=NOW - 0.1,
        detection_confidence=0.9, card_name=name or None,
        name_confidence=0.9 if name else 0.0,
    )


def _state() -> MatchState:
    state = MatchState(
        scene=SCENE_MATCH, in_match=True, game_number=3, turn_number=4,
        my_hq=None, enemy_hq=None,
    )
    state.units = [
        _unit(1, "mine", observed=True, name="T-34"),
        _unit(2, "enemy", observed=False, name="虎式"),
        _unit(3, "unknown", observed=True),
        _unit(4, "mine", observed=False),
    ]
    return state


def _card(**overrides: Any) -> dict[str, Any]:
    payload = {
        "observation_id": "card:7:0",
        "session_id": SESSION,
        "frame_id": 7,
        "source_frame": 7,
        "observed_at": NOW - 0.2,
        "region": "card_hand",
        "appearance": "hand_full",
        "card_id": "t34_1942",
        "name": "T-34 1942型",
        "conf": 0.91,
        "raw_card_id": "raw_top1_must_not_leak",
    }
    payload.update(overrides)
    return payload


def _build(**overrides: Any) -> dict[str, Any]:
    state = overrides.pop("state", None) or _state()
    kwargs: dict[str, Any] = {
        "models": FAKE_MODELS,
        "observations": [_card()],
        "session_id": SESSION,
        "frame_id": 7,
        "observed_at": NOW,
        "now": NOW,
        "ui_observed_at": NOW,
        "context_valid": True,
        "reason": "in_match",
        "observation_valid": True,
        "detector_available": True,
        "unit_diagnostics": {"status": "ok", "observation_valid": True,
                             "detector_available": True},
        "ui_signals": ["frontline", "unit_board", "card_hand",
                       "end_turn_button"],
        "events": [],
    }
    kwargs.update(overrides)
    return build_situation(state, **kwargs)


# ------------------------------------------------------------------ pure builder

def test_build_situation_shape_units_cards_and_source() -> None:
    situation = _build()

    assert situation["schema"] == SITUATION_SCHEMA
    assert situation["valid"] is True and situation["fresh"] is True
    assert situation["source"]["session_id"] == SESSION
    assert situation["source"]["frame_id"] == 7
    assert situation["source"]["captured_at"] == NOW
    assert situation["source"]["observed_at"] == NOW
    assert situation["source"]["ui_observed_at"] == NOW
    assert situation["source"]["context_valid"] is True
    assert situation["capabilities"]["units"]["supported"] is True
    assert situation["capabilities"]["cards"]["trusted_card_count"] == 2
    assert situation["capabilities"]["scene"]["boundary"] == (
        "frontline_or_end_turn_button")
    assert situation["limits"]["match_boundary_unavailable"] is True
    assert situation["limits"]["game_number_source"] == "local_observation_segment"
    assert "match_boundary_unavailable" in situation["llm_notice"]

    units = situation["facts"]["units"]
    assert units["counts"] == {
        "observed": 2, "retained": 2,
        "observed_mine": 1, "observed_enemy": 0, "observed_unknown": 1,
        "retained_mine": 1, "retained_enemy": 1, "retained_unknown": 0,
    }
    assert [item["instance_id"] for item in units["observed"]["mine"]] == [1]
    assert [item["instance_id"] for item in units["observed"]["unknown"]] == [3]
    enemy = units["retained"]["enemy"][0]
    assert enemy["current"] is False and enemy["observed"] is False
    assert "name" not in enemy and "card" not in enemy
    assert enemy["last_known_name"] == "虎式"
    mine = units["observed"]["mine"][0]
    assert mine["current"] is True and mine["name"] == "T-34"

    assert len(situation["recognized_cards"]) == 1
    card = situation["recognized_cards"][0]
    assert card["session_id"] == SESSION
    assert card["source_frame"] == 7
    assert card["observed_at"] == round(NOW - 0.2, 3)
    assert card["appearance"] == "hand_full"
    assert card["claim"] == "card_face_observation"
    assert card["board_identity"] is False
    assert card["played"] is None and card["died"] is None
    assert "raw_top1_must_not_leak" not in json.dumps(situation)
    assert situation["card_observations"]["reason"] == ""


def test_card_ttl_cross_session_and_capability_gate() -> None:
    observations = [
        _card(),
        _card(observation_id="card:8:0", source_frame=8,
              observed_at=NOW - 3.0),  # stale > 2s
        _card(observation_id="card:9:0", source_frame=9,
              session_id="other-session"),  # cross-session
    ]
    situation = _build(observations=observations)
    assert [item["observation_id"] for item in situation["recognized_cards"]] == [
        "card:7:0"]
    assert situation["card_observations"]["dropped_stale"] == 1
    assert situation["card_observations"]["dropped_cross_session"] == 1

    empty = _build(models=LIMITED_MODELS)
    assert empty["recognized_cards"] == []
    assert empty["card_observations"]["reason"] == "whitelist_empty"
    assert empty["capabilities"]["cards"]["whitelist_empty"] is True
    # Limited UI capability is not "everything unknown": a real button signal
    # and its match scene remain facts.
    assert empty["capabilities"]["ui"]["available"] is True
    assert empty["facts"]["scene"] == "match"
    assert "end_turn_button" in empty["facts"]["ui_signals"]
    assert empty["facts"]["units"]["counts"] is None
    assert empty["facts"]["units"]["observed"] is None
    assert empty["facts"]["units"]["retained"] is None
    assert "units" in {entry["field"] for entry in empty["limitations"]}
    assert "hq" in {entry["field"] for entry in empty["limitations"]}
    assert "match_boundary" in {entry["field"] for entry in empty["limitations"]}


def test_source_freshness_uses_ui_sample_not_publication() -> None:
    stale = _build(observed_at=NOW, ui_observed_at=NOW - 2.0, now=NOW)
    assert stale["source"]["captured_at"] == NOW
    assert stale["source"]["observed_at"] == NOW  # never refreshed to now
    assert stale["source"]["ui_observed_at"] == NOW - 2.0
    assert stale["source"]["ui_age_ms"] == 2000.0
    assert stale["valid"] is False and stale["fresh"] is False

    fresh = _build(observed_at=NOW, ui_observed_at=NOW - 1.4, now=NOW)
    assert fresh["valid"] is True

    invalid = _build(context_valid=False, reason="scene_not_match")
    assert invalid["valid"] is False
    assert invalid["source"]["context_valid"] is False
    assert invalid["facts"]["units"]["counts"] is None


def test_events_are_safe_and_unverified_chatter_is_suppressed() -> None:
    chatter = GameEvent(
        "chatter", 5, "局势平稳，适合简短点评一下盘面。",
        details={"session_id": SESSION, "game": 3,
                 "image": b"pixels", "raw_ocr": "SECRET",
                 "api_key": "SECRET", "chat_text": "SECRET"})
    situation = _build(
        models=LIMITED_MODELS,
        observations=[],
        state=MatchState(scene=SCENE_MATCH, in_match=True, game_number=3),
        events=[chatter],
    )
    rendered = json.dumps(situation, ensure_ascii=False)
    assert "SECRET" not in rendered
    assert "pixels" not in rendered
    assert situation["facts"]["events"] == []
    assert situation["facts"]["units"]["counts"] is None

    # With no verified board/card fact, no chatter may claim a quiet board.
    verified = _build(events=[chatter])
    assert len(verified["facts"]["events"]) == 1
    assert verified["facts"]["events"][0]["kind"] == "chatter"


def test_unit_list_is_bounded_with_truncation_meta() -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True)
    state.units = [_unit(index, "mine", observed=True) for index in range(1, 31)]
    situation = _build(state=state, max_units=18)
    units = situation["facts"]["units"]
    assert units["truncated"] is True
    assert units["counts"]["observed"] == 30
    assert units["counts"]["observed_mine"] == 30
    assert len(units["observed"]["mine"]) == 18
    assert situation["meta"]["truncated"]["units"] is True
    assert situation["limits"]["truncated"]["units"] is True


# ------------------------------------------------------------- model semantics

def test_board_unit_public_shape_keeps_legacy_and_marks_trajectory() -> None:
    legacy = BoardUnit(instance_id=1, side="mine", lane="front",
                       attack=2, health=3)
    payload = legacy.to_public_dict()
    assert set(payload) == {"id", "side", "lane", "at", "hp", "desc"}

    retained = _unit(2, "enemy", observed=False, name="虎式").to_public_dict()
    assert retained["observed"] is False and retained["current"] is False
    assert retained["last_seen_frame"] == 7
    assert "observed_at" in retained and "detection_conf" in retained
    assert "track_confirmed" in retained
    assert "name" not in retained and "card" not in retained
    assert retained["last_known_name"] == "虎式"

    current = _unit(3, "mine", observed=True, name="T-34").to_public_dict()
    assert current["current"] is True and current["name"] == "T-34"


def test_semantic_value_and_fingerprint_ignore_volatile_but_track_facts() -> None:
    state = _state()
    state.situation = _build()
    baseline = state.semantic_fingerprint()

    # Same functional facts, different source/publication times and frames.
    other = _state()
    other.situation = _build(
        observed_at=NOW + 5.0, now=NOW + 5.0,
        ui_observed_at=NOW + 5.0, frame_id=99,
        observations=[_card(observed_at=NOW + 5.0 - 0.2,
                            frame_id=99, source_frame=99)])
    other.detected_at = NOW + 99.0
    assert other.semantic_fingerprint() == baseline

    # Observed false is a functional change.
    changed = _state()
    changed.situation = _build()
    changed.units[0].observed = False
    # Rebuild preserves the changed observation state.
    changed.situation = _build(state=changed)
    assert changed.semantic_fingerprint() != baseline

    # Capability boundary changes are functional.
    limited = _state()
    limited.situation = _build(models=LIMITED_MODELS)
    assert limited.semantic_fingerprint() != baseline

    normalized = semantic_value({"frame_id": 4, "observed_at": 1.0,
                                 "nested": {"age_ms": 5, "keep": True}})
    assert normalized == {"nested": {"keep": True}}


def test_situation_helpers_are_pure_copies_and_context_text_has_no_api_names() -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True)
    assert situation_payload(state) == {}
    assert situation_facts(state) == {}

    state.situation = _build()
    payload = situation_payload(state)
    payload["facts"]["scene"] = "tampered"
    assert state.situation["facts"]["scene"] == "match"
    assert situation_facts(state)["scene"] == "match"

    text = situation_context_text(state.situation)
    assert "LiveStatePublisher" not in text
    assert "kards_get_live_state" in text
    assert "单位/棋盘能力不可用" not in text  # supported in fake capabilities
    limited_text = situation_context_text(_build(models=LIMITED_MODELS))
    assert "单位/棋盘能力不可用" in limited_text
    assert "卡面身份不可用" in limited_text


# ------------------------------------------------------------------ main wiring

class _AlwaysDirty:
    def __get__(self, obj, objtype=None):
        return True

    def __set__(self, obj, value):
        pass


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    logger = FakeLogger()
    pushes: list[dict[str, Any]] = []
    base = MAIN.NekoPluginBase
    monkeypatch.setattr(base, "enable_file_logging",
                        lambda self, log_level=None: logger, raising=False)
    monkeypatch.setattr(base, "data_path",
                        lambda self, name: Path(tmp_path) / name, raising=False)

    def record_push(self: Any, **kwargs: Any) -> dict[str, Any]:
        pushes.append(kwargs)
        return {"submitted": True}

    monkeypatch.setattr(base, "push_message", record_push, raising=False)
    instance = PLUGIN_CLASS(ctx=object())
    instance.config = FakeConfig()
    instance._events_log = []
    instance.pushes = pushes
    instance.scheduler.due = lambda *args, **kwargs: True
    monkeypatch.setattr(type(instance), "_change_dirty", _AlwaysDirty(),
                        raising=False)
    return instance


def _now_capture(frame_id: int, color: tuple[int, int, int]):
    from kards_companion.perception.capture import CaptureResult
    from PIL import Image
    return CaptureResult(
        frame_id=frame_id, captured_at=time.time(), width=8, height=8,
        backend="dxcam", image=Image.new("RGB", (8, 8), color),
        foreground=True, source="dxcam")


def _health(frame_id: int) -> CaptureHealthSnapshot:
    return trusted_health(frame_id)


class _BoardDetector:
    def __init__(self, detections: list[Any]) -> None:
        self._detections = list(detections)
        self.calls = 0

    def detect(self, _image: Any) -> list[Any]:
        self.calls += 1
        return list(self._detections)


def _configure_fake_capabilities(plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "_models_payload", lambda: dict(FAKE_MODELS))
    monkeypatch.setattr(MAIN, "CARD_CROP_MIN_SIDE", 2)
    plugin._trusted_card_count = 2
    plugin._session_id = SESSION
    plugin.tracker.set_session_id(SESSION)


def test_ui_to_situation_read_respond_tool_share_one_snapshot(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._cfg = {
        "tick_ms": 333, "chatter_interval_seconds": 0,
        "user_chat_quiet_window_seconds": 0.0,
        "battle_output_quiet_window_seconds": 0.0,
        "battle_output_backpressure_seconds": 0.0,
    }
    plugin._apply_config({"kards": plugin._cfg})
    plugin.arbiter.update(MAIN.ArbiterConfig(
        mode="companion", min_priority=1, cooldown_seconds=0.0,
        critical_cooldown_seconds=0.0))
    _configure_fake_capabilities(plugin, monkeypatch)
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    front = _detection("frontline", 5, (0.0, 0.0, 8.0, 1.0))
    unit = _detection("unit_board", 0, (2.0, 4.0, 5.0, 7.0))
    card = _detection("card_hand", 1, (1.0, 1.0, 6.0, 6.0))
    plugin.ui_detector = _BoardDetector([front, unit, card])
    plugin.card_classifier = FakeCardClassifier([
        FakeCardResult("t34_1942", "T-34 1942型", 0.91),
        FakeCardResult("t34_1942", "T-34 1942型", 0.92),
    ])
    plugin._capture = FakeCapture(
        [_now_capture(1, (0, 0, 0)), _now_capture(2, (255, 255, 255))],
        [_health(1), _health(2)])

    plugin._perceive_once()
    plugin._perceive_once()

    state = plugin._pipeline_state
    situation = state.situation
    assert situation["schema"] == SITUATION_SCHEMA
    assert situation["source"]["session_id"] == SESSION
    assert situation["source"]["frame_id"] == 2
    assert situation["recognized_cards"]
    assert situation["recognized_cards"][0]["session_id"] == SESSION
    assert situation["facts"]["units"]["counts"]["observed"] >= 1
    assert plugin._last_trusted_snapshot.state.situation is situation

    # Read channel carries the same situation payload.
    core_reads = [
        push for push in plugin.pushes
        if push.get("metadata", {}).get("kind") == "game_live_state"
        and push.get("metadata", {}).get("segment") == "core"]
    assert core_reads
    wire = json.loads(core_reads[-1]["parts"][0]["text"].removeprefix("KARDS:"))
    assert wire["situation"]["schema"] == SITUATION_SCHEMA
    assert wire["situation"]["source"]["session_id"] == SESSION

    # Respond prompt is built from the same state.
    responds = [push for push in plugin.pushes
                if push.get("ai_behavior") == "respond"]
    assert responds
    prompt = responds[0]["parts"][0]["text"]
    # The prompt budget may trim card/unit detail, but it must carry the same
    # situation capability/limit sections (never a legacy empty board).
    assert '"capabilities"' in prompt
    assert "units" in prompt or "match_boundary_unavailable" in prompt

    # Tool is the unified view, not tracker.current internals.
    tool = asyncio.run(plugin.kards_get_live_state())
    assert tool["paused"] is False
    assert tool["state"]["schema"] == SITUATION_SCHEMA
    assert tool["situation"] == tool["state"]
    assert tool["state"]["recognized_cards"][0]["card_id"] == "t34_1942"
    assert "evidence" not in json.dumps(tool, ensure_ascii=False)


def test_tool_hides_facts_when_consent_or_dry_run_blocks(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._apply_config({"kards": {
        "llm_data_consent": False, "auto_start_monitoring": False}})
    blocked = asyncio.run(plugin.kards_get_live_state())
    assert blocked["state"] is None and blocked["situation"] is None
    assert blocked["recent_events"] == []
    assert blocked["reason"] == "consent_required"

    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": True,
        "auto_start_monitoring": False}})
    dry = asyncio.run(plugin.kards_get_live_state())
    assert dry["state"] is None and dry["situation"] is None
    assert dry["reason"] == "dry_run"


def test_static_screen_heartbeat_produces_second_independent_ui_sample(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    # Use the real 2-frame classifier: one sample can never confirm match.
    from kards_companion.perception.screens import ScreenClassifier
    plugin.screen_classifier = ScreenClassifier(confirm_frames=2)
    monkeypatch.setattr(plugin, "_models_payload", lambda: dict(FAKE_MODELS))
    detector = FakeUiDetector()
    plugin.ui_detector = detector
    # Do not force every task due: heartbeat must bridge the dirty=false gap.
    plugin.scheduler.due = lambda task, **kwargs: task == "unit"
    monkeypatch.setattr(type(plugin), "_change_dirty", False)
    plugin._capture = FakeCapture(
        [_now_capture(1, (0, 0, 0)), _now_capture(2, (0, 0, 0))],
        [_health(1), _health(2)])

    plugin._perceive_once()
    assert plugin._pipeline_state.scene == SCENE_UNKNOWN
    assert len(detector.calls) == 1
    assert plugin._change_dirty is False

    plugin._last_ui_observation_mono = time.monotonic() - 2.0
    plugin._last_ui_observation_at = time.time() - 2.0
    plugin._perceive_once()
    assert len(detector.calls) == 2
    assert plugin._pipeline_state.scene == SCENE_MATCH
    assert plugin._pipeline_state.situation["facts"]["scene"] == "match"
    assert plugin._last_ui_frame_id == 2

    # Re-consuming the same frame id is not a third confirmation sample.
    plugin._last_ui_observation_mono = time.monotonic() - 2.0
    plugin._last_ui_frame_id = 3
    plugin._last_ui_observation_at = time.time()
    plugin._capture = FakeCapture([_now_capture(3, (0, 0, 0))], [_health(3)])
    plugin._perceive_once()
    assert len(detector.calls) == 2


def test_ui_failure_invalidates_match_context_and_rebases_next_sample(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    _configure_fake_capabilities(plugin, monkeypatch)
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)

    class FlakyDetector(_BoardDetector):
        def __init__(self) -> None:
            super().__init__([_detection("frontline", 5, (0.0, 0.0, 8.0, 1.0))])
            self.fail = False

        def detect(self, image: Any) -> Any:
            self.calls += 1
            if self.fail:
                raise RuntimeError("ui down")
            return list(self._detections)

    detector = FlakyDetector()
    plugin.ui_detector = detector
    plugin._capture = FakeCapture(
        [_now_capture(1, (0, 0, 0)), _now_capture(2, (0, 0, 0)),
         _now_capture(3, (0, 0, 0))],
        [_health(1), _health(2), _health(3)])

    plugin._perceive_once()
    assert plugin.tracker.in_match
    assert plugin._last_trusted_snapshot.state.scene == SCENE_MATCH

    detector.fail = True
    plugin._perceive_once()
    failed = plugin._pipeline_state
    assert failed.scene == SCENE_UNKNOWN
    assert failed.my_turn is None
    assert plugin._last_ui_observation_at is not None  # old sample remains
    assert plugin._ui_observation_failed is True
    assert plugin._context_validity()["valid"] is False
    assert plugin._context_validity()["reason"] == "ui_observation_unavailable"
    assert plugin._ui_rebase_pending is True

    detector.fail = False
    plugin._perceive_once()
    assert plugin._ui_observation_failed is False
    assert plugin._ui_rebase_pending is False
    assert plugin._pipeline_state.scene == SCENE_MATCH


def test_context_enter_is_idempotent_gated_and_expire_clears_local_marker(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": False,
        "auto_start_monitoring": False}})
    monkeypatch.setattr(plugin, "_models_payload", lambda: dict(FAKE_MODELS))
    enters: list[dict[str, Any]] = []
    original_enter = plugin.publisher.enter

    def spy_enter(*args: Any, **kwargs: Any) -> Any:
        enters.append(kwargs)
        return original_enter(*args, **kwargs)

    monkeypatch.setattr(plugin.publisher, "enter", spy_enter)
    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=2)

    plugin._push_live_state(state)
    assert len(enters) == 1
    first_token = plugin._read_context_entered_token
    assert first_token
    assert "LiveStatePublisher" not in (
        enters[0].get("text") or "")
    # Order on the same tick: context enter first, then the core read.
    assert plugin.pushes[0]["metadata"].get("context_action") == "enter"
    assert plugin.pushes[1]["metadata"].get("kind") == "game_live_state"
    assert plugin.pushes[1]["metadata"].get("segment") == "core"
    pushes_after_read = len(plugin.pushes)
    reads: list[int] = []
    original_read = plugin.dispatcher.push_read
    monkeypatch.setattr(
        plugin.dispatcher, "push_read",
        lambda *args, **kwargs: (reads.append(1), original_read(*args, **kwargs))[1])
    # Every valid publish calls enter again so the publisher can refresh TTL
    # and re-check route/capability; it coalesces unchanged slots internally.
    plugin._push_live_state(state)
    assert len(enters) == 2
    assert len(reads) >= 1

    token = plugin.gate.get_token()
    plugin._expire_read_context("test_expire", token=token)
    assert plugin._read_context_entered_token == ""
    expired = [
        push for push in plugin.pushes[pushes_after_read:]
        if push.get("metadata", {}).get("context_expired") is True]
    assert expired


def test_config_restriction_expires_context_without_pushing(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": False,
        "auto_start_monitoring": False}})
    calls: list[str] = []
    original = plugin._expire_read_context
    monkeypatch.setattr(
        plugin, "_expire_read_context",
        lambda reason, token=None: (
            calls.append(reason), original(reason, token=token))[1])

    pushes_before = len(plugin.pushes)
    plugin._apply_config({"kards": {
        "llm_data_consent": False, "dry_run": False,
        "auto_start_monitoring": False}})
    assert "config_changed" in calls
    # consent=false blocks every host cleanup push.
    assert len(plugin.pushes) == pushes_before

    pushes_before = len(plugin.pushes)
    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": True,
        "auto_start_monitoring": False}})
    assert calls.count("config_changed") == 2
    assert len(plugin.pushes) == pushes_before

    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": False,
        "auto_start_monitoring": False, "mode": "silent"}})
    assert calls.count("config_changed") == 3


def test_reset_session_drops_card_watermark_and_pending_frames(plugin: Any) -> None:
    plugin._session_id = SESSION
    plugin._trusted_card_count = 2
    plugin._card_consumed_frame = 9
    plugin._card_pending_frame = 10
    plugin._card_pending_at = 123.0
    plugin._card_pending_session = SESSION
    plugin._card_pending = [("card_hand", object())]
    plugin._recognized_cards = [{"session_id": SESSION, "card_id": "t34"}]
    plugin._last_ui_observation_at = 123.0
    plugin._last_ui_frame_id = 9

    with plugin._lifecycle_lock:
        plugin._reset_runtime_state_locked(reason="session_reset")

    assert plugin._card_consumed_frame == 0
    assert plugin._card_pending_frame == 0
    assert plugin._card_pending_at == 0.0
    assert plugin._card_pending_session == ""
    assert plugin._card_pending == []
    assert plugin._recognized_cards == []
    assert plugin._last_ui_observation_at is None
    assert plugin._last_ui_frame_id == 0


def test_scene_exit_or_terminal_clears_current_card_faces(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._session_id = SESSION
    plugin._recognized_cards = [{"session_id": SESSION, "card_id": "t34"}]
    plugin._card_pending = [("card_hand", object())]
    plugin._clear_current_cards("confirmed_exit")
    assert plugin._recognized_cards == []
    assert plugin._card_pending == []


def test_unit_detector_unavailable_is_not_an_empty_board() -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True)
    situation = _build(
        state=state, observation_valid=False, detector_available=False,
        unit_diagnostics={"status": "model_unavailable",
                          "observation_valid": False,
                          "detector_available": False})
    units = situation["facts"]["units"]
    assert units["supported"] is True
    assert units["status"] == "unavailable"
    assert units["counts"] is None
    assert units["observed"] is None and units["retained"] is None
    assert "counts" not in json.dumps(units) or '"counts": null' in json.dumps(units)
    assert "units" in {entry["field"] for entry in situation["limitations"]}


def test_invalid_context_cards_units_and_events_are_not_current() -> None:
    chatter = GameEvent("chatter", 5, "board quiet",
                        details={"session_id": SESSION, "game": 3})
    situation = _build(context_valid=False, reason="scene_not_match",
                       events=[chatter])
    assert situation["facts"]["scene"] == "unknown"
    assert situation["facts"]["in_match"] is False
    assert situation["facts"]["units"]["counts"] is None
    assert situation["recognized_cards"] == []
    assert situation["card_observations"]["reason"] == "context_invalid"
    assert situation["facts"]["events"] == []
    assert situation["valid"] is False


def test_turn_is_local_counter_not_actual_and_scene_conf_is_not_invented() -> None:
    situation = _build()
    assert situation["facts"]["turn"] is None
    assert situation["facts"]["local_turn_counter"] == 4
    assert situation["unknown"]["turn_number"] == "local_counter_not_actual"
    assert "turn_number" in {entry["field"] for entry in situation["limitations"]}
    assert situation["meta"]["confidence"]["scene"] is None

    explicit = _build(scene_confidence=0.87)
    assert explicit["meta"]["confidence"]["scene"] == 0.87


def test_future_or_stale_source_timestamps_are_invalid() -> None:
    future_ui = _build(ui_observed_at=NOW + 1.0, now=NOW)
    assert future_ui["valid"] is False
    assert future_ui["source"]["ui_observed_at"] == NOW + 1.0

    stale_capture = _build(observed_at=NOW - 2.0, ui_observed_at=NOW - 2.0,
                           now=NOW)
    assert stale_capture["valid"] is False
    assert stale_capture["source"]["captured_at"] == NOW - 2.0

    # Missing/invalid UI capability cannot keep an in_match fact.
    unavailable_models = {
        "ui_det": {"state": "unavailable", "loaded": False,
                   "supported_classes": [], "unsupported_classes": []},
        "card_cls": {"state": "unavailable", "loaded": False},
        "trusted_card_count": 0,
    }
    no_ui = _build(models=unavailable_models)
    assert no_ui["facts"]["in_match"] is False
    assert no_ui["facts"]["scene"] == "unknown"
    assert no_ui["facts"]["ui_signals"] == []


def test_main_never_emits_quiet_board_chatter_without_verified_facts(
        plugin: Any) -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=1)
    state.situation = _build(
        models=LIMITED_MODELS, observations=[],
        state=MatchState(scene=SCENE_MATCH, in_match=True, game_number=1))
    chatter = GameEvent(E_CHATTER, 5, "局势平稳，适合简短点评一下盘面。",
                        details={"game": 1})
    assert plugin._grounded_candidates([chatter], state) == []

    verified = MatchState(scene=SCENE_MATCH, in_match=True, game_number=1)
    verified.situation = _build()
    assert plugin._grounded_candidates([chatter], verified) == [chatter]


def test_builder_never_serializes_bytes_or_object_repr() -> None:
    situation = _build(
        observations=[_card(card_id=b"SECRET_BYTES")],
        unit_diagnostics={"status": b"SECRET_STATUS",
                          "observation_valid": True,
                          "detector_available": True},
        ui_signals=[b"SECRET_SIGNAL", object()],
    )
    rendered = json.dumps(situation, ensure_ascii=False)
    assert "SECRET_BYTES" not in rendered
    assert "SECRET_STATUS" not in rendered
    assert "SECRET_SIGNAL" not in rendered
    assert "<object>" in rendered  # type name only, never the payload


def test_zero_game_number_is_unknown_not_a_fake_zero() -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=0)
    situation = _build(state=state)
    assert situation["facts"]["game_number"] is None
    assert situation["unknown"]["game_number"] == "local_segment"


def test_large_situation_still_builds_a_bounded_prompt() -> None:
    from kards_companion.delivery.publisher import build_commentary_prompt

    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=7)
    state.units = [_unit(index, "mine", observed=True, name="T-34")
                   for index in range(1, 19)]
    observations = [
        _card(observation_id=f"card:7:{index}", card_id=f"card_{index}",
              name=f"T-34 {index}")
        for index in range(20)
    ]
    state.situation = build_situation(
        state,
        models=FAKE_MODELS,
        observations=observations,
        session_id=SESSION,
        frame_id=7,
        observed_at=NOW,
        now=NOW,
        ui_observed_at=NOW,
        context_valid=True,
        reason="in_match",
        observation_valid=True,
        detector_available=True,
        unit_diagnostics={"status": "ok", "observation_valid": True,
                          "detector_available": True},
        ui_signals=["frontline", "unit_board", "end_turn_button"],
        scene_confidence=0.9,
    )
    rendered = json.dumps(state.situation, ensure_ascii=False)
    assert len(rendered) > 4000
    prompt = build_commentary_prompt(
        "event", state, max_prompt_chars=1600, now=NOW)
    assert len(prompt) <= 1600
    assert "KARDS 陪玩解说边界" in prompt

    # The limited capability table must also build without a prompt error.
    limited_state = MatchState(scene=SCENE_MATCH, in_match=True)
    limited_state.situation = build_situation(
        limited_state, models=LIMITED_MODELS, observations=[],
        session_id=SESSION, frame_id=1, observed_at=NOW, now=NOW,
        ui_observed_at=NOW, context_valid=True, reason="in_match")
    limited_prompt = build_commentary_prompt(
        "event", limited_state, max_prompt_chars=1600, now=NOW)
    assert len(limited_prompt) <= 1600


def test_card_future_timestamp_uses_the_global_tolerance() -> None:
    accepted = _build(
        observations=[_card(observed_at=NOW + 0.2)], now=NOW)
    assert len(accepted["recognized_cards"]) == 1

    rejected = _build(
        observations=[_card(observed_at=NOW + 0.5)], now=NOW)
    assert rejected["recognized_cards"] == []
    assert rejected["card_observations"]["dropped_invalid"] == 1


def test_terminal_capability_is_consistent_with_limits_and_unknown() -> None:
    models = {
        "ui_det": {
            "state": "loaded", "loaded": True,
            "supported_classes": [
                "end_turn_button", "screen_victory", "screen_defeat"],
            "unsupported_classes": [
                "unit_board", "card_hand", "played_card", "frontline", "hq"],
            "diagnostics": "",
        },
        "card_cls": {"state": "loaded", "loaded": True},
        "trusted_card_count": 0,
    }
    state = MatchState(scene="victory", in_match=False, game_number=5)
    situation = _build(state=state, models=models,
                       observations=[], context_valid=True,
                       reason="victory")
    scene_cap = situation["capabilities"]["scene"]
    assert scene_cap["terminal_signals_supported"] == [
        "screen_victory", "screen_defeat"]
    assert scene_cap["match_boundary_unavailable"] is False
    assert situation["limits"]["match_boundary_unavailable"] is False
    assert situation["unknown"]["result"] == "observed"
    assert situation["unknown"]["match_boundary"] == "terminal_signals_only"
    boundary = next(entry for entry in situation["limitations"]
                    if entry["field"] == "match_boundary")
    assert boundary["status"] == "limited"
    assert "victory/defeat" in boundary["note"]


def test_unit_capability_preserves_detector_and_observation_state() -> None:
    unknown = _build(observation_valid=None, detector_available=None,
                     unit_diagnostics={})
    capability = unknown["capabilities"]["units"]
    assert capability["detector_available"] is None
    assert capability["observation_valid"] is None
    assert unknown["facts"]["units"]["detector_available"] is None
    assert unknown["facts"]["units"]["counts"] is not None

    unavailable = _build(
        state=MatchState(scene=SCENE_MATCH, in_match=True),
        observation_valid=False, detector_available=False,
        unit_diagnostics={"status": "model_unavailable",
                          "observation_valid": False,
                          "detector_available": False})
    capability = unavailable["capabilities"]["units"]
    assert capability["detector_available"] is False
    assert capability["observation_valid"] is False
    assert unavailable["facts"]["units"]["detector_available"] is False
    assert unavailable["facts"]["units"]["counts"] is None


def test_active_character_change_retires_old_context_without_config(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": False,
        "auto_start_monitoring": False}})
    monkeypatch.setattr(plugin, "_models_payload", lambda: dict(FAKE_MODELS))
    role = {"name": "A"}
    monkeypatch.setattr(MAIN, "resolve_target_lanlan",
                        lambda _plugin: role["name"])
    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=1)

    plugin._push_live_state(state)
    token_before = plugin.gate.get_token()
    first_count = len(plugin.pushes)
    assert any(p.get("metadata", {}).get("context_action") == "enter"
               and p.get("target_lanlan") == "A" for p in plugin.pushes)
    assert any(p.get("metadata", {}).get("kind") == "game_live_state"
               and p.get("target_lanlan") == "A" for p in plugin.pushes)

    # Active character changes; no config reload/token change occurs.
    role["name"] = "B"
    plugin._push_live_state(state)
    assert plugin.gate.get_token() == token_before
    new_pushes = plugin.pushes[first_count:]
    assert any(p.get("metadata", {}).get("context_expired") is True
               and p.get("target_lanlan") == "A" for p in new_pushes)
    assert any(p.get("metadata", {}).get("context_action") == "enter"
               and p.get("target_lanlan") == "B" for p in new_pushes)
    assert any(p.get("metadata", {}).get("kind") == "game_live_state"
               and p.get("target_lanlan") == "B" for p in new_pushes)
    enter_targets = [p.get("target_lanlan") for p in plugin.pushes
                     if p.get("metadata", {}).get("context_action") == "enter"]
    assert enter_targets == ["A", "B"]


def test_read_wire_uses_situation_ui_time_not_capture(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": False,
        "auto_start_monitoring": False}})
    ui_at = time.time()
    captured_at = ui_at - 1.0
    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=3)
    state.situation = _build(
        state=state, observed_at=captured_at, ui_observed_at=ui_at,
        now=ui_at)
    plugin._push_live_state(state)
    core = [p for p in plugin.pushes
            if p.get("metadata", {}).get("kind") == "game_live_state"
            and p.get("metadata", {}).get("segment") == "core"]
    assert core
    wire = json.loads(core[-1]["parts"][0]["text"].removeprefix("KARDS:"))
    assert wire["observed_at"] == round(ui_at, 3)
    assert wire["observed_at"] != round(captured_at, 3)


def test_tool_recent_events_come_from_facts_and_blocked_is_empty(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kards_companion.runtime.snapshot import FrameMeta, RuntimeSnapshot

    plugin._monitoring = True
    plugin._apply_config({"kards": {
        "llm_data_consent": True, "dry_run": False,
        "auto_start_monitoring": False}})
    monkeypatch.setattr(plugin, "_models_payload", lambda: dict(FAKE_MODELS))
    plugin._session_id = SESSION
    plugin.tracker.set_session_id(SESSION)
    now = time.time()
    state = MatchState(scene=SCENE_MATCH, in_match=True, game_number=1)
    plugin.tracker.update(state)
    plugin.scenario.update(state, now=now)
    event = GameEvent("unit_played", 5, "对手展开了单位。",
                      details={"session_id": SESSION, "game": 1})
    state.situation = _build(
        state=state, session_id=SESSION, events=[event],
        observed_at=now, ui_observed_at=now, now=now)
    with plugin._lifecycle_lock:
        plugin._last_trusted_snapshot = RuntimeSnapshot(
            seq=1, observed_at=now, health=trusted_health(1),
            frame=FrameMeta(frame_id=1, captured_at=now, width=8, height=8,
                            backend="dxcam", valid=True, reason=""),
            state=state, image=object())
        plugin._last_ui_observation_at = now
        plugin._last_ui_frame_id = 1

    payload = asyncio.run(plugin.kards_get_live_state())
    assert payload["paused"] is False
    assert [item["kind"] for item in payload["recent_events"]] == ["unit_played"]
    assert len(payload["recent_events"]) <= 10

    plugin._apply_config({"kards": {
        "llm_data_consent": False, "dry_run": False,
        "auto_start_monitoring": False}})
    blocked = asyncio.run(plugin.kards_get_live_state())
    assert blocked["state"] is None
    assert blocked["recent_events"] == []
