"""Resident commit -> query/follow-up handoff. Synthetic pixels, not OCR accuracy."""
import asyncio
import copy
import threading
import time
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

from plugin.plugins.hsr_companion.adapters.text_reader import ObservationSuperseded
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import followup, live_refresh, manual_refresh
from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import TEXT, frame, picture
from plugin.plugins.hsr_companion.tests.test_delivery_races import OTHER, commit, complete_commit
from plugin.plugins.hsr_companion.tests.test_page_followup import png


def equipment_plan():
    return replace(build_query_plan("这个遗器词条怎么样"), fields=frozenset({"equipment"}),
                   topics=frozenset({"equipment"}), equipment_kinds=frozenset({"main_stat", "sub_stat"}))


def test_busy_verification_keeps_commit_but_withholds_challenged_fields(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    def busy(*args, **kwargs):
        raise ObservationSuperseded()

    async def run():
        await commit(owner, picture())
        original = copy.deepcopy(owner._committed_observation)
        owner._vision = SimpleNamespace(verify_text_regions=busy)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        assert not await live_refresh.validate_before_delivery(owner)
        assert owner._committed_observation == original
        current = await owner._current_visual_evidence()
        assert not satisfied_fields(equipment_plan(), current)
        assert current["page_type"] == "relic_detail"
        assert owner._last_delivery_verification["state"] == "busy"

    asyncio.run(run())


def test_scoped_delivery_ignores_unreturned_unrelated_text(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        # The animated TEXT proof belongs to an unrelated on-screen counter.
        raw = owner._committed_observation
        raw["screen_facts"]["facts"][0].update(scope="resource", kind="resource_count", label="星琼")
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        plan = replace(equipment_plan(), equipment_kinds=frozenset({"sub_stat"}))
        current = await owner._current_visual_evidence()
        selected = await live_refresh.validated_delivery(owner, current, plan=plan)
        assert selected is not None
        assert [f["kind"] for f in selected["screen_facts"]["facts"]] == ["sub_stat"]
        assert not selected["screen_facts"]["visible_text"]
        assert not selected["characters"]

    asyncio.run(run())


def test_scoped_cache_and_direct_delivery_share_the_same_field_guard(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        snapshot = owner._reusable_observations["full"]
        snapshot["evidence"]["screen_facts"]["facts"][0].update(scope="resource", kind="resource_count")
        owner._committed_observation["screen_facts"]["facts"][0].update(scope="resource", kind="resource_count")
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        plan = replace(equipment_plan(), equipment_kinds=frozenset({"sub_stat"}))
        selected = await try_reuse(owner, navigation=False, full=True, plan=plan)
        assert selected is not None
        assert [f["kind"] for f in selected["screen_facts"]["facts"]] == ["sub_stat"]

    asyncio.run(run())


def test_new_commit_clears_deferred_field_mask(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        coordinates = {tuple(TEXT["region"][k] for k in ("left", "top", "right", "bottom"))}
        from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
        ObservationStore(owner).defer_regions(coordinates)
        assert not satisfied_fields(equipment_plan(), await owner._current_visual_evidence())
        await complete_commit(owner, picture("text"), "20")
        assert satisfied_fields(equipment_plan(), await owner._current_visual_evidence()) == {"equipment"}

    asyncio.run(run())


def test_manual_refresh_character_only_on_relic_page_is_partial(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._observer_task = SimpleNamespace(done=lambda: False)

    async def run():
        await commit(owner, picture())
        manual_refresh.request_manual_refresh(owner)
        # Commit the actual partial observation. Mutating only a returned
        # projection fabricated a packet inconsistent with its committed
        # identity/rows and raced the background manual task's first read.
        from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import commit_raw, raw_page
        from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL
        partial = raw_page()
        partial["screen_facts"]["facts"] = []
        partial["characters"] = [{"name": "真珠", "entity_id": "character.pearl",
                                  "identity_scope": "detail_title", **CONTROL}]
        await commit_raw(owner, partial)
        await owner._manual_refresh_task
        assert manual_refresh.public_manual_refresh(owner)["state"] == "partial"

    asyncio.run(run())


def test_busy_query_joins_resident_commit_without_discard_or_second_producer(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    calls = []

    def busy(*args, **kwargs):
        raise ObservationSuperseded()

    async def run():
        await commit(owner, picture())
        owner._vision = SimpleNamespace(verify_text_regions=busy)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        owner._visual_update_event = asyncio.Event()
        epoch = getattr(owner, "_observation_epoch", 0)

        async def resident():
            await asyncio.sleep(.08)
            calls.append("existing_producer")
            await commit(owner, picture("text"), "20")

        owner._observer_task = asyncio.create_task(resident())
        selected = await live_refresh.refresh_for_query(owner, timeout=1, require_content=True, query_plan=equipment_plan())
        await owner._observer_task
        assert selected is not None, owner._last_refresh_diagnostics
        assert selected["screen_facts"]["facts"][0]["value"] == "20"
        assert getattr(owner, "_observation_epoch", 0) == epoch
        assert calls == ["existing_producer"]

    asyncio.run(run())


def test_next_producer_waits_for_bounded_delivery_turn(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        async with live_refresh.delivery_turn(owner):
            admission = asyncio.create_task(live_refresh.wait_delivery_turn(owner))
            await asyncio.sleep(0)
            assert not admission.done()
        await admission
        assert owner._delivery_waiters == 0
        try:
            async with live_refresh.delivery_turn(owner):
                raise RuntimeError("test cancellation boundary")
        except RuntimeError:
            pass
        assert owner._delivery_waiters == 0

    asyncio.run(run())


def test_followup_uses_scoped_guard_and_only_submits_requested_facts(tmp_path, monkeypatch):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._target_character = lambda: "猫娘"
    monkeypatch.setattr(type(owner), "bus", property(lambda self: SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: []))))

    async def activity(*args, **kwargs):
        return "available", None

    monkeypatch.setattr(followup, "latest_user_activity", activity)
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)

    async def run():
        await complete_commit(owner, picture())
        owner._committed_observation["screen_facts"]["facts"][0].update(scope="resource", kind="resource_count")
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        plan = replace(equipment_plan(), equipment_kinds=frozenset({"sub_stat"}))
        armed = followup.arm_followup(owner, query="副词条怎么样", visual={"fresh": False}, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False, plan=plan)
        assert armed["pending"]
        await asyncio.wait_for(owner._followup_task, 2)
        assert owner._followup_status["outcome"] == "ready"

    asyncio.run(run())
    answers = [s for s in sent if s.get("ai_behavior") == "respond"]
    assert len(answers) == 1
    from utils.result_parser import parse_push_message_content
    received = parse_push_message_content(answers[0]["parts"][0]["text"], lang="zh")
    for value in ("速度2", "暴击率3%", "生命值80", "效果抵抗5%"):
        assert value in received
    assert "resource_count" not in received and "主：" not in received


def test_true_page_switch_still_blocks_scoped_equipment(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("page")))
        selected = await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=equipment_plan())
        assert selected is None
        assert not (await owner._current_visual_evidence())["fresh"]

    asyncio.run(run())


def test_cached_text_cannot_be_approved_using_new_commit_text(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture(), "10")
        old = copy.deepcopy(owner._reusable_observations["full"])
        await commit(owner, picture("text"), "20")
        owner._reusable_observations["full"] = old
        owner._vision = SimpleNamespace(verify_text_regions=lambda payload, proofs, **kw:
            {proof_key(p) for p in proofs if p.get("text") == "20"})
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        selected = await try_reuse(owner, navigation=False, full=True, plan=equipment_plan())
        assert selected is None or not any(f["kind"] == "main_stat" for f in selected["screen_facts"]["facts"])

    asyncio.run(run())


def test_pending_field_in_manual_result_prevents_complete_label():
    value = {"page_type": "relic_detail", "fresh": True, "read_scope": "full",
             "screen_facts": {"facts": [{"kind": "main_stat", "scope": "equipment"},
                                         {"kind": "sub_stat", "scope": "equipment"}],
                              "field_checks": [{"kind": "sub_stat", "scope": "equipment", "state": "pending"}]}}
    assert not manual_refresh.content_ready(value)


def test_actual_adapter_lock_busy_does_not_delete_commit(tmp_path):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    owner, _, _ = plugin_fixture(tmp_path)
    adapter = LocalVisionAdapter(None)
    acquired, release = threading.Event(), threading.Event()

    def occupy():
        with adapter._inference_lock:
            acquired.set()
            release.wait(3)

    thread = threading.Thread(target=occupy)
    thread.start()
    assert acquired.wait(1)

    async def run():
        await commit(owner, picture())
        original = copy.deepcopy(owner._committed_observation)
        owner._vision = adapter
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        assert not await live_refresh.validate_before_delivery(owner)
        assert owner._last_delivery_verification["state"] == "busy"
        assert owner._committed_observation == original

    try:
        asyncio.run(run())
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


def test_scoped_confirmation_updates_only_checked_patches(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    calls = []

    def verify(payload, proofs, **kwargs):
        calls.append(1)
        return {proof_key(p) for p in proofs}

    async def run():
        await commit(owner, picture())
        owner._vision = SimpleNamespace(verify_text_regions=verify)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        original_time = owner._committed_observation["observed_at"]
        assert await try_reuse(owner, navigation=False, full=True, plan=equipment_plan()) is not None
        assert await try_reuse(owner, navigation=False, full=True, plan=equipment_plan()) is not None
        assert len(calls) == 1
        assert owner._committed_observation["observed_at"] == original_time

    asyncio.run(run())


def test_tiny_changed_heading_is_not_accepted_as_same_page(tmp_path):
    from PIL import Image, ImageDraw
    owner, _, _ = plugin_fixture(tmp_path)
    with Image.open(BytesIO(picture())) as image:
        ImageDraw.Draw(image).rectangle((10, 8, 14, 12), fill="white")
        changed = png(image)

    async def run():
        await commit(owner, picture())
        before = copy.deepcopy(owner._committed_observation)
        epoch = getattr(owner, "_observation_epoch", 0)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed))
        assert await try_reuse(owner, navigation=False, full=True, plan=equipment_plan()) is None
        assert not (await owner._current_visual_evidence())["fresh"]
        assert owner._committed_observation == before  # Wait for producer, no fake page acceptance.
        assert getattr(owner, "_observation_epoch", 0) == epoch

    asyncio.run(run())


def test_page_control_text_binding_respects_public_coordinate_precision():
    from plugin.plugins.hsr_companion.runtime.delivery_scope import scoped_guard
    from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp
    control = {"region": {"left": .0100000123, "top": .01, "right": .3, "bottom": .1}}
    row = {"region": {**control["region"], "left": .01}, "text": "遗器替换"}
    facts = [{"kind": "main_stat", "scope": "equipment", "value": "10", "evidence": [{**TEXT, "text": "10"}]}]
    evidence = {"screen_facts": {"facts": facts, "visible_text": [row]}}
    owner = SimpleNamespace(_accepted_frame_page_regions=[control], _committed_observation={})
    selected = scoped_guard(owner, evidence, equipment_plan(), [([control, TEXT], region_stamp(picture(), [control, TEXT]))])
    assert selected is not None
    assert selected[1][0][0]["text"] == "遗器替换"
    assert selected[1][0][0]["control"] is True


def test_unrelated_withdrawal_does_not_clear_deferred_main_stat(tmp_path):
    from plugin.plugins.hsr_companion.runtime.delivery_scope import coordinates
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        store = ObservationStore(owner)
        store.defer_regions({coordinates(TEXT)})
        store.withdraw_regions({coordinates(OTHER)})
        current = await owner._current_visual_evidence()
        assert not current["screen_facts"]["facts"]
        assert owner._committed_observation["screen_facts"]["facts"][0]["kind"] == "main_stat"

    asyncio.run(run())


def test_committed_result_is_offered_to_waiting_consumer_before_next_pass(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        owner._visual_query_waiters = 1
        admission = asyncio.create_task(live_refresh.wait_delivery_turn(owner))
        await asyncio.sleep(0)
        assert not admission.done()
        async with live_refresh.delivery_turn(owner):
            await asyncio.sleep(0)
            assert not admission.done()
            live_refresh.mark_delivery_verified(owner)  # Admission alone is not an ACK.
        await admission
        assert owner._delivery_seen_revision == (getattr(owner, "_observation_epoch", 0), owner._observation_serial)

    asyncio.run(run())
