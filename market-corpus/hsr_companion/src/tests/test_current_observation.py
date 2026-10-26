from __future__ import annotations

import asyncio
import base64
import importlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion import HsrCompanionPlugin
from plugin.plugins.hsr_companion.assistant import AssistantFrame, VisionAssistantClient
from plugin.plugins.hsr_companion.assistant_runtime.server import CaptureState
from plugin.plugins.hsr_companion.core import CharacterRegistry
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.detectors import analyze_layout
from plugin.plugins.hsr_companion.runtime import CompanionRuntime
from plugin.plugins.hsr_companion.runtime.observation import asks_screen_identity, public_visual_evidence
from plugin.plugins.hsr_companion.session import start_session, utc_now_iso
from plugin.sdk.plugin import Ok

ROOT = Path(__file__).resolve().parents[1]
ROLE = {"entity_id": "character.silverwolf", "name": "银狼", "identity_scope": "hud_label", "evidence": "unknown_name_region"}


def analysis(*, roles=True, scene="unknown"):
    return {
        "ok": True, "source": "assistant_local_ocr", "observed_at": utc_now_iso(),
        "page": {"page_type": "unknown" if scene == "unknown" else "activity_menu"},
        "scene": {"primary_state": scene, "verified": scene != "unknown", "confidence": "high" if scene != "unknown" else "low"},
        "characters": [dict(ROLE)] if roles else [],
        "recognition": {"portrait_templates": {"reason": "no_reliable_portrait"}},
    }


def plugin_fixture(tmp_path):
    plugin = object.__new__(HsrCompanionPlugin)
    payload = json.loads((ROOT / "resources/character_registry.json").read_text(encoding="utf-8"))
    plugin._characters = CharacterRegistry(payload)
    plugin._catalog = ExternalKnowledgeCatalog(registry=payload, root=tmp_path)
    plugin._runtime = CompanionRuntime(global_rate_seconds=0)
    plugin._observer_status = {"state": "observing", "enabled": True, "proactive_enabled": True}
    plugin._tool_audit_limit = 100
    plugin._observer_last_signature = None
    plugin._observer_last_push_monotonic = 0
    values = {"player_context": {"owned_characters": ["花火"]}}
    sent = []

    async def get(key):
        return Ok(values.get(key))

    async def set_value(key, value):
        values[key] = value
        return Ok(value)

    async def touch(reason):
        return start_session({}, reason=reason), False

    async def wait_pack(**kwargs):
        return {}

    plugin.store = SimpleNamespace(get=get, set=set_value)
    plugin._touch_session = touch
    plugin._wait_for_data_pack = wait_pack
    plugin._data_pack_runtime = plugin._catalog.status()
    plugin.push_message = lambda **kwargs: sent.append(kwargs) or {"submitted": True}
    return plugin, values, sent


@pytest.mark.parametrize("query", ["识别当前屏幕里的角色", "右侧角色", "还有谁"])
def test_ocr_result_reaches_chat_without_model_supplied_names(tmp_path, query):
    plugin, values, sent = plugin_fixture(tmp_path)

    async def run():
        await plugin._apply_local_analysis(analysis(), image_fingerprint="same-pixels", source="assistant_local_ocr")
        pending = await plugin.hsr_companion_prepare(query=query, observed_character_names=["花火"])
        assert pending["screen_identity_answer"]["allowed"] is False
        assert pending["current_visual_evidence"]["status"] == "confirming"
        assert pending["screen_identity_answer"]["names"] == []
        # A second independently captured frame can have identical pixels.
        await plugin._apply_local_analysis(analysis(), image_fingerprint="same-pixels", source="assistant_local_ocr")
        result = await plugin.hsr_companion_prepare(query=query, observed_character_names=["花火"])
        assert result["screen_identity_answer"]["names"] == ["银狼"]
        assert result["screen_identity_answer"]["allowed"] is True
        assert result["current_visual_evidence"]["scene"]["primary_state"] == "unknown"
        assert result["current_game_state"]["primary_state"] == "unknown"
        assert "已确认界面角色：银狼" in result["authoritative_context"]
        assert "花火" not in str(result["character_resolution"])
        assert result["player_context"] == {}
        assert result["pending_profile_proposal"] == {}
        assert result["knowledge_results"] == []
        assert any("当前已确认界面角色：银狼" in str(item["parts"]) for item in sent)
        assert values["last_local_vision"]["runtime"]["stable_character_ids"] == [ROLE["entity_id"]]

    asyncio.run(run())


