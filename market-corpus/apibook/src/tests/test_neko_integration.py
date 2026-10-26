"""Exercise the real host save route and runtime resolver against disposable data."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from apibook import ApiPresetsPlugin
from apibook.domain import build_payload, normalize_preset
from apibook.host import provider_catalog
from plugin.sdk.plugin import Err, Ok


@pytest.fixture
def real_catalog():
    from utils.api_config_loader import get_config

    raw = get_config()
    return provider_catalog({"success": True, "core_api_providers_full": raw["core_api_providers"], "assist_api_providers_full": raw["assist_api_providers"], "api_key_registry": raw["api_key_registry"], "tts_providers": [{"key": "gptsovits"}]})


def test_real_save_route_and_resolver_a_b_a(tmp_path, monkeypatch, real_catalog):
    import httpx
    import utils.config_manager as managers
    from apibook.connectivity import probe_plan
    from main_routers.config_router import core_config as route
    from utils.api_config_loader import get_config
    from utils.config_manager.core_config import CoreConfigMixin

    class Manager(CoreConfigMixin):
        def get_config_path(self, _):
            return tmp_path / "core_config.json"

        def load_json_config(self, name, default):
            path = self.get_config_path(name)
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

        def save_json_config(self, name, data):
            self.load_json_config(name, {})  # Validate current UTF-8 before replacement.
            self.get_config_path(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        def _migrated_openclaw_url(self, value):
            return value

        def _config_needs_region(self, _):
            return False  # No geoip/background thread in this isolated test.

    manager = Manager()
    snapshots = []

    async def reload():
        snapshots.append(manager.get_core_config())

    async def noop(*_, **__):
        return {}

    class NoNetwork:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, *_):
            return None

    monkeypatch.setattr(managers, "get_config_manager", lambda: manager)
    monkeypatch.setattr(route, "get_session_manager", lambda: {})
    monkeypatch.setattr(route, "get_initialize_character_data", lambda: reload)
    monkeypatch.setattr(route, "ensure_default_yui_voice_for_free_api", noop)
    monkeypatch.setattr(route, "_auto_resolve_provider_urls_for_save", noop)
    monkeypatch.setattr(httpx, "AsyncClient", NoNetwork)
    base = {"name": "A", "notes": "", "core": "qwen", "assist": "openai", "keys": {"qwen": "qwen-account-a", "openai": "openai-account-a"}, "slots": {}}
    a = normalize_preset(base | {"vendors": {"vendor_1": {"name": "Local vendor", "url": "https://a.example.test/v1", "key": "conversation-account-a"}}, "slots": {"conversation": {"provider": "custom", "vendor_id": "vendor_1", "model": "model-a"}, "tts": {"provider": "gptsovits", "url": "http://127.0.0.1:9880", "model": "", "key": ""}}})
    b = normalize_preset(base | {"name": "B", "core": "openai", "assist": "qwen", "keys": {"qwen": "qwen-account-b", "openai": "openai-account-b"}})
    off = normalize_preset(a | {"name": "A disabled", "enableCustomApi": False})
    manager.save_json_config("core_config.json", {"coreApi": "qwen", "assistApi": "openai", "ttsVoiceId": "user-voice", "disableTts": False, "openclawUrl": "http://127.0.0.1:1234"})

    async def save(payload):
        async def request_json():
            return payload

        response = await route.update_core_config(SimpleNamespace(json=request_json))
        assert response.get("success"), response

    async def run():
        await save(build_payload(a, real_catalog))
        assert snapshots[-1]["CONVERSATION_MODEL"] == "model-a"
        # The host's regular follow_core resolver keeps the assist model ID if
        # no ID is materialized. Do not expose this incompatible route.
        from utils.config_manager import get_assist_api_profiles

        await save(build_payload(a, real_catalog) | {
            "conversationModelProvider": "follow_core", "conversationModelId": "",
            "conversationModelUrl": "", "conversationModelApiKey": "",
        })
        followed = manager.get_model_api_config("conversation", _core_config=snapshots[-1])
        profiles = get_assist_api_profiles()
        assert followed["model"] == profiles["openai"]["CONVERSATION_MODEL"]
        assert followed["model"] != profiles["qwen"]["CONVERSATION_MODEL"]
        assert followed["base_url"] == profiles["qwen"]["OPENROUTER_URL"]
        await save(build_payload(a, real_catalog))
        # Reproduce why an incremental key-only switch leaves previous overrides.
        await save({"coreApi": "openai", "assistApi": "qwen", "coreApiKey": "openai-account-b", "assistApiKeyQwen": "qwen-account-b", "enableCustomApi": True})
        assert snapshots[-1]["CONVERSATION_MODEL"] == "model-a"
        for preset in [b, a, off, a, b]:
            await save(build_payload(preset, real_catalog))
            runtime = snapshots[-1]
            metadata = get_config()
            targets = probe_plan(preset, {"success": True, "core_api_providers_full": metadata["core_api_providers"],
                                         "assist_api_providers_full": metadata["assist_api_providers"],
                                         "api_key_registry": metadata["api_key_registry"]})
            for slot, model_type in [("conversation", "conversation"), ("summary", "summary"), ("correction", "correction"),
                                     ("emotion", "emotion"), ("vision", "vision"), ("agent", "agent"),
                                     ("gameMain", "game_main"), ("gameSummary", "game_summary"), ("omni", "realtime")]:
                target = next(g for g in targets if slot in g["functions"])
                resolved = manager.get_model_api_config(model_type, _core_config=runtime)
                assert target["model"] == resolved["model"], slot
                assert target["key"] == resolved["api_key"], slot
            assert runtime["CORE_API_KEY"] == preset["keys"][preset["core"]]
            if preset is b:
                from utils.config_manager import get_assist_api_profiles

                assert runtime["CONVERSATION_MODEL"] == get_assist_api_profiles()["qwen"]["CONVERSATION_MODEL"]
                # With custom API disabled, standard requests use the assist
                # key, not the inactive custom-slot key in the raw snapshot.
                assert runtime["OPENROUTER_API_KEY"] == "qwen-account-b"
                assert runtime["GPTSOVITS_ENABLED"] is False
                assert "a.example.test" not in runtime["CONVERSATION_MODEL_URL"]
            elif preset is off:
                assert runtime["ENABLE_CUSTOM_API"] is False
                assert runtime["CONVERSATION_MODEL"] == profiles["openai"]["CONVERSATION_MODEL"]
                assert runtime["GPTSOVITS_ENABLED"] is True  # Native independent switch.
            else:
                assert runtime["CONVERSATION_MODEL_API_KEY"] == "conversation-account-a"
                assert runtime["GPTSOVITS_ENABLED"] is True
            saved = manager.load_json_config("core_config.json", {})
            assert saved["enableCustomApi"] is preset["enableCustomApi"]
            if preset is off:
                assert saved["conversationModelId"] == "model-a"
                assert saved["conversationModelUrl"] == "https://a.example.test/v1"
                assert saved["conversationModelApiKey"] == "conversation-account-a"
            assert saved["ttsVoiceId"] == "user-voice"
            assert saved["disableTts"] is False
            assert "coreModel" not in saved and "notes" not in saved
            assert "vendors" not in saved and "vendor_id" not in json.dumps(saved)

    asyncio.run(run())


def test_sdk_instance_actions_save_only_and_failed_apply_status(tmp_path, monkeypatch, real_catalog):
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "sdk-data"))

    class Context:
        plugin_id = "apibook"
        logger = None
        config_path = Path(__file__).resolve().parents[1] / "plugin.toml"
        metadata = {}
        bus = None
        _effective_config = {}

        async def get_own_config(self, timeout=5):
            return {"config": {"apibook": {"main_server_port": 48911}}}

    plugin = ApiPresetsPlugin(Context())
    calls = []

    class Host:
        async def catalog(self):
            return real_catalog

        async def apply(self, payload):
            calls.append(payload)
            raise TimeoutError

    async def host():
        return Host()

    plugin._host = host

    async def run():
        assert isinstance(await plugin.startup(), Ok)
        saved = await plugin.save_preset({"name": "local", "keys": {"qwen": "own-secret"}})
        assert isinstance(saved, Ok)
        assert calls == []
        assert "own-secret" not in json.dumps(saved.value)
        item = saved.value
        assert isinstance(await plugin.apply_preset(item["id"], item["revision"]), Err)
        assert calls == []
        failed = await plugin.apply_preset(item["id"], item["revision"], confirmed=True)
        assert isinstance(failed, Err) and failed.error.code == "host_timeout"
        context = await plugin.context()
        assert "own-secret" not in json.dumps(context)
        assert context["last_application"]["status"] == "unknown"
        assert len(calls) == 1
        assert plugin._repository.path.is_relative_to(tmp_path / "sdk-data")
        assert isinstance(await plugin.shutdown(), Ok)

    asyncio.run(run())
