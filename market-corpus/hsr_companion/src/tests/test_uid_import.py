"""Frozen UID scope: partial snapshots allowed; wrong account/old mixed builds not."""

import asyncio
import io
import json
import threading
import urllib.error
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters import showcase as transport
from plugin.plugins.hsr_companion.adapters.showcase import ShowcaseError, ShowcaseResponse, validate_uid
from plugin.plugins.hsr_companion.runtime.data_freshness import guidance_freshness
from plugin.plugins.hsr_companion.runtime.guidance import GuidanceCatalog, build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.uid_import import (
    STORE_KEY,
    confirm_preview,
    normalize_showcase,
    profile_with_roster,
    roster_view,
)
from plugin.plugins.hsr_companion.runtime.uid_service import UidImportService
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.sdk.plugin import Err, PluginStore, unwrap_or

ROOT = Path(__file__).resolve().parents[1]
UID = "100000001"  # Synthetic. Tests never query real accounts.
NOW = datetime(2026, 10, 18, 13, tzinfo=timezone.utc)
REGISTRY = json.loads((ROOT / "resources/character_registry.json").read_text(encoding="utf-8"))


def avatar(form_id=1503):
    return {
        "avatarId": form_id,
        "level": 80,
        "rank": 0,
        "promotion": 6,
        "equipment": {"tid": 23055, "level": 80, "rank": 1},
        "skillTreeList": [{"pointId": 1503001, "level": 6}],
        "relicList": [
            {
                "tid": 61300 + slot,
                "type": slot,
                "level": 15,
                "_flat": {
                    "setID": 130,
                    "props": [
                        {"type": "HPDelta", "value": 705.5999999999999},
                        {"type": "AttackDelta", "value": 19.051896},
                        {"type": "CriticalChance", "value": 0.05832},
                        {"type": "SpeedDelta", "value": 6.6},
                        {"type": "DefenceAddedRatio", "value": 0.0972},
                    ],
                },
            }
            for slot in range(1, 7)
        ],
    }


def payload(*avatars):
    return {
        "uid": UID,
        "region": "CN",
        "ttl": 60,
        "owner": {"private": "must not be saved"},
        "detailInfo": {
            "uid": UID,
            "nickname": "private nickname",
            "signature": "private signature",
            "birthday": 1234,
            "isDisplayAvatar": True,
            "avatarDetailList": list(avatars or [avatar()]),
        },
    }


def preview(raw=None, **kwargs):
    return normalize_showcase(raw or payload(), uid=UID, registry=REGISTRY, now=NOW, **kwargs)


@pytest.mark.parametrize(
    "uid", ["", "https://localhost/", "10000000", "1000000000", "800000001", "１１１０９１７２２", True, "1../secret"]
)
def test_uid_is_cn_ascii_only(uid):
    with pytest.raises(ShowcaseError):
        validate_uid(uid)


@pytest.mark.parametrize("uid", ["100000001", "200000001", "500000001"])
def test_supported_cn_prefixes(uid):
    assert validate_uid(uid) == uid


@pytest.mark.parametrize("form,name", [(1503, "真珠"), (1505, "绯英"), (1208, "符玄")])
def test_exact_registry_identity_and_whitelisted_fields(form, name):
    raw = payload(avatar(form))
    original = deepcopy(raw)
    result = preview(raw)
    build = result["characters"][str(form)]
    assert raw == original
    assert build["name"] == name and build["eidolon"] == 0
    assert len(build["relics"]) == 6
    assert build["light_cone"]["superimposition"] == 1
    assert all(r["complete"] for r in build["relics"])
    assert build["relics"][0]["main_stat"]["display"] == "705"
    assert [row["display"] for row in build["relics"][0]["sub_stats"]] == ["19", "5.8%", "6", "9.7%"]
    assert build["panel"]["state"] == "not_provided"
    assert build["source_updated_at"] is None and not build["visually_confirmed"]
    assert not any(
        word in str(result) for word in ("private nickname", "private signature", "birthday", "must not be saved")
    )


@pytest.mark.parametrize("field,value", [("rank", 7), ("level", True), ("level", 81), ("promotion", -1)])
def test_bad_numeric_fields_rejected(field, value):
    row = avatar()
    row[field] = value
    with pytest.raises(ShowcaseError):
        preview(payload(row))