@pytest.mark.parametrize("reason", ["capture_paused", "game_closed", "capture_error", "plugin_restarted", "observation_stopped"])
def test_observation_invalidation_removes_current_facts_but_not_history(tmp_path, reason):
    plugin, values, sent = plugin_fixture(tmp_path)
    values["character_corrections"] = [{"active": False, "canonical_name": "火花"}]
    values["experiences"] = [{"summary": "历史经历"}]

    async def run():
        await plugin._apply_local_analysis(analysis(scene="menu"), image_fingerprint="a", source="assistant_local_ocr")
        await plugin._apply_local_analysis(analysis(scene="menu"), image_fingerprint="b", source="assistant_local_ocr")
        await plugin._invalidate_current_observation(reason)
        visual = await plugin._current_visual_evidence()
        assert visual["characters"] == []
        assert visual["fresh"] is False
        if reason == "capture_error":
            # A transport failure masks answers but does not destroy the read.
            assert values["last_local_vision"]["characters"]
            assert plugin._observation_pending_reason == reason
            assert plugin._runtime.stable_character_ids == (ROLE["entity_id"],)
        else:
            assert values["current_game_state"] == {}
            assert values["active_game_visual_request"] == {}
            assert plugin._runtime.stable_character_ids == ()
            assert plugin._runtime.stable_scene == "unknown"
        assert values["experiences"] == [{"summary": "历史经历"}]
        assert values["character_corrections"][0]["canonical_name"] == "火花"
        assert sent[-1]["ai_behavior"] == "read"
        assert "当前已确认界面角色：无" in str(sent[-1]["parts"])

    asyncio.run(run())


def test_unknown_frame_clears_prior_menu_without_losing_independent_role(tmp_path):
    plugin, values, _ = plugin_fixture(tmp_path)

    async def run():
        await plugin._apply_local_analysis(analysis(roles=False, scene="menu"), image_fingerprint="a", source="assistant_local_ocr")
        assert values["current_game_state"]["primary_state"] == "menu"
        await plugin._apply_local_analysis(analysis(), image_fingerprint="b", source="assistant_local_ocr")
        assert values["current_game_state"] == {}
        assert plugin._runtime.stable_scene == "unknown"
        assert plugin._runtime.pending_character_ids == (ROLE["entity_id"],)

    asyncio.run(run())


@pytest.mark.parametrize("state", ["paused", "waiting", "stopped", "error"])
def test_paused_observer_cannot_expose_cached_names(state):
    value = analysis()
    value["runtime"] = {"stable_character_ids": [ROLE["entity_id"]], "stable_scene": "menu"}
    result = public_visual_evidence(value, {"state": state})
    assert result["characters"] == []
    assert result["status"] == state
    assert result["scene"]["primary_state"] == "unknown"


def test_expired_or_future_observation_cannot_expose_names():
    now = datetime.now(timezone.utc)
    value = analysis()
    value["runtime"] = {"stable_character_ids": [ROLE["entity_id"]]}
    for timestamp in (now - timedelta(seconds=61), now + timedelta(seconds=10)):
        value["observed_at"] = timestamp.isoformat()
        assert public_visual_evidence(value, {"state": "observing"}, now=now)["characters"] == []


@pytest.mark.parametrize("query,intent,expected", [
    ("识别当前屏幕里的角色", "conversation", True),
    ("画面上是谁", "conversation", True),
    ("这个是谁", "character_identity", True),
    ("这个角色是谁", "conversation", True),
    ("右侧角色", "conversation", True),
    ("右边是谁", "conversation", True),
    ("队伍栏还有谁", "conversation", True),
    ("还有谁？", "conversation", True),
    ("还有谁呀？", "conversation", True),
    ("银狼还有哪些技能", "knowledge", False),
    ("银狼的战技是什么", "knowledge", False),
])
def test_screen_identity_intent_does_not_depend_on_model_selecting_right_enum(query, intent, expected):
    assert asks_screen_identity(query, intent) is expected


def test_unsupported_page_returns_explicit_no_identity(tmp_path):
    plugin, values, _ = plugin_fixture(tmp_path)
    value = analysis(roles=False, scene="menu")
    value["runtime"] = {"stable_scene": "menu"}
    value["recognition"]["portrait_templates"]["reason"] = "unsupported_portrait_page"
    values["last_local_vision"] = value
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
    ObservationStore(plugin).commit(value)  # Current input, not an answer promoted from disk history.
    result = asyncio.run(plugin.hsr_companion_prepare(query="画面里的角色是谁", observed_character_names=["花火"]))
    assert result["current_visual_evidence"]["status"] == "unsupported_page"
    assert result["screen_identity_answer"]["names"] == []
    assert result["screen_identity_answer"]["unavailable_reply"] == "目前无法确认画面中的角色。"


