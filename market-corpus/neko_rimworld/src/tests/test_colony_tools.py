"""Five-domain normal flows through native binding, model input and delivery.

Scripted models verify dispatch and retained evidence without provider calls;
they are not evidence of live model decision quality or token savings.
"""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from plugin.plugins.neko_rimworld.agent.context.model_context import make_pruner
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger, AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession
from plugin.plugins.neko_rimworld.agent.runtime.tools import ToolRegistry
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import semantic_interaction_factory
from plugin.plugins.neko_rimworld.game.observation import Observation
from plugin.plugins.neko_rimworld.game.protocol import QUERY_RESULT, Payload, SessionDescriptor, make_envelope
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.message_contracts import ToolCall

from .test_agent import FakeLogger, settings, turn
from .test_semantic_tools import Session, _update
from .test_semantic_tools import catalog as base_catalog

OPERATIONS = ("inspect_planet", "inspect_factions", "inspect_quests", "inspect_trade", "inspect_research", "evaluate_journey", "evaluate_trade", "inspect_resources")


def catalog():
    value = base_catalog()
    value["colony_state"] = json.loads((Path(__file__).parent / "fixtures/colony_state_catalog.json").read_text(encoding="utf-8-sig"))
    return value


def earlier_catalog():
    # Exact fixture from plugin 4794078 / Mod d6033ea, before additive parameters.
    value = base_catalog()
    value["colony_state"] = json.loads((Path(__file__).parent / "fixtures/colony_state_catalog_d6033ea.json").read_text(encoding="utf-8-sig"))
    return value


def native(request, data, *, epoch="3" * 32):
    envelope = make_envelope(QUERY_RESULT, "1" * 32, 1,
                             payload=Payload(status="ok", data=data, issues=[]),
                             session=SessionDescriptor("2" * 32, epoch, 420, 1), request_id="4" * 32)
    return Observation.from_response(request["operation"], request["arguments"], envelope)


def facts(rows):
    return {"format": "colony_state.v1", "read_only": True,
            "sample": {"colony_id": "2" * 32, "load_epoch": "3" * 32, "started_tick": 420, "finished_tick": 420, "atomic": False},
            "scope": {"population": "selected public objects"}, "total": len(rows), "rows": rows,
            "page": {"offset": 0, "returned": len(rows), "has_more": False, "next_offset": None}}


def interaction(data=None, source=None):
    calls = []
    source = source or catalog()
    native_schemas = {row["operation"]: row["parameters"] for row in source.get("colony_state", {}).get("operations", [])}

    async def query(request):
        Draft202012Validator(native_schemas[request["operation"]]).validate(request["arguments"])
        calls.append(copy.deepcopy(request))
        return native(request, data or facts([]))

    setup = make_game_tools(source, query)
    reader = semantic_interaction_factory(setup, Session(), _update)
    return reader, calls


def test_additive_publication_does_not_expose_internal_reads_or_require_discovery():
    reader, calls = interaction()
    tools = {t.name: t for t in reader.tools()}
    assert {"look", "count", "evaluate_journey", "evaluate_trade"} <= tools.keys()
    assert not {"inspect_planet", "inspect_factions", "inspect_quests", "inspect_trade", "inspect_research"} & tools.keys()
    assert not any("discover" in name for name in tools)
    assert calls == [] and reader.session.observations == []
    before, _ = interaction(source=base_catalog())
    old = {t.name: t for t in before.tools()}
    assert tools.keys() - old.keys() == {"evaluate_journey", "evaluate_trade"}
    for name in old.keys() - {"look"}:
        assert tools[name].schema() == old[name].schema()
    # A connection without the new capability still has the valid base surface.
    assert "colony_state" not in before.setup.directory
    assert "evaluate_trade" not in old and "evaluate_journey" not in old
    registry = ToolRegistry()
    for tool in reader.tools():
        registry.register(tool)
    for tool in registry.schemas():
        Draft202012Validator.check_schema(tool["function"]["parameters"])


