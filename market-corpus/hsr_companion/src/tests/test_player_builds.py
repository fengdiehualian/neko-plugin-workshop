"""D2 parallel inputs: offline save, UID reuse, protected edits, formal reads."""

import asyncio
import inspect
import json
import sqlite3
import tomllib
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from plugin.plugins.hsr_companion.data.catalog import normalize_lookup_text
from plugin.plugins.hsr_companion.runtime.player_builds import (
    STORE_KEY,
    combined_roster,
    player_build_service,
    selection_options,
)
from plugin.plugins.hsr_companion.runtime.uid_import import STORE_KEY as UID_KEY
from plugin.plugins.hsr_companion.tests.test_uid_import import NOW, UID, avatar, payload, setup_service
from plugin.sdk.plugin import Err, PluginStore, SdkError, unwrap_or

ROOT = Path(__file__).resolve().parents[1]
FORM = "character_form.1503"
CONE = {"id": "light_cone.23055", "level": 80, "superimposition": 1}
PANEL = {"hp": "3311", "atk": "1754", "def": "3132", "spd": "143", "crit_rate": "8.2", "crit_dmg": "80.4"}


def fixture(tmp_path):
    owner, values, sent, uid, clock, calls = setup_service(tmp_path)
    database = tmp_path / "catalog.sqlite"
    records = [
        {"id": "light_cone.23055", "entity_type": "light_cone", "name": "献给明日的色彩", "path": "Elation"},
        {"id": "light_cone.20000", "entity_type": "light_cone", "name": "锋镝", "path": "Rogue"},
        {"id": "relic_set.133", "entity_type": "relic_set", "name": "戏梦点星的伶人", "desc": ["二件", "四件"]},
        {"id": "relic_set.132", "entity_type": "relic_set", "name": "应天涉远的卜者", "desc": ["二件", "四件"]},
        {"id": "relic_set.301", "entity_type": "relic_set", "name": "太空封印站", "desc": ["二件"]},
    ]
    for raw_id in (1501, 1503, 1504, 1208, 1306):
        role = next(r for r in owner._characters.records if str(raw_id) in map(str, r.get("source_ids", [])))
        records.append(
            {
                "id": f"character_form.{raw_id}",
                "entity_type": "character_form",
                "name": role["name"],
                "path_id": "Elation",
            }
        )
    records.extend(owner._catalog.records)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE records(id TEXT PRIMARY KEY, entity_type TEXT, name TEXT, name_key TEXT, search_text TEXT, payload_json TEXT)"
        )
        connection.executemany(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            [
                (
                    r["id"],
                    r["entity_type"],
                    r["name"],
                    normalize_lookup_text(r["name"]),
                    normalize_lookup_text(r["name"]),
                    json.dumps(r, ensure_ascii=False),
                )
                for r in records
            ],
        )
    owner._catalog.database_path = database
    return owner, values, sent, uid, clock, calls


async def seed(uid):
    staged = await uid.stage(UID, True)
    await uid.confirm(staged["preview"]["preview_id"])


async def save(owner, changes, form=FORM):
    roster = await player_build_service(owner).roster(editing=True)
    rows = roster["characters"]
    old = next((r for r in rows if r["form_id"] == form), {})
    return unwrap_or(
        await owner.save_manual_build(form, changes, old.get("edit_revision", roster["new_character_revision"])), {}
    )["player_build_save"]


def test_offline_new_character_partial_panel_and_formal_tool(tmp_path):
    owner, values, sent, _, _, calls = fixture(tmp_path)
    before = deepcopy(values)

    async def run():
        result = await save(owner, {"level": 80, "eidolon": 0, "light_cone": CONE, "panel": PANEL})
        assert result["status"] == "saved"
        tool = await owner.hsr_get_player_context(character_names=["真珠"])
        row = tool["imported_roster"]["characters"][0]
        assert row["name"] == "真珠" and row["light_cone"]["name"] == "献给明日的色彩"
        assert row["panel"]["values"] == PANEL and row["panel"]["state"] == "provided"
        assert row["panel"]["includes_equipment_bonuses"] and not row["visually_confirmed"]
        assert "真珠" in tool["profile"]["owned_characters"]
        assert not row["relics"] and not row["traces"]

    asyncio.run(run())
    assert not calls and not sent and not values.get(UID_KEY)
    assert values["player_context"] == before["player_context"]


