from __future__ import annotations

import asyncio
import json
import logging
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.core.context import PluginContext
from plugin.plugins.bilibili_integration import (
    DYNAMIC_PUBLISH_INPUT_SCHEMA,
    BiliDMPlugin,
)
from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError


def tool_plugin(result):
    instance = object.__new__(BiliDMPlugin)
    instance.publish_dynamic = AsyncMock(return_value=result)
    instance.i18n = SimpleNamespace(t=lambda key, *, default="": f"localized:{key}")
    return instance


def test_registration_uses_sdk_ipc_and_is_idempotent():
    root = Path(__file__).parents[1]
    context = PluginContext(
        plugin_id="bilibili_integration",
        config_path=root / "plugin.toml",
        logger=logging.getLogger("test.bilibili-tool"),
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
    )
    instance = object.__new__(BiliDMPlugin)
    NekoPluginBase.__init__(instance, context)
    instance._register_publish_dynamic_tool()
    instance._register_publish_dynamic_tool()
    messages = []
    while not context.message_queue.empty():
        messages.append(context.message_queue.get_nowait())
    registrations = [item for item in messages if item.get("type") == "LLM_TOOL_REGISTER"]
    assert len(registrations) == 1
    registration = registrations[0]
    assert registration["name"] == "publish_bilibili_dynamic"
    assert registration["plugin_id"] == "bilibili_integration"
    assert registration["timeout_seconds"] == 60.0
    assert registration["parameters"] == DYNAMIC_PUBLISH_INPUT_SCHEMA
    bundles = [json.loads(path.read_text(encoding="utf-8")) for path in (root / "i18n").glob("*.json")]
    assert registration["description"] in {bundle["tools.publish_dynamic.description"] for bundle in bundles}
    assert any(item.get("entry_id") == "__llm_tool__publish_bilibili_dynamic" for item in messages)
    assert [item["name"] for item in instance.list_llm_tools()] == ["publish_bilibili_dynamic"]


@pytest.mark.asyncio
async def test_tool_delegates_to_existing_publisher_and_returns_link():
    published = {"dynamic_id": "42", "url": "https://t.bilibili.com/42"}
    instance = tool_plugin(Ok(published))
    images = ["https://example.com/photo.png"]
    response = await instance.publish_bilibili_dynamic(text="hello", images=images)
    assert response == {"output": published, "is_error": False}
    instance.publish_dynamic.assert_awaited_once_with(text="hello", images=images)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,locale_key", [
    ("DYNAMIC_INVALID_INPUT", "ui.dynamic.invalid_input"),
    ("DYNAMIC_INVALID_IMAGE", "ui.dynamic.invalid_image"),
    ("DYNAMIC_IMAGE_TOO_LARGE", "ui.dynamic.image_too_large"),
    ("DYNAMIC_NOT_CONFIGURED", "ui.dynamic.not_configured"),
    ("DYNAMIC_PUBLISH_FAILED", "ui.dynamic.failed"),
    ("DYNAMIC_PUBLISH_UNCERTAIN", "ui.dynamic.uncertain"),
])
async def test_tool_errors_are_explicit_localized_and_not_retried(code, locale_key):
    instance = tool_plugin(Err(SdkError(f"{code}: internal detail")))
    response = await instance.publish_bilibili_dynamic(text="hello")
    assert response == {
        "output": {"message": f"localized:{locale_key}", "retryable": False},
        "is_error": True,
        "error": code,
    }
    assert "internal detail" not in str(response)
    assert instance.publish_dynamic.await_count == 1


@pytest.mark.asyncio
async def test_tool_budget_includes_lifecycle_lock_wait(monkeypatch):
    import plugin.plugins.bilibili_integration as module

    monkeypatch.setattr(module, "DYNAMIC_TOOL_TIMEOUT_SECONDS", 0.01)
    instance = tool_plugin(None)
    instance._lifecycle_lock = asyncio.Lock()
    instance.publish_dynamic = BiliDMPlugin.publish_dynamic.__get__(instance)
    await instance._lifecycle_lock.acquire()
    try:
        response = await instance.publish_bilibili_dynamic(text="hello")
    finally:
        instance._lifecycle_lock.release()
    assert response["is_error"] is True
    assert response["error"] == "DYNAMIC_PUBLISH_UNCERTAIN"
    assert response["output"]["retryable"] is False


@pytest.mark.asyncio
async def test_tool_external_cancellation_propagates():
    instance = tool_plugin(None)
    instance.publish_dynamic.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await instance.publish_bilibili_dynamic(text="hello")
    assert instance.publish_dynamic.await_count == 1
