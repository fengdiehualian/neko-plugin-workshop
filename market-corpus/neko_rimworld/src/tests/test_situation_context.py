"""Native background preparation, bounded projection and shared execution."""

import asyncio
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from plugin.plugins.neko_rimworld.agent.context.situation_context import compile_situation
from plugin.plugins.neko_rimworld.agent.delivery.claims import check_claims
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger, AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import semantic_interaction_factory
from plugin.plugins.neko_rimworld.game.observation import Observation
from plugin.plugins.neko_rimworld.game.protocol import QUERY_RESULT, Payload, SessionDescriptor, make_envelope
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.game.situation import SituationProvider, SituationSnapshot
from plugin.plugins.neko_rimworld.message_contracts import AssistantTurn, ToolCall
from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError
from plugin.plugins.neko_rimworld.workspace.runtime import WorkspaceAgent
from plugin.plugins.neko_rimworld.workspace.storage import WorkspaceStorage

from .consultation_setup import consultation_setup
from .test_runtime_repair import LOG, settings, submit
from .test_semantic_tools import Session, _update, public_source


def native(tick=10, *, items=None):
    sections = {name: {"status": "ok", "source": name, "items": []} for name in ("threats", "conditions", "alerts", "quests", "events")}
    if items:
        for name, rows in items.items():
            sections[name]["items"] = rows
    data = {"format": "situation_snapshot.v1", "sample": {"map_id": "Map_1", "finished_tick": tick},
            "clock": {"tick": tick}, "map": {"id": "Map_1", "label": "Home"}, "sections": sections}
    envelope = make_envelope(QUERY_RESULT, "1" * 32, 1, payload=Payload(status="ok", data=data, issues=[]),
                             session=SessionDescriptor("2" * 32, "3" * 32, tick, 1), request_id="4" * 32)
    return Observation.from_response("read_situation", {}, envelope)


@pytest.mark.asyncio
async def test_cache_uses_game_ticks_singleflight_and_retains_true_sample():
    clock = SimpleNamespace(game_tick=10, colony_id="2" * 32, load_epoch="3" * 32)
    calls = []

    async def query(request):
        calls.append(request)
        await asyncio.sleep(0)
        return native(clock.game_tick)

    provider = SituationProvider(query, lambda: clock)
    first, second = await asyncio.gather(provider.snapshot(), provider.snapshot())
    assert len(calls) == 1 and not first.cache_hit and second.cache_hit
    clock.game_tick = 30_009
    reused = await provider.snapshot()
    assert reused.cache_hit and reused.observation.value == first.observation.value
    assert reused.checked_tick == 30_009
    clock.game_tick = 30_010
    fresh = await provider.snapshot()
    assert not fresh.cache_hit and len(calls) == 2
    assert fresh.observation.value["response"]["payload"]["data"]["sample"]["finished_tick"] == 30_010
    clock.load_epoch = "5" * 32
    with pytest.raises(ToolExecutionError, match="different loaded world"):
        await provider.snapshot()


def test_projection_budget_direct_quest_sources_unknown_and_raw_retention():
    observation = native(items={
        "quests": [{"quest_id": "Quest_1", "id": "Quest_1", "label": "Help", "state": "Ongoing", "source": "QuestManager"}],
        "events": [{"quest_id": "Quest_1", "id": "Letter_2", "label": "Deadline", "source": "Archive"}],
        "alerts": [{"id": "a", "label": "Medical", "count": None}],
        "conditions": [{"id": str(i), "label": "Conditions " * 30} for i in range(100)],
    })
    original = observation.to_dict()
    compiled = compile_situation(SituationSnapshot(observation, True, 20))
    assert compiled["tokens"] <= 1_000 and compiled["cache_hit"]
    assert compiled == compile_situation(SituationSnapshot(observation, True, 20))
    assert observation.value == original
    view = json.loads(compiled["content"].split("\n", 1)[1])
    quest = view["sections"]["quests"]["items"][0]
    assert quest["quest_id"] == "Quest_1" and len(quest["facts"]) == 2
    assert {f["section"] for f in quest["facts"]} == {"quests", "events"}
    assert view["sections"]["events"]["items"] == []
    assert view["sections"]["alerts"]["items"][0]["count"] is None
    assert view["sections"]["conditions"]["omitted_items"] > 0
    assert compiled["projection_sha256"] == hashlib.sha256(compiled["content"].encode()).hexdigest()


