import asyncio
import json

import httpx
import pytest
from apibook import ApiPresetsPlugin
from apibook.connectivity import probe_plan, public_groups, safe_http_status
from apibook.connectivity import test_preset as run_probes
from apibook.domain import PresetError, normalize_preset
from apibook.storage import PresetRepository
from plugin.sdk.plugin import Err, Ok


@pytest.fixture
def probe_metadata():
    models = dict.fromkeys(("conversation_model", "summary_model", "correction_model", "vision_model", "agent_model"), "shared")
    return {
        "success": True,
        "core_api_providers_full": {"qwen": {"core_url": "wss://voice.example.test", "core_model": "voice"}},
        "assist_api_providers_full": {"qwen": {"openrouter_url": "https://text.example.test/v1", "emotion_model": "small", **models}},
        "api_key_registry": {"qwen": {"config_field": "assistApiKeyQwen"}},
    }


@pytest.fixture
def probe_preset():
    return normalize_preset({"name": "test", "core": "qwen", "assist": "qwen", "enableCustomApi": True, "keys": {"qwen": "private-key"}})


def test_disabled_overrides_probe_active_builtin_models_only(probe_metadata, probe_preset):
    custom = {"provider": "custom", "url": "https://inactive.example.test", "model": "inactive", "key": "unused"}
    probe_preset["slots"] = {"conversation": custom, "omni": custom | {"url": "wss://inactive.example.test"}, "tts": custom, "image": custom}
    probe_preset["enableCustomApi"] = False
    groups = probe_plan(probe_preset, probe_metadata)
    assert not any("inactive" in json.dumps(g) or "unused" in json.dumps(g) for g in groups)
    assert next(g for g in groups if "conversation" in g["functions"])["model"] == "shared"
    assert next(g for g in groups if "omni" in g["functions"])["model"] == "voice"
    assert next(g for g in groups if "image" in g["functions"])["status"] == "not_configured"
    assert probe_preset["slots"]["conversation"] == custom


def test_same_url_model_merges_functions_and_different_model_key_protocol_do_not(probe_metadata, probe_preset):
    custom = {"provider": "custom", "url": "https://text.example.test/v1", "model": "shared", "key": "private-key"}
    probe_preset["slots"] = {"conversation": custom, "summary": custom.copy(),
                             "agent": custom | {"key": "other-account"}, "tts": custom.copy()}
    groups = probe_plan(probe_preset, probe_metadata)
    shared = next(g for g in groups if "conversation" in g["functions"])
    assert set(shared["functions"]) == {"assist", "conversation", "summary", "gameMain", "gameSummary", "correction", "vision"}
    assert "emotion" not in shared["functions"] and "agent" not in shared["functions"] and "tts" not in shared["functions"]
    assert next(g for g in groups if "emotion" in g["functions"])["model"] == "small"
    assert next(g for g in groups if "core" in g["functions"])["functions"] == ["core", "omni"]
    assert sum(len(g["functions"]) for g in groups) == 13 and len(groups) <= 13
    result = json.dumps(public_groups(groups))
    assert "private-key" not in result and "other-account" not in result and "example.test" not in result


def test_sdk_core_fallback_deduplicates_with_matching_assist(probe_metadata, probe_preset):
    probe_metadata["core_api_providers_full"]["qwen"]["core_url"] = ""
    groups = probe_plan(probe_preset, probe_metadata)
    shared = next(g for g in groups if "core" in g["functions"])
    assert {"core", "omni", "assist", "conversation"}.issubset(shared["functions"])
    assert shared["note"] == "credential_only" and shared["model"] == "shared"


def test_missing_keys_and_unsupported_speech_image_never_send_chat_probes(probe_metadata, probe_preset):
    probe_preset["keys"] = {}
    probe_preset["slots"]["image"] = {"provider": "custom", "url": "https://image.example.test", "model": "image", "key": ""}
    groups = probe_plan(probe_preset, probe_metadata)
    assert all(g["status"] == "not_configured" for g in groups if any(f in g["functions"] for f in ["core", "assist", "conversation"]))
    assert next(g for g in groups if "tts" in g["functions"])["status"] == "unsupported"
    assert next(g for g in groups if "image" in g["functions"])["status"] == "unsupported"


