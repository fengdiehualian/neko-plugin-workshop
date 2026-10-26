import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from plugin.plugins.neko_sous import client as module
from plugin.plugins.neko_sous.client import NekoSousClient
from websockets.asyncio.server import serve


async def running():
    return True


async def wait_until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def welcome(ws, *, wrong_id=False, modes=False):
    hello = json.loads(await ws.recv())
    assert hello["type"] == "hello"
    assert hello["payload"]["client_name"] == "neko_sous"
    await ws.send(json.dumps({
        "version": 1, "type": "welcome", "id": "server-1",
        "reply_to": "wrong" if wrong_id else hello["id"],
        "payload": {"session_id": "session-1", "server_version": "0.1.0",
                    "heartbeat_interval_seconds": 1, "heartbeat_timeout_seconds": 3,
                    **({"capabilities": ["mode_switch"], "mode": "companion"} if modes else {})},
    }))


async def pong(ws, ping):
    await ws.send(json.dumps({"version": 1, "type": "pong", "id": "pong-1",
                              "reply_to": ping["id"], "payload": {}}))


@asynccontextmanager
async def server(monkeypatch, handler):
    async with serve(handler, "127.0.0.1", 0) as instance:
        port = instance.sockets[0].getsockname()[1]
        monkeypatch.setattr(module, "ADDRESS", f"ws://127.0.0.1:{port}/neko")
        yield


@pytest.mark.asyncio
async def test_startup_and_missing_game_never_connect():
    async def stopped():
        return False
    client = NekoSousClient(probe=stopped)
    assert not client.desired and client._task is None
    state = await client.start()
    assert state["last_error"]["code"] == "game_not_running"
    assert state["game_state"] == "stopped"
    assert client._task is None and not client.desired


@pytest.mark.asyncio
async def test_process_detection_failure_is_not_reported_as_game_stopped():
    async def denied():
        raise PermissionError("denied")
    client = NekoSousClient(probe=denied)
    state = await client.start()
    assert state["game_state"] == "unknown"
    assert state["last_error"]["code"] == "game_check_failed"
    assert client._task is None


@pytest.mark.asyncio
async def test_heartbeat_and_manual_disconnect_stop_reconnection(monkeypatch):
    pings = []
    connections = []
    async def handler(ws):
        connections.append(ws)
        await welcome(ws)
        async for raw in ws:
            ping = json.loads(raw)
            pings.append(ping)
            await pong(ws, ping)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await asyncio.gather(client.start(), client.start())
            await wait_until(lambda: len(pings) >= 2)
            assert client.state == "connected" and client.session_id == "session-1"
            assert len(connections) == 1
            await client.stop()
            await asyncio.sleep(1.1)
            assert len(connections) == 1
            assert client.state == "disconnected" and not client.desired and client._task is None
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_unexpected_disconnect_reconnects(monkeypatch):
    count = 0
    async def handler(ws):
        nonlocal count
        count += 1
        await welcome(ws)
        if count == 1:
            await ws.close()
            return
        async for raw in ws:
            await pong(ws, json.loads(raw))
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: count >= 2 and client.state == "connected")
            assert client.last_error is None
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_welcome_correlation_is_required(monkeypatch):
    async def handler(ws):
        await welcome(ws, wrong_id=True)
        await ws.wait_closed()
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.last_error is not None)
            assert client.last_error["code"] == "protocol_error"
            assert client.state == "reconnecting" and not client.session_id
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_server_error_is_preserved_for_ui(monkeypatch):
    async def handler(ws):
        await ws.send(json.dumps({"version": 1, "type": "error", "id": "busy", "reply_to": None,
                                  "payload": {"code": "server_busy", "message": "Another client"}}))
        await ws.wait_closed()
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.last_error is not None)
            assert client.last_error["code"] == "server_busy"
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_missing_pong_causes_reconnect(monkeypatch):
    async def handler(ws):
        await welcome(ws)
        await ws.wait_closed()
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.last_error is not None)
            assert client.last_error["code"] == "heartbeat_timeout"
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_game_exit_waits_without_connecting_and_recovers(monkeypatch):
    game = True
    connections = []
    async def probe():
        return game
    async def handler(ws):
        connections.append(ws)
        await welcome(ws)
        async for raw in ws:
            await pong(ws, json.loads(raw))
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=probe)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            game = False
            await connections[0].close()
            await wait_until(lambda: client.state == "waiting_game")
            assert len(connections) == 1
            game = True
            await wait_until(lambda: len(connections) == 2 and client.state == "connected")
        finally:
            await client.stop()


def test_error_deduplication():
    client = NekoSousClient()
    client.report_error("connection_lost", "closed")
    error_id = client.last_error["id"]
    client.report_error("connection_lost", "closed")
    assert client.last_error["id"] == error_id


def test_locales_cover_all_backend_errors_and_states():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    english = json.loads((root / "i18n/en.json").read_text(encoding="utf-8"))
    chinese = json.loads((root / "i18n/zh-CN.json").read_text(encoding="utf-8"))
    assert set(english) == set(chinese)
    assert all(english.values()) and all(chinese.values())
    for state in ("disconnected", "connecting", "connected", "reconnecting", "waiting_game"):
        assert f"connection.{state}" in english