def test_internal_contract_does_not_change_any_model_tool_schema():
    source = public_source()
    async def query(_):
        return native()
    before = make_game_tools(source, query)
    source = copy.deepcopy(source)
    source["situation_snapshot"] = {"version": "situation_snapshot.v1", "operation": "read_situation", "parameters": {"map": "string"}}
    after = make_game_tools(source, query)
    assert before.directory == after.directory
    assert [t.schema() for t in before.tools] == [t.schema() for t in after.tools]
    assert [t.schema() for t in semantic_interaction_factory(before, Session(), _update).tools()] == [
        t.schema() for t in semantic_interaction_factory(after, Session(), _update).tools()]
    assert not any(t.name == "read_situation" for t in after.tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["standalone", "prepared", "persistent"])
async def test_shared_bootstrap_before_first_dispatch_with_raw_report_and_one_background(kind):
    observation = native(items={"conditions": [{"id": "c", "label": "Solar flare"}]})
    async def query(_):
        return observation
    setup = replace(consultation_setup(), situation_provider=SituationProvider(query))
    reports, inputs = [], []
    class Model:
        async def complete(self, messages, tools, *, timeout):
            inputs.append(copy.deepcopy(messages))
            return submit("Consultation complete.")
    async def provide():
        return setup
    async def save(record):
        reports.append(record)
        return {"report_id": record["report_id"]}
    runtime = AgentRuntime(audit_registry=AgentAuditRegistry(), logger=LOG, model_factory=lambda _: Model(),
                           tools_provider=provide, report_sink=save)
    if kind == "standalone":
        result = await runtime.run("What next?", settings())
    else:
        session = AgentRunSession.create(ledger=AgentAuditLedger("What next?"), question="What next?",
                                        workspace_id="work" if kind == "persistent" else None,
                                        context_messages=({"role": "user", "content": "Previous exchange."},))
        result = await runtime.execute(session, settings())
    assert result["status"] == "ok"
    background = [m for m in inputs[0] if m.get("name") == "game_situation"]
    assert len(background) == 1 and background[0]["role"] == "user"
    assert "Solar flare" in background[0]["content"]
    assert inputs[0][-2] == {"role": "user", "content": "What next?"}
    assert reports[0]["observations"][0] == observation.value
    assert reports[0]["situation_context"] == result["situation_context"]
    assert result["situation_context"]["observation_id"] == observation.value["observation_id"]


def test_length_repair_never_replays_background():
    run = AgentRunSession.create(ledger=AgentAuditLedger("q"), question="q")
    run.messages.insert(1, {"role": "user", "name": "game_situation", "content": "large background"})
    run.submission.answer = "saved conclusion"
    run.submission.errors = {"delivery": {"code": "handoff_over_budget"}}
    assert "large background" not in json.dumps(run.length_repair_messages())


@pytest.mark.asyncio
@pytest.mark.parametrize("code,continues", [("query_timeout", True), ("bridge_busy", True), ("unsupported", True),
                                           ("world_changed", False), ("connection_changed", False),
                                           ("invalid_catalog", False), ("protocol_error", False)])
async def test_source_failure_is_disclosed_and_world_admission_remains_fatal(code, continues):
    async def query(_):
        raise ToolExecutionError(code, "Native source failed.")
    setup = replace(consultation_setup(), situation_provider=SituationProvider(query))
    inputs = []
    class Model:
        async def complete(self, messages, tools, *, timeout):
            inputs.append(copy.deepcopy(messages))
            return submit("Consultation complete.")
    async def provide():
        return setup
    runtime = AgentRuntime(audit_registry=AgentAuditRegistry(), logger=LOG, model_factory=lambda _: Model(), tools_provider=provide)
    result = await runtime.run("What next?", settings())
    assert bool(inputs) is continues
    if continues:
        assert result["status"] == "ok" and result["situation_context"]["status"] == "unavailable"
        background = next(m for m in inputs[0] if m.get("name") == "game_situation")
        assert background["role"] == "user" and code in background["content"]
        assert result["situation_context"]["raw_sha256"] is None
        assert result["observations"] == []
    else:
        assert result["status"] == "degraded" and "situation_context" not in result


