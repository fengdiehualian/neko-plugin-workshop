import asyncio
import json

import httpx
import pytest
from apibook import ApiPresetsPlugin
from apibook.domain import DEFAULTS, KEEP_SECRET, SLOTS, PresetError, build_payload, normalize_preset, public_preset
from apibook.host import NekoHost, preset_from_host_config, provider_catalog
from apibook.storage import MAX_BYTES, PresetRepository
from plugin.sdk.plugin import Err, Ok


@pytest.fixture
def metadata():
    return {
        "success": True,
        "core_api_providers_full": {"free": {"name": "Free"}, "qwen": {"name": "Qwen"}, "openai": {"name": "OpenAI"}},
        "assist_api_providers_full": {"free": {"name": "Free"}, "qwen": {"name": "Qwen"}, "openai": {"name": "OpenAI"}},
        "api_key_registry": {"qwen": {"config_field": "assistApiKeyQwen"}, "openai": {"config_field": "assistApiKeyOpenai"}},
        "tts_providers": [{"key": "gptsovits"}],
    }


@pytest.fixture
def preset():
    return {"name": "国内账号", "notes": "测试备注", "core": "qwen", "assist": "openai", "keys": {"qwen": "key-qwen", "openai": "key-openai"}, "slots": {}}


def test_secret_roundtrip_copy_independence_conflict_and_delete(tmp_path, preset):
    path = tmp_path / "presets.json"
    repo = PresetRepository(path)
    item = repo.save(preset)
    masked = public_preset(item["preset"])
    assert "key-qwen" not in json.dumps(masked)
    assert masked["keys"]["qwen"] == KEEP_SECRET
    masked["notes"] = "更新"
    changed = repo.save(masked, item["id"], item["revision"])
    assert changed["preset"]["keys"]["qwen"] == "key-qwen"
    with pytest.raises(PresetError, match="conflict"):
        repo.save(masked, item["id"], item["revision"])
    copied = repo.copy(item["id"], "副本")
    copied["preset"]["keys"]["qwen"] = "different-account"
    repo.save(copied["preset"], copied["id"], copied["revision"])
    assert repo.find(repo.read(), item["id"])["preset"]["keys"]["qwen"] == "key-qwen"
    assert "更新" in path.read_text(encoding="utf-8")
    repo.delete(item["id"], changed["revision"])
    assert len(PresetRepository(path).read()["presets"]) == 1


def test_storage_limit_corruption_and_temp_cleanup(tmp_path, preset, monkeypatch):
    repo = PresetRepository(tmp_path / "presets.json")
    repo.save(preset)
    before = repo.path.read_bytes()
    with pytest.raises(PresetError, match="duplicate_name"):
        repo.save(preset)
    import apibook.storage as storage

    monkeypatch.setattr(storage.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("simulated")))
    with pytest.raises(OSError):
        repo.save(preset | {"name": "new"})
    assert repo.path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [repo.path]
    repo.path.write_bytes(b"\xff")
    with pytest.raises(UnicodeError):
        repo.save(preset)
    assert repo.path.read_bytes() == b"\xff"
    repo.path.write_text("x" * (MAX_BYTES + 1), encoding="utf-8")
    with pytest.raises(PresetError, match="storage_limit"):
        repo.read()


def test_capacity_and_clear_key(tmp_path, preset):
    repo = PresetRepository(tmp_path / "presets.json")
    for index in range(100):
        repo.save(preset | {"name": str(index)})
    with pytest.raises(PresetError, match="preset_limit"):
        repo.save(preset)
    assert len(repo.read()["presets"]) == 100
    assert normalize_preset(preset | {"keys": {"qwen": ""}}, preset)["keys"]["qwen"] == ""


@pytest.mark.parametrize("url", ["https://user:pass@example.test/v1", "https://example.test?key=secret", "ftp://example.test", "https://example.test/#x"])
def test_rejects_credential_urls(preset, url):
    preset["slots"] = {"conversation": {"provider": "custom", "url": url, "model": "m", "key": ""}}
    with pytest.raises(PresetError, match="invalid_url"):
        normalize_preset(preset)