def test_reference_schema_requires_exactly_one_id_or_name():
    reader, _ = interaction()
    tools = {t.name: t for t in reader.tools()}
    for tool, field in (("evaluate_journey", "caravan"), ("evaluate_trade", "trader")):
        validator = Draft202012Validator(tools[tool].parameters["properties"][field])
        assert validator.is_valid({"id": "native-id"})
        assert validator.is_valid({"name": "采购队"})
        assert not validator.is_valid({"id": "native-id", "name": "采购队"})
        assert not validator.is_valid({})


def test_look_description_advertises_business_domains_only_when_supported():
    reader, _ = interaction()
    before, _ = interaction(source=base_catalog())
    description = next(tool.description for tool in reader.tools() if tool.name == "look")
    base_description = next(tool.description for tool in before.tools() if tool.name == "look")
    assert description.startswith(base_description)
    for phrase in ("world places/caravans", "faction relations", "quest requirements/rewards/deadlines",
                   "merchant stock", "research progress/prerequisites", 'object.kind="traders",aspects=["stock"]',
                   "count totals alone do not identify seller or ownership"):
        assert phrase in description and phrase not in base_description


@pytest.mark.asyncio
@pytest.mark.parametrize("obj,aspects,focus,expected", [
    ({"kind": "world"}, ["places"], {}, {"operation": "inspect_planet", "kind": "world"}),
    ({"kind": "world_object", "category": "settlement", "near": {"kind": "map", "map": "current"}}, ["identity", "location"], {}, {"operation": "inspect_planet", "kind": "world_objects", "category": "settlement", "near": {"kind": "map", "map": "current"}}),
    ({"kind": "world_objects", "category": "settlement"}, ["identity", "location"], {"faction": {"id": "Faction_6"}}, {"operation": "inspect_planet", "kind": "world_objects", "category": "settlement", "faction": "Faction_6"}),
    ({"kind": "caravan", "name": "采购队"}, ["location", "route", "supplies"], {}, {"operation": "inspect_planet", "kind": "caravans", "names": ["采购队"]}),
    ({"kind": "faction", "names": ["贸易联盟"]}, ["relations", "settlements"], {}, {"operation": "inspect_factions", "names": ["贸易联盟"]}),
    ({"kind": "quest", "id": "Quest42"}, ["requirements", "rewards", "deadlines"], {}, {"operation": "inspect_quests", "ids": ["Quest42"]}),
    ({"kind": "trader", "name": "研究贸易商船", "map": "Map_0"}, ["stock"], {"items": ["ComponentIndustrial"]}, {"operation": "inspect_trade", "names": ["研究贸易商船"], "map": "Map_0", "items": ["ComponentIndustrial"]}),
    ({"kind": "research", "names": ["MicroelectronicsBasics"]}, ["progress", "requirements", "unlocks"], {}, {"operation": "inspect_research", "names": ["MicroelectronicsBasics"]}),
    ({"kind": "resources", "map": "Map_0"}, ["quantity", "distribution"], {"items": ["ComponentIndustrial"]}, {"operation": "inspect_resources", "map": "Map_0", "items": ["ComponentIndustrial"]}),
    ({"kind": "research"}, ["requirements", "unlocks"], {"items": ["ComponentIndustrial"]}, {"operation": "inspect_research", "items": ["ComponentIndustrial"]}),
])
async def test_direct_business_reads_preserve_selection_and_native_evidence(obj, aspects, focus, expected):
    data = facts([{"id": "selected", "name": "目标", "location": {"layer_id": 0, "tile_id": 12},
                   "route": {"state": "moving"}, "supplies": {"food_days": 3.0}, "meaning": "Current selected-object facts."}])
    reader, calls = interaction(data)
    args = {"object": obj, "aspects": aspects, "focus": focus, "limit": 10, "offset": 2}
    schema = next(t.parameters for t in reader.tools() if t.name == "look")
    Draft202012Validator(schema).validate(args)
    result = await reader.look(args)
    operation = expected["operation"]
    expected_args = {key: value for key, value in expected.items() if key != "operation"}
    assert calls == [{"operation": operation, "arguments": {**expected_args, "aspects": aspects, "limit": 10, "offset": 2}}]
    assert result["rows"] == data["rows"] and result["sample"] == data["sample"]
    assert reader.session.observations[0]["response"]["payload"]["data"] == data
    view = json.loads(make_pruner().compile({"tool": "look", "ok": True, "data": result}, arguments=args).content)
    assert view["data"]["rows"] == data["rows"]  # id+location must not discard route/supplies.


