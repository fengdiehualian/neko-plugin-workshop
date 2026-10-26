import base64
import importlib
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError
from plugin.sdk.plugin import Err, Ok


def module(plugin_module, name):
    return importlib.import_module(plugin_module.__name__ + "." + name)


def image_raw():
    # Core's transport header, not a rendered image fixture.
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 128, 128)
    return {"content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}]}




def model_response(content="Observed response", finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(model_dump=lambda **_: {"content": content, "tool_calls": []}))],
        usage=SimpleNamespace(model_dump=lambda **_: {"prompt_tokens": 100, "completion_tokens": 12, "total_tokens": 112}))


def gateway(create):
    return SimpleNamespace(get_client=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))), aclose=AsyncMock())


def test_host_profile_uses_one_existing_model_call_and_reports_unsupported_depth_parameters(plugin_module, run_async):
    create=AsyncMock(return_value=model_response())
    planner=module(plugin_module,'neko_planner').NekoPlanner(gateway(create),1024)
    profile={'profile':'routine','reason':'routine_current_goal','max_output_tokens':256,'parameters':{'reasoning_effort':'low'}}
    result=run_async(planner.decide_profile([{'role':'user','content':'facts'}],[],profile))
    assert create.await_count==1 and create.call_args.kwargs['max_completion_tokens']==256
    assert 'reasoning_effort' not in create.call_args.kwargs
    assert result['model_status']['decision_profile']['fallback']=='host_reasoning_parameters_unavailable'
    assert result['model_status']['decision_profile']['parameters_applied'] is False
    assert planner.max_output_tokens==1024