def test_complete_payload_and_a_b_a_reset(preset, metadata):
    catalog = provider_catalog(metadata)
    a = normalize_preset(preset | {"slots": {"conversation": {"provider": "custom", "url": "https://a.example.test/v1", "model": "model-a", "key": "key-a"}}})
    b = normalize_preset(preset | {"name": "B", "core": "openai", "assist": "qwen"})
    state = {"characterVoice": "unchanged", "conversationModelUrl": "stale"}
    for chosen in [a, b, a]:
        payload = build_payload(chosen, catalog)
        state.update(payload)
        assert state["coreApiKey"] == chosen["keys"][chosen["core"]]
        assert state["characterVoice"] == "unchanged"
        assert "ttsVoiceId" not in payload and "resolvedProviderUrls" not in payload
        for slot in SLOTS:
            if slot not in chosen["slots"]:
                assert state[slot + "ModelProvider"] == DEFAULTS[slot]
                assert all(state[slot + "Model" + suffix] == "" for suffix in ["Url", "Id", "ApiKey"])
        if chosen is b:
            assert state["enableCustomApi"] is False
        else:
            assert state["conversationModelApiKey"] == "key-a"
    for key in ["qwen", "openai"]:
        assert catalog["key_fields"][key] in state
    assert catalog["slot_providers"]["omni"] == ["follow_core", "custom"]
    assert catalog["slot_providers"]["conversation"] == ["follow_assist", "custom"]
    assert "qwen" not in catalog["slot_providers"]["tts"]


def test_masks_custom_keys_and_rejects_missing_builtin_key(preset, metadata):
    value = {"provider": "custom", "url": "http://127.0.0.1:9000/v1", "model": "local", "key": "slot-secret"}
    a = normalize_preset(preset | {"slots": {"tts": value}})
    masked = public_preset(a)
    assert "slot-secret" not in json.dumps(masked)
    assert normalize_preset(masked, a) == a
    a["keys"]["qwen"] = ""
    with pytest.raises(PresetError, match="required_key"):
        build_payload(a, provider_catalog(metadata))


def test_host_only_public_metadata_and_post_and_client_closed(preset, metadata, monkeypatch):
    calls, closed = [], []
    real_client = httpx.AsyncClient

    def handler(request):
        calls.append((request.method, str(request.url), request.content))
        return httpx.Response(200, json=metadata if request.method == "GET" else {"success": True, "sessions_ended": 2})

    class Client(real_client):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))

        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            closed.append(self.is_closed)

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    async def run():
        host = NekoHost(48911)
        catalog = await host.catalog()
        result = await host.apply(build_payload(preset, catalog))
        assert result["sessions_ended"] == 2

    asyncio.run(run())
    assert [c[:2] for c in calls] == [("GET", "http://127.0.0.1:48911/api/config/api_providers"), ("POST", "http://127.0.0.1:48911/api/config/core_api")]
    assert closed == [True, True]


@pytest.mark.parametrize("result,code", [({"success": False, "error": "配置已保存但重新加载失败: secret"}, "saved_reload_failed"), ({"success": False, "error": "secret"}, "host_rejected"), ([], "host_response_invalid")])
def test_partial_failure_never_claims_success(result, code):
    host = NekoHost(48911)

    async def response(*_):
        return result

    host.request = response
    with pytest.raises(PresetError, match=code):
        asyncio.run(host.apply({}))


def test_operation_cancel_finishes_disk_and_releases_lock(tmp_path):
    import threading

    plugin = object.__new__(ApiPresetsPlugin)
    plugin._lock, plugin._stopping = asyncio.Lock(), False
    plugin._error = lambda code: Err(code)
    started, finish = threading.Event(), threading.Event()

    def write():
        started.set()
        finish.wait(3)
        (tmp_path / "done").write_text("done", encoding="utf-8")

    async def run():
        task = asyncio.create_task(plugin._run(lambda: plugin._disk(write)))
        await asyncio.to_thread(started.wait, 3)
        assert isinstance(await plugin._run(lambda: plugin._disk(write)), Err)
        task.cancel()
        await asyncio.sleep(0)
        assert plugin._lock.locked()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not plugin._lock.locked()
        assert isinstance(await plugin.shutdown(), Ok)
        assert isinstance(await plugin._run(lambda: plugin._disk(write)), Err)

    asyncio.run(run())
    assert (tmp_path / "done").exists()


def test_locale_keys_match_and_every_error_translated():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    messages = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "i18n").glob("*.json"))]
    assert len(messages) == 8
    assert all(set(m) == set(messages[0]) for m in messages)
    assert all(all(isinstance(v, str) and v for v in m.values()) for m in messages)


def test_unknown_catalog_field_cannot_write_arbitrary_settings(metadata):
    metadata["api_key_registry"]["evil"] = {"config_field": "ttsVoiceId"}
    assert "evil" not in provider_catalog(metadata)["key_fields"]
    assert all(provider not in DEFAULTS.values() for provider in provider_catalog(metadata)["core"])