def test_model_specific_probe_results_no_raw_errors_and_no_host_writes(probe_metadata, probe_preset):
    calls = []

    class Host:
        async def request(self, method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                return probe_metadata
            assert path == "/api/config/test_connectivity"
            if payload.get("model") == "small":
                return {"success": False, "error_code": "auth_failed", "error": "private-key upstream text"}
            return {"success": True}

    result = asyncio.run(run_probes(Host(), probe_preset))
    assert result["tested"] == 3
    assert len([c for c in calls if c[0] == "POST"]) == 3
    assert {c[2].get("model") for c in calls if c[0] == "POST"} == {None, "shared", "small"}
    assert next(g for g in result["groups"] if "emotion" in g["functions"])["status"] == "failed"
    assert "private-key" not in json.dumps(result)
    assert all(c[1] != "/api/config/core_api" for c in calls)
    # Every save/test starts a fresh round, rather than trusting a stale result.
    asyncio.run(run_probes(Host(), probe_preset))
    assert len([c for c in calls if c[0] == "POST"]) == 6


def test_probe_concurrency_bounded_and_cancel_releases_all_requests(probe_metadata, probe_preset):
    for index, slot in enumerate(["conversation", "summary", "correction", "emotion", "vision", "agent", "omni", "tts"]):
        probe_preset["slots"][slot] = {"provider": "custom", "url": ("wss" if slot == "omni" else "https") + "://custom.example.test", "model": f"model-{index}", "key": ""}
    active = maximum = released = 0

    class Host:
        async def request(self, method, path, payload=None):
            nonlocal active, maximum, released
            if method == "GET":
                return probe_metadata
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
                released += 1

    async def run():
        task = asyncio.create_task(run_probes(Host(), probe_preset))
        while active < 4:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0 and maximum == 4 and released == 4

    asyncio.run(run())


def test_free_400_matches_native_policy_and_custom_models_remain_strict(probe_metadata, probe_preset):
    from main_routers.config_router.connectivity import _classify_openai_error
    from openai import BadRequestError

    probe_metadata["core_api_providers_full"]["free"] = {"core_url": "wss://free.example.test", "core_model": "free-model", "is_free_version": True}
    probe_metadata["assist_api_providers_full"]["free"] = {"openrouter_url": "https://free.example.test/v1", "conversation_model": "free-model", "is_free_version": True}
    probe_preset.update(core="free", assist="free", keys={})
    probe_preset["slots"]["emotion"] = {"provider": "custom", "url": "https://free.example.test/v1", "model": "invalid-model", "key": "free-access"}
    error = BadRequestError("private upstream message", response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")), body={})
    calls = []

    class Host:
        async def request(self, method, path, payload=None):
            if method == "GET":
                return probe_metadata
            calls.append(payload)
            if payload.get("provider_scope") == "core":
                return {"success": True}
            return _classify_openai_error(error, is_free=payload.get("is_free", False))

    result = asyncio.run(run_probes(Host(), probe_preset))
    free_group = next(g for g in result["groups"] if "assist" in g["functions"])
    custom_group = next(g for g in result["groups"] if "emotion" in g["functions"])
    assert free_group["status"] == "connected"
    assert free_group["free_policy"] is True
    assert custom_group["status"] == "failed" and custom_group["http_status"] == 400
    assert not custom_group["free_policy"]
    assert len(calls) == 3  # One core, one shared free config, one distinct custom model.
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(('raw', 'expected'), [
    ({'http_status': 401}, 401), ({'status_code': 503}, 503),
    ({'error': 'HTTP 400 private-key https://private.test'}, 400),
    ({'error': 'HTTP 404 private-key'}, 404),
    ({'error': 'HTTP 500 private-key'}, 500),
    ({'http_status': True}, None), ({'http_status': 999}, None),
    ({'error': 'private-key HTTP 400'}, None),
])
def test_status_allowlist_never_retains_raw_errors(raw, expected):
    assert safe_http_status(raw) == expected


def test_backend_http_error_retains_only_status_and_connection_code(probe_metadata, probe_preset):
    class Host:
        async def request(self, method, path, payload=None):
            if method == 'GET':
                return probe_metadata
            if payload.get('model') == 'small':
                return {'success': False, 'error_code': 'ws_error', 'error': 'HTTP 404 private-key'}
            error = PresetError('host_http_error')
            error.http_status = 503
            raise error

    result = asyncio.run(run_probes(Host(), probe_preset))
    emotion = next(g for g in result['groups'] if 'emotion' in g['functions'])
    core = next(g for g in result['groups'] if 'core' in g['functions'])
    assert emotion['error_code'] == 'ws_error' and emotion['http_status'] == 404
    assert core['error_code'] == 'backend_unavailable' and core['http_status'] == 503
    assert 'private-key' not in json.dumps(result)


def test_saved_revision_probe_snapshot_conflict_and_shutdown(tmp_path, probe_metadata, probe_preset):
    plugin = object.__new__(ApiPresetsPlugin)
    plugin._lock, plugin._stopping = asyncio.Lock(), False
    plugin._repository = PresetRepository(tmp_path / "presets.json")
    plugin._last_test = None
    plugin._error = lambda code: Err(code)

    class Host:
        async def request(self, method, path, payload=None):
            return probe_metadata if method == "GET" else {"success": False, "error_code": "timeout"}

    async def host():
        return Host()

    plugin._host = host

    async def run():
        saved = await plugin.save_preset(probe_preset)
        assert isinstance(saved, Ok)
        item = saved.value
        result = await plugin.test_preset(item["id"], item["revision"])
        assert isinstance(result, Ok) and result.value["tested"] == 3
        assert "private-key" not in json.dumps(plugin._last_test)
        assert len(plugin._last_test["groups"]) <= 13
        conflict = await plugin.test_preset(item["id"], item["revision"] + 1)
        assert isinstance(conflict, Err) and conflict.error == "conflict"
        changed = await plugin.save_preset(item["preset"] | {"notes": "changed"}, item["id"], item["revision"])
        assert isinstance(changed, Ok) and plugin._last_test is None
        assert plugin._repository.read()["presets"][0]["preset"]["keys"]["qwen"] == "private-key"
        await plugin.test_preset(item["id"], changed.value["revision"])
        await plugin.shutdown()
        assert plugin._last_test is None and not plugin._lock.locked()

    asyncio.run(run())