@pytest.mark.asyncio
async def test_actual_persistent_history_has_only_current_background_and_cached_sample(tmp_path):
    clock = SimpleNamespace(game_tick=10, colony_id="2" * 32, load_epoch="3" * 32)
    reads, inputs = [], []
    async def query(_):
        reads.append(clock.game_tick)
        return native(clock.game_tick)
    setup = replace(consultation_setup(), situation_provider=SituationProvider(query, lambda: clock))
    async def provide():
        return setup
    class Model:
        async def complete(self, messages, tools, *, timeout):
            inputs.append(copy.deepcopy(messages))
            return submit("Consultation complete.")
    runtime = AgentRuntime(audit_registry=AgentAuditRegistry(), logger=LOG, model_factory=lambda _: Model(), tools_provider=provide)
    storage = WorkspaceStorage(tmp_path / "work.db")
    try:
        work = await storage.call("create", "owner")
        agent = WorkspaceAgent(storage, runtime)
        for question in ("First question", "Second question", "Third question"):
            turn = await storage.call("accept", work, "owner", question)
            result = await agent.execute(work, "owner", turn, settings(), colony_id=clock.colony_id)
            assert result["history_saved"]
            if question == "First question":
                clock.game_tick = 20
            elif question == "Second question":
                cached = result["result"]["situation_context"]
                assert cached["cache_hit"] and cached["sample"]["finished_tick"] == 10
                assert result["result"]["freshness"]["game_tick"] == 10
                clock.game_tick = 30_010
        assert reads == [10, 30_010]
        assert len(inputs) == 3
        for request in inputs:
            assert sum(m.get("name") == "game_situation" for m in request) == 1
            history = [m["content"] for m in request if "Caller-attached colony workspace history" in m.get("content", "")]
            assert all("Game situation snapshot" not in item for item in history)
    finally:
        await storage.close()


def test_snapshot_scope_describes_sampled_map_and_keeps_native_order():
    observation = native(items={"alerts": [{"id": "urgent", "label": "Critical", "priority": "Critical"},
                                           {"id": "minor", "label": "Low", "priority": "Low"}]})
    observation.value["response"]["payload"]["data"]["sections"]["alerts"]["scope"] = "Global cross-map UI cache."
    compiled = compile_situation(SituationSnapshot(observation, True, 99))
    view = json.loads(compiled["content"].split("\n", 1)[1])
    assert view["map_at_sample"]["id"] == "Map_1" and "map" not in view
    assert "cross-map" in view["sections"]["alerts"]["scope"]
    assert [row["id"] for row in view["sections"]["alerts"]["items"]] == ["urgent", "minor"]
    assert compiled["checked_tick"] == 99 and view["situation_at_tick"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["recovery", "finalizing", "length_repair"])
async def test_background_is_fixed_across_research_finalization_and_omitted_from_length_repair(mode):
    observation = native()
    async def query(_):
        return observation
    setup = replace(consultation_setup(), situation_provider=SituationProvider(query))
    async def provide():
        return setup
    inputs = []
    class Model:
        async def complete(self, messages, tools, *, timeout):
            inputs.append(copy.deepcopy(messages))
            if mode == "finalizing":
                assert [tool["function"]["name"] for tool in tools] == ["submit_response"]
            if len(inputs) == 1:
                if mode == "length_repair":
                    return AssistantTurn("offline", "offline", "tool_calls", "", (ToolCall("submit", "submit_response", json.dumps({"answer": " word" * 200, "detail": "Retained complete detail."})),))
                return AssistantTurn("offline", "offline", "stop", "I have reviewed the available context.")
            return submit("Consultation complete.")
    runtime = AgentRuntime(audit_registry=AgentAuditRegistry(), logger=LOG, model_factory=lambda _: Model(), tools_provider=provide)
    session = AgentRunSession.create(ledger=AgentAuditLedger("q"), question="q")
    if mode == "finalizing":
        session.ledger._started_monotonic -= 6
    result = await runtime.execute(session, settings())
    assert result["status"] == "ok" and len(inputs) == 2
    assert sum(m.get("name") == "game_situation" for m in inputs[0]) == 1
    if mode == "length_repair":
        assert "Game situation snapshot" not in json.dumps(inputs[1])
        assert "Retained complete detail." in result["answer"]
    else:
        assert [m for m in inputs[0] if m.get("name") == "game_situation"] == [m for m in inputs[1] if m.get("name") == "game_situation"]