@pytest.mark.asyncio
async def test_both_computations_are_direct_named_reads_with_one_audited_observation_each():
    reader, calls = interaction(facts([{"currency": "Silver", "buy_total": 250, "sell_total": 50}]))
    tools = {t.name: t for t in reader.tools()}
    journey = {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}, {"id": "Settlement2"}], "include": ["supplies"]}
    trade = {"trader": {"name": "研究贸易商船", "map": "current"}, "negotiator": {"name": "Lumi"}, "buy": [{"item": "工业零部件", "quantity": 10}], "sell": [{"item": "木材", "quantity": 100}]}
    await tools["evaluate_journey"].handler(journey)
    await tools["evaluate_trade"].handler(trade)
    assert calls == [{"operation": "evaluate_journey", "arguments": journey}, {"operation": "evaluate_trade", "arguments": trade}]
    assert len(reader.session.observations) == 2
    await tools["evaluate_journey"].handler({"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}]})
    assert "include" not in calls[-1]["arguments"]


@pytest.mark.asyncio
async def test_loaded_journey_binds_requested_goods_in_one_read():
    reader, calls = interaction()
    tool = next(t for t in reader.tools() if t.name == "evaluate_journey")
    args = {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}],
            "cargo_add": [{"item": "WoodLog", "quantity": 40}],
            "cargo_remove": [{"item": "Silver", "quantity": 100}], "include": ["supplies"]}
    Draft202012Validator(tool.parameters).validate(args)
    await tool.handler(args)
    assert calls == [{"operation": "evaluate_journey", "arguments": args}]
    assert len(reader.session.observations) == 1


@pytest.mark.asyncio
async def test_supported_earlier_catalog_keeps_existing_journey_project_and_stock_reads():
    reader, calls = interaction(source=earlier_catalog())
    tools = {t.name: t for t in reader.tools()}
    journey = {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}], "include": ["supplies"]}
    await tools["evaluate_journey"].handler(journey)
    await reader.look({"object": {"kind": "research", "name": "Fabrication"}, "aspects": ["requirements"]})
    await reader.look({"object": {"kind": "traders", "name": "商船"}, "aspects": ["stock"], "focus": {"items": ["ComponentIndustrial"]}})
    assert [c["operation"] for c in calls] == ["evaluate_journey", "inspect_research", "inspect_trade"]
    assert calls[0]["arguments"] == journey
    assert "items" not in calls[1]["arguments"]
    assert calls[2]["arguments"]["items"] == ["ComponentIndustrial"]
    assert "cargo_add" not in tools["evaluate_journey"].parameters["properties"]
    assert "cargo_remove" not in tools["evaluate_journey"].parameters["properties"]
    assert "loaded travel" not in tools["evaluate_journey"].description
    focus = tools["look"].parameters["properties"]["focus"]
    assert "items" in focus["properties"]  # Still supported for trader stock.
    assert "faction" not in focus["properties"]
    assert "Research product" not in focus["description"]
    assert "focus.faction" not in focus["description"]
    assert "resource quantities by source" not in tools["look"].description
    for tool in tools.values():
        Draft202012Validator.check_schema(tool.parameters)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("evaluate_journey", {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}], "cargo_add": [{"item": "WoodLog", "quantity": 40}]}),
    ("evaluate_journey", {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}], "cargo_remove": [{"item": "WoodLog", "quantity": 40}]}),
    ("look", {"object": {"kind": "research"}, "aspects": ["requirements"], "focus": {"items": ["ComponentIndustrial"]}}),
    ("look", {"object": {"kind": "world_objects"}, "aspects": ["identity"], "focus": {"faction": {"id": "Faction_6"}}}),
    ("look", {"object": {"kind": "resources"}, "aspects": ["quantity"], "focus": {"items": ["WoodLog"]}}),
])
async def test_earlier_catalog_does_not_publish_or_dispatch_unsupported_additions(name, args):
    from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError
    reader, calls = interaction(source=earlier_catalog())
    tool = next(t for t in reader.tools() if t.name == name)
    assert not Draft202012Validator(tool.parameters).is_valid(args)
    with pytest.raises(ToolExecutionError):
        await tool.handler(args)
    assert calls == [] and reader.session.observations == []


