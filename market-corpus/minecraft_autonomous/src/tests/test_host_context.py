import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agent_mc_autonomy.host_context import HostContextPort
from plugin.sdk.plugin import Err
from test_heartbeat import setup


def adapter(plugin):
    return importlib.import_module(plugin.__class__.__module__ + ".neko_host_context")


@pytest.mark.parametrize("url", ["https://example.com/api/characters", "http://127.0.0.1:48911/private",
    "http://user:secret@127.0.0.1/api/characters", "http://localhost/api/characters?token=secret"])
def test_character_profile_requires_an_explicit_public_loopback_endpoint(plugin_instance, url):
    with pytest.raises(ValueError):
        adapter(plugin_instance).HostContextSettings.parse({"host_context": {"character_api_url": url}})


def test_profile_whitelists_one_character_and_uses_no_translation_or_memory_api(plugin_instance, monkeypatch, run_async):
    module = adapter(plugin_instance)
    response = SimpleNamespace(raise_for_status=Mock(), json=lambda: {"当前猫娘": "OtherRole", "猫娘": {
        "TestRole": {"system_prompt": "有耐心。" * 2000, "api_key": "private", "_reserved": {"token": "private"}},
        "OtherRole": {"system_prompt": "not selected"}}})
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    factory = Mock(return_value=manager)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    settings = module.HostContextSettings(character_api_url="http://127.0.0.1:48911/api/characters")
    result = run_async(module.character_profile(plugin_instance, settings, "TestRole"))
    assert set(result) == {"agent_name", "persona", "persona_source"}
    assert result["agent_name"] == "TestRole" and result["persona_source"] == "character_profile"
    assert len(result["persona"].encode()) <= 2400 and "private" not in json.dumps(result)
    assert factory.call_args.kwargs == {"timeout": 3, "trust_env": False, "follow_redirects": False}
    assert client.get.call_args.kwargs == {"params": {"language": "zh-CN"}}
    assert run_async(module.character_profile(plugin_instance, settings, "TestRole")) == result
    assert client.get.await_count == 1


def test_default_structured_neko_traits_are_shared_without_voice_or_reserved_data(plugin_instance, monkeypatch, run_async):
    module = adapter(plugin_instance)
    response = SimpleNamespace(raise_for_status=Mock(), json=lambda: {"猫娘": {"TestRole": {
        "核心特质": "温和，好奇", "行为特点": "先照顾眼前的事", "性格": ["开朗", "自信"], "自称": "我",
        "voice_id": "private_voice", "lighting": "private_setting", "_reserved": {"api_key": "private_key"}}}})
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(get=AsyncMock(return_value=response))
    monkeypatch.setattr(module.httpx, "AsyncClient", Mock(return_value=manager))
    config = module.HostContextSettings(character_api_url="http://127.0.0.1:48911/api/characters")
    result = run_async(module.character_profile(plugin_instance, config, "TestRole"))
    assert "温和，好奇" in result["persona"] and "先照顾眼前的事" in result["persona"]
    assert "开朗；自信" in result["persona"]
    assert "private_" not in json.dumps(result)


@pytest.mark.parametrize("profile", [None, {}, {"system_prompt": ""}, {"system_prompt": "   "}])
def test_absent_or_empty_profile_is_not_a_shared_personality(plugin_instance, monkeypatch, run_async, profile):
    module = adapter(plugin_instance)
    roles = {} if profile is None else {"TestRole": profile}
    response = SimpleNamespace(raise_for_status=Mock(), json=lambda: {"猫娘": roles})
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(get=AsyncMock(return_value=response))
    monkeypatch.setattr(module.httpx, "AsyncClient", Mock(return_value=manager))
    settings = module.HostContextSettings(character_api_url="http://127.0.0.1:48911/api/characters")
    assert run_async(module.character_profile(plugin_instance, settings, "TestRole")) == {}


def test_personality_and_memory_requests_are_independent(plugin_instance, monkeypatch):
    port, _, _ = setup(plugin_instance, monkeypatch)
    HostContextPort(port.directory, now=port.now).publish({"agent_name": "TestRole", "persona": "", "memories": []},
        session="123:", revision=port.mailbox.intent_revision())
    request = adapter(plugin_instance).context_request(port, port.now)
    assert request["needed"] and request["persona_needed"]
    assert not request["memory_needed"]