def test_wrong_uid_and_region_not_saved():
    for raw in (payload(), payload()):
        raw["detailInfo"]["uid"] = "200000001"
        with pytest.raises(ShowcaseError, match="uid_mismatch"):
            preview(raw)
    raw = payload()
    raw["region"] = "EU"
    with pytest.raises(ShowcaseError, match="uid_mismatch"):
        preview(raw)


def test_private_or_empty_showcase_not_a_replacement_roster():
    raw = payload()
    raw["detailInfo"]["avatarDetailList"] = []
    with pytest.raises(ShowcaseError, match="showcase_empty"):
        preview(raw)
    raw = payload()
    raw["detailInfo"]["isDisplayAvatar"] = False
    with pytest.raises(ShowcaseError, match="details_private"):
        preview(raw)


def test_partial_values_explicit_unknown_not_fabricated():
    a = avatar()
    a["relicList"][0]["_flat"]["props"].pop()
    result = preview(payload(a))["characters"]["1503"]
    assert not result["relics"][0]["complete"]
    assert len(result["relics"][0]["sub_stats"]) == 3
    assert "relic_stats:1" in result["missing"]
    a["relicList"][0]["_flat"]["props"][0]["type"] = "FutureNewProperty"
    result = preview(payload(a))["characters"]["1503"]
    assert result["relics"][0]["main_stat"]["unit"] == "unknown"


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("CriticalChanceBase", 0.32399999999999995, "32.4%"),
        ("CriticalDamageBase", 0.648, "64.8%"),
        ("StatusProbabilityBase", 0.432, "43.2%"),
        ("CriticalChance", 0.05184, "5.1%"),
        ("CriticalChance", 0.323999, "32.3%"),
    ],
)
def test_primary_and_secondary_property_codes_and_numeric_display(kind, value, expected):
    a = avatar()
    a["relicList"][0]["_flat"]["props"][0] = {"type": kind, "value": value}
    row = preview(payload(a))["characters"]["1503"]["relics"][0]
    assert row["complete"] and row["main_stat"]["display"] == expected
    assert row["main_stat"]["raw_value"] == value


def test_api_slot_numbers_not_scanner_slot_order():
    rows = preview()["characters"]["1503"]["relics"]
    assert rows[4]["slot"] == "连结绳" and rows[5]["slot"] == "位面球"


def test_duplicates_dedup_identical_but_conflicts_do_not_choose_first():
    assert len(preview(payload(avatar(), avatar()))["characters"]) == 1
    second = avatar()
    second["rank"] = 1
    result = preview(payload(avatar(), second, avatar(1505)))
    assert set(result["characters"]) == {"1505"}
    assert result["conflicting_form_ids"] == ["1503"]
    with pytest.raises(ShowcaseError, match="conflicting_characters"):
        preview(payload(avatar(), second))


def test_relic_slot_conflict_never_merged():
    a = avatar()
    a["relicList"][1]["type"] = 1
    with pytest.raises(ShowcaseError, match="conflicting_relic_slot"):
        preview(payload(a))


def test_merge_preserves_absent_characters_and_replaces_whole_build():
    first = preview(payload(avatar(), avatar(1505)))
    saved = confirm_preview({"preview": first}, first["preview_id"], now=NOW)
    original = deepcopy(saved)
    changed = avatar()
    changed["relicList"] = []
    new = normalize_showcase(payload(changed), uid=UID, registry=REGISTRY, now=NOW + timedelta(hours=2))
    saved["preview"] = new
    result = confirm_preview(saved, new["preview_id"], now=NOW + timedelta(hours=2))
    assert result["characters"]["1503"]["relics"] == []
    assert "six_equipped_relics" in result["characters"]["1503"]["missing"]
    assert result["characters"]["1505"] == original["characters"]["1505"]
    assert result["characters"]["1503"]["fetched_at"] != result["characters"]["1505"]["fetched_at"]


def test_expired_preview_and_another_account_rejected():
    stage = preview()
    with pytest.raises(ShowcaseError, match="preview_expired"):
        confirm_preview({"preview": stage}, stage["preview_id"], now=NOW + timedelta(minutes=31))
    with pytest.raises(ShowcaseError, match="different_account"):
        confirm_preview({"uid": "200000001", "preview": stage}, stage["preview_id"], now=NOW)


