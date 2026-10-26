import asyncio
import importlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from agent_mc_autonomy.narration import NarrationPort
from agent_mc_autonomy.ownership import session_lease
from test_recent_context import prepare


def adapter(plugin, name):
    return importlib.import_module(plugin.__class__.__module__ + "." + name)


def configured(plugin, monkeypatch):
    review, snapshot, raw, settings, clock, save, host, _ = prepare(plugin, monkeypatch)
    raw["heartbeat"]["enabled"] = False  # Voice must not depend on a main review.
    raw["speech_output"] = {"enabled": True, "planner_narration": True, "base_url": "http://127.0.0.1:32123"}
    module = adapter(plugin, "neko_narration")
    monkeypatch.setattr(module, "review_port", lambda *_: review)
    monkeypatch.setattr(module, "character_profile", AsyncMock(return_value={
        "agent_name": "TestRole", "persona": "喜欢探险", "persona_source": "character_profile"}))
    return module, review, raw, settings, clock, save


@pytest.mark.parametrize("outcome", ["accepted", "busy", "unknown"])
def test_same_planning_line_uses_current_cat_voice_without_review_or_model(plugin_instance, monkeypatch, run_async, outcome):
    module, review, raw, settings, _, _ = configured(plugin_instance, monkeypatch)
    speech = adapter(plugin_instance, "neko_speech")
    requests = []
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/api/game/{game_type}/speak": {"post": {}}}})
        data = json.loads(request.content)
        assert data["line"] == "这山景真漂亮，去找块石头搭个小窝。"
        assert data["lanlan_name"] == "TestRole" and not data["interrupt_audio"]
        assert data["wait_for_audio_completion"] and data["mirror_text"]
        if outcome == "unknown":
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(200, json={"ok": outcome == "accepted", "audio_completed": outcome == "accepted", "reason": "busy"})

    monkeypatch.setattr(speech.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    async def scenario():
        assert (await plugin_instance.native_planner_voice()).value["reason"] == "no_new_line"
        binding = module.owned_runtime(plugin_instance, settings, "TestRole", review, 0)
        port = NarrationPort(review.directory, now=review.now)
        assert port.offer('{"spoken_text":"这山景真漂亮，去找块石头搭个小窝。"}', binding)["offered"]
        result = await plugin_instance._native_planner_voice()
        assert result["submitted"] and result["extra_model_calls"] == 0
        assert not requests  # HTTP/TTS runs independently after enqueue.
        await asyncio.gather(*plugin_instance._speech_output_tasks)
        assert (await plugin_instance.native_planner_voice()).value["reason"] == "no_new_line"
        with port.connection() as db:
            assert db.execute("SELECT state FROM lines").fetchone()[0] == {"accepted": "accepted", "busy": "rejected", "unknown": "unknown"}[outcome]

    with session_lease(review.directory):
        run_async(scenario())
    assert [request.url.path for request in requests] == ["/openapi.json", "/api/game/minecraft_autonomous/speak"]


@pytest.mark.parametrize("change", ["stop", "disabled", "foreign_owner", "no_profile"])
def test_stopped_or_unowned_never_enqueues(plugin_instance, monkeypatch, run_async, change):
    module, review, raw, _, _, _ = configured(plugin_instance, monkeypatch)
    if change == "stop":
        plugin_instance._operator_state(True)
    if change == "disabled":
        raw["speech_output"]["planner_narration"] = False
    if change == "no_profile":
        module.character_profile.return_value = {}
    lease = session_lease(review.directory)
    with lease:
        if change == "foreign_owner":
            monkeypatch.setattr(module, "owned_runtime", lambda *_: None)
        result = run_async(plugin_instance.native_planner_voice()).value
    assert not result["submitted"] and not getattr(plugin_instance, "_speech_output_tasks", None)
    if change != "no_profile":
        module.character_profile.assert_not_awaited()


def test_blank_target_resolves_public_current_cat_and_filters_private_fields(plugin_instance, monkeypatch, run_async):
    module = adapter(plugin_instance, "neko_host_context")
    real_client = httpx.AsyncClient
    def handler(request):
        assert request.url.path == "/api/characters"
        return httpx.Response(200, json={"当前猫娘": "UserCat", "猫娘": {
            "UserCat": {"核心特质": ["好奇", "活泼"], "voice_id": "private", "api_key": "private"}}})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    profile = run_async(module.character_profile(plugin_instance,
        module.HostContextSettings(True, "http://127.0.0.1:32123/api/characters"), "", allow_current=True))
    assert profile["agent_name"] == "UserCat" and "活泼" in profile["persona"]
    assert "private" not in json.dumps(profile)


def test_timer_finishes_owned_speech_before_sdk_closes_callback_loop(plugin_instance, monkeypatch, run_async):
    module, review, _, settings, _, _ = configured(plugin_instance, monkeypatch)
    speech = adapter(plugin_instance, "neko_speech")
    requests = []
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/api/game/{game_type}/speak": {"post": {}}}})
        return httpx.Response(200, json={"ok": True, "audio_completed": True})

    monkeypatch.setattr(speech.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    async def scenario():
        await plugin_instance.native_planner_voice()
        binding = module.owned_runtime(plugin_instance, settings, "TestRole", review, 0)
        assert NarrationPort(review.directory, now=review.now).offer('{"spoken_text":"这山风真舒服。"}', binding)["offered"]
        result = (await plugin_instance.native_planner_voice()).value
        # The real SDK wraps EACH timer callback in asyncio.run(...). Returning
        # with a pending child task would cancel it before the next timer tick.
        assert result["submitted"] and len(requests) == 2
        assert all(task.done() for task in plugin_instance._speech_output_tasks)

    with session_lease(review.directory):
        run_async(scenario())
