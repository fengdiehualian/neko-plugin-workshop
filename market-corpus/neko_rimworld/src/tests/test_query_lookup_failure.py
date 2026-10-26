"""Replay a real native deadline through dispatch, model JSON and raw audit."""

import copy
import json
from pathlib import Path

import pytest
from plugin.plugins.neko_rimworld.agent.runtime.tools import ToolRegistry
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import semantic_interaction_factory
from plugin.plugins.neko_rimworld.game.observation import Observation
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.message_contracts import ToolCall

from .test_semantic_tools import Session, _update, catalog


def deadline_receipt():
    # Detached unchanged observation ffda7b79ccab from pipeline-v1 D01.
    return json.loads((Path(__file__).parent / "fixtures/definition_lookup_deadline.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,rest,action", [
    ("count", {"metric": "units", "detail": 5}, "instance counting"),
    ("look", {"aspects": ["identity"], "detail": 5}, "instance reading"),
])
async def test_real_definition_deadline_reports_phase_without_retry_or_false_quantity(tool, rest, action):
    receipt = deadline_receipt()
    saved = copy.deepcopy(receipt)
    calls = []

    async def query(request):
        calls.append(copy.deepcopy(request))
        assert request == receipt["request"]
        return Observation(copy.deepcopy(receipt))

    session = Session()
    reader = semantic_interaction_factory(make_game_tools(catalog(), query), session, _update)
    registry = ToolRegistry()
    registry.register(next(t for t in reader.tools() if t.name == tool))
    args = {"object": {"kind": "things", "definition": "WoodLog", "map": "current"}, **rest}
    result = await registry.execute(registry.prepare(ToolCall("read", tool, json.dumps(args))))
    visible = json.loads(result.as_message()["content"])["data"]

    assert visible["status"] == "unavailable"
    assert visible["failure_stage"] == "definition_resolution"
    assert visible["resolution"]["coverage"]["scan"] == "partial"
    assert visible["execution"] == "not_run" and visible["coverage"]["scan"] == "not_run"
    assert visible["measures"] == {} and visible["facts"] == []
    assert action + " was not run" in visible["explanation"]
    assert "Quantity remains unknown" in visible["explanation"]
    assert "Changing map or display limit does not change this definition scan" in visible["explanation"]
    assert "remain conditional" in visible["explanation"]
    assert visible["issues"][0]["reason"] == "deadline_exceeded"
    assert visible["issues"][0]["path"] == ["results", "0"]
    assert "narrow the query" not in result.as_message()["content"]
    assert len(calls) == 1  # No hidden retry or instance query.
    assert session.observations == [saved] and receipt == saved
    assert session.observations[0]["response"]["payload"]["issues"][0]["message"].endswith("narrow the query.")


@pytest.mark.asyncio
async def test_complete_definition_miss_is_not_reported_as_deadline_or_incomplete_scan():
    receipt = deadline_receipt()
    payload = receipt["response"]["payload"]
    payload["issues"] = []
    payload["status"] = "ok"
    row = payload["data"]["results"][0]
    row.update(scan_complete=True, matched_count=0)
    row["page"]["has_more"] = False

    async def query(request):
        return Observation(copy.deepcopy(receipt))

    session = Session()
    reader = semantic_interaction_factory(make_game_tools(catalog(), query), session, _update)
    result = await reader.count({"object": {"kind": "things", "definition": "WoodLog"}, "metric": "units"})
    assert result["resolution"]["status"] == "missing_in_scope"
    assert result["resolution"]["coverage"]["scan"] == "complete"
    assert "failure_stage" not in result and result["issues"] == []
    assert result["execution"] == "not_run" and result["measures"] == {}
