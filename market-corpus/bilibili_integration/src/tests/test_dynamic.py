from __future__ import annotations

import asyncio
import base64
import json
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from bilibili_api import Credential
from bilibili_api.exceptions import ResponseCodeException
from PIL import Image
from plugin.plugins.bilibili_integration import BiliDMPlugin, dynamic_publisher
from plugin.plugins.bilibili_integration.bili_client import BiliDMClient
from plugin.sdk.plugin import Err, Ok

HTTP_CLIENT = httpx.AsyncClient


@pytest.fixture
def sdk_send(monkeypatch):
    send = AsyncMock(return_value={"dyn_id_str": "1234567890123456789"})
    monkeypatch.setattr(dynamic_publisher.dynamic, "send_dynamic", send)
    return send


@pytest.fixture
def credential(monkeypatch):
    account = Credential(sessdata="test-session", bili_jct="test-csrf")
    monkeypatch.setattr(account, "get_cookies", AsyncMock(return_value={"SESSDATA": "test-session", "bili_jct": "test-csrf"}))
    return account


@pytest.fixture
def png_bytes():
    output = BytesIO()
    Image.new("RGB", (3, 2), "red").save(output, format="PNG")
    return output.getvalue()


def data_url(content):
    return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


def mock_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        dynamic_publisher.httpx, "AsyncClient",
        lambda **kwargs: HTTP_CLIENT(transport=transport, **kwargs),
    )


def make_plugin(client):
    plugin = object.__new__(BiliDMPlugin)
    plugin.bili_client = client
    plugin._lifecycle_lock = asyncio.Lock()
    plugin.logger = MagicMock()
    return plugin


@pytest.mark.asyncio
async def test_publish_plain_text_preserves_literal_mentions(credential, sdk_send):
    result = await dynamic_publisher.publish_dynamic(credential, " hello @someone [smile] ")
    assert result == {
        "dynamic_id": "1234567890123456789",
        "url": "https://t.bilibili.com/1234567890123456789",
    }
    sdk_send.assert_awaited_once()
    args = sdk_send.await_args.kwargs
    assert args["credential"] is credential
    assert args["info"].contents == [{"biz_id": "", "type": 1, "raw_text": "hello @someone [smile]"}]
    assert args["info"].pics == []


@pytest.mark.asyncio
async def test_image_only_publish_uploads_from_memory(credential, sdk_send, png_bytes, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "api.bilibili.com"
        assert request.method == "POST"
        assert "SESSDATA=test-session" in request.headers["cookie"]
        assert "bili_jct=test-csrf" in request.headers["cookie"]
        assert png_bytes in request.content
        assert b'name="csrf"' in request.content
        return httpx.Response(200, json={"code": 0, "data": {"image_url": "https://i0.hdslb.com/test.png"}})

    mock_http(monkeypatch, handle)

    async def send(*, info, credential):
        assert info.contents[0]["raw_text"] == ""
        assert len(info.pics) == 1
        picture = info.pics[0]
        assert (picture.width, picture.height, picture.imageType) == (3, 2, "png")
        assert await picture.upload(credential) is picture
        assert picture.url == "https://i0.hdslb.com/test.png"
        return {"dyn_id_str": "42"}

    sdk_send.side_effect = send
    result = await dynamic_publisher.publish_dynamic(credential, images=[data_url(png_bytes)])
    assert result["dynamic_id"] == "42"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_remote_image_never_receives_account_cookies(credential, sdk_send, png_bytes, monkeypatch):
    def handle(request):
        assert request.url.host == "images.example.com"
        assert "cookie" not in request.headers
        return httpx.Response(200, content=png_bytes)

    mock_http(monkeypatch, handle)
    await dynamic_publisher.publish_dynamic(credential, "caption", ["https://images.example.com/photo"])
    picture = sdk_send.await_args.kwargs["info"].pics[0]
    assert picture.content == png_bytes
    assert picture.imageType == "png"


@pytest.mark.asyncio
@pytest.mark.parametrize("text,images", [
    (" ", None), ("a" * 2001, None), (None, None), ("hello", "not-a-list"),
    ("hello", ["https://example.com/a.png"] * 10), ("hello", [42]), ("hello", [""]),
    ("hello", ["file:///private/photo.png"]), ("hello", ["https://"]),
    ("hello", ["https://user:password@example.com/pic.png"]),
])
async def test_invalid_input_cannot_publish(credential, sdk_send, text, images):
    with pytest.raises(dynamic_publisher.DynamicInputError, match="DYNAMIC_INVALID_INPUT"):
        await dynamic_publisher.publish_dynamic(credential, text, images)
    sdk_send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "data:image/png;base64,%%%", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,bm90LWFuLWltYWdl",
])
async def test_invalid_image_cannot_publish(credential, sdk_send, source):
    with pytest.raises(dynamic_publisher.DynamicInputError, match="DYNAMIC_INVALID_IMAGE"):
        await dynamic_publisher.publish_dynamic(credential, images=[source])
    sdk_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_download_is_rejected_before_sdk_call(credential, sdk_send, monkeypatch):
    monkeypatch.setattr(dynamic_publisher, "MAX_IMAGE_BYTES", 16)
    mock_http(monkeypatch, lambda _: httpx.Response(200, content=b"x" * 17))
    with pytest.raises(dynamic_publisher.DynamicInputError, match="DYNAMIC_IMAGE_TOO_LARGE"):
        await dynamic_publisher.publish_dynamic(credential, images=["https://example.com/big.png"])
    sdk_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_data_url_is_rejected_before_decoding(credential, sdk_send, monkeypatch):
    monkeypatch.setattr(dynamic_publisher, "MAX_IMAGE_BYTES", 16)
    with pytest.raises(dynamic_publisher.DynamicInputError, match="DYNAMIC_IMAGE_TOO_LARGE"):
        await dynamic_publisher.publish_dynamic(credential, images=[data_url(b"x" * 1000)])
    sdk_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_credentials_cannot_publish(sdk_send):
    with pytest.raises(dynamic_publisher.DynamicInputError, match="DYNAMIC_NOT_CONFIGURED"):
        await dynamic_publisher.publish_dynamic(Credential(), "hello")
    sdk_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_download_failure_cannot_publish(credential, sdk_send, monkeypatch):
    mock_http(monkeypatch, lambda _: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await dynamic_publisher.publish_dynamic(credential, images=["https://example.com/missing.png"])
    sdk_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_rejection_is_not_retried(credential, sdk_send):
    sdk_send.side_effect = ResponseCodeException(-101, "not logged in")
    with pytest.raises(ResponseCodeException):
        await dynamic_publisher.publish_dynamic(credential, "hello")
    assert sdk_send.await_count == 1


@pytest.mark.asyncio
async def test_publish_timeout_is_uncertain_and_not_retried(credential, sdk_send, monkeypatch):
    monkeypatch.setattr(dynamic_publisher, "PUBLISH_TIMEOUT_SECONDS", 0.01)

    async def block(**_):
        await asyncio.Event().wait()

    sdk_send.side_effect = block
    with pytest.raises(dynamic_publisher.DynamicPublishUncertainError):
        await dynamic_publisher.publish_dynamic(credential, "hello")
    assert sdk_send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"dyn_id_str": "0"}, {"dyn_id_str": "bad"}, {"dyn_id_str": "１２"}, None])