@pytest.mark.asyncio
async def test_resource_selection_is_explicit_and_product_research_is_unambiguous():
    from plugin.plugins.neko_rimworld.agent.world.query_surface import SurfaceError
    reader, calls = interaction()
    schema = next(t.parameters for t in reader.tools() if t.name == "look")
    args = {"object": {"kind": "resources"}, "aspects": ["distribution"]}
    assert not Draft202012Validator(schema).is_valid(args)
    with pytest.raises(SurfaceError, match="requires focus.items"):
        await reader.look(args)
    with pytest.raises(SurfaceError, match="not both"):
        await reader.look({"object": {"kind": "research", "name": "Fabrication"},
                           "aspects": ["requirements"], "focus": {"items": ["ComponentIndustrial"]}})
    assert calls == []


def test_count_distinguishes_group_limit_from_instance_examples():
    reader, _ = interaction()
    description = next(t.description for t in reader.tools() if t.name == "count")
    assert "With by, detail limits groups, not instance examples" in description


@pytest.mark.asyncio
async def test_caravan_identity_route_keeps_native_current_location_in_model_view():
    row = {"id": "Caravan_7", "name": "采购队", "kind": "caravan",
           "location": {"layer_id": 0, "tile_id": 123},
           "members": [{"id": "Thing_Human1", "name": "Lumi"}],
           "route": {"moving": True, "paused": False, "night_resting": False,
                     "mass_usage": 20.0, "mass_capacity": 35.0, "immobilized_by_mass": False,
                     "destination": {"layer_id": 0, "tile_id": 124},
                     "travel_ticks_estimate": 12000, "travel_days_estimate": 0.2}}
    reader, _ = interaction(facts([row]))
    args = {"object": {"kind": "caravans", "name": "采购队"}, "aspects": ["identity", "route"]}
    result = await reader.look(args)
    view = json.loads(make_pruner().compile({"tool": "look", "ok": True, "data": result}, arguments=args).content)
    assert view["data"]["rows"] == [row]
    assert view["data"]["rows"][0]["location"] != view["data"]["rows"][0]["route"]["destination"]


@pytest.mark.asyncio
async def test_selected_trader_stock_continues_with_nested_page_offset():
    reader, calls = interaction()

    async def query(request):
        calls.append(copy.deepcopy(request))
        offset = request["arguments"]["offset"]
        row = {"id": "TradeShip_1", "name": "研究贸易商船", "channel": "orbital",
               "stock": {"status": "ok", "total": 3,
                         "rows": [{"def_name": "WoodLog" if offset == 0 else "ComponentIndustrial", "units": 40}],
                         "page": {"offset": offset, "limit": 1, "returned": 1, "has_more": offset < 2, "next_offset": offset + 1 if offset < 2 else None}}}
        data = facts([row])
        data["scope"]["paging"] = "selected_trader_stock"
        return native(request, data)

    # Bind by operation name rather than assuming catalog tool order.
    reader.original["inspect_trade"] = next(t for t in make_game_tools(catalog(), query).tools if t.name == "inspect_trade")
    args = {"object": {"kind": "trader", "name": "研究贸易商船"}, "aspects": ["stock"], "limit": 1}
    first = await reader.look(args)
    continuation = first["rows"][0]["stock"]["page"]["next_offset"]
    second = await reader.look({**args, "offset": continuation})
    assert [c["arguments"]["offset"] for c in calls] == [0, 1]
    assert first["rows"][0]["id"] == second["rows"][0]["id"] == "TradeShip_1"
    assert second["rows"][0]["stock"]["rows"][0]["def_name"] == "ComponentIndustrial"
    spec = next(t for t in reader.tools() if t.name == "look")
    assert "stock.page.next_offset" in spec.parameters["properties"]["focus"]["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unavailable", "incomplete"])
