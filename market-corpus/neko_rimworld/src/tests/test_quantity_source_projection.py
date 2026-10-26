"""Number-local meanings survive the ordinary JSON model view and raw audit."""

import copy
import json

import pytest
from plugin.plugins.neko_rimworld.agent.context.model_context import make_pruner
from plugin.plugins.neko_rimworld.agent.world.query_surface import Surface
from plugin.plugins.neko_rimworld.agent.world.semantic_surface import normalize_object, plan_count, render

from .test_colony_tools import facts, interaction
from .test_semantic_tools import catalog


@pytest.mark.parametrize("held,fogged", [(True, True), (False, True), (True, False), (False, False)])
@pytest.mark.parametrize("by", [None, "definition"])
def test_physical_counts_keep_flags_meaning_and_all_original_numbers(held, fogged, by):
    surface = Surface(catalog()["public_query"])
    obj = normalize_object({"kind": "things", "category": "Item", "include_held": held, "include_fogged": fogged})
    plan = plan_count(surface, obj, "units", by, 40)
    request = copy.deepcopy(plan.request)
    result = {"from": "things", "type": "Thing", "state": "complete", "fields": "complete", "listing": "complete",
              "scope": {"args": {"include_held": held, "visible_only": not fogged}},
              "matched": 1374, "aggregates": {"records": 1374, "units": 2740},
              "rows": [{"key": {"definition.def_name": "Gun_AssaultRifle"}, "aggregates": {"records": 13, "units": 13}}] if by else [],
              "page": {"has_more": False}}
    native = {"status": "ok", "data": {"sample": {"load_epoch": "load", "game_tick": 420, "sequence": 5}, "results": [result]},
              "observation_id": "physical-count", "issues": []}
    before = copy.deepcopy(native)
    output = render(plan, native)
    view = json.loads(make_pruner().compile({"tool": "count", "ok": True, "data": output}).content)["data"]
    assert view["quantity_basis"] == {"population": "physical_objects", "include_held": held,
                                       "include_fogged": fogged, "availability": "not_established_by_this_read"}
    assert view["measures"] == {"records": 1374, "units": 2740}
    assert view["scope"]["resolved"]["include_held"] is held
    assert view["scope"]["resolved"]["include_fogged"] is fogged
    assert view["sample"] == output["sample"]
    if by:
        assert view["groups"] == output["groups"]
        assert view["groups"][0]["measures"]["units"] == 13
    assert native == before and plan.request == request


@pytest.mark.asyncio
async def test_exact_resource_subset_and_distinct_sources_survive_model_input_and_raw_audit():
    data = facts([{"def_name": "MealSimple", "label": "simple meal", "observed_units": 0,
                   "coverage": "complete_in_scope", "sources": []},
                  {"def_name": "WoodLog", "label": "wood", "observed_units": 37,
                   "coverage": "complete_in_scope", "sources": [
                       {"source": "spawned_ground", "units": 30, "records": 1},
                       {"source": "player_pawn_held", "holder": {"id": "Vicky", "name": "Vicky"}, "units": 5, "records": 1},
                       {"source": "trader_stock", "holder": {"id": "Seller", "channel": "orbital"}, "units": 2, "records": 1}]}])
    data["scope"].update(selection={"mode": "exact_definitions_or_labels", "requested": ["simple meal", "WoodLog"],
                                   "resolved_definitions": ["MealSimple", "WoodLog"], "coverage": "selected_definitions_only"},
                         ownership="physical location/holder is not ownership or availability",
                         excluded=["fogged things", "off-map caravan cargo", "unpaid quest rewards"])
    # Existing unknown/sample fields must not disappear when the header is added.
    data.update(extension={"unknown": None}, stage="current_inventory")
    original = copy.deepcopy(data)
    reader, calls = interaction(data)
    args = {"object": {"kind": "resources"}, "aspects": ["quantity", "distribution"],
            "focus": {"items": ["simple meal", "WoodLog"]}}
    output = await reader.look(args)
    view = json.loads(make_pruner().compile({"tool": "look", "ok": True, "data": output}, arguments=args).content)["data"]
    for key, value in data.items():
        assert view[key] == value
    assert len(calls) == 1 and calls[0]["arguments"]["items"] == ["simple meal", "WoodLog"]
    assert reader.session.observations[0]["response"]["payload"]["data"] == original
    assert data == original


@pytest.mark.asyncio
async def test_stock_market_value_basis_is_local_and_never_relabels_as_quote():
    data = facts([{"id": "Seller", "name": "seller", "channel": "orbital", "stock": {
        "status": "ok", "price_basis": "market_value_reference_not_negotiated_quote", "total": 1,
        "rows": [{"def_name": "WoodLog", "units": 2, "market_value_reference": 32}],
        "page": {"offset": 0, "returned": 1, "has_more": False, "next_offset": None}}}])
    original = copy.deepcopy(data)
    reader, calls = interaction(data)
    args = {"object": {"kind": "traders", "id": "Seller"}, "aspects": ["stock"], "focus": {"items": ["WoodLog"]}}
    output = await reader.look(args)
    view = json.loads(make_pruner().compile({"tool": "look", "ok": True, "data": output}, arguments=args).content)["data"]
    assert view["rows"][0]["stock"]["price_basis"] == "market_value_reference_not_negotiated_quote"
    assert view["rows"][0]["stock"]["rows"] == original["rows"][0]["stock"]["rows"]
    assert len(calls) == 1 and calls[0]["operation"] == "inspect_trade"
    assert reader.session.observations[0]["response"]["payload"]["data"] == original


def test_pawn_count_does_not_receive_item_stock_header():
    surface = Surface(catalog()["public_query"])
    plan = plan_count(surface, normalize_object({"kind": "colonists"}), "records", None, 0)
    native = {"status": "ok", "issues": [], "data": {"sample": {}, "results": [
        {"state": "complete", "fields": "complete", "listing": "complete", "matched": 3,
         "scope": {}, "aggregates": {"records": 3}, "rows": [], "page": {"has_more": False}}]}}
    output = render(plan, native)
    assert output["measures"] == {"records": 3} and "quantity_basis" not in output