def test_uid_prefill_and_later_confirm_do_not_overwrite_manual_fields(tmp_path):
    owner, values, _, uid, clock, calls = fixture(tmp_path)

    async def run():
        await seed(uid)
        service = player_build_service(owner)
        old = (await service.roster(editing=True))["characters"][0]
        assert old["level"] == 80 and len((await service.roster(details=True))["characters"][0]["relics"]) == 6
        assert (await save(owner, {"eidolon": 2, "panel": PANEL, "light_cone": {**CONE, "superimposition": 5}}))[
            "status"
        ] == "saved"
        clock[0] += timedelta(minutes=2)
        await seed(uid)
        row = (await service.roster(details=True))["characters"][0]
        assert row["eidolon"] == 2 and row["light_cone"]["superimposition"] == 5
        assert row["panel"]["values"] == PANEL and row["level"] == 80
        raw = values[UID_KEY]["characters"]["1503"]
        assert raw["eidolon"] == 0 and raw["light_cone"]["superimposition"] == 1
        assert raw["panel"]["state"] == "not_provided"

    asyncio.run(run())
    assert len(calls) == 2


def test_manual_sets_quarantine_old_individual_relics_but_leave_uid_snapshot_intact(tmp_path):
    owner, values, _, uid, _, _ = fixture(tmp_path)

    async def run():
        await seed(uid)
        raw = deepcopy(values[UID_KEY])
        assert (
            await save(owner, {"sets": {"outer": [{"id": "relic_set.133", "pieces": 4}], "inner": "relic_set.301"}})
        )["status"] == "saved"
        tool = await owner.hsr_get_player_context(character_names=["真珠"])
        row = tool["imported_roster"]["characters"][0]
        assert row["relics"] == []
        assert row["relic_sets"] == ["戏梦点星的伶人", "太空封印站"]
        assert "manual_sets_not_individual_relics" in row["missing"]
        assert values[UID_KEY] == raw and len(raw["characters"]["1503"]["relics"]) == 6

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"level": True}, "invalid_number"),
        ({"level": 81}, "invalid_number"),
        ({"eidolon": 7}, "invalid_number"),
        ({"panel": {"def": "NaN"}}, "invalid_panel"),
        ({"panel": {"def": "-1"}}, "invalid_panel"),
        ({"panel": {"crit_rate": "8.2%"}}, "invalid_panel"),
        ({"panel": {"spd": "1e3"}}, "invalid_panel"),
        ({"panel": {"unknown": "2"}}, "invalid_panel"),
        ({"panel": {"hp": "1000001"}}, "invalid_panel"),
        ({"light_cone": {"id": "light_cone.20000", "level": 80}}, "invalid_cone"),
        ({"light_cone": {**CONE, "id": "light_cone.999999"}}, "unknown_cone"),
        ({"relics": []}, "invalid_changes"),
        ({}, "invalid_changes"),
        ({"sets": {"outer": [{"id": "relic_set.301", "pieces": 4}], "inner": None}}, "invalid_sets"),
        ({"sets": {"outer": [{"id": "relic_set.133", "pieces": 4}], "inner": "relic_set.133"}}, "invalid_sets"),
        ({"sets": {"outer": [{"id": "relic_set.133", "pieces": 2}] * 2, "inner": None}}, "invalid_sets"),
        (
            {
                "sets": {
                    "outer": [{"id": "relic_set.133", "pieces": 4}, {"id": "relic_set.132", "pieces": 2}],
                    "inner": None,
                }
            },
            "invalid_sets",
        ),
    ],
)
def test_bad_manual_values_do_not_change_any_saved_data(tmp_path, changes, code):
    owner, values, _, _, _, _ = fixture(tmp_path)
    before = deepcopy(values)
    result = asyncio.run(save(owner, changes))
    assert result == {"status": "error", "error_code": code}
    assert values == before