def test_background_does_not_authorize_complete_actor_health_claims():
    observation = native(items={"alerts": [{"id": "a", "label": "Colonist needs treatment", "targets": [{"id": "Pawn_1", "name": "Rescue_Patient"}]}]}).to_dict()
    text = "Rescue_Patient 完全健康。"
    assert check_claims(text, observations=[observation]) == check_claims(text, observations=[])


def test_partial_source_reasons_and_error_reach_model_background():
    observation = native()
    alerts = observation.value["response"]["payload"]["data"]["sections"]["alerts"]
    alerts.update(status="partial", reasons=["native_report_not_observed"],
                  error={"code": "unsupported_native_layout", "message": "Unsupported report."})
    compiled = compile_situation(SituationSnapshot(observation, False, 10))
    view = json.loads(compiled["content"].split("\n", 1)[1])
    assert view["sections"]["alerts"]["reasons"] == ["native_report_not_observed"]
    assert view["sections"]["alerts"]["error"]["code"] == "unsupported_native_layout"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,continues", [("failed", "situation_result_too_large", True),
                                                  ("stale_session", "old_snapshot", False),
                                                  ("failed", "unsupported_game_implementation", False)])
async def test_failed_native_envelope_is_retained_and_stale_or_admission_failures_abort(status, code, continues):
    envelope = make_envelope(QUERY_RESULT, "1" * 32, 1,
                             payload=Payload(status=status, error_code=code, message="Native read failed.", issues=[]),
                             session=SessionDescriptor("2" * 32, "3" * 32, 10, 1), request_id="4" * 32)
    observation = Observation.from_response("read_situation", {}, envelope)
    async def query(_):
        return observation
    setup = replace(consultation_setup(), situation_provider=SituationProvider(query))
    async def provide():
        return setup
    inputs = []
    class Model:
        async def complete(self, messages, tools, *, timeout):
            inputs.append(copy.deepcopy(messages))
            return submit("Consultation complete.")
    runtime = AgentRuntime(audit_registry=AgentAuditRegistry(), logger=LOG, model_factory=lambda _: Model(), tools_provider=provide)
    result = await runtime.run("q", settings())
    assert result["observations"] == [observation.value]
    assert bool(inputs) is continues
    if continues:
        assert result["situation_context"]["status"] == "unavailable"
        assert result["situation_context"]["raw_sha256"] is not None
        assert code in next(m["content"] for m in inputs[0] if m.get("name") == "game_situation")
    else:
        assert result["status"] == "degraded"


@pytest.mark.parametrize("length", [400, 1200, 2400, 4800])
@pytest.mark.parametrize("name,leading,trailing", [
    ("alerts", {"id": "critical", "priority": "Critical"}, {"id": "minor", "priority": "Low", "label": "Minor"}),
    ("events", {"id": "new", "created_tick": 10}, {"id": "old", "created_tick": 1, "label": "Old"}),
])
def test_long_native_text_never_reverses_priority_or_recency(length, name, leading, trailing):
    rows = [{**leading, "label": "危" * length}, trailing]
    observation = native(items={name: rows})
    original = observation.to_dict()
    compiled = compile_situation(SituationSnapshot(observation, False, 10))
    view = json.loads(compiled["content"].split("\n", 1)[1])
    selected = view["sections"][name]["items"]
    assert compiled["tokens"] <= 1_000
    assert selected[0]["id"] == leading["id"]
    assert selected[0]["label_truncated"] is True
    assert len(selected[0]["label"]) <= 120
    assert [row["id"] for row in selected] == [row["id"] for row in rows[:len(selected)]]
    assert observation.value == original