async def test_successful_quote_read_preserves_unavailable_or_incomplete_business_status(status):
    data = facts([])
    data.update(status=status, currency="Silver")
    data["scope"] = {"trader": {"id": "TradeShip_1", "name": "研究贸易商船", "channel": "orbital"},
                     "negotiator": {"id": "Thing_Human1", "name": "Lumi"}}
    if status == "unavailable":
        data["blockers"] = ["No usable reachable communications console for this negotiator."]
    else:
        data["rows"] = [{"item": "ComponentIndustrial", "player_direction": "buy", "requested_units": 10,
                         "available_units": 4, "status": "insufficient_units"}]
        data["total"] = 1
        data["page"].update(returned=1)
    reader, _ = interaction(data)
    tools = {tool.name: tool for tool in reader.tools()}
    args = {"trader": {"name": "研究贸易商船"}, "negotiator": {"name": "Lumi"},
            "buy": [{"item": "ComponentIndustrial", "quantity": 10}]}
    result = await tools["evaluate_trade"].handler(args)
    view = json.loads(make_pruner().compile({"tool": "evaluate_trade", "ok": True, "data": result}, arguments=args).content)
    assert view["data"]["status"] == status
    assert view["data"]["read_status"] == "ok"
    assert "settlement" not in view["data"]
    if status == "unavailable":
        assert view["data"]["blockers"] == data["blockers"]
    else:
        assert view["data"]["rows"] == data["rows"]
    assert reader.session.observations[0]["response"]["payload"]["data"]["status"] == status


@pytest.mark.asyncio
async def test_world_identity_change_uses_existing_fatal_invalidation():
    calls = []

    async def query(request):
        calls.append(request)
        return native(request, facts([]), epoch=("3" if len(calls) == 1 else "5") * 32)

    from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError
    reader = semantic_interaction_factory(make_game_tools(catalog(), query), Session(), _update)
    await reader.look({"object": {"kind": "research"}, "aspects": ["progress"]})
    with pytest.raises(ToolExecutionError) as error:
        await reader.look({"object": {"kind": "quests"}, "aspects": ["deadlines"]})
    assert error.value.code == "world_changed" and not error.value.recoverable
    assert len(reader.session.observations) == 1


class Script:
    def __init__(self, turns):
        self.turns = list(turns)
        self.inputs = []

    async def complete(self, messages, tools, *, timeout):
        self.inputs.append(copy.deepcopy({"messages": messages, "tools": tools}))
        return self.turns.pop(0)


def call(name, args, index):
    return turn("", ToolCall(str(index), name, json.dumps(args, ensure_ascii=False)))


