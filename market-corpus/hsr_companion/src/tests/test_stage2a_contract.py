"""Stage 2A regression: resident production, evidence, readiness and handoff.

Synthetic frames/recognition with real serial adapter admission. These tests
do not claim real OCR accuracy or the host's final spoken response.
"""
import asyncio
import copy
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter, serial_ocr
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import followup, live_refresh
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_chain_20261017 import equipment_plan
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import TEXT, frame, picture, text_result
from plugin.plugins.hsr_companion.tests.test_delivery_races import complete_commit as commit


async def partial_commit(owner):
    raw = copy.deepcopy(owner._committed_observation)
    raw["screen_facts"]["facts"] = []
    await owner._apply_local_analysis(raw, image_fingerprint="partial", source="assistant_local_ocr")


def test_partial_cache_does_not_finish_query_before_resident_complete_commit(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        await partial_commit(owner)
        owner._reusable_observations.pop("full_previous", None)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
        owner._visual_update_event = asyncio.Event()

        async def resident():
            await asyncio.sleep(.05)
            await commit(owner, picture(), "20")
            await asyncio.Event().wait()

        owner._observer_task = asyncio.create_task(resident())
        try:
            selected = await live_refresh.refresh_for_query(owner, timeout=1.5, require_content=True,
                                                           query_plan=equipment_plan())
            assert selected is not None
            assert satisfied_fields(equipment_plan(), selected) == {"equipment"}
            assert selected["screen_facts"]["facts"][0]["value"] == "20"
        finally:
            owner._observer_task.cancel()
            await asyncio.gather(owner._observer_task, return_exceptions=True)

    asyncio.run(run())


def test_partial_resident_pass_retains_independently_revalidated_full_snapshot(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        for _ in range(3):
            await partial_commit(owner)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
        selected = await try_reuse(owner, navigation=False, full=True, plan=equipment_plan())
        assert selected is not None
        assert satisfied_fields(equipment_plan(), selected) == {"equipment"}
        assert selected["screen_facts"]["facts"][0]["value"] == "10"
        assert selected["reuse"]["ocr_repeated"] is False
        # No merging of yesterday's item into today's current projection.
        assert not (await owner._current_visual_evidence())["screen_facts"]["facts"]

    asyncio.run(run())


def test_retained_snapshot_never_authorizes_changed_item(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        await partial_commit(owner)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        assert await try_reuse(owner, navigation=False, full=True, plan=equipment_plan()) is None

    asyncio.run(run())


def test_partial_pass_does_not_replace_more_complete_display_archive(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        before = copy.deepcopy(owner._last_successful_observation)
        await partial_commit(owner)
        assert owner._last_successful_observation == before
        display = await ObservationStore(owner).display()
        assert display["last_successful"]["status"] == "historical"
        assert not display["last_successful"]["fresh"]

    asyncio.run(run())


def test_failed_delivery_attempt_does_not_acknowledge_revision(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        revision = (getattr(owner, "_observation_epoch", 0), owner._observation_serial)
        async with live_refresh.delivery_turn(owner):
            pass  # An attempt never obtained a verified packet.
        assert getattr(owner, "_delivery_seen_revision", None) != revision

    asyncio.run(run())


def test_resident_ocr_and_delivery_verification_do_not_race_adapter_gate(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Vision(LocalVisionAdapter):
        @serial_ocr
        def analyze(self, payload, **kwargs):
            entered.set()
            assert release.wait(3)
            calls.append("analysis")
            return text_result()

        @serial_ocr
        def verify_text_regions(self, payload, proofs, **kwargs):
            calls.append("verification")
            return {proof_key(p) for p in proofs}

    async def run():
        await commit(owner, picture())
        owner._vision = Vision(None)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        producer = asyncio.create_task(live_refresh.guarded_analysis(owner, picture(), helper=True))
        assert await asyncio.to_thread(entered.wait, 1)
        consumer = asyncio.create_task(live_refresh.validate_before_delivery(owner, plan=equipment_plan()))
        try:
            await asyncio.sleep(.3)  # Exceeds the adapter's old .2s busy admission.
            assert consumer.done(), "old proof review must yield to the resident producer, not queue a stale frame"
            release.set()
            produced, delivered = await asyncio.wait_for(asyncio.gather(producer, consumer), 3)
            assert produced is not None
            assert not delivered  # No old packet was certified while native OCR owned the producer gate.
            assert calls[0] == "analysis"
            assert "verification" in calls
        finally:
            release.set()
            await asyncio.gather(producer, consumer, return_exceptions=True)

    asyncio.run(run())


def test_changed_literal_remains_unconfirmed_even_with_shared_page_control():
    # A page/control proof cannot certify a changed numeric value.
    value = text_result()
    value["screen_facts"] = {"facts": [{"kind": "main_stat", "scope": "equipment",
        "label": "主词条·防御力", "value": "10", "evidence": [{**TEXT, "text": "10"}]}], "visible_text": []}
    selected = live_refresh.stable_screen_content(value, picture(), picture("text"))
    assert not selected["screen_facts"]["facts"]


def test_complete_different_item_replaces_archive_instead_of_freezing_old_labels(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture(), "10")
        raw = copy.deepcopy(owner._committed_observation)
        raw["screen_facts"]["facts"][0].update(label="主词条·攻击力", value="99")
        await owner._apply_local_analysis(raw, image_fingerprint="different", source="assistant_local_ocr")
        assert owner._last_successful_observation["screen_facts"]["facts"][0]["value"] == "99"

    asyncio.run(run())


def test_retained_old_snapshot_needs_current_proof_and_cannot_bypass_pause(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        await partial_commit(owner)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
        old = owner._reusable_observations["full_previous"]
        old["evidence"]["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        selected = await try_reuse(owner, navigation=False, full=True, plan=equipment_plan())
        assert selected is not None and selected["observed_at"] == old["evidence"]["observed_at"]
        old["evidence"]["observed_at"] = datetime.now(timezone.utc).isoformat()
        owner._observer_status.update(enabled=False, state="paused")
        assert await try_reuse(owner, navigation=False, full=True, plan=equipment_plan()) is None

    asyncio.run(run())


def test_followup_can_deliver_verified_retained_packet_not_partial_current(tmp_path, monkeypatch):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._target_character = lambda: "猫娘"
    monkeypatch.setattr(type(owner), "bus", property(lambda self: SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: []))))
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)

    async def activity(*args, **kwargs):
        return "available", None

    monkeypatch.setattr(followup, "latest_user_activity", activity)

    async def run():
        await commit(owner, picture())
        await partial_commit(owner)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
        assert followup.arm_followup(owner, query="这个遗器词条怎么样", visual={"fresh": False},
            asked_at=time.time(), scene_only=False, navigation=False, content=True,
            controlled=False, plan=equipment_plan())["pending"]
        await asyncio.wait_for(owner._followup_task, 2)
        assert owner._followup_status["outcome"] == "ready"
        messages = [s for s in sent if s.get("ai_behavior") == "respond"]
        assert len(messages) == 1
        # The compact notification is the receiving transport's current text
        # contract, not the older JSON wrapper. Still prove the admitted value
        # and every requested row rather than asserting only a string format.
        direct = owner._answer_deliveries[-1]
        assert direct["equipment"]["main_stat_present"]
        assert direct["equipment"]["sub_stat_count"] == 4
        text = messages[0]["parts"][0]["text"]
        assert "主：防御力10" in text
        assert all(row in text for row in ("速度2", "暴击率3%", "生命值80", "效果抵抗5%"))
        assert owner._equipment_handoffs[-1]["channel"] == "followup"
        assert owner._equipment_handoffs[-1]["main_stat_present"]

    asyncio.run(run())


def test_rejected_retained_item_does_not_mask_new_partial_item(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture(), "10")
        retained = copy.deepcopy(owner._reusable_observations["full"])
        await commit(owner, picture("text"), "20")
        raw = copy.deepcopy(owner._committed_observation)
        raw["screen_facts"]["facts"] = raw["screen_facts"]["facts"][:1]
        await owner._apply_local_analysis(raw, image_fingerprint="new_partial", source="assistant_local_ocr")
        owner._reusable_observations["full_previous"] = retained
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        selected = await try_reuse(owner, navigation=False, full=True, plan=equipment_plan())
        assert selected is not None
        assert selected["screen_facts"]["facts"][0]["value"] == "20"
        assert not getattr(owner, "_deferred_verification", None)
        assert (await owner._current_visual_evidence())["screen_facts"]["facts"][0]["value"] == "20"

    asyncio.run(run())


def test_retained_text_revalidation_does_not_rebase_newer_commit_guard(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    from plugin.plugins.hsr_companion.runtime.delivery_scope import update_checked_patches
    from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp

    async def run():
        await commit(owner, picture(), "10")
        retained = copy.deepcopy(owner._reusable_observations["full"])
        await commit(owner, picture("text"), "20")
        current_guard = copy.deepcopy(owner._accepted_frame_full_query_guard)
        patches = region_stamp(picture(), retained["guard"][0])
        update_checked_patches(owner, patches, selected_snapshot=retained)
        assert owner._accepted_frame_full_query_guard == current_guard
        assert retained["guard"][1] == patches

    asyncio.run(run())


def test_actual_resident_loop_begins_before_equipment_question_and_commits_once(tmp_path):
    from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop, health, sample
    owner, _, _ = plugin_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Vision(LocalVisionAdapter):
        @serial_ocr
        def analyze(self, payload, **kwargs):
            calls.append(getattr(owner, "_visual_query_waiters", 0))
            entered.set()
            assert release.wait(3)
            value = text_result()
            value["page"] = {"page_type": "relic_detail", "label": "遗器详情"}
            value["screen_facts"] = {"facts": [
                {"kind": "main_stat", "scope": "equipment", "label": "主词条·防御力", "value": "10",
                 "evidence": [{**TEXT, "text": "10"}]},
                {"kind": "equipment_slot", "scope": "equipment", "label": "遗器部位", "value": "躯干",
                 "evidence": [{**TEXT, "text": "躯干"}]},
                {"kind": "equipment_level", "scope": "equipment", "label": "遗器强化等级", "value": "+15",
                 "evidence": [{**TEXT, "text": "+15"}]},
                *[{"kind": "sub_stat", "scope": "equipment", "label": "副词条·" + label, "value": number,
                   "evidence": [{**TEXT, "text": label + number}]}
                  for label, number in (("速度", "2"), ("效果抵抗", "5%"), ("暴击率", "3%"), ("生命值", "80"))]],
                "visible_text": []}
            return value

    async def run():
        configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: sample(picture())))
        owner._vision = Vision(None)
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            assert calls == [0]
            query = asyncio.create_task(live_refresh.refresh_for_query(owner, timeout=2,
                require_content=True, query_plan=equipment_plan()))
            await asyncio.sleep(.1)
            release.set()
            selected = await query
            assert selected is not None, owner._last_refresh_diagnostics
            assert satisfied_fields(equipment_plan(), selected) == {"equipment"}
            from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
            assert equipment_card(selected)["complete"]
            assert calls == [0]
            assert owner._observation_serial == 1
        finally:
            release.set()
            owner._observer_stop_requested = True
            owner._observer_task.cancel()
            await asyncio.gather(owner._observer_task, return_exceptions=True)
            await live_refresh.drain_analysis_worker(owner)

    asyncio.run(run())


def test_cancelled_native_work_is_drained_before_next_admission(tmp_path):
    from plugin.plugins.hsr_companion.runtime.vision_scheduler import analyze_image
    owner, _, _ = plugin_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def analyze(payload):
        calls.append(payload)
        entered.set()
        assert release.wait(3)
        return text_result()

    owner._vision = SimpleNamespace(analyze=analyze)

    async def run():
        first = asyncio.create_task(analyze_image(owner, b"first"))
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(analyze_image(owner, b"second"))
        try:
            await asyncio.sleep(.05)
            assert calls == [b"first"]
            assert not second.done()
            release.set()
            assert await asyncio.wait_for(second, 2)
            assert calls == [b"first", b"second"]
            assert owner._vision_schedule_waiters == 0
        finally:
            release.set()
            await asyncio.gather(second, return_exceptions=True)

    asyncio.run(run())