@pytest.mark.parametrize(
    "outer",
    [
        None,
        [],
        [{"id": "relic_set.133", "pieces": 4}],
        [{"id": "relic_set.133", "pieces": 2}],
        [{"id": "relic_set.133", "pieces": 2}, {"id": "relic_set.132", "pieces": 2}],
    ],
)
def test_allowed_set_modes_and_unknown_fields_are_not_fabricated(tmp_path, outer):
    owner, _, _, _, _, _ = fixture(tmp_path)

    async def run():
        assert (await save(owner, {"sets": {"outer": outer, "inner": None}, "panel": {"def": "3132"}}))[
            "status"
        ] == "saved"
        row = (await player_build_service(owner).roster(details=True))["characters"][0]
        assert row["level"] is None and row["eidolon"] is None and row["light_cone"] is None
        assert row["panel"]["state"] == "partial" and len(row["panel"]["stats"]) == 1
        assert "atk" not in row["panel"]["values"]

    asyncio.run(run())


def test_stale_draft_rejected_and_manual_saves_serialized(tmp_path):
    owner, values, _, _, _, _ = fixture(tmp_path)

    async def run():
        revision = (await player_build_service(owner).roster(editing=True))["new_character_revision"]
        first, second = await asyncio.gather(
            owner.save_manual_build(FORM, {"level": 80}, revision),
            owner.save_manual_build(FORM, {"eidolon": 2}, revision),
        )
        assert unwrap_or(first, {})["player_build_save"]["status"] == "saved"
        assert unwrap_or(second, {})["player_build_save"]["error_code"] == "stale_edit"
        assert len(values[STORE_KEY]["characters"]) == 1

    asyncio.run(run())


def test_manual_panel_then_cone_edit_keeps_both_and_does_not_duplicate_role(tmp_path):
    owner, _, _, _, _, _ = fixture(tmp_path)

    async def run():
        await save(owner, {"panel": PANEL})
        await save(owner, {"light_cone": CONE})
        rows = (await player_build_service(owner).roster())["characters"]
        assert len(rows) == 1 and rows[0]["panel"]["values"] == PANEL and rows[0]["light_cone"]["id"] == CONE["id"]

    asyncio.run(run())


def test_enka_failure_or_clear_does_not_erase_manual_builds(tmp_path):
    from plugin.plugins.hsr_companion.adapters.showcase import ShowcaseError

    owner, values, _, uid, clock, _ = fixture(tmp_path)

    async def run():
        await seed(uid)
        await save(owner, {"panel": PANEL})
        manual = deepcopy(values[STORE_KEY])

        def fail(_):
            raise ShowcaseError("provider_unavailable", http_status=500)

        uid.fetch = fail
        clock[0] += timedelta(minutes=2)
        assert (await uid.stage(UID, True))["status"] == "error"
        await uid.clear(True)
        assert values[STORE_KEY] == manual
        assert (await player_build_service(owner).roster())["characters"][0]["panel"]["values"] == PANEL

    asyncio.run(run())


def test_new_uid_cannot_mix_old_account_edits(tmp_path):
    owner, values, _, uid, _, _ = fixture(tmp_path)

    async def run():
        await seed(uid)
        await save(owner, {"panel": PANEL})
        values[UID_KEY] = {**values[UID_KEY], "uid": "200000001"}
        row = (await player_build_service(owner).roster(details=True))["characters"][0]
        assert row["panel"]["state"] == "not_provided" and "manual_fields" not in row
        assert values[STORE_KEY]["characters"]

    asyncio.run(run())


