from unittest.mock import AsyncMock

from plugin.sdk.plugin import Err, Ok


def test_quick_start_saves_common_settings_then_uses_normal_start(plugin_instance, run_async, monkeypatch):
    saved = AsyncMock(return_value=Ok({"saved": True}))
    started = AsyncMock(return_value=Ok({"accepted": True}))
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", started)
    result = run_async(plugin_instance.quick_start(connection={"host": "fixture.test", "port": 25565,
        "username": "Alice", "auth": "offline", "version": "1.21.1", "connection_type": "custom"}))
    assert isinstance(result, Ok)
    patch = saved.call_args.args[0]
    assert patch["runtime"]["mode"] == "embedded"
    assert patch["autonomy"]["enabled"] is True
    assert patch["planner"]["route"] == "host"
    assert patch["heartbeat"]["enabled"] is True
    assert "auto_start" not in str(patch)
    assert started.await_count == 1
    assert "Minecraft" in started.call_args.kwargs["goal"]


def test_quick_start_validation_failure_never_starts(plugin_instance, run_async, monkeypatch):
    saved = AsyncMock(return_value=Err({"code": "invalid_settings"}))
    started = AsyncMock()
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", started)
    assert isinstance(run_async(plugin_instance.quick_start(connection={"host": "bad url"})), Err)
    started.assert_not_awaited()


def test_quick_start_rejects_secrets_and_unknown_controls(plugin_instance, run_async, monkeypatch):
    saved = AsyncMock()
    started = AsyncMock()
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", started)
    assert isinstance(run_async(plugin_instance.quick_start(connection={"password": "do-not-store"})), Err)
    saved.assert_not_awaited()
    started.assert_not_awaited()


def test_quick_start_refuses_an_owned_session_before_saving(plugin_instance, run_async, monkeypatch):
    monkeypatch.setattr("agent_mc_autonomy.ownership.session_is_owned", lambda _: True)
    saved = AsyncMock()
    started = AsyncMock()
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", started)
    result = run_async(plugin_instance.quick_start(connection={"host": "fixture.test", "username": "Alice"}))
    assert isinstance(result, Err)
    saved.assert_not_awaited()
    started.assert_not_awaited()


def test_quick_start_never_saves_or_starts_over_unknown_work(plugin_instance, run_async, monkeypatch):
    from agent_mc_autonomy.configuration import Settings

    raw = run_async(plugin_instance.config.dump())
    settings = Settings.parse(raw)
    host = settings.data_root(plugin_instance.data_path()) / "sessions" / settings.identity / "host"
    host.mkdir(parents=True, exist_ok=True)
    def unknown(_):
        raise RuntimeError("unknown_native_dispatch")
    monkeypatch.setattr("agent_mc_autonomy.mailbox.Mailbox.check_uncertain", unknown)
    saved = AsyncMock()
    started = AsyncMock()
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", started)
    result = run_async(plugin_instance.quick_start(connection={"host": "fixture.test", "username": "Alice"}))
    assert isinstance(result, Err)
    saved.assert_not_awaited()
    started.assert_not_awaited()


def test_first_play_defaults_follow_current_cat_and_keep_generated_account(plugin_instance, run_async, monkeypatch):
    import re

    raw = {}
    async def update(patch):
        for section, values in patch.items():
            raw.setdefault(section, {}).update(values)
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(side_effect=lambda: raw))
    monkeypatch.setattr(plugin_instance.config, "update", update)
    started = AsyncMock(return_value=Ok({"accepted": True}))
    monkeypatch.setattr(plugin_instance, "start", started)
    result = run_async(plugin_instance.quick_start(connection={"auth": "offline"}, character=""))
    assert isinstance(result, Ok)
    raw = run_async(plugin_instance.config.dump())
    assert raw["server"]["host"] == "127.0.0.1"
    nickname = raw["server"]["username"]
    assert re.fullmatch(r"Neko_[a-f0-9]{8}", nickname)
    assert raw["autonomy"]["max_decisions"] == 0
    assert raw["speech_output"]["enabled"] is True
    assert raw["speech_output"]["base_url"] == "http://127.0.0.1:48911"
    assert raw["host_context"]["character_api_url"] == "http://127.0.0.1:48911/api/characters"
    assert raw["heartbeat"]["target"] == raw["presentation"]["target"] == ""
    assert isinstance(run_async(plugin_instance.quick_start(connection={"auth": "offline"})), Ok)
    assert run_async(plugin_instance.config.dump())["server"]["username"] == nickname


def test_quick_start_retains_custom_route_endpoint_limits_and_voice_opt_out(plugin_instance, run_async, monkeypatch):
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={"speech_output": {"base_url": "http://localhost:32123", "enabled": False},
        "host_context": {"character_api_url": "http://localhost:32123/api/characters"},
        "autonomy": {"max_decisions": 7}, "planner": {"route": "direct"}}))
    saved = AsyncMock(return_value=Ok({"saved": True}))
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", AsyncMock(return_value=Ok({"accepted": True})))
    result = run_async(plugin_instance.quick_start(connection={"host": "fixture.test", "username": "Alice"},
        use_neko_model=False, narration=False))
    assert isinstance(result, Ok)
    patch = saved.call_args.args[0]
    assert patch["speech_output"] == {"enabled": False}
    assert "planner" not in patch and "max_decisions" not in patch["autonomy"]
    assert "character_api_url" not in patch["host_context"]


def test_quick_start_custom_origin_and_current_role_do_not_disable_speech(plugin_instance, run_async, monkeypatch):
    saved = AsyncMock(return_value=Ok({"saved": True}))
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", AsyncMock(return_value=Ok({"accepted": True})))
    result = run_async(plugin_instance.quick_start(connection={"host": "fixture.test", "username": "Alice"},
        character="", neko_url="http://localhost:32123"))
    assert isinstance(result, Ok)
    patch = saved.call_args.args[0]
    assert patch["speech_output"]["enabled"] is True
    assert patch["speech_output"]["base_url"] == "http://localhost:32123"
    assert patch["host_context"]["character_api_url"] == "http://localhost:32123/api/characters"


def test_quick_start_never_invents_microsoft_identity_or_uses_remote_tts(plugin_instance, run_async, monkeypatch):
    saved, started = AsyncMock(), AsyncMock()
    monkeypatch.setattr(plugin_instance, "save_settings", saved)
    monkeypatch.setattr(plugin_instance, "start", started)
    for args in ({"connection": {"auth": "microsoft"}},
                 {"connection": {"host": "fixture.test", "username": "Alice"}, "neko_url": "https://foreign.test"}):
        assert isinstance(run_async(plugin_instance.quick_start(**args)), Err)
    saved.assert_not_awaited()
    started.assert_not_awaited()