def test_overworld_needs_both_registry_hud_and_left_navigation(tmp_path):
    plugin, _, _ = plugin_fixture(tmp_path)
    boxes = [
        {"text": "银狼LV.999", "score": .994, "left": 1100, "right": 1190, "top": 300, "bottom": 324},
        {"text": "请前往千星城中心城区", "score": .98, "left": 30, "right": 300, "top": 180, "bottom": 205},
    ]
    result = analyze_layout("\n".join(item["text"] for item in boxes), boxes, plugin._catalog, width=1280, height=800)
    assert result["scene"]["primary_state"] == "exploration"
    assert result["scene"]["confidence"] == "mixed"
    assert result["characters"][0]["name"] == "银狼LV.999"
    boxes[1].update(left=30, right=600, top=650, bottom=680)
    result = analyze_layout("\n".join(item["text"] for item in boxes), boxes, plugin._catalog, width=1280, height=800)
    assert result["scene"]["primary_state"] == "unknown"


@pytest.mark.parametrize("foreground", [True, False])
def test_helper_sequences_independent_identical_captures(monkeypatch, foreground):
    module = importlib.import_module(CaptureState.__module__)
    state = CaptureState()
    window = SimpleNamespace(minimized=False, foreground=foreground, public=lambda: {"foreground": foreground})
    monkeypatch.setattr(module, "_find_window", lambda: window)
    monkeypatch.setattr(module, "_capture", lambda window, **kwargs: (b"identical-frame", "image/jpeg", time.time()))
    calls = []

    def next_capture(delay):
        calls.append(True)
        if len(calls) == 2:
            state.stop.set()
        return state.stop.is_set()

    state.stop.wait = next_capture
    state.update()
    assert state.frame_seq == 2
    assert state.snapshot(include_image=True, since_frame=1)["image_base64"]
    state.capture = {"state": "paused"}
    assert state.snapshot(include_image=True)["image_base64"] == ""


@pytest.mark.parametrize("state,age,running,minimized", [
    ("paused", 1, True, False), ("capturing", 20, True, False),
    ("capturing", 1, False, False), ("capturing", 1, True, True),
])
def test_client_rejects_unavailable_frames(state, age, running, minimized):
    client = VisionAssistantClient()
    client._get_json = lambda path: {
        "frame_seq": 1, "timestamp": time.time() - age, "mime": "image/jpeg",
        "image_base64": base64.b64encode(b"frame").decode(), "capture": {"state": state, "isolated_window": True},
        "game": {"running": running, "foreground": False, "minimized": minimized},
    }
    assert client.fetch_frame() is None


def test_client_accepts_fresh_isolated_game_frame_while_chat_has_focus():
    client = VisionAssistantClient()
    client._get_json = lambda path: {
        "frame_seq": 1, "timestamp": time.time(), "mime": "image/jpeg",
        "image_base64": base64.b64encode(b"game-only").decode(),
        "capture": {"state": "capturing", "isolated_window": True},
        "game": {"running": True, "foreground": False, "minimized": False},
    }
    assert client.fetch_frame().image_bytes == b"game-only"


def test_unknown_id_or_mismatched_name_cannot_enter_current_facts(tmp_path):
    plugin, values, _ = plugin_fixture(tmp_path)

    async def run():
        value = analysis()
        value["characters"] = [{**ROLE, "name": "花火"}, {**ROLE, "entity_id": "invented.character"}]
        await plugin._apply_local_analysis(value, image_fingerprint="a", source="assistant_structured_vision")
        assert values["last_local_vision"]["characters"] == []
        assert plugin._runtime.pending_character_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["focus_only", "paused", "minimized", "closed", "pid_changed", "legacy_capture"])