def test_snapshot_does_not_expire_like_pixels_or_update_its_own_age():
    staged = preview()
    state = confirm_preview({"preview": staged}, staged["preview_id"], now=NOW)
    current = roster_view(state, now=NOW + timedelta(days=2), details=True)
    assert current["characters"][0]["freshness"] == "refresh_recommended"
    assert current["characters"][0]["relics"][0]["sub_stats"][0]["display"] == "19"
    assert current["characters"][0]["fetched_at"] == NOW.isoformat()
    assert not current["roster_complete"] and not current["visually_confirmed"]
    manual = {"owned_characters": ["黄泉"], "favorite_characters": ["花火"]}
    merged = profile_with_roster(manual, current)
    assert merged["owned_characters"] == ["黄泉", "真珠"]
    assert manual == {"owned_characters": ["黄泉"], "favorite_characters": ["花火"]}
    assert UID not in str(current) and "nickname" not in str(current)


def setup_service(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)
    clock = [NOW]
    calls = []

    def fetch(uid):
        calls.append(uid)
        return ShowcaseResponse(payload(), 60)

    service = UidImportService(owner, fetch=fetch, clock=lambda: clock[0])
    owner._uid_import_service = service
    return owner, values, sent, service, clock, calls


def test_consent_preview_confirm_tool_and_screen_separation(tmp_path):
    owner, values, sent, service, _, calls = setup_service(tmp_path)
    values["last_local_vision"] = {"unchanged": True}

    async def run():
        no = await owner.preview_uid_import(UID, consent=False)
        assert unwrap_or(no, {})["uid_import"]["error_code"] == "consent_required" and not calls
        staged = unwrap_or(await owner.preview_uid_import(UID, consent=True), {})["uid_import"]
        assert not values.get(STORE_KEY, {}).get("characters")
        saved = unwrap_or(await owner.confirm_uid_import(staged["preview"]["preview_id"]), {})["uid_import"]
        assert saved["roster"]["characters"][0]["name"] == "真珠"
        got = await owner.hsr_get_player_context(character_names=["真珠"])
        assert got["profile"]["owned_characters"] == ["花火", "真珠"]
        assert len(got["imported_roster"]["characters"][0]["relics"]) == 6
        assert values["player_context"] == {"owned_characters": ["花火"]}
        assert values["last_local_vision"] == {"unchanged": True}
        assert calls == [UID] and not sent
        assert UID not in str(got)

    asyncio.run(run())


def test_polling_and_cooldown_do_not_fetch(tmp_path):
    _, values, _, service, clock, calls = setup_service(tmp_path)

    async def run():
        first = await service.stage(UID, True)
        assert first["retry_after_seconds"] == 60
        for _ in range(10):
            service.view(await service.read())
        assert (await service.stage(UID, True))["status"] == "cooldown"
        assert calls == [UID]
        clock[0] += timedelta(seconds=61)
        await service.stage(UID, True)
        assert calls == [UID, UID]
        assert not values[STORE_KEY].get("characters")

    asyncio.run(run())


def test_formal_advice_uses_imported_account_but_screen_question_does_not(tmp_path):
    owner, values, sent, service, _, calls = setup_service(tmp_path)

    async def run():
        stage = await service.stage(UID, True)
        await service.confirm(stage["preview"]["preview_id"])
        owner._observer_status.update(enabled=False, state="paused")
        result = await owner.hsr_companion_prepare(query="真珠遗器光锥怎么配")
        advice = result["guidance_answer"]
        assert advice["player_conditions"]["owned_source"] == "confirmed_profile_and_uid_snapshot"
        assert "真珠" in advice["player_conditions"]["owned_characters"]
        assert advice["imported_builds"]["characters"][0]["light_cone"]["id"] == "light_cone.23055"
        assert not result["followup"]["pending"] and not sent
        assert calls == [UID]  # A chat question does not refresh the provider.
        assert values["player_context"] == {"owned_characters": ["花火"]}
        screen = await owner.hsr_companion_prepare(query="现在是什么页面？都有谁？")
        assert not screen["screen_identity_answer"]["allowed"]
        assert not screen["screen_identity_answer"]["names"]
        assert screen["player_context"] == {}
        mixed = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么？适合真珠吗？")
        card = mixed["guidance_answer"]["assessment"]["equipment_answer"]
        assert not card.get("main_stat") and not card.get("sub_stats")
        assert not mixed["current_visual_evidence"]["fresh"]
        assert calls == [UID]

    asyncio.run(run())