FLOWS = [
    ("装40木材再去采购据点是否赶得上？本地还有多少木材？", [
        ("look", {"object": {"kind": "resources", "map": "current"}, "aspects": ["quantity", "distribution"], "focus": {"items": ["WoodLog"]}}, "inspect_resources", [
            {"def_name": "WoodLog", "label": "木材", "observed_units": 80, "coverage": "complete_in_scope",
             "sources": [{"source": "spawned_ground", "units": 40, "records": 2},
                         {"source": "trader_stock", "holder": {"id": "Ship1", "name": "商船", "channel": "orbital"}, "units": 40, "records": 1}]}]),
        ("evaluate_journey", {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}], "cargo_add": [{"item": "WoodLog", "quantity": 40}], "include": ["supplies"]}, "evaluate_journey", [
            {"destination": {"id": "Settlement1", "name": "采购据点"}, "travel_days_estimate": 1.1,
             "cargo_scenario": {"status": "ok", "travel_days_estimate": 1.4, "mass_usage": 26.75,
                                "food_basis": "current_food_only"}}]),
    ]),
    ("木材补给还剩多久，采购队去任务地点是否赶得上？", [
        ("look", {"object": {"kind": "quest", "name": "木材补给"}, "aspects": ["requirements", "deadlines"]}, "inspect_quests", [{"id": "Quest42", "name": "木材补给", "requirements": [{"item": "WoodLog", "quantity": 40}], "deadlines": [{"kind": "delivery", "remaining_days": 3}]}]),
        ("evaluate_journey", {"caravan": {"name": "采购队"}, "destinations": [{"name": "采购据点"}], "include": ["supplies"]}, "evaluate_journey", [{"destination": {"id": "Settlement1", "name": "采购据点"}, "travel_days_estimate": 1.2, "food_days": 2.8}]),
    ]),
    ("工业零部件的研究条件是什么，商船有多少，让Lumi买10个卖100木材多少钱？", [
        ("look", {"object": {"kind": "research", "name": "MicroelectronicsBasics"}, "aspects": ["progress", "requirements"]}, "inspect_research", [{"id": "MicroelectronicsBasics", "progress": {"real": 500, "apparent": 500}, "requirements": {"status": "available"}}]),
        ("look", {"object": {"kind": "trader", "name": "研究贸易商船"}, "aspects": ["stock"], "focus": {"items": ["ComponentIndustrial"]}}, "inspect_trade", [{"id": "Ship1", "stock": [{"item": "ComponentIndustrial", "quantity": 20}]}]),
        ("evaluate_trade", {"trader": {"name": "研究贸易商船"}, "negotiator": {"name": "Lumi"}, "buy": [{"item": "ComponentIndustrial", "quantity": 10}], "sell": [{"item": "WoodLog", "quantity": 100}]}, "evaluate_trade", [{"currency": "Silver", "player_buy_total": 250, "player_sell_total": 50, "player_net_payment": 200, "rounding": "native"}]),
    ]),
    ("自产零部件需要什么研究条件？", [
        ("look", {"object": {"kind": "research"}, "aspects": ["requirements", "unlocks"], "focus": {"items": ["ComponentIndustrial"]}}, "inspect_research", [
            {"id": "Fabrication", "requirements": {"can_start_now": False,
             "bench_conditions": [{"bench": {"id": "Bench1", "def": "HiTechResearchBench"},
                                    "required_facilities_satisfied": False}], "suitable_benches": []}}]),
    ]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["standalone", "prepared"])
@pytest.mark.parametrize("question,flow", FLOWS)
async def test_integrated_flows_keep_actual_messages_native_evidence_and_full_delivery(mode, question, flow):
    reads, observations, reports = [], [], []

    async def query(request):
        index = len(reads)
        assert request["operation"] == flow[index][2]
        schemas = {row["operation"]: row["parameters"] for row in catalog()["colony_state"]["operations"]}
        Draft202012Validator(schemas[request["operation"]]).validate(request["arguments"])
        reads.append(copy.deepcopy(request))
        observation = native(request, facts(flow[index][3]))
        observations.append(observation.to_dict())
        return observation

    async def provide():
        return make_game_tools(catalog(), query)

    async def save(record):
        reports.append(record)
        return {"report_id": record["report_id"]}

    detail = "\n".join(json.dumps(step[3], ensure_ascii=False) for step in flow)
    model = Script([*(call(name, args, i) for i, (name, args, _, _) in enumerate(flow)),
                    call("submit_response", {"answer": "所问事实已核实，计算使用当前样本。", "detail": detail}, "submit")])
    runtime = AgentRuntime(tools_provider=provide, report_sink=save, audit_registry=AgentAuditRegistry(), logger=FakeLogger(), model_factory=lambda _: model)
    if mode == "standalone":
        result = await runtime.run(question, settings())
    else:
        session = AgentRunSession.create(ledger=AgentAuditLedger(question), question=question,
                                        context_messages=({"role": "user", "content": "Previous consultation; not current facts."},))
        result = await runtime.execute(session, settings())
    assert result["status"] == "ok"
    assert len(reads) == len(flow) and result["observations"] == observations
    assert reports[0]["observations"] == observations
    assert detail in reports[0]["full_text"]
    names = {tool["function"]["name"] for tool in model.inputs[0]["tools"]}
    assert {"look", "evaluate_journey", "evaluate_trade"} <= names
    assert not set(OPERATIONS[:5]) & names
    for index, (_, args, _, rows) in enumerate(flow):
        schema = next(t["function"]["parameters"] for t in model.inputs[index]["tools"] if t["function"]["name"] == flow[index][0])
        Draft202012Validator(schema).validate(args)
        tools = [m for m in model.inputs[index + 1]["messages"] if m.get("role") == "tool"]
        view = json.loads(tools[-1]["content"])
        assert view["ok"] and view["data"]["rows"] == rows
        assert view["data"]["sample"]["atomic"] is False
    assert not any(m.get("name") == "game_situation" for m in model.inputs[0]["messages"])