@pytest.mark.asyncio
async def test_shutdown_from_new_event_loop_after_worker_was_cancelled():
    # Host shutdown hooks may run in a new asyncio.run after the command loop closes.
    client = NekoSousClient(probe=running)
    async def first_loop():
        await client.start()
    await asyncio.to_thread(asyncio.run, first_loop())
    assert client._task.done()
    await client.stop()
    assert not client.desired


async def confirm_mode(ws, request, mode=None):
    await ws.send(json.dumps({"version": 1, "type": "mode_changed", "id": "mode-response",
                              "reply_to": request["id"], "payload": {"mode": mode or request["payload"]["mode"]}}))


@pytest.mark.asyncio
async def test_mode_only_changes_after_confirmation_and_heartbeats_continue(monkeypatch):
    received = asyncio.Event()
    allow_confirmation = asyncio.Event()
    async def handler(ws):
        await welcome(ws, modes=True)
        async for raw in ws:
            request = json.loads(raw)
            if request["type"] == "set_mode":
                received.set()
                await allow_confirmation.wait()
                await confirm_mode(ws, request)
            else:
                await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            assert client.mode == "companion" and client.mode_supported
            pending = asyncio.create_task(client.set_mode("playmate"))
            await asyncio.wait_for(received.wait(), 3)
            assert client.mode == "companion" and client.pending_mode == "playmate"
            allow_confirmation.set()
            await pending
            assert client.mode == "playmate" and client.pending_mode is None
            await asyncio.sleep(1.1)
            assert client.state == "connected"
            await client.set_mode("companion")
            assert client.mode == "companion"
        finally:
            allow_confirmation.set()
            await client.stop()


@pytest.mark.asyncio
async def test_rejected_mode_keeps_confirmed_mode(monkeypatch):
    async def handler(ws):
        await welcome(ws, modes=True)
        async for raw in ws:
            request = json.loads(raw)
            if request["type"] == "set_mode":
                await ws.send(json.dumps({"version": 1, "type": "error", "id": "rejected",
                                          "reply_to": request["id"], "payload": {"code": "invalid_mode", "message": "rejected"}}))
            else:
                await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            await client.set_mode("playmate")
            assert client.mode == "companion" and client.state == "connected"
            assert client.last_error["code"] == "invalid_mode"
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_old_server_cannot_receive_mode_requests(monkeypatch):
    requests = []
    async def handler(ws):
        await welcome(ws)
        async for raw in ws:
            request = json.loads(raw)
            requests.append(request["type"])
            await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            await client.set_mode("playmate")
            assert client.mode == "companion" and not client.mode_supported
            assert client.last_error["code"] == "mode_not_supported"
            assert "set_mode" not in requests
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_disconnect_and_reconnect_reset_mode_without_replaying_request(monkeypatch):
    connections = []
    mode_requests = []
    async def handler(ws):
        connections.append(ws)
        await welcome(ws, modes=True)
        async for raw in ws:
            request = json.loads(raw)
            if request["type"] == "set_mode":
                mode_requests.append(request)
                await confirm_mode(ws, request)
            else:
                await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            await client.set_mode("playmate")
            assert client.mode == "playmate"
            await connections[0].close()
            await wait_until(lambda: client.state == "reconnecting")
            assert client.mode == "companion" and not client.mode_supported
            await wait_until(lambda: len(connections) == 2 and client.state == "connected")
            assert client.mode == "companion" and len(mode_requests) == 1
            await client.set_mode("playmate")
            await client.stop()
            assert client.mode == "companion"
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_mode_and_rejects_offline_changes(monkeypatch):
    received = asyncio.Event()
    async def handler(ws):
        await welcome(ws, modes=True)
        async for raw in ws:
            request = json.loads(raw)
            if request["type"] == "set_mode":
                received.set()
            else:
                await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        await client.start()
        await wait_until(lambda: client.state == "connected")
        pending = asyncio.create_task(client.set_mode("playmate"))
        await asyncio.wait_for(received.wait(), 3)
        await client.stop()
        await asyncio.wait_for(pending, 2)
        assert client.mode == "companion" and client.pending_mode is None
        await client.set_mode("playmate")
        assert client.last_error["code"] == "not_connected"


@pytest.mark.asyncio
async def test_wrong_mode_confirmation_cannot_change_mode(monkeypatch):
    async def handler(ws):
        await welcome(ws, modes=True)
        async for raw in ws:
            request = json.loads(raw)
            if request["type"] == "set_mode":
                await confirm_mode(ws, request, "companion")
            else:
                await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            await client.set_mode("playmate")
            assert client.mode == "companion"
            assert client.last_error["code"] == "protocol_error"
        finally:
            await client.stop()


@pytest.mark.asyncio
async def test_mode_confirmation_timeout_resets_connection(monkeypatch):
    async def handler(ws):
        await welcome(ws, modes=True)
        async for raw in ws:
            request = json.loads(raw)
            if request["type"] == "ping":
                await pong(ws, request)
    async with server(monkeypatch, handler):
        client = NekoSousClient(probe=running)
        try:
            await client.start()
            await wait_until(lambda: client.state == "connected")
            await client.set_mode("playmate")
            assert client.mode == "companion" and client.pending_mode is None
            assert client.last_error["code"] == "mode_timeout"
            assert client.state == "reconnecting"
        finally:
            await client.stop()