@pytest.mark.parametrize("code", ["network_unavailable", "showcase_empty", "unsupported_response", "rate_limited"])
def test_fetch_failure_preserves_saved_builds(tmp_path, code):
    _, values, _, service, clock, _ = setup_service(tmp_path)

    async def run():
        p = await service.stage(UID, True)
        await service.confirm(p["preview"]["preview_id"])
        before = deepcopy(values[STORE_KEY]["characters"])
        clock[0] += timedelta(seconds=61)

        def fail(uid):
            raise ShowcaseError(code, 120)

        service.fetch = fail
        failed = await service.stage(UID, True)
        assert failed["status"] == "error" and failed["retry_after_seconds"] == 120
        assert values[STORE_KEY]["characters"] == before

    asyncio.run(run())


def test_inflight_query_cannot_repopulate_reset_state(tmp_path):
    owner, values, _, service, _, calls = setup_service(tmp_path)
    ready, release = threading.Event(), threading.Event()

    def fetch(uid):
        ready.set()
        release.wait(2)
        return ShowcaseResponse(payload(), 60)

    service.fetch = fetch

    async def run():
        task = asyncio.create_task(service.stage(UID, True))
        await asyncio.to_thread(ready.wait, 1)
        assert (await service.stage(UID, True))["status"] == "reading"
        owner._uid_import_generation = 1
        values.pop(STORE_KEY, None)
        release.set()
        assert (await task)["status"] == "cancelled"
        assert STORE_KEY not in values and not calls

    asyncio.run(run())


def test_clear_is_scoped_to_uid_and_preserves_cooldown(tmp_path):
    owner, values, _, service, _, calls = setup_service(tmp_path)

    async def run():
        p = await service.stage(UID, True)
        await service.confirm(p["preview"]["preview_id"])
        assert (await service.clear(False))["status"] == "error"
        assert (await service.clear(True))["roster"]["characters"] == []
        assert values["player_context"] == {"owned_characters": ["花火"]}
        assert (await service.stage(UID, True))["status"] == "cooldown"
        assert calls == [UID]

    asyncio.run(run())


def test_public_sdk_store_survives_reopen_without_touching_manual_profile(tmp_path):
    owner, _, _, service, _, _ = setup_service(tmp_path)
    directory = tmp_path / "uid-persistent-check"

    async def run():
        store = PluginStore(plugin_id="hsr_companion", plugin_dir=directory, enabled=True)
        owner._get_store = store.get

        async def save(key, value):
            result = await store.set(key, value)
            assert not isinstance(result, Err)

        owner._store_value = save
        staged = await service.stage(UID, True)
        await service.confirm(staged["preview"]["preview_id"])
        await store.close()
        reopened = PluginStore(plugin_id="hsr_companion", plugin_dir=directory, enabled=True)
        owner._get_store = reopened.get
        readback = await service.read()
        assert readback["uid"] == UID
        assert readback["characters"]["1503"]["relics"][0]["sub_stats"][0]["display"] == "19"
        assert unwrap_or(await reopened.get("player_context"), None) is None
        await reopened.close()

    asyncio.run(run())


def test_another_account_is_rejected_before_network_and_store_failure_keeps_data(tmp_path):
    owner, values, _, service, clock, calls = setup_service(tmp_path)

    async def run():
        staged = await service.stage(UID, True)
        await service.confirm(staged["preview"]["preview_id"])
        before = deepcopy(values[STORE_KEY])
        clock[0] += timedelta(seconds=61)
        assert (await service.stage("200000001", True))["error_code"] == "different_account"
        assert calls == [UID] and values[STORE_KEY] == before

        async def broken_save(*args):
            raise RuntimeError("disk unavailable")

        owner._store_value = broken_save
        result = unwrap_or(await owner.preview_uid_import(UID, consent=True), {})
        assert result["uid_import"]["error_code"] == "storage_failed"
        assert values[STORE_KEY] == before and calls == [UID]

    asyncio.run(run())


