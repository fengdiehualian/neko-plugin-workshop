"""Advertised look facets share selections without weakening public queries."""

import copy
import itertools

import pytest
from plugin.plugins.neko_rimworld.agent.world.query_surface import Surface, SurfaceError
from plugin.plugins.neko_rimworld.agent.world.semantic_surface import _compose_blocks, plan_look

from .test_semantic_tools import catalog


def surface():
    return Surface(catalog()["public_query"])


@pytest.mark.parametrize("kind", ["pawn", "pawns", "colonists"])
@pytest.mark.parametrize("aspects", list(itertools.permutations(("identity", "mood", "profile"))))
def test_identity_profile_and_mood_compose_in_any_order(kind, aspects):
    # B14's legal mixed look delegates these three facets to the query planner.
    obj = {"kind": kind, "ids": ["Thing_Human1"], "map": "Map_0"}
    original = copy.deepcopy(obj)
    plan = plan_look(surface(), obj, aspects, 5)
    selected = plan.request["select"]
    assert selected["profile"]["select"]["gender"] is True
    assert selected["profile"]["select"]["race_def_name"] is True
    assert selected["profile"]["select"]["traits"]["limit"] == 5
    assert selected["mood"]["select"]["thought_groups"]["limit"] == 5
    assert selected["id"] is True and selected["name"] is True
    assert "profile.gender" in plan.covers
    assert obj == original
    separate = [plan_look(surface(), obj, (aspect,), 5) for aspect in aspects]
    assert plan.covers == set().union(*(item.covers for item in separate))


def test_same_facet_paths_merge_identical_options_and_overlapping_leaf_fields():
    blocks = [
        {"at": "", "args": {"id": "Thing_Human1"}, "fields": ["profile.gender"]},
        {"at": "profile", "args": {"mode": "current"}, "fields": ["gender", "race_def_name"]},
        {"at": "profile", "args": {"mode": "current"}, "fields": ["gender"]},
    ]
    original = copy.deepcopy(blocks)
    request = surface().compile({"source": "pawn", "blocks": _compose_blocks(blocks)})
    assert request["select"]["profile"] == {
        "args": {"mode": "current"}, "select": {"gender": True, "race_def_name": True}
    }
    assert request["args"] == {"id": "Thing_Human1"}
    assert blocks == original


@pytest.mark.parametrize("option,left,right", [
    ("args", {"mode": "current"}, {"mode": "history"}),
    ("limit", 5, 10),
    ("where", {"gender": {"eq": "Male"}}, {"gender": {"eq": "Female"}}),
])
def test_different_options_at_same_relation_remain_conflicts(option, left, right):
    with pytest.raises(SurfaceError, match="Conflicting aspect option"):
        _compose_blocks([
            {"at": "profile", option: left, "fields": ["gender"]},
            {"at": "profile", option: right, "fields": ["race_def_name"]},
        ])


@pytest.mark.parametrize("reverse", [False, True])
def test_scalar_or_default_selection_cannot_be_combined_with_explicit_children(reverse):
    for whole in ("scalar", "default"):
        blocks = ([{"at": "", "fields": ["profile"]}] if whole == "scalar"
                  else [{"at": "profile", "fields": True}])
        blocks.append({"at": "profile", "fields": ["gender"]})
        if reverse:
            blocks.reverse()
        with pytest.raises(SurfaceError):
            surface().compile({"source": "pawn", "blocks": _compose_blocks(blocks)})


def test_raw_query_compiler_still_rejects_overlapping_fields_and_duplicate_blocks():
    for blocks in (
        [{"at": "", "fields": ["profile.gender"]}, {"at": "profile", "fields": ["gender"]}],
        [{"at": "profile", "fields": ["gender"]}, {"at": "profile", "fields": ["race_def_name"]}],
    ):
        with pytest.raises(SurfaceError, match="Overlapping field|Duplicate block"):
            surface().compile({"source": "pawn", "blocks": blocks})