def test_neko_image_slot_falls_back_without_provider_details(plugin_module, run_async):
    planner = module(plugin_module, "neko_planner")
    error = APIStatusError("private provider detail", response=httpx.Response(409, request=httpx.Request("POST", "http://unused")),
                           body={"error": {"code": "model_capability_mismatch", "message": "secret"}})
    create = AsyncMock(side_effect=[error, model_response(), model_response()])
    host = gateway(create)
    brain = planner.NekoPlanner(host, 512)
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": [
        {"type": "text", "text": "actual facts"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    result = run_async(brain.decide(messages, []))
    assert result["model_status"]["fallback"] == "model_capability_mismatch"
    assert result["model_status"]["usage"]["total_tokens"] == 112
    assert [c.kwargs["model"] for c in create.call_args_list] == ["vision", "decision"]
    assert create.call_args_list[0].kwargs["messages"] == messages
    assert create.call_args_list[1].kwargs["messages"][-1]["content"] == "actual facts"
    assert "secret" not in json.dumps(result)
    assert messages[-1]["content"][1]["type"] == "image_url"  # No mutation of caller history.
    run_async(brain.decide(messages, []))
    assert create.call_args_list[-1].kwargs["model"] == "decision"
    assert host.get_client.await_count == 1


def test_neko_text_and_multimodal_use_declared_aliases(plugin_module, run_async):
    planner = module(plugin_module, "neko_planner")
    create = AsyncMock(return_value=model_response())
    brain = planner.NekoPlanner(gateway(create), 1024)
    text = [{"role": "user", "content": "facts"}]
    assert run_async(brain.decide(text, []))["model_status"]["route"] == "decision"
    images = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    assert run_async(brain.decide(images, []))["model_status"]["route"] == "vision"
    assert create.call_args.kwargs["max_completion_tokens"] == 1024


def test_missing_decision_is_visible_without_dispatch_or_leaking(plugin_module, run_async):
    planner = module(plugin_module, "neko_planner")
    err = APIStatusError("secret", response=httpx.Response(403, request=httpx.Request("POST", "http://unused")),
                         body={"error": {"code": "model_usage_not_bound"}})
    brain = planner.NekoPlanner(gateway(AsyncMock(side_effect=err)), 1024)
    result = run_async(brain.decide([{"role": "user", "content": "facts"}], []))
    assert result["tool_calls"] == [] and result["model_status"]["reason"] == "model_usage_not_bound"
    assert run_async(planner.NekoPlanner(None, 1024).decide([], []))["model_status"]["route"] == "unavailable"


def test_legacy_sdk_model_check_reports_missing_gateway(plugin_instance, run_async, monkeypatch):
    monkeypatch.delattr(type(plugin_instance.ctx), "models")
    result = run_async(plugin_instance.check_model(mode="text"))
    assert isinstance(result, Ok)
    assert result.value["structured_probe_passed"] is False
    assert result.value["model_status"]["reason"] == "model_gateway_unavailable"


def test_incomplete_neko_output_uses_same_bounded_retry_contract(plugin_module, run_async):
    planner = module(plugin_module, "neko_planner")
    brain = planner.NekoPlanner(gateway(AsyncMock(return_value=model_response(finish="length"))), 512)
    with pytest.raises(planner.PlannerResponseError):
        run_async(brain.decide([], []))


def test_invalid_vision_response_retries_text_once(plugin_module, run_async):
    planner = module(plugin_module, "neko_planner")
    create = AsyncMock(side_effect=[model_response(finish="length"), model_response()])
    brain = planner.NekoPlanner(gateway(create), 512)
    messages = [{"role": "user", "content": [{"type": "text", "text": "current facts"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    result = run_async(brain.decide(messages, []))
    assert result["model_status"]["route"] == "decision"
    assert result["model_status"]["fallback"] == "invalid_vision_response"
    assert create.await_count == 2
    assert create.call_args.kwargs["messages"][-1]["content"] == "current facts"














def test_dashboard_has_bounded_actual_data_and_no_secret_config(plugin_instance, plugin_module, run_async, tmp_path, monkeypatch):
    config = {"runtime": {"mode": "external", "data_directory": str(tmp_path)},
              "server": {"host": "example.test", "username": "Alice"}, "private_provider_key": "not-for-ui"}
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value=config))
    settings = plugin_module.Settings.parse(config)
    directory = tmp_path / "sessions" / settings.identity / "host"
    directory.mkdir(parents=True)
    (directory / "status.json").write_text(json.dumps({"phase": "running", "facts": {"parts": [{"key": "gear", "text": "x" * 2000}]}}), encoding="utf-8")
    (directory / "decisions.jsonl").write_text("\n".join(json.dumps({"turnId": str(i), "response": {"content": "reply", "model_status": {"usage": {"total_tokens": 10}}}}) for i in range(35)) + '\n{"partial":', encoding="utf-8")
    result = run_async(plugin_instance.dashboard())
    assert result["runtime"]["live_verified"] is False
    assert len(result["runtime"]["facts"][0]["text"]) == 900
    assert len(result["audit"]["decisions"]) == 20 and result["audit"]["sample_tokens"] == 200
    assert "not-for-ui" not in json.dumps(result)
    assert plugin_instance._worker is None


def test_settings_save_validates_without_starting_or_copying_provider_keys(plugin_instance, run_async, monkeypatch):
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert isinstance(run_async(plugin_instance.save_settings({"fast": {"api_key": "secret"}})), Err)
    assert isinstance(run_async(plugin_instance.save_settings({"server": {"version": "1.20.6"}})), Err)
    assert not update.await_count
    patch = {"perception": {"enabled": False}, "fast": {"mode": "rules"}}
    result = run_async(plugin_instance.save_settings(patch))
    assert isinstance(result, Ok) and not result.value["running_session_changed"]
    update.assert_awaited_once_with(patch)
    assert plugin_instance._worker is None


def test_memory_settings_use_shared_config_without_starting_or_exposing_secrets(plugin_instance, plugin_module, run_async, monkeypatch):
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    monkeypatch.setenv("GAME_MEMORY_KEY", "private-memory-fixture")
    patch = {"memory": {"mode": "remote", "provider": "powercontext", "endpoint": "https://memory.example.org",
                        "api_key_env": "GAME_MEMORY_KEY", "scope_id": "existing-game-scope", "learning_enabled": True}}
    result = run_async(plugin_instance.save_settings(patch))
    assert isinstance(result, Ok) and result.value["running_session_changed"] is False
    update.assert_awaited_once_with(patch)
    dashboard = module(plugin_module, "dashboard")
    state = dashboard.public_config(plugin_module.Settings.parse(patch))
    assert state["memory"]["scope_id"] == "existing-game-scope"
    assert state["memory"]["learning_enabled"] is True
    assert "private-memory-fixture" not in json.dumps(state)
    assert isinstance(run_async(plugin_instance.save_settings({"memory": {"api_key": "secret"}})), Err)
    assert plugin_instance._worker is None


def test_explicit_model_diagnostic_uses_same_gateway_and_never_dispatches(plugin_instance, plugin_module, run_async):
    check = module(plugin_module, "model_check")
    create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls",
        message=SimpleNamespace(model_dump=lambda **_: {"tool_calls": [{"id": "probe", "type": "function",
            "function": {"name": "report_probe", "arguments": '{"answer":"RED"}'}}]}))], usage=None))
    host = gateway(create)
    result = run_async(check.check_model(host, "vision"))
    assert result["vision_probe_passed"]
    assert result["game_action_dispatched"] is False
    assert create.call_args.kwargs["model"] == "vision"
    assert base64.b64decode(check.calibration_png())[:8] == b"\x89PNG\r\n\x1a\n"
    assert host.aclose.await_count == 1 and plugin_instance._worker is None
    assert isinstance(run_async(plugin_instance.check_model("other")), Err)