def test_game_availability_rechecked_after_ocr_not_chat_focus(tmp_path, monkeypatch, change):
    plugin, values, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(HsrCompanionPlugin.__module__)
    health_calls = []

    def health():
        health_calls.append(True)
        after = len(health_calls) > 1
        return {"connected": True, "frame_seq": 1, "capture": {
            "state": "paused" if after and change == "paused" else "capturing",
            "isolated_window": not (after and change == "legacy_capture")},
            "game": {"pid": 2 if after and change == "pid_changed" else 1,
                     "running": not (after and change == "closed"), "foreground": not after,
                     "minimized": after and change == "minimized"}}

    plugin._assistant_client = SimpleNamespace(health=health, fetch_frame=lambda seq: AssistantFrame(
        1, time.time(), b"frame", "image/jpeg", "same", {"pid": 1, "running": True, "foreground": True}, {"state": "capturing"}))
    plugin._assistant_process = SimpleNamespace(snapshot=lambda: {})
    plugin._assistant_frame_seq = 0
    plugin._announce_game_presence = lambda game: None
    plugin._vision = SimpleNamespace(analyze=lambda payload: analysis())
    monkeypatch.setattr(module, "visual_fingerprint", lambda payload: "same-pixels")

    async def stop_after_pause(delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=stop_after_pause, CancelledError=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin._observer_loop())
    if change == "focus_only":
        assert values["last_local_vision"]["characters"] == [ROLE]
        assert plugin._observer_status["state"] == "observing"
    else:
        assert not values.get("last_local_vision")
        assert plugin._runtime.pending_character_ids == ()
        assert plugin._observer_status["state"] == "paused"


@pytest.mark.parametrize("processing_seconds", [20, 31])
@pytest.mark.parametrize("foreground", [True, False])
def test_slow_analysis_is_not_reported_as_leaving_game(tmp_path, monkeypatch, processing_seconds, foreground):
    plugin, values, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(HsrCompanionPlugin.__module__)
    clock = [100.0]
    captured = time.time() - 2
    game = {"pid": 1, "running": True, "foreground": foreground}
    plugin._assistant_client = SimpleNamespace(
        health=lambda: {"connected": True, "game": game, "capture": {"state": "capturing", "isolated_window": True}},
        fetch_frame=lambda seq: AssistantFrame(1, captured, b"frame", "image/jpeg", "same", game, {"state": "capturing"}),
    )
    plugin._assistant_process = SimpleNamespace(snapshot=lambda: {})
    plugin._assistant_frame_seq = 0
    plugin._announce_game_presence = lambda game: None

    def analyze(payload):
        clock[0] += processing_seconds
        return analysis()

    plugin._vision = SimpleNamespace(analyze=analyze)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "visual_fingerprint", lambda payload: "same-pixels")

    async def stop(delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=stop, CancelledError=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin._observer_loop())
    if processing_seconds <= 30:
        assert plugin._observer_status["state"] == "observing"
        assert abs(datetime.fromisoformat(values["last_local_vision"]["observed_at"]).timestamp() - captured) < 1e-6
    else:
        assert not values.get("last_local_vision")
        assert plugin._observer_status["state"] == "updating"
        assert "耗时" in plugin._observer_status["message"]
    assert "离开" not in plugin._observer_status["message"]


@pytest.mark.parametrize("foreground", [True, False])
def test_static_clean_helper_frame_is_recaptured_not_replayed(tmp_path, monkeypatch, foreground):
    plugin, values, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(HsrCompanionPlugin.__module__)
    window = SimpleNamespace(minimized=False, foreground=foreground, public=lambda: {"running": True, "foreground": foreground, "pid": 1})
    frame = AssistantFrame(1, time.time(), b"first-capture", "image/jpeg", "same", window.public(), {"state": "capturing"})
    frames = iter([frame, None])
    plugin._assistant_client = SimpleNamespace(
        health=lambda: {"connected": True, "frame_seq": 1, "game": window.public(), "capture": {"state": "capturing", "isolated_window": True}},
        fetch_frame=lambda seq: next(frames),
    )
    plugin._assistant_process = SimpleNamespace(snapshot=lambda: {})
    plugin._assistant_frame_seq = 0
    plugin._announce_game_presence = lambda game: None
    captures = []

    def capture(window):
        captures.append(True)
        return b"fresh-second-capture", "image/jpeg", b"same"

    analyzed = []
    plugin._vision = SimpleNamespace(analyze=lambda payload: analyzed.append(payload) or analysis())
    monkeypatch.setattr(module, "find_hsr_window", lambda: window)
    monkeypatch.setattr(module, "capture_game_window", capture)
    monkeypatch.setattr(module, "visual_fingerprint", lambda payload: "same-pixels")

    async def tick(delay):
        if len(analyzed) >= 2:
            raise asyncio.CancelledError()
        plugin._observer_last_push_monotonic = time.monotonic() - 4

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=tick, CancelledError=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin._observer_loop())
    assert captures == [True]
    assert analyzed == [b"first-capture", b"fresh-second-capture"]
    assert values["last_local_vision"]["runtime"]["stable_character_ids"] == [ROLE["entity_id"]]