def test_oversized_entry_keeps_traceable_native_cues_and_stops_before_skipping_sources():
    rows = [{"id": str(index), "label": "Urgent " * 12, "priority": "Critical" if index == 0 else "Low",
             "large_native_detail": "原始事实" * 1200} for index in range(30)]
    observation = native(items={"alerts": rows})
    compiled = compile_situation(SituationSnapshot(observation, False, 10))
    selected = json.loads(compiled["content"].split("\n", 1)[1])["sections"]["alerts"]
    assert selected["items"][0]["id"] == "0"
    assert selected["items"][0]["priority"] == "Critical"
    assert all(item["details_omitted"] for item in selected["items"])
    assert [item["id"] for item in selected["items"]] == [str(i) for i in range(len(selected["items"]))]
    assert selected["omitted_items"] == len(rows) - len(selected["items"])
    assert compiled["tokens"] <= 1_000
    assert observation.value["response"]["payload"]["data"]["sections"]["alerts"]["items"] == rows


def test_verbose_early_columns_leave_room_for_later_native_leading_entries():
    observation = native(items={
        "threats": [{"id": "hostiles", "label": "Raid", "visible_hostile_count": 20,
                     "large_native_detail": "敌人" * 1800}],
        "conditions": [{"id": "condition", "label": "危" * 120, "description": "天气" * 200}],
        "alerts": [{"id": "urgent", "label": "Critical", "priority": "Critical"}],
        "quests": [{"id": "Quest_1", "label": "Ongoing", "state": "Ongoing", "description": "任务" * 200}],
        "events": [{"id": "newest", "label": "Recent event", "created_tick": 10}],
    })
    compiled = compile_situation(SituationSnapshot(observation, False, 10))
    sections = json.loads(compiled["content"].split("\n", 1)[1])["sections"]
    assert compiled["tokens"] <= 1_000
    assert all(sections[name]["items"] for name in ("threats", "conditions", "alerts", "quests", "events"))
    assert sections["alerts"]["items"][0]["id"] == "urgent"


@pytest.mark.parametrize("quest_state,native_quest", [("EndedSuccess", False), ("Ongoing", True)])
def test_grouped_quest_scope_does_not_apply_native_current_quest_filter_to_event_sources(quest_state, native_quest):
    items = {"events": [{"id": "Letter_1", "quest_id": "Quest_0", "label": "Supply request",
                          "quest_state": quest_state, "created_tick": 10}]}
    if native_quest:
        items["quests"] = [{"id": "Quest_0", "label": "Supply request", "state": quest_state}]
    observation = native(items=items)
    data = observation.value["response"]["payload"]["data"]
    native_scope = "Visible non-dismissed quests in NotYetAccepted or Ongoing state, colony-wide."
    data["sections"]["quests"]["scope"] = native_scope
    data["map"]["source"] = "Verse.Map"
    data["clock"]["source"] = "Verse.TickManager"
    original = observation.to_dict()
    compiled = compile_situation(SituationSnapshot(observation, False, 10))
    view = json.loads(compiled["content"].split("\n", 1)[1])
    quests = view["sections"]["quests"]
    assert "scope" not in quests and quests["native_scope"] == native_scope
    assert quests["group_scope"] == "Linked sources; event-only does not establish current/actionable quests."
    group = quests["items"][0]
    assert group["quest_id"] == "Quest_0"
    events = [fact for fact in group["facts"] if fact["section"] == "events"]
    assert events[0]["id"] == "Letter_1" and events[0]["quest_state"] == quest_state
    assert any(fact["section"] == "quests" for fact in group["facts"]) is native_quest
    assert observation.value == original
    assert "source" not in view["map_at_sample"] and "source" not in view["clock"]
    assert compiled["tokens"] <= 1_000