async def test_missing_or_invalid_dynamic_id_is_uncertain(credential, sdk_send, response):
    sdk_send.return_value = response
    with pytest.raises(dynamic_publisher.DynamicPublishUncertainError):
        await dynamic_publisher.publish_dynamic(credential, "hello")
    assert sdk_send.await_count == 1


@pytest.mark.asyncio
async def test_plugin_entry_does_not_require_listeners(credential, sdk_send):
    client = BiliDMClient(sesdata=credential.sessdata, bili_jct=credential.bili_jct)
    assert client.is_running is False
    result = await make_plugin(client).publish_dynamic("hello")
    assert isinstance(result, Ok)
    assert result.value["dynamic_id"] == "1234567890123456789"
    assert client.is_running is False


@pytest.mark.asyncio
async def test_plugin_entry_returns_safe_errors_without_logging_payloads():
    secret = "sensitive-test-payload"
    client = SimpleNamespace(publish_dynamic=AsyncMock(side_effect=RuntimeError(secret)))
    plugin = make_plugin(client)
    result = await plugin.publish_dynamic("hello")
    assert isinstance(result, Err)
    assert "DYNAMIC_PUBLISH_FAILED" in str(result.error)
    assert secret not in str(result.error)
    assert secret not in str(plugin.logger.mock_calls)
    plugin.bili_client = None
    assert isinstance(await plugin.publish_dynamic("hello"), Err)


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry(credential, sdk_send):
    sdk_send.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await dynamic_publisher.publish_dynamic(credential, "hello")
    assert sdk_send.await_count == 1


def test_locale_keys_placeholders_and_ui_references_match():
    root = Path(__file__).parents[1]
    bundles = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((root / "i18n").glob("*.json"))]
    assert len(bundles) == 8
    expected = set(bundles[0])
    for bundle in bundles:
        assert set(bundle) == expected
        for key in expected:
            assert sorted(re.findall(r"\{[^{}]+\}", bundle[key])) == sorted(re.findall(r"\{[^{}]+\}", bundles[0][key]))
    page = (root / "static/index.html").read_text(encoding="utf-8")
    script = (root / "static/script.js").read_text(encoding="utf-8")
    dynamic_keys = set(re.findall(r"(?:ui\.dynamic\.|(?:entries|actions)\.publish_dynamic\.)[a-z_]+", page + script))
    assert dynamic_keys <= expected