def test_main_brain_selected_context_reaches_framework_and_is_not_public(plugin_instance, monkeypatch, run_async):
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    inspection = run_async(plugin_instance.minecraft_autonomy_review(review_id, "inspect"))
    assert inspection["context_request"]["needed"]
    context = {"persona": "会照顾已有物资", "current_intent": "收集食物",
               "memories": [{"id": "memory-1", "source": "recall_memory", "text": "不要拆原有建筑"}]}
    result = run_async(plugin_instance.minecraft_autonomy_review(review_id, "continue", "准备采集", context=context))
    assert result["acknowledged"] and result["host_context"]["memory_count"] == 1
    stored = HostContextPort(port.directory, now=port.now).read(port.mailbox.intent_revision())
    assert stored["stable"]["agent_name"] == "TestRole"
    assert stored["stable"]["persona_source"] == "host_projection"
    assert stored["dynamic"]["memories"] == context["memories"]
    assert "不要拆原有建筑" not in json.dumps(port.state(), ensure_ascii=False)
    assert "会照顾已有物资" not in json.dumps(result, ensure_ascii=False)


def test_context_does_not_change_identity_or_copy_unrelated_configuration(plugin_instance, run_async):
    module = adapter(plugin_instance)
    for supplied in ({"agent_name": "OtherRole"}, {"api_key": "private"}, []):
        with pytest.raises(ValueError):
            run_async(module.shared_context(plugin_instance, {}, "TestRole", supplied, {}))


def test_profile_refresh_preserves_only_fresh_selected_memory_unless_explicitly_cleared(plugin_instance, monkeypatch, run_async):
    module = adapter(plugin_instance)
    monkeypatch.setattr(module, "character_profile", AsyncMock(return_value={"agent_name": "TestRole",
        "persona": "有耐心", "persona_source": "character_profile"}))
    memories = [{"id": "m-1", "source": "recall_memory", "text": "保护建筑"}]
    existing = {"stable": {"agent_name": "TestRole"}, "dynamic": {"memories": memories}}
    result = run_async(module.shared_context(plugin_instance, {}, "TestRole", None, {}, existing=existing))
    assert result["memories"] == memories
    assert result["memories_selected"] is False
    cleared = run_async(module.shared_context(plugin_instance, {}, "TestRole", {"memories": []}, {}, existing=existing))
    assert cleared["memories"] == []
    assert cleared["memories_selected"] is True
    other = {**existing, "stable": {"agent_name": "OtherRole"}}
    assert run_async(module.shared_context(plugin_instance, {}, "TestRole", None, {}, existing=other))["memories"] == []


def test_disabling_context_invalidates_private_projection_without_stopping_game(plugin_instance, monkeypatch, run_async):
    port, _, _ = setup(plugin_instance, monkeypatch)
    projection = HostContextPort(port.directory)
    projection.publish({"agent_name": "TestRole"}, session="123:", revision=0)
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    result = run_async(plugin_instance.save_settings({"host_context": {"enabled": False}}))
    assert result.value["saved"] and not result.value["running_session_changed"]
    assert not projection.path.exists()
    assert not plugin_instance.data_path("operator.json").exists()
    assert not adapter(plugin_instance).context_request(port, port.now, enabled=False)["needed"]
    assert isinstance(run_async(plugin_instance.save_settings({"host_context": {"api_key": "private"}})), Err)


def test_opt_out_during_profile_read_cannot_publish_late_context(plugin_instance, monkeypatch, run_async):
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    module = adapter(plugin_instance)
    monkeypatch.setattr(plugin_instance.config, "update", AsyncMock())

    async def profile(*_):
        await plugin_instance.save_settings({"host_context": {"enabled": False}})
        return {"agent_name": "TestRole", "persona": "过期", "persona_source": "character_profile"}

    monkeypatch.setattr(module, "character_profile", profile)
    result = run_async(plugin_instance.minecraft_autonomy_review(review_id, "continue", "继续"))
    assert result["error"] == "configuration_changed_or_stopped"
    assert not (port.directory / "host-context.json").exists()
