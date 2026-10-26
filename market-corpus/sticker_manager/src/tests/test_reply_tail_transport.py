"""Actual Sender/Library -> SDK -> ASGI host -> WebSocket display contract."""
import asyncio
import base64
import importlib.machinery
import importlib.util
import json
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import FakeHostContext, build_plugin, ensure_sdk_stub
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.services.bundled_pack import open_pack


def test_facade_stub_keeps_a_real_shared_sdk_importable(tmp_path, monkeypatch):
    from types import ModuleType

    plugin_path = tmp_path / "plugin"
    shared = plugin_path / "sdk" / "shared"
    shared.mkdir(parents=True)
    (shared / "__init__.py").write_text("sentinel = 'real shared SDK'\n", encoding="utf-8")
    package = ModuleType("plugin")
    package.__path__ = [str(plugin_path)]
    monkeypatch.setitem(sys.modules, "plugin", package)
    monkeypatch.setitem(sys.modules, "plugin.sdk", None)
    monkeypatch.setitem(sys.modules, "plugin.sdk.plugin", None)
    ensure_sdk_stub()
    # Resolve from the new namespace, ignoring any preloaded shared SDK.
    spec = importlib.machinery.PathFinder.find_spec("plugin.sdk.shared", sys.modules["plugin.sdk"].__path__)
    assert spec is not None and spec.loader is not None
    actual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual)
    assert actual.sentinel == "real shared SDK"
    assert sys.modules["plugin.sdk.plugin"].llm_tool is not None


@pytest.mark.parametrize("outcome", ["submitted", "cancelled", "lost_ack"])
@pytest.mark.parametrize("image_source", ["generated", "largest_official"])
def test_actual_sender_sdk_host_transport(tmp_path, run_async, monkeypatch, outcome, image_source):
    host_tail = pytest.importorskip("main_logic.reply_tail")
    # The plugin and host both have a services package; transport uses the host.
    monkeypatch.syspath_prepend(str(Path(host_tail.__file__).parents[1]))
    import httpx
    from fastapi import FastAPI
    from main_logic.core._shared import _ReplyTurn
    from main_logic.core.turn import TurnMixin
    from main_logic.reply_tail import ReplyTailRegistry
    from main_routers import tool_router
    from main_routers.cookies_login_router import verify_local_access
    from PIL import Image
    from plugin.sdk.shared.core import reply_tail as sdk
    from plugin.sdk.shared.core.context import SdkContext
    from starlette.websockets import WebSocketState

    # Normal server startup binds the real image validator used by reply-tail.
    importlib.import_module("app.main_server.character_runtime")

    image = BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        image, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (2, 2), "green")], duration=80, loop=0,
    )
    gif_bytes = image.getvalue()
    if image_source == "largest_official":
        pack = Path(__file__).resolve().parents[1] / "official/official_pack.zip"
        with open_pack(pack) as archive:
            rows = json.loads(archive.read("manifest.json"))["stickers"]
            row = max(rows, key=lambda row: archive.getinfo("stickers/" + row["file"]).file_size)
            gif_bytes = archive.read("stickers/" + row["file"])
        assert 256 * 1024 < len(gif_bytes) <= 1024 * 1024

    async def exercise():
        registry = ReplyTailRegistry()
        owner = _ReplyTurn("speech-A", "request-A", session=object())
        manager = SimpleNamespace(lanlan_name="K", websocket=SimpleNamespace(
            state=SimpleNamespace(reply_tail_version=1), client_state=WebSocketState.CONNECTED,
            send_json=AsyncMock(),
        ))
        manager.render_chat_blocks = TurnMixin.render_chat_blocks.__get__(manager)
        context = registry.tool_context(manager, owner, "call-A", "plugin:sticker_manager")
        app = FastAPI()
        app.dependency_overrides[verify_local_access] = lambda: None
        app.include_router(tool_router.router)
        monkeypatch.setattr(tool_router, "reply_tail_registry", registry)
        original_http = httpx.AsyncClient
        monkeypatch.setattr(sdk.httpx, "AsyncClient", lambda **kw: original_http(
            **kw, transport=httpx.ASGITransport(app=app),
        ))
        plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
        plugin._settings = StickerManagerSettings(
            enabled=True, send=SendSettings(recent_dedup_count=0),
        )
        client = SdkContext(SimpleNamespace(plugin_id="sticker_manager")).reply_tail
        plugin._sender._plugin.ctx.reply_tail = client
        plugin._library.load(force=True)
        sticker, error = plugin._library.add(data=gif_bytes, desc="wave", tags=[])
        assert not error
        if outcome == "lost_ack":
            original_register = client.register

            async def lost(*args, **kwargs):
                assert (await original_register(*args, **kwargs))["status"] == "registered"
                raise TimeoutError("host accepted but ACK lost")

            client.register = lost
        result = await plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"host_reply": context})
        assert result.get("queued") == sticker.id and result.get("sent") == "", result
        assert plugin._library.get(sticker.id).use_count == 0
        assert not host.push.calls
        manager.websocket.send_json.assert_not_awaited()
        if outcome == "cancelled":
            plugin._settings = replace(plugin._settings, enabled=False)
            settled = await plugin._sender.drain(settings=plugin._settings)
            assert not settled[0][1].ok
        owner.reply_tail.completed = True
        owner.turn_ended = True
        await registry.finish(owner.reply_tail)
        if outcome != "cancelled":
            settled = await plugin._sender.drain(settings=plugin._settings)
            assert settled[0][1].ok
            frame = manager.websocket.send_json.call_args.args[0]
            assert frame["request_id"] == "request-A"
            assert frame["metadata"]["source_name"] == "sticker_manager"
            assert base64.b64decode(frame["blocks"][0]["url"].split(",", 1)[1]) == gif_bytes
            assert plugin._library.get(sticker.id).use_count == 1
            assert len(plugin._library.read_usage()) == 1
            await plugin._sender.drain(settings=plugin._settings)
            assert plugin._library.get(sticker.id).use_count == 1
        else:
            manager.websocket.send_json.assert_not_awaited()
            assert plugin._library.get(sticker.id).use_count == 0
        assert not host.push.calls
        assert not plugin._sender._host_pending and not plugin._sender._inflight

    run_async(asyncio.wait_for(exercise(), 10))


def test_actual_registration_surface_opts_in_only_supported_sdk(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    previous = {"name": "sticker_send", "description": "old", "parameters": {}, "role": None}
    registered = []
    plugin.list_llm_tools = lambda: [previous]
    plugin.unregister_llm_tool = lambda name: True

    def modern(*, name, description, parameters, handler, timeout, role, reply_tail=False):
        registered.append(reply_tail)
        return True

    plugin.register_llm_tool = modern
    assert plugin._apply_send_tool_surface()
    assert registered == [True]

    def old(*, name, description, parameters, handler, timeout, role):
        registered.append("old")
        return True

    plugin.register_llm_tool = old
    assert plugin._apply_send_tool_surface()
    assert registered == [True, "old"]