@pytest.mark.asyncio
async def test_no_demand_does_not_add_reads_background_or_prompt_policy(monkeypatch):
    monkeypatch.setattr(AgentAuditLedger, "elapsed_seconds", property(lambda _: 0.0))
    models = []
    for source in (base_catalog(), earlier_catalog(), catalog()):
        async def query(_):
            raise AssertionError("No new domain was requested")

        async def provide():
            return make_game_tools(source, query)

        model = Script([call("submit_response", {"answer": "No current game facts were needed."}, "submit")])
        models.append(model)
        runtime = AgentRuntime(tools_provider=provide, audit_registry=AgentAuditRegistry(), logger=FakeLogger(), model_factory=lambda _: model)
        result = await runtime.run("Explain the general concept without checking the game.", settings())
        assert result["observations"] == []
    assert all(model.inputs[0]["messages"] == models[0].inputs[0]["messages"] for model in models)


@pytest.mark.asyncio
async def test_ordinary_existing_look_keeps_identical_native_work_and_facts():
    calls, results = [], []
    for source in (base_catalog(), catalog()):
        batch = []

        async def query(request):
            batch.append(copy.deepcopy(request))
            assert request["operation"] == "query"
            data = {"sample": {"load_epoch": "3" * 32, "game_tick": 420, "sequence": 1},
                    "results": [{"from": "game", "type": "GameState", "scope": {}, "state": "complete", "fields": "complete", "listing": "complete", "matched": 1,
                                 "rows": [{"paused": True, "current_map_id": "Map_0"}], "page": {"has_more": False}}]}
            return native(request, data)

        reader = semantic_interaction_factory(make_game_tools(source, query), Session(), _update)
        result = await reader.look({"object": {"kind": "game"}, "aspects": ["identity"]})
        result.pop("observation_id")
        calls.append(batch)
        results.append(result)
    assert len(calls[0]) == 1 and calls[0] == calls[1]
    assert results[0] == results[1]
    assert results[1]["facts"][0]["facts"]["identity"]["object"]["paused"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("args,rows,extra", [
    ({"object": {"kind": "quests", "id": "Quest_1"}, "aspects": ["rewards"]},
     [{"id": "Quest_1", "rewards": [{"lifecycle": "offered_not_delivered", "items": [{"def": "ComponentIndustrial", "units": 8}]}]}], {}),
    ({"object": {"kind": "research"}, "aspects": ["requirements"], "focus": {"items": ["ComponentIndustrial"]}},
     [{"id": "Fabrication", "requirements": {"bench_components": {"matching_buildings": [{"id": "Bench1", "def": "HiTechResearchBench", "required_facilities": [{"def": "MultiAnalyzer", "existing_on_map": 0, "linked": False, "active_linked": False}]}]}}}],
     {"production_paths": [{"item": {"def": "ComponentIndustrial"}, "recipes": [{"recipe": {"def": "Make_ComponentIndustrial"}, "workbenches": [{"def": "FabricationBench"}], "research": [{"def": "Fabrication"}]}]}]}),
    ({"object": {"kind": "resources"}, "aspects": ["distribution"], "focus": {"items": ["WoodLog"]}}, [],
     {"status": "unavailable", "selection": [{"item": "WoodLog", "status": "incomplete"}], "coverage": {"complete": False, "reason": "read_deadline"}}),
])
async def test_decision_bindings_survive_projection_and_raw_audit(args, rows, extra):
    data = {**facts(rows), **extra}
    reader, _ = interaction(data)
    result = await reader.look(args)
    view = json.loads(make_pruner().compile({"tool": "look", "ok": True, "data": result}, arguments=args).content)
    for key in data:
        assert view["data"][key] == data[key]
    assert reader.session.observations[0]["response"]["payload"]["data"] == data
