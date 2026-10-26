"""Synthetic cache/projection regressions, not a replay of missing live frames."""
import asyncio
import copy
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.runtime import answer_message
from plugin.plugins.hsr_companion.runtime.consultation import for_followup
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
from plugin.plugins.hsr_companion.tests import test_guidance as fixtures
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture
from plugin.plugins.hsr_companion.tests.test_delivery_races import complete_commit
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import assess, relic

book, catalog = fixtures.book, fixtures.catalog


def changed_digits():
    image = Image.open(BytesIO(picture())).convert("RGB")
    draw = ImageDraw.Draw(image)
    for y in (125, 205, 250, 290, 330):
        draw.rectangle((55, y, 65, y + 10), fill="white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("include_page", [True, False])
@pytest.mark.parametrize("rows,old_matches", [(1, False), (0, False), (1, True)])
def test_reviewed_empty_old_packet_cannot_hide_current_partial(tmp_path, include_page, rows, old_matches):
    owner, _, _ = plugin_fixture(tmp_path)
    checks = []

    async def run():
        owner._answer_query_generation = owner._query_refresh_generation = 9
        owner._active_refresh_trace = {"started": time.monotonic(), "stages": []}
        await complete_commit(owner, picture(), "10")
        old = copy.deepcopy(owner._reusable_observations["full"])
        payload = changed_digits()
        await complete_commit(owner, payload, "20")
        raw = copy.deepcopy(owner._committed_observation)
        raw["screen_facts"]["facts"] = raw["screen_facts"]["facts"][:rows]
        await owner._apply_local_analysis(raw, image_fingerprint="new-partial", source="assistant_local_ocr")
        owner._reusable_observations["full_previous"] = old

        def reject_old(data, proofs, **kw):
            checks.append(1)
            return set()

        owner._vision = SimpleNamespace(verify_text_regions=reject_old)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda _: frame(picture() if old_matches else payload))
        plan = build_query_plan("这件呢？给她合适吗", "assess_relic",
                                screen_fields=["equipment", "page"] if include_page else ["equipment"])
        before = copy.deepcopy(owner._committed_observation)
        for _ in range(2):
            selected = await try_reuse(owner, navigation=False, full=True, plan=plan)
            card = equipment_card(selected or {})
            expected = "10" if old_matches else "20" if rows else None
            assert (card.get("main_stat") or {}).get("value") == expected
            assert len(card["sub_stats"]) == (4 if old_matches else 0), "Never splice packets"
            if rows and not old_matches:
                from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
                assert ObservationSnapshot.select(owner, selected, fields=plan.fields).matches(owner)
        assert owner._committed_observation == before
        assert len(checks) <= 2, "One old review per attempt, not an OCR retry loop"
        chosen = [r for r in owner._active_refresh_trace["stages"] if r["stage"] == "reuse_postreview_selected"]
        if rows and not old_matches:
            assert chosen and chosen[0]["before_rows"] == 0 and chosen[0]["after_rows"] == 1
        owner._observer_status.update(enabled=False, state="paused")
        assert await try_reuse(owner, navigation=False, full=True, plan=plan) is None

    asyncio.run(run())


def brief(book, catalog, *, subs=None):
    return assess("这件遗器适合绯英吗？", relic(subs=subs) if subs is not None else relic(), book, catalog)


def test_brief_selects_properties_without_requiring_spoken_numbers(book, catalog, monkeypatch):
    guidance = brief(book, catalog)
    reply = guidance["reply_plan"]
    assert reply["value_policy"] == "only_when_requested_or_decisive"
    assert len(guidance["assessment"]["equipment_answer"]["sub_stats"]) == 4
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    data = {"guidance_answer": guidance, "equipment_answer": guidance["assessment"]["equipment_answer"],
            "screen_facts": {"facts": guidance["assessment"]["facts"]}}
    text, receipt = answer_message.render_followup(data, plan=build_query_plan("这件遗器适合绯英吗？", "assess_relic"), ready=True)
    assert receipt["has_content"] and receipt["complete"]
    assert "38.8%" not in text and "8.7%" not in text and "11.6%" not in text
    assert reply["conclusion"] in text and reply["next_step"] in text