def test_current_api_draft_excludes_credentials_and_builtin_models(metadata):
    raw = {
        "success": True, "coreApi": "qwen", "assistApi": "openai",
        "api_key": "core-secret", "api_key_display": "key***end",
        "assistApiKeyQwen": "assist-secret", "mcpToken": "mcp-secret",
        "conversationModelProvider": "custom", "conversationModelUrl": "https://custom.example.test/v1",
        "conversationModelId": "my-model", "conversationModelApiKey": "slot-secret",
        "summaryModelProvider": "follow_assist", "summaryModelUrl": "https://builtin.example.test",
        "summaryModelId": "builtin-model", "summaryModelApiKey": "builtin-secret",
        "ttsModelProvider": "gptsovits", "ttsModelUrl": "http://127.0.0.1:9880",
        "ttsModelId": "", "ttsModelApiKey": "tts-secret", "ttsVoiceId": "private-voice",
        "emotionModelProvider": "qwen", "emotionModelId": "do-not-freeze",
        "visionModelProvider": "follow_core",
    }
    before = json.dumps(raw, sort_keys=True)
    result = preset_from_host_config(raw, provider_catalog(metadata))
    draft = result["preset"]
    assert draft["name"] == "" and draft["keys"] == {}
    assert draft["core"] == "qwen" and draft["assist"] == "openai"
    assert draft["slots"]["conversation"] == {"provider": "custom", "url": "https://custom.example.test/v1", "model": "my-model", "key": ""}
    assert draft["slots"]["summary"] == {"provider": "follow_assist"}
    assert draft["slots"]["tts"] == {"provider": "gptsovits", "url": "http://127.0.0.1:9880", "model": "", "key": ""}
    assert set(result["skipped_slots"]) == {"emotion", "vision"}
    assert draft["slots"]["image"] == {"provider": "disabled"}
    assert len(draft["slots"]) == len(SLOTS)
    assert all(value not in json.dumps(result) for value in ["secret", "key***end", "private-voice", "builtin-model", "do-not-freeze"])
    assert json.dumps(raw, sort_keys=True) == before


@pytest.mark.parametrize("enabled", [False, True])
def test_import_save_copy_apply_preserves_custom_state(tmp_path, metadata, enabled):
    catalog = provider_catalog(metadata)
    draft = preset_from_host_config({
        "success": True, "coreApi": "free", "assistApi": "free", "enableCustomApi": enabled,
        "conversationModelProvider": "custom", "conversationModelUrl": "https://saved.example.test/v1",
        "conversationModelId": "saved-model", "conversationModelApiKey": "host-secret",
    }, catalog)["preset"]
    assert draft["enableCustomApi"] is enabled
    repo = PresetRepository(tmp_path / "presets.json")
    item = repo.save(draft | {"name": "import"})
    copied = repo.copy(item["id"], "copy")
    for chosen in [item, copied]:
        payload = build_payload(chosen["preset"], catalog)
        assert payload["enableCustomApi"] is enabled
        assert payload["conversationModelId"] == "saved-model"
        assert payload["conversationModelUrl"] == "https://saved.example.test/v1"
        assert payload["conversationModelApiKey"] == ""
    toggled = repo.save(item["preset"] | {"enableCustomApi": not enabled}, item["id"], item["revision"])
    assert toggled["preset"]["slots"] == item["preset"]["slots"]


def test_custom_switch_legacy_defaults_and_strict_bool(preset, metadata):
    assert normalize_preset(preset)["enableCustomApi"] is False
    custom = preset | {"slots": {"conversation": {"provider": "custom", "url": "https://legacy.example.test", "model": "legacy", "key": ""}}}
    assert normalize_preset(custom)["enableCustomApi"] is True
    disabled = normalize_preset(custom | {"enableCustomApi": False})
    assert build_payload(disabled, provider_catalog(metadata))["enableCustomApi"] is False
    assert public_preset(disabled)["enableCustomApi"] is False
    for value in [0, 1, "false", None]:
        with pytest.raises(PresetError, match="invalid_preset"):
            normalize_preset(custom | {"enableCustomApi": value})