def test_unbound_manual_data_survives_first_uid_bind_and_later_bound_edit(tmp_path):
    owner, _, _, uid, _, _ = fixture(tmp_path)

    async def run():
        await save(owner, {"panel": PANEL})
        await seed(uid)
        await save(owner, {"eidolon": 2})
        row = (await player_build_service(owner).roster())["characters"][0]
        assert row["panel"]["values"] == PANEL and row["eidolon"] == 2

    asyncio.run(run())


def test_manual_fire_added_without_eight_role_cap_or_losing_fuxuan(tmp_path):
    owner, values, _, uid, _, _ = fixture(tmp_path)
    from plugin.plugins.hsr_companion.adapters.showcase import ShowcaseResponse

    uid.fetch = lambda _: ShowcaseResponse(
        payload(*(avatar(i) for i in (1503, 1504, 1208, 1306, 1401, 1402, 1403, 1404))), 60
    )

    async def run():
        await seed(uid)
        assert (await save(owner, {"level": 80, "eidolon": 0}, "character_form.1501"))["status"] == "saved"
        rows = (await player_build_service(owner).roster())["characters"]
        assert len(rows) == 9
        assert {"火花", "花火", "符玄"} <= {r["name"] for r in rows}
        assert len(values[UID_KEY]["characters"]) == 8

    asyncio.run(run())


def test_manual_write_failure_is_reported_without_touching_uid_or_context(tmp_path):
    owner, values, _, _, _, _ = fixture(tmp_path)
    before = deepcopy(values)

    async def fail(*_):
        return Err(SdkError("synthetic disk failure"))

    owner.store.set = fail
    assert asyncio.run(save(owner, {"panel": PANEL}))["error_code"] == "storage_failed"
    assert values == before


def test_persistent_reload_preserves_manual_and_uid_in_same_player_store(tmp_path):
    owner, _, _, uid, _, _ = fixture(tmp_path)
    directory = tmp_path / "player-data"
    owner.store = PluginStore(plugin_id="hsr_companion", plugin_dir=directory)

    async def run():
        await seed(uid)
        await save(owner, {"panel": PANEL})
        owner.store = PluginStore(plugin_id="hsr_companion", plugin_dir=directory)
        owner._player_build_service = None
        tool = await owner.hsr_get_player_context(character_names=["真珠"])
        row = tool["imported_roster"]["characters"][0]
        assert row["panel"]["values"] == PANEL and len(row["relics"]) == 6

    asyncio.run(run())


def test_selector_catalog_and_manifest_use_known_ids_and_async_public_entry(tmp_path):
    owner, _, _, _, _, _ = fixture(tmp_path)
    options = selection_options(owner._catalog, owner._characters)
    assert {"火花", "花火"} <= {r["name"] for r in options["characters"]}
    assert {r["id"] for r in options["outer_sets"]} == {"relic_set.132", "relic_set.133"}
    assert {r["id"] for r in options["inner_sets"]} == {"relic_set.301"}
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    entry = next(r for r in manifest["plugin"]["entries"] if r["id"] == "save_manual_build")
    assert entry["input_schema"]["required"] == ["form_id", "changes", "expected_revision"]
    assert inspect.iscoroutinefunction(owner.save_manual_build)


def test_uid_only_projection_keeps_previous_contract(tmp_path):
    owner, values, _, uid, _, _ = fixture(tmp_path)
    from plugin.plugins.hsr_companion.runtime.uid_import import roster_view

    async def run():
        await seed(uid)
        assert combined_roster(values[UID_KEY], {}, now=NOW) == roster_view(values[UID_KEY], now=NOW)

    asyncio.run(run())


def test_new_character_draft_cannot_be_saved_into_a_changed_account(tmp_path):
    owner, values, _, _, _, _ = fixture(tmp_path)

    async def run():
        revision = (await player_build_service(owner).roster(editing=True))["new_character_revision"]
        values[UID_KEY] = {"uid": UID, "characters": {}}
        result = unwrap_or(await owner.save_manual_build(FORM, {"level": 80}, revision), {})
        assert result["player_build_save"]["error_code"] == "stale_edit"
        assert not values.get(STORE_KEY)

    asyncio.run(run())


