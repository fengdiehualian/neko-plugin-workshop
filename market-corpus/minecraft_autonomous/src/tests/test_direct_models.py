import importlib
from dataclasses import replace
from unittest.mock import AsyncMock

from plugin.sdk.plugin import Err, Ok


def test_direct_settings_save_and_dashboard_do_not_call_models(plugin_instance, plugin_module, monkeypatch, run_async):
    raw = {"runtime": {"mode": "external"}}
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value=raw))
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"planner": {"route": "direct", "provider": "google", "model": "account-model", "stream": True,
                         "parameters": {"thinkingConfig": {"thinkingBudget": 1024}}, "auth": {"mode": "api_key"}}}
    assert isinstance(run_async(plugin_instance.save_settings(patch)), Ok)
    assert update.call_args.args[0] == patch
    assert isinstance(run_async(plugin_instance.save_settings({"planner": {**patch["planner"], "auth": {"access_token": "secret"}}})), Err)
    assert isinstance(run_async(plugin_instance.save_settings({"planner": {**patch["planner"], "parameters": {"messages": []}}})), Err)
    assert update.await_count == 1 and plugin_instance._worker is None
    module = importlib.import_module(plugin_module.__name__+".direct_planner")
    shown = module.public_planner(patch["planner"])
    assert shown["protocol"] == "gemini" and shown["api_key_env"] == "GEMINI_API_KEY"


def test_explicit_reasoning_history_round_trips_without_a_model_or_worker(plugin_instance, plugin_module, monkeypatch, run_async):
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={}))
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"planner": {"route": "direct", "provider": "openai-compatible", "model": "fixture",
                         "base_url": "http://localhost:1/v1", "auth": {"mode": "none"},
                         "reasoning_history": "current_turn"}}
    assert isinstance(run_async(plugin_instance.save_settings(patch)), Ok)
    assert update.call_args.args[0] == patch
    module = importlib.import_module(plugin_module.__name__+".direct_planner")
    assert module.public_planner(patch["planner"])["reasoning_history"] == "current_turn"
    assert isinstance(run_async(plugin_instance.save_settings({"planner": {**patch["planner"], "protocol": "responses"}})), Err)
    assert update.await_count == 1 and plugin_instance._worker is None


def test_direct_route_uses_shared_provider_and_host_stays_default(plugin_module):
    module = importlib.import_module(plugin_module.__name__+".direct_planner")
    settings = plugin_module.Settings()
    assert isinstance(module.create_planner({}, None, settings), module.NekoPlanner)
    raw = {"planner": {"route": "direct", "provider": "anthropic", "model": "test", "auth": {"mode": "none"}}}
    direct = module.create_planner(raw, None, settings)
    assert isinstance(direct, module.HttpPlanner)
    assert direct.config.protocol == "anthropic"
    import asyncio
    asyncio.run(direct.aclose())
    settings = replace(settings, fast=replace(settings.fast, mode="host"))
    wrapper = module.create_planner(raw, None, settings)
    assert isinstance(wrapper, module.DirectWithHostFast)
    asyncio.run(wrapper.aclose())


def test_external_observer_cannot_probe_its_own_direct_config(plugin_instance, run_async, monkeypatch):
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={"runtime": {"mode": "external"}}))
    assert isinstance(run_async(plugin_instance.check_direct_model()), Err)


def test_draft_catalog_needs_no_model_and_does_not_save_or_infer(plugin_instance, run_async, monkeypatch):
    from agent_mc_autonomy import model_admin
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={}))
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    listing = AsyncMock(return_value={"models": [], "inference_called": False})
    monkeypatch.setattr(model_admin, "list_models", listing)
    draft = {"route": "direct", "provider": "google", "model": ""}
    assert isinstance(run_async(plugin_instance.check_direct_model(catalog=True, planner=draft)), Ok)
    assert listing.await_args.args[0].protocol == "gemini"
    assert not update.called and plugin_instance._worker is None
    assert isinstance(run_async(plugin_instance.check_direct_model(catalog=False, planner=draft)), Err)
    assert listing.await_count == 1


def test_thinking_budget_validation_happens_before_saving(plugin_instance, run_async, monkeypatch):
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={}))
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    draft = {"planner": {"route": "direct", "provider": "anthropic", "model": "example",
                         "parameters": {"thinking": {"type": "enabled", "budget_tokens": 1024}}}}
    assert isinstance(run_async(plugin_instance.save_settings(draft)), Err)
    assert not update.called
    assert isinstance(run_async(plugin_instance.save_settings({**draft, "autonomy": {"max_output_tokens": 2048}})), Ok)
