from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from PIL import Image
from plugin.plugins.selfie import SelfiePlugin, _chat, _media, _volcengine
from plugin.plugins.selfie._config import SelfieError, Settings
from plugin.sdk.shared.i18n import PluginI18n

ROOT = Path(__file__).resolve().parents[1]


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (16, 12), "blue").save(output, "PNG")
    return output.getvalue()


def configuration(**updates):
    result = {"provider": "volcengine", "base_url": "https://api.example.test/api/v3",
              "api_key": "secret-for-tests", "model": "test-i2i", "reference_image": "reference.png"}
    result.update(updates)
    return result


@pytest.fixture
def plugin(tmp_path):
    (tmp_path / "reference.png").write_bytes(image_bytes())
    instance = object.__new__(SelfiePlugin)
    instance.ctx = SimpleNamespace(plugin_id="selfie", config_path=tmp_path / "plugin.toml",
                                   images=SimpleNamespace(upload=AsyncMock(return_value={
                                       "type": "image", "url": "http://127.0.0.1:48916/media/test-image"})))
    instance.config = SimpleNamespace(dump=AsyncMock(return_value={"selfie": configuration()}))
    instance.i18n = PluginI18n({p.stem: json.loads(p.read_text(encoding="utf-8"))
                              for p in (ROOT / "i18n").glob("*.json")})
    instance._busy = False
    instance._last_started = float("-inf")
    instance._locale = "en"
    instance.push_message = Mock(return_value={"submitted": True})
    return instance


@pytest.mark.parametrize("provider,base,expected", [
    ("volcengine", "https://api.test/api/v3", "https://api.test/api/v3/images/generations"),
    ("volcengine", "https://api.test/api/v3/images/generations/", "https://api.test/api/v3/images/generations"),
    ("chat", "https://api.test/v1", "https://api.test/v1/chat/completions"),
    ("chat", "https://api.test/v1/chat/completions", "https://api.test/v1/chat/completions"),
    ("chat", "https://api.test/v1/chat", "https://api.test/v1/chat"),
])
def test_endpoint_normalization(provider, base, expected):
    settings = Settings.from_config(configuration(provider=provider, base_url=base))
    assert settings.endpoint == expected
    assert "secret-for-tests" not in repr(settings)


@pytest.mark.parametrize("update", [
    {"api_key": ""}, {"reference_image": ""}, {"model": ""}, {"provider": "other"},
    {"base_url": "http://api.test/v1"}, {"base_url": "https://user:secret@api.test/v1"},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": 241}, {"cooldown_seconds": -1},
])
def test_invalid_settings_rejected(update):
    with pytest.raises(SelfieError, match="config"):
        Settings.from_config(configuration(**update))


@pytest.mark.parametrize("message", [
    {"images": [{"type": "image_url", "image_url": {"url": "https://cdn.test/result"}}]},
    {"content": [{"type": "image_url", "image_url": {"url": "https://cdn.test/result"}}]},
    {"content": "![selfie](https://cdn.test/result)"},
    {"content": "![selfie](<https://cdn.test/result>)"},
    {"content": [{"type": "text", "text": "![selfie](https://cdn.test/result)"}]},
])
def test_extract_structured_and_markdown_images(message):
    assert _media.extract_chat_image({"choices": [{"message": message}]}) == "https://cdn.test/result"


def test_extract_data_uri_and_plain_image_url():
    uri = "data:image/png;base64," + base64.b64encode(image_bytes()).decode()
    assert _media.extract_chat_image({"choices": [{"message": {"content": uri}}]}) == uri
    assert _media.extract_chat_image({"choices": [{"message": {
        "content": "Here is your picture: https://cdn.test/a.png?signature=test"}}]}) == (
            "https://cdn.test/a.png?signature=test")


@pytest.mark.parametrize("result", [{}, {"choices": []}, {"choices": [{"message": {"content": "No image"}}]}])
def test_missing_image_is_an_error(result):
    with pytest.raises(SelfieError):
        _media.extract_chat_image(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [_volcengine, _chat])