def test_unfilled_and_cleared_values_remain_unknown_not_default_zero(tmp_path):
    owner, _, _, _, _, _ = fixture(tmp_path)

    async def run():
        assert (
            await save(
                owner, {"level": None, "eidolon": None, "light_cone": {**CONE, "level": None, "superimposition": None}}
            )
        )["status"] == "saved"
        row = (await player_build_service(owner).roster(details=True))["characters"][0]
        assert row["level"] is None and row["eidolon"] is None
        assert row["light_cone"]["level"] is None and row["light_cone"]["superimposition"] is None

    asyncio.run(run())


def test_formal_guidance_reads_manual_conditions_without_waiting_for_pixels(tmp_path):
    owner, values, _, _, _, calls = fixture(tmp_path)

    async def run():
        await save(owner, {"panel": PANEL, "light_cone": CONE})
        result = await owner.hsr_companion_prepare(query="真珠没有专武怎么配装？")
        advice = result["guidance_answer"]
        assert advice["player_conditions"]["owned_source"] == "confirmed_profile_and_player_builds"
        row = advice["imported_builds"]["characters"][0]
        assert row["panel"]["values"]["def"] == "3132"
        assert row["light_cone"]["id"] == CONE["id"]
        assert not result["reply_demand"]["observation"]["screen"]
        assert not values.get("last_local_vision")

    asyncio.run(run())
    assert not calls


def test_save_receipt_contains_read_back_build_and_next_edit_revision(tmp_path):
    owner, values, _, uid, _, calls = fixture(tmp_path)

    async def run():
        await seed(uid)
        catalog_before = owner._catalog.database_path.read_bytes()
        uid_before = deepcopy(values[UID_KEY])
        receipt = await save(owner, {"panel": {"def": "3132"}})
        assert receipt["readback_state"] == "confirmed"
        row = receipt["build"]
        assert row["panel"]["values"] == {"def": "3132"}
        assert row["edit_revision"] == (await player_build_service(owner).roster(editing=True))["characters"][0]["edit_revision"]
        assert (await owner.hsr_get_player_context(character_names=["真珠"]))["imported_roster"]["characters"][0]["panel"] == row["panel"]
        assert values[UID_KEY] == uid_before
        assert owner._catalog.database_path.read_bytes() == catalog_before
        assert not set(values) - {"player_context", UID_KEY, STORE_KEY, "tool_audit"}

    asyncio.run(run())
    assert len(calls) == 1  # Only the explicitly staged fixture query, never saving.


def test_choice_notes_are_game_metadata_not_player_equipment(tmp_path):
    owner, values, _, _, _, _ = fixture(tmp_path)
    before = deepcopy(values)
    options = player_build_service(owner).options()
    cone = next(row for row in options["light_cones"] if row["id"] == CONE["id"])
    assert cone["signature_for"] == ["真珠"]
    assert cone["signature_form_ids"] == [FORM]
    unknown = next(row for row in options["light_cones"] if row["id"] == "light_cone.20000")
    assert unknown["signature_for"] == []
    assert options["outer_sets"][0]["effects"]
    assert values == before  # Listing the full game directory must not add owned roles/equipment.


def test_readback_error_does_not_misreport_successful_write_as_failed(tmp_path):
    owner, values, _, _, _, _ = fixture(tmp_path)
    original_write, original_read = owner._store_value, owner._get_store

    async def read_fails(key):
        if key == STORE_KEY:
            return Err(SdkError("synthetic read-back failure"))
        return await original_read(key)

    async def write_then_block_read(key, value):
        await original_write(key, value)
        owner._get_store = read_fails

    owner._store_value = write_then_block_read
    receipt = asyncio.run(save(owner, {"panel": {"def": "3132"}}))
    assert receipt == {"status": "saved", "form_id": FORM, "readback_state": "unavailable"}
    assert next(iter(values[STORE_KEY]["characters"].values()))["changes"]["panel"] == {"def": "3132"}
