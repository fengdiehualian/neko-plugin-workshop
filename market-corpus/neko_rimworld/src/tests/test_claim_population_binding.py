"""Population diagnostics retain scope and never infer it from a shared name."""

import copy

import pytest
from plugin.plugins.neko_rimworld.agent.delivery.claims import build_read_index, check_claims


def observation(result, *, tick=1, sequence=3):
    return {"response": {
        "connection_epoch": "connection", "colony_id": "colony", "load_epoch": "load",
        "game_tick": tick, "snapshot_sequence": sequence,
        "payload": {"status": "ok", "data": {"results": [result]}},
    }}


def population(root, total, names, *, complete, map_id="Map_0", where=None):
    return {
        "from": root,
        "scope": {"args": {"map_id": map_id}, "where": where or {}},
        "matched_count": total, "scan_complete": True, "fields_complete": True,
        "rows": [{"id": "Pawn_" + name, "name": name} for name in names],
        "page": {"offset": 0, "has_more": not complete},
    }


def test_b14_shared_member_does_not_bind_a_narrow_claim_to_broad_partial_population():
    readings = [
        observation(population("spawned_free_colonists", 3, ["Lumi", "Wonder", "Vicky"], complete=True)),
        observation(population("pawns", 101, ["Lumi", "Wonder", "Vicky", "Rat"], complete=False), sequence=7),
    ]
    original = copy.deepcopy(readings)
    diagnostics = check_claims("该3名自由殖民者范围内仅有Lumi显示饥饿念头。", observations=readings)
    assert diagnostics and all(item["binding"] == "unresolved" for item in diagnostics)
    assert all(item["scope_binding"] is None for item in diagnostics)
    assert "101" not in str(diagnostics) and "请先读完" not in str(diagnostics)
    assert readings == original
    index = build_read_index(readings)
    narrow, broad = index["collections"]
    assert narrow["complete"] and not broad["complete"]
    assert not broad.get("superseded")


def test_single_partial_population_is_a_candidate_not_a_proven_prose_binding():
    readings = [observation(population("pawns", 101, ["Lumi"], complete=False))]
    diagnostic = check_claims("Lumi是唯一候选。", observations=readings)[0]
    assert diagnostic["binding"] == "candidate"
    assert diagnostic["scope_binding"]["scopes"][-1]["from"] == "pawns"
    assert "不能仅据此确定" in diagnostic["hint"]
    assert "请先读完" not in diagnostic["hint"]


@pytest.mark.parametrize("difference", ["root", "map", "where", "tick", "owner", "relation"])
def test_equal_numbers_do_not_merge_different_populations_or_samples(difference):
    partial = population("pawns", 3, ["Lumi"], complete=False)
    complete = population("pawns", 3, ["Lumi", "Wonder", "Vicky"], complete=True)
    if difference == "root":
        complete["from"] = "spawned_free_colonists"
    if difference == "map":
        complete["scope"]["args"]["map_id"] = "Map_1"
    if difference == "where":
        complete["scope"]["where"] = {"dead": False}
    if difference == "owner":
        partial = {"id": "Pawn_Lumi", "name": "Lumi", "inventory": partial}
        complete = {"id": "Pawn_Wonder", "name": "Wonder", "inventory": complete}
    if difference == "relation":
        partial["scope"]["relation"] = "Pawn.inventory"
        complete["scope"]["relation"] = "Pawn.equipment"
    readings = [observation(partial), observation(complete, tick=2 if difference == "tick" else 1)]
    index = build_read_index(readings)
    assert len({c["population_key"] for c in index["collections"]}) == 2
    # A complete read of the other population cannot supersede this one.
    assert not next(c for c in index["collections"] if not c["complete"]).get("superseded")


def test_owner_relation_diagnostic_requires_that_owner_in_the_clause():
    relation = population("things", 12, ["Steel"], complete=False)
    relation["scope"]["relation"] = "Pawn.inventory"
    reading = observation({"id": "Pawn_Lumi", "name": "Lumi", "inventory": relation})
    assert check_claims("Wonder的全部12项库存均已读取。", observations=[reading]) == []
    diagnostic = check_claims("Lumi的全部12项库存均已读取。", observations=[reading])[0]
    assert diagnostic["scope_binding"]["subject"] == "Pawn_Lumi"


def test_complete_aggregate_does_not_complete_listing():
    result = population("things", 20, ["Steel"], complete=False)
    result["aggregates"] = {"quantity": {"complete": True, "value": 1170}}
    readings = [observation(result)]
    assert check_claims("总量来自全部20个匹配实例的完整聚合。", observations=readings) == []
    assert check_claims("所有20实例都已逐项检查。", observations=readings)
    collection = build_read_index(readings)["collections"][0]
    assert collection["aggregate_complete"] and not collection["complete"]


@pytest.mark.parametrize("difference", ["tick", "sequence", "owner"])
def test_other_sample_or_owner_aggregate_cannot_license_this_listing(difference):
    partial = population("things", 20, ["Steel"], complete=False)
    aggregate = copy.deepcopy(partial)
    aggregate.pop("rows")
    aggregate["aggregates"] = {"quantity": {"complete": True, "value": 1170}}
    if difference == "owner":
        partial = {"id": "Pawn_Lumi", "name": "Lumi", "inventory": partial}
        aggregate = {"id": "Pawn_Wonder", "name": "Wonder", "inventory": aggregate}
    readings = [observation(partial), observation(
        aggregate, tick=2 if difference == "tick" else 1,
        sequence=4 if difference == "sequence" else 3,
    )]
    index = build_read_index(readings)
    assert not next(c for c in index["collections"] if not c["complete"])["aggregate_complete"]