async def test_provider_payload_contains_reference_and_decodes_result(backend):
    settings = Settings.from_config(configuration(provider="chat" if backend is _chat else "volcengine"))
    reference = _media.normalize_reference(image_bytes())
    def respond(request):
        payload = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer secret-for-tests"
        assert payload["model"] == "test-i2i"
        assert payload["stream"] is False
        if backend is _volcengine:
            assert payload["image"] == reference
            assert payload["watermark"] is True
            result = {"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]}
        else:
            assert payload["messages"][0]["content"][1]["image_url"]["url"] == reference
            result = {"choices": [{"message": {"images": [{"image_url": {
                "url": "data:image/png;base64," + base64.b64encode(image_bytes()).decode()}}]}}]}
        return httpx.Response(200, json=result)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await backend.generate(client, settings, "selfie in a garden", reference) == image_bytes()


@pytest.mark.asyncio
async def test_remote_download_has_no_provider_authorization(monkeypatch):
    monkeypatch.setattr(_media, "validate_download_url", AsyncMock())
    requests = []
    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        if request.url.path == "/first":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, content=image_bytes())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await _media.download_image(client, "https://cdn.test/first") == image_bytes()
    assert len(requests) == 2
    assert _media.validate_download_url.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1/a", "https://user:secret@cdn.test/a", "file:///a"])
async def test_reject_untrusted_image_urls(url):
    with pytest.raises(SelfieError, match="image"):
        await _media.validate_download_url(url)


@pytest.mark.asyncio
async def test_reject_private_dns(monkeypatch):
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[
        (2, 1, 6, "", ("127.0.0.1", 443))]))
    with pytest.raises(SelfieError, match="image"):
        await _media.validate_download_url("https://private.test/a")


@pytest.mark.asyncio
async def test_bounded_response_and_compressed_response_rejected(monkeypatch):
    response = httpx.Response(200, content=b"oversize")
    with pytest.raises(SelfieError, match="too_large"):
        await _media.read_response(response, 2)
    response = httpx.Response(200, headers={"content-encoding": "br"})
    with pytest.raises(SelfieError, match="response"):
        await _media.read_response(response, 100)


@pytest.mark.asyncio
async def test_relative_reference_is_decoded_without_blocking_io(tmp_path):
    (tmp_path / "reference.png").write_bytes(image_bytes())
    async with httpx.AsyncClient() as client:
        uri = await _media.load_reference(client, "reference.png", tmp_path)
    decoded = _media.decode_base64(uri.split(",", 1)[1])
    with Image.open(BytesIO(decoded)) as image:
        assert image.format == "JPEG"
        assert image.size == (16, 12)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["volcengine", "chat"])
async def test_generation_upload_and_blind_chat_submission(plugin, monkeypatch, provider):
    plugin.config.dump.return_value["selfie"]["provider"] = provider
    generate = AsyncMock(return_value=image_bytes())
    monkeypatch.setattr(_volcengine if provider == "volcengine" else _chat, "generate", generate)
    result = await plugin.send_selfie_tool(scene="in a garden")
    assert result["submitted"] is True
    assert "acknowledged" in result["summary"]
    assert generate.await_args.args[3].startswith("data:image/jpeg;base64,")
    assert "Preserve the character's identity" in generate.await_args.args[2]
    plugin.ctx.images.upload.assert_awaited_once_with(image_bytes(), timeout=10.0)
    payload = plugin.push_message.call_args.kwargs
    assert payload["visibility"] == ["chat"]
    assert payload["ai_behavior"] == "blind"
    assert payload["parts"][0]["type"] == "image"
    assert payload["source"] == "selfie"
    assert plugin._busy is False
    second = await plugin.send_selfie_tool(scene="another scene")
    assert second["error"] == "SELFIE_COOLDOWN"
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_submission_rejection_is_not_success(plugin, monkeypatch):
    monkeypatch.setattr(_volcengine, "generate", AsyncMock(return_value=image_bytes()))
    plugin.push_message.return_value = {"submitted": False, "reason": "backpressure"}
    result = await plugin.send_selfie_tool(scene="in a garden")
    assert result["is_error"] is True
    assert result["error"] == "SELFIE_DELIVERY"
    assert "submitted" not in result


@pytest.mark.asyncio
async def test_overlap_does_not_start_two_paid_requests(plugin, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def generate(*_):
        started.set()
        await release.wait()
        return image_bytes()
    monkeypatch.setattr(_volcengine, "generate", generate)
    first = asyncio.create_task(plugin.send_selfie_tool(scene="one"))
    await asyncio.wait_for(started.wait(), 2)
    try:
        second = await plugin.send_selfie_tool(scene="two")
        assert second["error"] == "SELFIE_BUSY"
    finally:
        release.set()
        await first


@pytest.mark.asyncio
async def test_cancellation_clears_busy_flag(plugin, monkeypatch):
    started = asyncio.Event()
    async def generate(*_):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(_volcengine, "generate", generate)
    task = asyncio.create_task(plugin.send_selfie_tool(scene="one"))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin._busy is False
    plugin.push_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [
    (httpx.ReadTimeout("secret prompt"), "SELFIE_TIMEOUT"),
    (httpx.ConnectError("secret key"), "SELFIE_HTTP"),
    (ValueError("secret provider body"), "SELFIE_INTERNAL"),
    (SelfieError("no_image"), "SELFIE_NO_IMAGE"),
])
async def test_failures_are_sanitized_and_do_not_send(plugin, monkeypatch, failure, code):
    monkeypatch.setattr(_volcengine, "generate", AsyncMock(side_effect=failure))
    result = await plugin.send_selfie_tool(scene="private prompt")
    assert result["error"] == code
    assert "secret" not in json.dumps(result)
    assert "private prompt" not in json.dumps(result)
    plugin.push_message.assert_not_called()
    assert plugin._busy is False