def test_full_rows_and_percent_signs_stay_exact(book, catalog, monkeypatch):
    guidance = assess("完整报出这件遗器主词条和四项副词条，适合绯英吗？", relic(), book, catalog)
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    card = guidance["assessment"]["equipment_answer"]
    text, receipt = answer_message.render_followup({"guidance_answer": guidance, "equipment_answer": card},
        plan=build_query_plan("完整报出这件遗器主词条和四项副词条", "read_screen", screen_fields=["equipment"]), ready=True)
    for row in [card["main_stat"], *card["sub_stats"]]:
        assert row["label"] + row["value"] in text
    assert receipt["required_rows"] == 5 and receipt["complete"]


def test_metadata_does_not_create_fit_notice_but_final_assessment_does(book, catalog):
    first = brief(book, catalog, subs=[])["reply_plan"]
    same = copy.deepcopy(first)
    same["current_object"] = {"name": "新识别的名字", "slot": "位面球"}
    same["equipment_coverage"] = {**same["equipment_coverage"], "state": "partial"}
    assert not for_followup(same, previous=first)["answer_increment"]
    final = brief(book, catalog)["reply_plan"]
    assert for_followup(final, previous=first)["answer_increment"]
    assert not for_followup(final, previous=final)["answer_increment"]
    # Unspoken optional changes are NOT a correction entitlement. If the
    # value was selected for exact delivery, its correction still must send.
    corrected = copy.deepcopy(final)
    corrected["answer_basis"] = {**final["answer_basis"], "rows": [["sub_stat", "生命值", "81"]]}
    assert not for_followup(corrected, previous=final)["answer_increment"]
    final["evidence_rows"] = [{"kind": "sub_stat", "label": "生命值", "value": "80", "speak_value": True}]
    selected = for_followup(corrected, previous=final)
    assert selected["answer_increment"]
    assert selected["value_policy"] == "correct_previous_reading"
    assert selected["evidence_rows"] == [{"kind": "sub_stat", "label": "生命值", "value": "81", "speak_value": True}]


def test_late_identity_completes_even_when_advice_and_numbers_unchanged(book, catalog):
    final = brief(book, catalog)["reply_plan"]
    before = copy.deepcopy(final)
    before["evaluation_available"] = False
    assert for_followup(final, previous=before)["answer_increment"]


def test_metadata_stays_silent_until_final_fit_on_same_pending_request(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_guidance import plan
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
    query = "这件遗器适合绯英吗？"
    initial = relic(subs=[])
    initial["screen_facts"]["facts"] = [f for f in initial["screen_facts"]["facts"]
                                           if f["kind"] not in {"main_stat", "equipment_name", "equipment_slot"}]
    metadata = relic(subs=[])
    metadata["screen_facts"]["facts"] = [f for f in metadata["screen_facts"]["facts"] if f["kind"] != "main_stat"]
    final = relic()
    owner, sent = followup_owner(monkeypatch)
    owner._guidance_book, owner._catalog = book, catalog
    demand = plan(query, book, catalog)
    observed = demand.observation_plan(build_query_plan(query))
    reads = []
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)

    async def current():
        result = metadata if not reads else final
        reads.append(result)
        owner._observation_serial = result["screen_facts"]["observation_serial"]
        assert not sent, "Name/slot alone must not consume the single progress notice"
        return copy.deepcopy(result)

    owner._current_visual_evidence = current

    async def run():
        armed = followup.arm_followup(owner, query=query, visual=initial, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=observed, guidance_plan=demand, guidance_profile={})
        assert armed["pending"]
        pending = owner._pending_answer
        deadline = pending.expires_at
        await owner._followup_task
        assert pending.expires_at == deadline
        assert owner._pending_answer is None and owner._followup_status["answer_complete"], owner._followup_status

    asyncio.run(run())
    assert len(reads) == 2 and len(sent) == 1
    assert "绯英" in sent[0]["parts"][0]["text"]
    assert sent[0]["metadata"]["outcome"] == "ready"