@pytest.mark.parametrize("write_fails", [False, True])
def test_repeated_cancel_holds_lock_until_atomic_replace_finishes(tmp_path, monkeypatch, write_fails):
    import threading

    import apibook.storage as storage

    repo = PresetRepository(tmp_path / "presets.json")
    initial = repo.save({"name": "initial"})
    plugin = object.__new__(ApiPresetsPlugin)
    plugin._lock, plugin._stopping = asyncio.Lock(), False
    plugin._error = lambda code: Err(code)
    started, finish = threading.Event(), threading.Event()
    original_replace = storage.os.replace

    def delayed_replace(source, destination):
        started.set()
        assert finish.wait(5)
        if write_fails:
            raise OSError("simulated")
        original_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", delayed_replace)

    async def run():
        older = initial["preset"] | {"name": "older"}
        task = asyncio.create_task(plugin._run(lambda: plugin._disk(repo.save, older, initial["id"], 1)))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            assert plugin._lock.locked() and not task.done()
            assert isinstance(await plugin._run(lambda: plugin._disk(repo.save, {"name": "newer"}, initial["id"], 1)), Err)
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and not plugin._lock.locked()
        monkeypatch.setattr(storage.os, "replace", original_replace)
        current = repo.find(repo.read(), initial["id"])
        assert current["revision"] == (1 if write_fails else 2)
        newer = await plugin._run(lambda: plugin._disk(repo.save, current["preset"] | {"name": "newer"}, current["id"], current["revision"]))
        assert isinstance(newer, Ok)
        assert repo.find(repo.read(), current["id"])["preset"]["name"] == "newer"
        assert list(tmp_path.iterdir()) == [repo.path]
        assert isinstance(await plugin.shutdown(), Ok)

    asyncio.run(run())


def test_current_api_import_is_read_only_and_closes_clients(metadata, monkeypatch):
    calls, closed = [], []
    real_client = httpx.AsyncClient

    def handler(request):
        calls.append((request.method, request.url.path))
        raw = {"success": True, "coreApi": "free", "assistApi": "qwen", "api_key": "__NEKO_SECRET_MASKED__"}
        return httpx.Response(200, json=metadata if request.url.path.endswith("api_providers") else raw)

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))

        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            closed.append(self.is_closed)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = asyncio.run(NekoHost(48911).current_draft())
    assert result["preset"]["keys"] == {} and result["preset"]["name"] == ""
    assert calls == [("GET", "/api/config/api_providers"), ("GET", "/api/config/core_api")]
    assert closed == [True, True]


def test_current_api_draft_rejects_invalid_response(metadata):
    catalog = provider_catalog(metadata)
    for response in [[], {"success": False, "error": "private"}]:
        with pytest.raises(PresetError, match="host_response_invalid"):
            preset_from_host_config(response, catalog)
    with pytest.raises(PresetError, match="unsupported_provider"):
        preset_from_host_config({"success": True, "coreApi": "unknown"}, catalog)


def test_current_api_action_does_not_save_a_preset(tmp_path, monkeypatch, metadata):
    plugin = object.__new__(ApiPresetsPlugin)
    plugin._lock, plugin._stopping = asyncio.Lock(), False
    plugin._repository = PresetRepository(tmp_path / "presets.json")

    async def host():
        class Host:
            async def current_draft(self):
                return preset_from_host_config({"success": True, "coreApi": "free", "assistApi": "free"}, provider_catalog(metadata))

        return Host()

    monkeypatch.setattr(plugin, "_host", host)
    result = asyncio.run(plugin.current_api_draft())
    assert isinstance(result, Ok) and result.value["preset"]["name"] == ""
    assert not plugin._repository.path.exists()
    assert not plugin._lock.locked()


def test_key_book_preserves_unselected_provider_and_uses_registry_label(tmp_path, preset, metadata):
    metadata["api_key_registry"]["elevenlabs"] = {"config_field": "assistApiKeyElevenlabs", "label": "ElevenLabs TTS"}
    catalog = provider_catalog(metadata)
    assert catalog["labels"]["elevenlabs"] == "ElevenLabs TTS"
    metadata["keybook_api_providers_full"] = {"elevenlabs": {"name": "ElevenLabs Voice"}}
    catalog = provider_catalog(metadata)
    assert catalog["labels"]["elevenlabs"] == "ElevenLabs Voice"
    repo = PresetRepository(tmp_path / "presets.json")
    preset["keys"]["elevenlabs"] = "unselected-tts-key"
    saved = repo.save(preset)
    copied = repo.copy(saved["id"], "book-copy")
    assert copied["preset"]["keys"]["elevenlabs"] == "unselected-tts-key"
    assert public_preset(copied["preset"])["keys"]["elevenlabs"] == KEEP_SECRET
    copied["preset"]["keys"]["elevenlabs"] = "independent-key"
    repo.save(copied["preset"], copied["id"], copied["revision"])
    assert repo.find(repo.read(), saved["id"])["preset"]["keys"]["elevenlabs"] == "unselected-tts-key"
    payload = build_payload(saved["preset"], catalog)
    assert payload["assistApiKeyElevenlabs"] == "unselected-tts-key"
    assert payload["coreApi"] == "qwen" and payload["assistApi"] == "openai"
    assert all(payload[slot + "ModelId"] == "" for slot in SLOTS)