@pytest.mark.asyncio
async def test_tool_registration_localizes_and_scopes_to_character(plugin):
    plugin.config.dump.return_value["selfie"].update(locale="zh-CN", target_lanlan="灵")
    plugin.register_llm_tool = Mock()
    plugin.unregister_llm_tool = Mock()
    await plugin.startup()
    registration = plugin.register_llm_tool.call_args.kwargs
    assert registration["name"] == "send_selfie"
    assert registration["role"] == "灵"
    assert "自拍" in registration["description"]
    assert "主动" in registration["description"]
    assert isinstance(registration["parameters"]["properties"]["scene"]["description"], str)
    await plugin.shutdown()
    plugin.unregister_llm_tool.assert_called_with("send_selfie")


def test_all_locales_and_schema_translations_have_matching_keys():
    bundles = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "i18n").glob("*.json")}
    assert set(bundles) == {"en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt"}
    assert all(set(bundle) == set(bundles["en"]) for bundle in bundles.values())
    schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["selfie"]["properties"]["api_key"]["writeOnly"] is True
    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.endswith("-i18n"):
                    assert set(nested) == set(bundles)
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
    walk(schema)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [
    (ValueError("invalid image"), "SELFIE_IMAGE"),
    (RuntimeError("host unavailable"), "SELFIE_UPLOAD"),
    (TimeoutError("upload timeout"), "SELFIE_TIMEOUT"),
])
async def test_upload_errors_do_not_submit(plugin, monkeypatch, failure, code):
    monkeypatch.setattr(_volcengine, "generate", AsyncMock(return_value=image_bytes()))
    plugin.ctx.images.upload.side_effect = failure
    result = await plugin.send_selfie_tool(scene="one")
    assert result["error"] == code
    plugin.push_message.assert_not_called()


@pytest.mark.asyncio
async def test_unreadable_reference_does_not_start_generation(plugin, monkeypatch):
    plugin.config.dump.return_value["selfie"]["reference_image"] = "missing.png"
    generate = AsyncMock()
    monkeypatch.setattr(_volcengine, "generate", generate)
    result = await plugin.send_selfie_tool(scene="one")
    assert result["error"] == "SELFIE_REFERENCE"
    generate.assert_not_awaited()
    plugin.push_message.assert_not_called()

@pytest.mark.asyncio
async def test_only_dialog_tool_can_generate_selfie(plugin, monkeypatch):
    # Use the actual SDK registration and entry discovery, not mocked registry methods.
    plugin._routers = []
    plugin._dynamic_entries = {}
    plugin._llm_tools = {}
    plugin._notify_dynamic_entry_registered = Mock()
    plugin._notify_dynamic_entry_unregistered = Mock()
    plugin._notify_llm_tool_registered = Mock()
    plugin._notify_llm_tool_unregistered = Mock()
    generate = AsyncMock(return_value=image_bytes())
    monkeypatch.setattr(_volcengine, "generate", generate)

    await plugin.startup()
    await plugin.config_change()
    entries = plugin.collect_entries()
    action_entries = {key: handler for key, handler in entries.items()
                      if handler.meta.event_type == "plugin_entry"}
    # The host hides this reserved prefix from its independent plugin Agent.
    assert set(action_entries) == {"__llm_tool__send_selfie"}
    assert [tool["name"] for tool in plugin.list_llm_tools()] == ["send_selfie"]

    result = await action_entries["__llm_tool__send_selfie"].handler(scene="in a garden")
    assert result["submitted"] is True
    generate.assert_awaited_once()
    plugin.push_message.assert_called_once()

    await plugin.shutdown()
    assert plugin.list_llm_tools() == []
    assert not any(handler.meta.event_type == "plugin_entry"
                   for handler in plugin.collect_entries().values())