def test_retained_preview_keeps_its_own_uid_after_different_unbound_query_fails(tmp_path):
    _, _, _, service, clock, _ = setup_service(tmp_path)

    async def run():
        staged = await service.stage(UID, True)
        old_id = staged["preview"]["preview_id"]
        clock[0] += timedelta(seconds=61)

        def fail(uid):
            raise ShowcaseError("network_unavailable")

        service.fetch = fail
        result = await service.stage("200000001", True)
        assert result["uid"] == "200000001"
        assert result["preview"]["uid"] == UID
        assert result["preview"]["preview_id"] == old_id
        saved = await service.confirm(old_id)
        assert saved["uid"] == UID

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_cn_uid"),
        (404, "player_not_found"),
        (424, "maintenance"),
        (429, "rate_limited"),
        (503, "maintenance"),
        (301, "provider_unavailable"),
    ],
)
def test_transport_http_errors_are_safe_and_do_not_reveal_uid(monkeypatch, status, code):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://secret.invalid/" + UID, status, "private", {"Retry-After": "120"}, io.BytesIO()
        )

    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=fail))
    with pytest.raises(ShowcaseError) as error:
        transport.fetch_showcase(UID)
    assert str(error.value) == code and error.value.retry_seconds == 120
    assert UID not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [b"[]", b"not json", b'{"ttl":true}', b'{"ttl":-1}', b'{"ttl":86401}', b"\xff", b"x" * (transport.MAX_BYTES + 1)],
    ids=["array", "bad-json", "bool-ttl", "negative-ttl", "huge-ttl", "bad-encoding", "oversize"],
)
def test_transport_invalid_or_oversize_body_not_accepted(monkeypatch, body):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == transport.MAX_BYTES + 1
            return body

    monkeypatch.setattr(
        transport.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=lambda *a, **k: Response())
    )
    with pytest.raises(ShowcaseError):
        transport.fetch_showcase(UID)


def test_no_redirect_and_fixed_url_no_cookies(monkeypatch):
    assert transport._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b'{"ttl":90}'

    def fetch(request, timeout):
        assert request.full_url == "https://enka.network/api/hsr/uid/" + UID
        assert timeout == transport.REQUEST_TIMEOUT
        assert not request.has_header("Cookie") and not request.has_header("Authorization")
        assert request.has_header("User-agent")
        return Response()

    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=fetch))
    assert transport.fetch_showcase(UID).ttl == 90


def test_guidance_rotation_and_imported_conditions_do_not_make_current_pixels(tmp_path):
    owner, _, _, _, _, _ = setup_service(tmp_path)
    book = GuidanceCatalog(json.loads((ROOT / "resources/guidance.cn-4.6.json").read_text(encoding="utf-8")))
    current = guidance_freshness(book, now=NOW)
    assert any("来生泅渡" in (x["name"] or "") for x in current["active_periods"])
    later = NOW + timedelta(days=60)
    assert not guidance_freshness(book, now=later)["active_periods"]
    query = "本期混沌怎么配队"
    plan = plan_guidance(query, build_query_plan(query), book=book, catalog=owner._catalog, now=later)
    result = build_guidance(plan, query, book=book, catalog=owner._catalog, profile={}, now=later)
    assert not any(g.get("period_status") == "active" for g in result["guides"])


def test_preview_reports_only_new_and_changed_roles_but_preserves_whole_batch(tmp_path):
    _, _, _, service, clock, _ = setup_service(tmp_path)

    async def run():
        first = await service.stage(UID, True)
        await service.confirm(first["preview"]["preview_id"])
        clock[0] += timedelta(seconds=61)
        service.fetch = lambda _: ShowcaseResponse(payload(avatar(), avatar(1505)), 60)
        second = await service.stage(UID, True)
        assert second["preview"]["summary"] == {
            "added": 1,
            "updated": 0,
            "unchanged": 1,
            "retained": 0,
            "total_after": 2,
        }
        assert [r["change"] for r in second["preview"]["characters"]] == ["unchanged", "added"]
        saved = await service.confirm(second["preview"]["preview_id"])
        assert len(saved["roster"]["characters"]) == 2
        clock[0] += timedelta(seconds=61)
        changed = avatar()
        changed["rank"] = 1
        service.fetch = lambda _: ShowcaseResponse(payload(changed), 60)
        third = await service.stage(UID, True)
        assert third["preview"]["summary"] == {
            "added": 0,
            "updated": 1,
            "unchanged": 0,
            "retained": 1,
            "total_after": 2,
        }

    asyncio.run(run())


