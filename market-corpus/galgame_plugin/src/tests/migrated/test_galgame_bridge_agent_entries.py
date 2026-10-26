from __future__ import annotations

from pathlib import Path

import pytest
from plugin.sdk.plugin import Ok
from tests.support.plugin_harness import (
    FakeHostAdapter,
    FakeLLMGateway,
    make_active_plugin,
)

pytestmark = pytest.mark.plugin_integration


@pytest.mark.asyncio
async def test_agent_send_message_interrupts_awaiting_bridge(tmp_path: Path) -> None:
    plugin, _ctx = await make_active_plugin(tmp_path)
    assert isinstance(await plugin.galgame_set_mode(mode="choice_advisor"), Ok)
    fake_host = FakeHostAdapter()
    plugin._game_agent._host_adapter = fake_host
    plugin._game_agent._llm_gateway = FakeLLMGateway(
        reply_text="桥接还没确认状态变化。"
    )

    try:
        await plugin._game_agent.tick(plugin._snapshot_state())
        fake_host.tasks["task-1"]["status"] = "completed"
        await plugin._game_agent.tick(plugin._snapshot_state())

        result = await plugin.galgame_agent_command(
            action="send_message", message="先停一下，告诉我现在卡在哪"
        )

        assert isinstance(result, Ok)
        assert result.value["action"] == "send_message"
        assert result.value["result"] == "桥接还没确认状态变化。"
        assert plugin._game_agent._actuation is None
        assert fake_host.cancelled == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_agent_set_standby_interrupts_awaiting_bridge(tmp_path: Path) -> None:
    plugin, _ctx = await make_active_plugin(tmp_path)
    assert isinstance(await plugin.galgame_set_mode(mode="choice_advisor"), Ok)
    fake_host = FakeHostAdapter()
    plugin._game_agent._host_adapter = fake_host
    plugin._game_agent._llm_gateway = FakeLLMGateway(reply_text="unused")

    try:
        await plugin._game_agent.tick(plugin._snapshot_state())
        fake_host.tasks["task-1"]["status"] = "completed"
        await plugin._game_agent.tick(plugin._snapshot_state())

        result = await plugin.galgame_agent_command(
            action="set_standby", standby=True
        )

        assert isinstance(result, Ok)
        assert result.value["action"] == "set_standby"
        assert result.value["status"] == "standby"
        assert plugin._game_agent._actuation is None
        assert fake_host.cancelled == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_tick_recovers_after_temporary_host_unavailable(tmp_path: Path) -> None:
    plugin, _ctx = await make_active_plugin(tmp_path)
    assert isinstance(await plugin.galgame_set_mode(mode="choice_advisor"), Ok)
    fake_host = FakeHostAdapter(ready=False)
    plugin._game_agent._host_adapter = fake_host
    plugin._game_agent._llm_gateway = FakeLLMGateway(reply_text="unused")

    try:
        await plugin.bridge_tick()
        first_status = await plugin.galgame_agent_command(action="query_status")
        assert isinstance(first_status, Ok)
        assert first_status.value["status"] == "error"
        assert first_status.value["input_source"] == "bridge_sdk"

        fake_host.ready = True
        plugin._game_agent._next_actuation_at = 0.0
        await plugin.bridge_tick()
        recovered = await plugin.galgame_agent_command(action="query_status")

        assert isinstance(recovered, Ok)
        assert recovered.value["status"] == "active"
        assert recovered.value["input_source"] == "bridge_sdk"
        assert "push_policy" in recovered.value
        assert fake_host.started
    finally:
        await plugin.shutdown()
