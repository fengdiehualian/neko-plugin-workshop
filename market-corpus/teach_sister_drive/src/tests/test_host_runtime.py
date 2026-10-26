"""Plugin-owned input policy; no microphone, model, or game actions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from plugin.plugins.teach_sister_drive.host_runtime import make_input_router


def setup_route():
    state = {
        "game_type": "teach-sister-drive",
        "game_route_active": True,
        "session_id": "lesson-1",
        "_sdk_route_instance_id": "route-1",
    }
    mgr = SimpleNamespace(mirror_user_input=AsyncMock(), send_user_activity=AsyncMock())
    host = SimpleNamespace(
        _get_active_game_route_state=Mock(return_value=state),
        get_session_manager=lambda: {"Test": mgr},
        build_mirror_meta=lambda **kwargs: dict(kwargs),
        MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE="voice",
        MIRROR_USER_TEXT_INPUT_TYPE="text",
        _build_external_voice_event=lambda s, t: {"userVoiceText": t},
        _build_external_text_event=lambda s, t: {"userText": t},
        _append_game_dialog=Mock(),
        _append_game_output=Mock(),
    )
    original = AsyncMock(return_value="stock-result")
    return host, state, mgr, original, make_input_router(host, original)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,mode", [("user-voice", "voice"), ("user-text", "text")])
async def test_plugin_delivers_input_without_interrupt_or_generic_model(kind, mode):
    host, state, mgr, original, route = setup_route()
    for idx in range(2):
        assert await route("Test", state, "hello", source="test", kind=kind, mode=mode, request_id=str(idx)) is True
    assert mgr.mirror_user_input.await_count == 2
    assert host._append_game_output.call_count == 2
    assert host._append_game_output.call_args.args[1]["type"] == "game_external_input"
    assert mgr.mirror_user_input.call_args.kwargs["metadata"]["sdk_route_instance_id"] == "route-1"
    mgr.send_user_activity.assert_not_awaited()
    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_games_use_stock_handler():
    host, state, mgr, original, route = setup_route()
    state["game_type"] = "soccer"
    assert await route("Test", state, "hello", source="test", kind="user-voice", mode="voice") == "stock-result"
    original.assert_awaited_once()
    mgr.mirror_user_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_retransmit_is_deduplicated_but_distinct_requests_deliver():
    host, state, mgr, original, route = setup_route()
    for request in ("one", "two", "one"):
        await route("Test", state, "hello", source="test", kind="user-voice", mode="voice", request_id=request)
    assert host._append_game_output.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["replace", "exit"])
async def test_route_replaced_or_closed_during_mirror_never_gets_an_output(change):
    host, state, mgr, original, route = setup_route()

    async def mirror(*args, **kwargs):
        if change == "replace":
            host._get_active_game_route_state.return_value = dict(state)
        else:
            state["_exit_flow_started"] = True
        await asyncio.sleep(0)

    mgr.mirror_user_input.side_effect = mirror
    await route("Test", state, "hello", source="test", kind="user-voice", mode="voice", request_id="one")
    host._append_game_output.assert_not_called()
    mgr.send_user_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_or_stale_route_never_mirrors_input():
    host, state, mgr, original, route = setup_route()
    state["game_route_active"] = False
    await route("Test", state, "hello", source="test", kind="user-text", mode="text")
    state["game_route_active"] = True
    host._get_active_game_route_state.return_value = dict(state)
    await route("Test", state, "hello", source="test", kind="user-text", mode="text")
    mgr.mirror_user_input.assert_not_awaited()


def test_bootstrap_mounts_plugin_source_and_is_idempotent(monkeypatch):
    import sys

    from plugin.plugins.teach_sister_drive.host_runtime import install
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    host, state, mgr, original, route = setup_route()
    host._route_external_transcript_to_game = original
    monkeypatch.setitem(sys.modules, "main_routers.game_router", SimpleNamespace(runtime=host))
    app = Starlette()
    install(app)
    install(app)
    assert len(app.routes) == 2
    with TestClient(app) as client:
        assert client.get("/api/teach-sister-drive/plugin-health").json()["input_policy"] == "plugin"
        reply = client.get("/static/game/teach-sister-drive/companion.mjs")
        assert reply.status_code == 200
        assert "keepCompanionVisible" in reply.text
    assert getattr(host._route_external_transcript_to_game, "__sister_plugin_adapter__", False)