def test_cache_recovery_does_not_replace_richer_current_packet(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup, query_reuse
    from plugin.plugins.hsr_companion.tests.test_guidance import plan
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
    query = "这件遗器适合绯英吗？"
    current = relic(subs=[("暴击率", "8.7%")])
    degraded = copy.deepcopy(current)
    degraded["screen_facts"]["facts"] = [f for f in degraded["screen_facts"]["facts"]
                                            if f["kind"] not in {"main_stat", "sub_stat"}]
    owner, sent = followup_owner(monkeypatch, value=current)
    owner._guidance_book, owner._catalog = book, catalog
    owner._reusable_observations = {"full": {"evidence": degraded}}
    selected = plan(query, book, catalog)
    # Original recording selected page+equipment. A partial equipment field
    # alone is not marked confirmed by the existing completion contract.
    observed = selected.observation_plan(build_query_plan(query, screen_fields=["page", "equipment"]))
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)

    async def reuse(*a, **kw):
        return degraded

    monkeypatch.setattr(query_reuse, "try_reuse", reuse)

    async def run():
        pending = followup.AnswerRequest("coverage-test", query, "猫娘", time.time(), time.monotonic(),
            time.monotonic() + .6, "unknown", False, False, True, False,
            plan=observed, guidance_plan=selected, guidance_profile={})
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)

    asyncio.run(run())
    assert len(sent) == 1, owner._followup_status
    card = owner._test_answer_payloads[0]["equipment_answer"]
    assert card["main_stat"]["value"] == "38.8%" and card["sub_stats"][0]["value"] == "8.7%"


@pytest.mark.parametrize("change", ["new_revision", "new_question", "capture_failure", "new_challenge", "pause", "missing_literal_proof",
                                  "changed_packet_value", "missing_receipt_digest", "wrong_proof_region"])
def test_reviewed_retained_packet_is_not_a_permanent_or_unscoped_permission(tmp_path, change):
    from plugin.plugins.hsr_companion.runtime.delivery_scope import coordinates
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot, ObservationStore
    from plugin.plugins.hsr_companion.tests.test_delivery_chain_20261017 import equipment_plan
    from plugin.plugins.hsr_companion.tests.test_stage2a_contract import partial_commit
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await complete_commit(owner, picture(), "10")
        await partial_commit(owner)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda _: frame(picture()))
        plan = equipment_plan()
        selected = await try_reuse(owner, navigation=False, full=True, plan=plan)
        assert selected is not None
        assert ObservationSnapshot.select(owner, selected, fields=plan.fields).matches(owner)
        if change == "new_revision":
            await partial_commit(owner)
        elif change == "new_question":
            owner._answer_query_generation = getattr(owner, "_answer_query_generation", 0) + 1
        elif change == "capture_failure":
            owner._capture_failure_revision = getattr(owner, "_capture_failure_revision", 0) + 1
        elif change == "new_challenge":
            proof = selected["screen_facts"]["facts"][0]["evidence"][0]
            ObservationStore(owner).defer_regions({coordinates(proof)})
        elif change == "pause":
            owner._observer_status.update(state="paused", enabled=False)
        elif change == "missing_literal_proof":
            for fact in selected["screen_facts"]["facts"]:
                fact["evidence"] = []
        elif change == "changed_packet_value":
            selected["screen_facts"]["facts"][0]["value"] = "99"
        elif change == "missing_receipt_digest":
            selected["reuse"].pop("fact_packet_digest")
        else:
            selected["screen_facts"]["facts"][0]["evidence"][0]["region"]["left"] += .01
        assert not ObservationSnapshot.select(owner, selected, fields=plan.fields).matches(owner)

    asyncio.run(run())
