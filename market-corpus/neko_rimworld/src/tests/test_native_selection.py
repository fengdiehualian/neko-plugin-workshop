"""Selection-to-state composition for partially observed identities."""

import copy
from types import SimpleNamespace

import pytest
from plugin.plugins.neko_rimworld.agent.world.activity import SemanticActivityDataSource
from plugin.plugins.neko_rimworld.agent.world.look_tools import read_look

from .test_object_tools import Reader


@pytest.mark.asyncio
async def test_partial_colonist_identity_preserves_rows_and_reads_only_known_ids():
    # B21: selection enumerated all three colonists, but the read deadline
    # expired before the first identity's fields. Its marker is not an ID.
    missing = {"state": "unavailable", "reason": "deadline_exceeded"}
    rows = [
        {"id": missing, "name": missing, "map_id": missing},
        {"id": "Thing_Human981", "name": "Wonder", "map_id": "Map_0"},
        {"id": "Thing_Human984", "name": "Vicky", "map_id": "Map_0"},
    ]
    original = copy.deepcopy(rows)
    adapter = Reader(rows)
    result = await read_look(adapter, {
        "object": {"kind": "colonists"},
        "aspects": ["identity", "skills", "gear", "work"],
        "focus": {"skills": ["Shooting"]},
        "detail": 20,
    })
    states = [args for name, args in adapter.calls if name == "inspect_state"]
    assert len(states) == 1
    assert states[0]["targets"] == [
        {"id": "Thing_Human981", "map": "Map_0"},
        {"id": "Thing_Human984", "map": "Map_0"},
    ]
    assert result["status"] == "partial"
    assert len(result["facts"]) == 3
    assert result["facts"][0]["object"]["id"] == missing
    assert result["facts"][0]["state"]["reason"] == "identity_unavailable"
    assert "skills" not in result["facts"][0]["facts"]
    assert result["facts"][1]["facts"]["skills"]["skills_total"] == 1
    assert result["coverage"]["state_facets_read_successfully"] is False
    assert result["work_assignments"]["complete_for_selection"] is False
    assert rows == original


@pytest.mark.asyncio
async def test_unread_identity_does_not_issue_an_invalid_or_automatic_recovery_read():
    adapter = Reader([{"id": {"state": "unavailable", "reason": "deadline_exceeded"}}])
    result = await read_look(adapter, {
        "object": {"kind": "colonists"}, "aspects": ["skills"],
    })
    assert [name for name, _ in adapter.calls] == ["query"]
    assert result["status"] == "partial"
    assert result["facts"][0]["state"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_returned_weapon_id_needs_no_name_rediscovery_for_state_or_activity():
    # B11 already had this ID; the unqualified display name 'assault rifle'
    # was not the actual instance label 'assault rifle (normal)'.
    ref = {"kind": "thing", "id": "Thing_Gun_AssaultRifle50845", "map": "Map_0"}
    adapter = Reader([])
    result = await read_look(adapter, {"object": ref, "aspects": ["state"]})
    assert [name for name, _ in adapter.calls] == ["inspect_state"]
    assert adapter.calls[0][1]["targets"] == [ref]
    assert result["facts"][0]["object"]["id"] == ref["id"]

    # Resolving an explicit native reference is local; the eventual requested
    # activity performs its current reads through the existing native path.
    interaction = SimpleNamespace(session=SimpleNamespace(observations=[]))
    resolved = await SemanticActivityDataSource(interaction)._resolve(ref, "destination")
    assert resolved == ref