def test_viewer_setting_can_change_without_stopping_active_body(plugin_instance, run_async, monkeypatch):
    worker = SimpleNamespace(snapshot=lambda: {"worker_alive": True})
    plugin_instance._worker = worker
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"viewer": {"url": "http://127.0.0.1:3000/dungeon/"}}
    result = run_async(plugin_instance.save_settings(patch))
    assert isinstance(result, Ok) and not result.value["running_session_changed"]
    update.assert_awaited_once_with(patch)
    assert plugin_instance._worker is worker
    assert isinstance(run_async(plugin_instance.save_settings({"autonomy": {"enabled": False}})), Err)
    assert isinstance(run_async(plugin_instance.save_settings({**patch, "server": {"port": 25566}})), Err)
    assert update.await_count == 1


def test_bundled_catalog_setting_roundtrips_without_starting_game(plugin_instance, plugin_module, run_async, monkeypatch):
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"viewer": {"bundled_appearance_catalog_file": "D:/plugin/assets/appearances/catalog.json"}}
    result = run_async(plugin_instance.save_settings(patch))
    assert isinstance(result, Ok) and not result.value["running_session_changed"]
    update.assert_awaited_once_with(patch)
    state = module(plugin_module, "dashboard").public_config(plugin_module.Settings.parse(patch))
    assert state["viewer"]["bundled_appearance_catalog_file"] == patch["viewer"]["bundled_appearance_catalog_file"]
    assert plugin_instance._worker is None


@pytest.mark.parametrize("appearance", ["server", "bundled:yui-ysm", "bundled:yui-slim", "native:misc/3_default_boy:red"])
def test_web_default_roundtrips_without_start_or_account_change(plugin_instance, plugin_module, run_async, monkeypatch, appearance):
    raw = {"server": {"host": "fixture.test", "username": "Alice", "version": "1.21.1"}}
    original_identity = plugin_module.Settings.parse(raw).identity
    writes = []

    async def update(patch):
        writes.append(patch)
        raw.update({key: {**raw.get(key, {}), **value} for key, value in patch.items()})

    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(side_effect=lambda: raw))
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"viewer": {"default_appearance": appearance}}
    result = run_async(plugin_instance.save_settings(patch))
    assert isinstance(result, Ok) and result.value["running_session_changed"] is False
    state = run_async(plugin_instance.dashboard())
    assert state["settings"]["viewer"]["default_appearance"] == appearance
    assert state["settings"]["server"] == {**raw["server"], "port": 25565, "auth": "offline", "connection_type": "custom", "login_config_file": ""}
    assert plugin_module.Settings.parse(raw).identity == original_identity
    assert writes == [patch] and plugin_instance._worker is None
    assert isinstance(run_async(plugin_instance.save_settings({"viewer": {"default_appearance": 'server"<private>'}})), Err)
    assert writes == [patch]


def test_new_install_dashboard_shows_neko_web_default_and_external_runtime_stays_neutral(plugin_instance, run_async, monkeypatch, tmp_path):
    embedded = run_async(plugin_instance.dashboard())
    assert embedded["settings"]["viewer"]["default_appearance"] == "bundled:yui-ysm"
    assert embedded["settings"]["autonomy"]["enabled"] is False and plugin_instance._worker is None
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={"runtime": {"mode": "external", "data_directory": str(tmp_path)}}))
    external = run_async(plugin_instance.dashboard())
    assert external["settings"]["viewer"]["default_appearance"] == "server"
    assert plugin_instance._worker is None