def test_failed_http_query_keeps_safe_status_code_without_uid_or_body(tmp_path):
    _, values, _, service, _, _ = setup_service(tmp_path)

    def fail(uid):
        raise ShowcaseError("provider_unavailable", 60, http_status=500)

    service.fetch = fail

    async def run():
        failed = await service.stage(UID, True)
        assert failed["error_info"] == {"http_status": 500, "network_mode": "inherit"}
        assert values[STORE_KEY]["error_info"] == failed["error_info"]
        assert "body" not in str(failed["error_info"]) and UID not in str(failed["error_info"])

    asyncio.run(run())


def test_system_proxy_is_opt_in_and_no_proxy_wildcard_is_respected(monkeypatch):
    monkeypatch.setattr(transport.urllib.request, "getproxies", lambda: {"no": "localhost,127.0.0.1"})
    monkeypatch.setattr(
        transport.urllib.request, "getproxies_registry", lambda: {"https": "http://127.0.0.1:12345"}, raising=False
    )
    for key in list(transport.os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            monkeypatch.delenv(key)
    assert transport.request_proxies(use_system_proxy=False) == {"no": "localhost,127.0.0.1"}
    assert transport.request_proxies(use_system_proxy=True) == {
        "https": "http://127.0.0.1:12345",
        "no": "localhost,127.0.0.1",
    }
    monkeypatch.setattr(transport.urllib.request, "getproxies", lambda: {"no": "*"})
    assert transport.request_proxies(use_system_proxy=True) == {"no": "*"}
    monkeypatch.setattr(transport.urllib.request, "getproxies", lambda: {"https": "http://explicit.invalid:1"})
    assert transport.request_proxies(use_system_proxy=True) == {"https": "http://explicit.invalid:1"}


def test_explicit_empty_proxy_and_non_windows_never_enable_another_route(monkeypatch):
    monkeypatch.setattr(transport.urllib.request, "getproxies", lambda: {"no": "localhost"})
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setattr(
        transport.urllib.request, "getproxies_registry", lambda: {"https": "http://127.0.0.1:12345"}, raising=False
    )
    assert transport.request_proxies(use_system_proxy=True) == {"no": "localhost"}
    monkeypatch.delenv("HTTPS_PROXY")
    monkeypatch.setattr(transport.urllib.request, "getproxies_registry", None)
    assert transport.request_proxies(use_system_proxy=True) == {"no": "localhost"}


def test_eight_to_nine_keeps_removed_showcase_role_and_timestamp():
    ids = [1401, 1409, 1407, 1225, 1505, 1503, 1306, 1208]
    first = preview(payload(*(avatar(i) for i in ids)))
    state = confirm_preview({"preview": first}, first["preview_id"], now=NOW)
    original = deepcopy(state["characters"]["1208"])
    next_batch = normalize_showcase(
        payload(*(avatar(i) for i in [*ids[:-1], 1501])), uid=UID, registry=REGISTRY, now=NOW + timedelta(minutes=2)
    )
    state["preview"] = next_batch
    saved = confirm_preview(state, next_batch["preview_id"], now=NOW + timedelta(minutes=2))
    assert len(saved["characters"]) == 9
    assert saved["characters"]["1208"] == original
    assert saved["characters"]["1501"]["name"] == "火花"


def test_selected_proxy_is_passed_to_default_transport_and_success_clears_http_error(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import uid_service as service_module

    _, _, _, service, clock, _ = setup_service(tmp_path)
    service.fetch = None
    calls = []

    def fetch(uid, *, use_system_proxy=False):
        calls.append(use_system_proxy)
        if len(calls) == 1:
            raise ShowcaseError("provider_unavailable", 60, http_status=500)
        return ShowcaseResponse(payload(), 60)

    monkeypatch.setattr(service_module, "fetch_showcase", fetch)

    async def run():
        failed = await service.stage(UID, True, use_system_proxy=True)
        assert failed["use_system_proxy"] and failed["error_info"]["http_status"] == 500
        clock[0] += timedelta(seconds=61)
        recovered = await service.stage(UID, True, use_system_proxy=True)
        assert recovered["status"] == "preview" and recovered["error_info"] is None
        assert calls == [True, True]

    asyncio.run(run())
