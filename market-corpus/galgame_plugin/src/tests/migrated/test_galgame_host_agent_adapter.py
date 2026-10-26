from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from market_plugins.galgame_plugin.host_agent_adapter import (
    HostAgentAdapter,
    _tls_verify_for_base_url,
)


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _run_in_new_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.plugin_unit
def test_host_agent_adapter_tls_verify_keeps_localhost_exemption() -> None:
    assert _tls_verify_for_base_url("http://127.0.0.1:48915") is False
    assert _tls_verify_for_base_url("https://localhost:48915") is False
    assert _tls_verify_for_base_url("https://tool.example.test") is True


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_host_agent_adapter_round_trip_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    task_state = {"status": "running"}

    @app.get("/computer_use/availability")
    async def _availability():
        return {"ready": True, "reasons": []}

    @app.post("/computer_use/run")
    async def _run(payload: dict[str, Any]):
        return {"success": True, "task_id": "task-1", "status": "running", "instruction": payload["instruction"]}

    @app.get("/tasks/task-1")
    async def _task():
        return {"id": "task-1", "status": task_state["status"]}

    @app.post("/tasks/task-1/cancel")
    async def _cancel():
        task_state["status"] = "cancelled"
        return {"success": True, "task_id": "task-1", "status": "cancelled"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        adapter = HostAgentAdapter(_Logger(), tool_server_port=48915)
        monkeypatch.setattr(adapter, "_build_client", lambda: client)

        availability = await adapter.get_computer_use_availability()
        started = await adapter.run_computer_use_instruction("advance once")
        task = await adapter.get_task("task-1")
        cancelled = await adapter.cancel_task("task-1")

    assert availability["ready"] is True
    assert started["task_id"] == "task-1"
    assert task["status"] == "running"
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_host_agent_adapter_rebuilds_client_after_closed_loop_error(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()

    @app.get("/tasks/task-1")
    async def _task():
        return {"id": "task-1", "status": "running"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as fallback_client:
        adapter = HostAgentAdapter(_Logger(), tool_server_port=48915)

        class _BrokenSharedClient:
            is_closed = False

            async def request(self, *args, **kwargs):
                raise RuntimeError("Event loop is closed")

            async def aclose(self):
                self.is_closed = True

        built_clients = [_BrokenSharedClient(), fallback_client]
        monkeypatch.setattr(adapter, "_build_client", lambda: built_clients.pop(0))
        task = await adapter.get_task("task-1")

    assert task["status"] == "running"
    assert adapter._client is fallback_client


@pytest.mark.plugin_unit
def test_host_agent_adapter_rebuilds_client_after_loop_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = HostAgentAdapter(_Logger(), tool_server_port=48915)
    built_clients = []

    class _LoopAwareAdapterClient:
        def __init__(self, index: int) -> None:
            self.index = index
            self.is_closed = False

        async def request(self, method: str, url: str, **kwargs):
            del kwargs
            return httpx.Response(
                200,
                json={"ready": True, "client_index": self.index},
                request=httpx.Request(method, url),
            )

        async def aclose(self) -> None:
            self.is_closed = True

    def _build_client():
        client = _LoopAwareAdapterClient(len(built_clients) + 1)
        built_clients.append(client)
        return client

    monkeypatch.setattr(adapter, "_build_client", _build_client)

    first = _run_in_new_loop(adapter.get_computer_use_availability())
    second = _run_in_new_loop(adapter.get_computer_use_availability())

    assert first["client_index"] == 1
    assert second["client_index"] == 2
    assert len(built_clients) == 2
    assert built_clients[0].is_closed is True
    assert built_clients[1].is_closed is False
