"""Actual producer -> commit -> formal handoff with moving synthetic pixels.

OCR/verifier are explicit external fakes; identity flags are NEVER injected.
Live-window results are separately recorded, not inferred from these tests.
"""
import asyncio
import copy
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import live_refresh
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, raw_page
from plugin.plugins.hsr_companion.tests.test_current_observation import ROLE
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, frame

ACTOR = {"region": {"left": .5, "right": .9, "top": .5, "bottom": .6}}


def pixels(change=False):
    image = Image.new("RGB", (640, 400), "black")
    if change == "large":
        ImageDraw.Draw(image).rectangle((320, 200, 575, 239), fill="white")
    elif change:
        ImageDraw.Draw(image).rectangle((335, 207, 343, 215), fill="white")
    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def dynamic_owner(tmp_path, *, agrees=True, portrait=False, page="team_setup"):
    owner, values, sent = audit_owner(tmp_path)
    raw = raw_page(page)
    raw["screen_facts"] = {"facts": [], "visible_text": [{**CONTROL, "text": "页面标题"}]}
    raw["characters"] = [{**ROLE, **ACTOR, "identity_scope": "hud_portrait" if portrait else "team_selection"}]
    if not portrait:
        raw["characters"][0]["raw_label"] = "银狼"
    verified = []
    def verify(data, proofs, **kwargs):
        verified.extend(copy.deepcopy(proofs))
        return {proof_key(p) for p in proofs if agrees or p.get("text") != "银狼"}
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels(True)))
    owner._vision = SimpleNamespace(analyze=lambda data: copy.deepcopy(raw), verify_text_regions=verify)
    owner._analysis_game_pid = 1
    return owner, values, sent, verified


async def produce(owner):
    value = await live_refresh.guarded_analysis(owner, pixels(), helper=True)
    assert value is not None
    await owner._apply_local_analysis(value, image_fingerprint="dynamic-memory-only",
        source="assistant_local_ocr", deliver_event=False)
    return value


@pytest.mark.parametrize("page", ["team_setup", "warp", "character_detail", "exploration_hud", "story_dialogue"])
def test_dynamic_literal_name_reaches_formal_entry_via_real_producer(tmp_path, page):
    owner, _, _, verified = dynamic_owner(tmp_path, page=page)
    async def run():
        await produce(owner)
        await produce(owner)
        result = await owner.hsr_companion_prepare(query="现在是什么页面？都有谁？")
        assert result["query_answer"]["state"] == "ready", result["query_timing"]
        assert result["screen_identity_answer"]["names"] == ["银狼"]
        assert any(p.get("text") == "银狼" for p in verified)
    asyncio.run(run())


@pytest.mark.parametrize("portrait,agrees", [(False, False), (True, True)])
def test_unproven_actor_does_not_block_independently_confirmed_page(tmp_path, portrait, agrees):
    owner, _, _, _ = dynamic_owner(tmp_path, agrees=agrees, portrait=portrait)
    async def run():
        await produce(owner)
        await produce(owner)
        result = await owner.hsr_companion_prepare(query="现在是什么页面？都有谁？")
        assert result["screen_scene_answer"]["allowed"]
        assert result["screen_scene_answer"]["page_type"] == "team_setup"
        assert result["screen_identity_answer"]["names"] == []
        assert result["query_answer"]["state"] == "partial"
    asyncio.run(run())


def test_missing_identity_proof_preserves_page_without_authorizing_names(tmp_path):
    owner, _, _, _ = dynamic_owner(tmp_path)
    async def run():
        await produce(owner)
        await produce(owner)
        owner._accepted_frame_identity_guarded = False  # Deliberate missing-proof fault, not success setup.
        owner._reusable_observations = {}
        current = await owner._current_visual_evidence()
        selected = await live_refresh.validated_delivery(owner, current, plan=build_query_plan("现在是什么页面？都有谁？"))
        assert selected is not None
        assert selected["page_type"] == "team_setup"
        assert selected["characters"] == []
        assert not getattr(owner, "_observation_pending_reason", "")
    asyncio.run(run())


@pytest.mark.parametrize("path", ["direct", "cache"])
@pytest.mark.parametrize("agrees", [True, False])
def test_dynamic_postcommit_actor_check_and_partial_page(tmp_path, path, agrees):
    owner, _, _, proofs = dynamic_owner(tmp_path, agrees=agrees)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels()))
    async def run():
        await produce(owner)
        await produce(owner)
        original = copy.deepcopy(owner._committed_observation)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels(True)))
        plan = build_query_plan("现在是什么页面？都有谁？")
        if path == "cache":
            selected = await try_reuse(owner, navigation=False, full=True, plan=plan)
        else:
            selected = await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=plan)
        assert selected is not None
        assert selected["page_type"] == "team_setup"
        assert [c["name"] for c in selected["characters"]] == (["银狼"] if agrees else [])
        assert any(p.get("text") == "银狼" for p in proofs)
        assert owner._committed_observation == original  # Quarantine is read-side only.
        assert not getattr(owner, "_observation_pending_reason", "")
        if not agrees:
            current = await owner._current_visual_evidence()
            assert current["page_type"] == "team_setup" and not current["characters"]
    asyncio.run(run())


@pytest.mark.parametrize("path", ["direct", "cache"])
def test_real_page_control_change_still_rejects_all_paths(tmp_path, path):
    owner, _, _, _ = dynamic_owner(tmp_path)
    async def run():
        await produce(owner)
        await produce(owner)
        image = Image.open(BytesIO(pixels(True))).convert("RGB")
        ImageDraw.Draw(image).rectangle((0, 0, 200, 50), fill="white")
        out = BytesIO()
        image.save(out, format="PNG")
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(out.getvalue()))
        # Model a changed heading, not an oracle that approves every old label.
        owner._vision.verify_text_regions = lambda data, proofs, **kw: {
            proof_key(p) for p in proofs if p.get("text") != "页面标题"}
        plan = build_query_plan("现在是什么页面？都有谁？")
        if path == "cache":
            result = await try_reuse(owner, navigation=False, full=True, plan=plan)
        else:
            result = await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=plan)
        assert result is None
        assert not (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())


@pytest.mark.parametrize("path", ["direct", "cache"])
def test_changed_equipment_value_preserves_page_not_old_stat(tmp_path, path):
    from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import commit_raw
    owner, _, _ = audit_owner(tmp_path)
    raw = raw_page("relic_detail")
    raw["screen_facts"]["facts"] = [{"scope": "equipment", "kind": "main_stat", "label": "主词条·速度",
        "value": "25", "evidence": [{**ACTOR, "text": "速度25"}]}]
    owner._vision = SimpleNamespace(verify_text_regions=lambda *a, **kw: set())
    async def run():
        await commit_raw(owner, raw, pixels())
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels(True)))
        plan = build_query_plan("现在是什么页面？这件遗器的主词条是什么？")
        if path == "cache":
            selected = await try_reuse(owner, navigation=False, full=True, plan=plan)
        else:
            selected = await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=plan)
        assert selected is not None
        assert selected["page_type"] == "relic_detail"
        assert not selected["screen_facts"]["facts"]
        assert owner._committed_observation["screen_facts"]["facts"][0]["value"] == "25"
    asyncio.run(run())


def test_followup_consumes_actual_producer_admitted_name(tmp_path, monkeypatch):
    import time

    from plugin.plugins.hsr_companion.runtime import followup
    owner, _, sent, _ = dynamic_owner(tmp_path)
    owner._target_character = lambda: "test-cat-never-sent-to-host"
    async def available(*a, **kw):
        return "available", None
    monkeypatch.setattr(followup, "checked_activity", available)
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    packets = []
    original = followup.record_answer_delivery
    def record(*args, **kwargs):
        packets.append(kwargs["payload"])
        return original(*args, **kwargs)
    monkeypatch.setattr(followup, "record_answer_delivery", record)
    async def run():
        await produce(owner)
        await produce(owner)
        plan = build_query_plan("现在是什么页面？都有谁？")
        req = followup.AnswerRequest("dynamic", plan.query, owner._target_character(), time.time(), 0,
            float("inf"), "team_setup", False, False, False, False, plan)
        owner._pending_answer = req
        await asyncio.wait_for(followup.deliver_followup(owner, req), 2)
        replies = [s for s in sent if s.get("metadata", {}).get("kind") == "query_followup"]
        assert len(replies) == 1 and replies[0]["metadata"]["outcome"] == "ready"
        # The approved D1 notification uses compact text, not the former JSON
        # envelope. Check the actual admitted packet AND receiving bridge.
        from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import received_text
        assert [c["name"] for c in packets[0]["characters"]] == ["银狼"]
        assert "角色：银狼" in received_text(replies[0])
    asyncio.run(run())


def test_low_score_classification_anchor_keeps_literal_for_strict_reread():
    from plugin.plugins.hsr_companion.detectors.layout import scene_control_regions
    boxes = [{"text": "行迹", "score": .72, "left": 10, "top": 20, "right": 80, "bottom": 40}]
    controls = scene_control_regions({"page_type": "character_detail"}, boxes, width=640, height=400)
    assert len(controls) == 1 and controls[0]["text"] == "行迹"
    assert controls[0]["control"] is True


def test_control_reread_still_requires_high_confidence_on_new_pixels():
    from plugin.plugins.hsr_companion.adapters.text_verification import verify_text_regions
    backend = SimpleNamespace(recognize_line=lambda im: [("行迹", .72)])
    proof = {**ACTOR, "text": "行迹", "control": True}
    assert verify_text_regions(backend, pixels(), [proof]) == set()
    backend.recognize_line = lambda im: [("行迹", .99)]
    assert verify_text_regions(backend, pixels(), [proof]) == {proof_key(proof)}


def test_large_dynamic_name_background_is_not_coarse_page_switch(tmp_path):
    owner, _, _, _ = dynamic_owner(tmp_path)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels("large")))
    async def run():
        await produce(owner)
        await produce(owner)
        result = await owner.hsr_companion_prepare(query="现在是什么页面？都有谁？")
        assert result["screen_identity_answer"]["names"] == ["银狼"]
        assert result["query_answer"]["state"] == "ready"
    asyncio.run(run())


def test_resident_watch_quarantines_identity_not_independent_page(tmp_path):
    owner, _, _, _ = dynamic_owner(tmp_path)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels()))
    async def run():
        await produce(owner)
        await produce(owner)
        epoch = getattr(owner, "_observation_epoch", 0)
        assert await live_refresh.watch_latest_frame(owner, pixels("large"), helper=True)
        current = await owner._current_visual_evidence()
        assert current["page_type"] == "team_setup"
        assert not current["characters"]
        assert getattr(owner, "_observation_epoch", 0) == epoch
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels("large")))
        recovered = await try_reuse(owner, full=True, navigation=False, plan=build_query_plan("现在是什么页面？都有谁？"))
        assert recovered is not None and recovered["characters"][0]["name"] == "银狼"
    asyncio.run(run())


def test_same_capture_early_page_keeps_its_own_control_proof(tmp_path):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog
    count = []
    def extract(im):
        count.append(True)
        return ("常用队伍快速编队已启用", [box("常用队伍快速编队已启用", 100, 365)]) if len(count) == 1 else ("", [])
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    previews = []
    result = adapter.analyze_live(pixels(), cancelled=lambda: False, on_scene=previews.append)
    controls = result["recognition"]["scene_regions"]
    assert controls and controls == previews[0]["recognition"]["scene_regions"]
    assert controls[0]["text"] == "常用队伍快速编队已启用"


def test_scene_only_controls_keep_literal_not_only_box(tmp_path):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=lambda im: ("队伍编成", [box("队伍编成", 20, 20)]))
    value = adapter.analyze_scene(pixels())
    assert value["recognition"]["scene_regions"]
    assert all(r.get("text") and r.get("control") for r in value["recognition"]["scene_regions"])


def test_ambiguous_task_literal_does_not_erase_social_hud_or_location(tmp_path):
    from plugin.plugins.hsr_companion.detectors.exploration import task_stacks
    from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page
    from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
    from plugin.plugins.hsr_companion.tests.test_navigation_evidence import row
    from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog
    boxes = [row("地点甲", 68, 17, 194, 54), row("任务甲", 99, 374, 302, 414),
        row("任务乙", 99, 374, 302, 414), row("询问向导", 104, 425, 420, 461),
        row("请前往目的地甲", 152, 478, 632, 511), row("Enter", 100, 1423, 202, 1468)]
    assert not task_stacks(boxes, width=2560, height=1600)
    page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=2560, height=1600)
    assert page["page_type"] == "exploration_hud"
    facts = extract_screen_facts(boxes, width=2560, height=1600, page=page, catalog=catalog())["facts"]
    location = next(f for f in facts if f["kind"] == "current_location")
    assert location["value"] == "地点甲"
    assert not any(f["kind"] in {"tracked_quest", "quest_objective"} for f in facts)
    # An independently readable navigation destination remains a destination,
    # not a task title/objective or current location.
    assert all(f["value"] == "目的地甲" for f in facts if f["kind"] == "quest_destination")
    literal = [p for p in location["evidence"] if p.get("validation_scope") != "control_presence"]
    assert [p["text"] for p in literal] == ["地点甲"]


def test_partial_navigation_reuses_proven_location_without_inventing_task(tmp_path):
    from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import commit_raw, fact
    owner, _, _ = audit_owner(tmp_path)
    raw = raw_page("exploration_hud")
    raw["screen_facts"]["facts"] = [fact("current_location", "location", "当前位置", "地点甲", 0)]
    raw["scene"].update(primary_state="exploration", verified=True, confidence="high")
    async def run():
        await commit_raw(owner, raw)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels()))
        selected = await try_reuse(owner, navigation=True, plan=build_query_plan("现在在哪，接下来任务做什么"))
        assert selected is not None
        assert [f["value"] for f in selected["screen_facts"]["facts"]] == ["地点甲"]
    asyncio.run(run())


@pytest.mark.parametrize("before_query", [True, False])
@pytest.mark.parametrize("agrees", [True, False])
def test_resident_control_quarantine_is_released_only_by_matching_scoped_proof(tmp_path, monkeypatch, before_query, agrees):
    owner, _, _, _ = dynamic_owner(tmp_path)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels()))
    image = Image.open(BytesIO(pixels())).convert("RGB")
    ImageDraw.Draw(image).rectangle((20, 15, 28, 23), fill="white")
    out = BytesIO()
    image.save(out, format="PNG")
    changed = out.getvalue()
    original_verify = live_refresh.revalidate_guard_text
    async def verify(*args, **kwargs):
        if not before_query:
            await live_refresh.watch_latest_frame(owner, changed, helper=True)
        return await original_verify(*args, **kwargs)
    monkeypatch.setattr(live_refresh, "revalidate_guard_text", verify)
    async def run():
        await produce(owner)
        await produce(owner)
        epoch = getattr(owner, "_observation_epoch", 0)
        owner._vision.verify_text_regions = lambda data, proofs, **kw: {proof_key(p) for p in proofs} if agrees else set()
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed))
        if before_query:
            await live_refresh.watch_latest_frame(owner, changed, helper=True)
            assert owner._observation_pending_reason == "verification_pending_control"
        selected = await try_reuse(owner, navigation=False, full=True, plan=build_query_plan("现在是什么页面？都有谁？"))
        assert getattr(owner, "_observation_epoch", 0) == epoch
        if agrees:
            assert selected is not None
            assert selected["characters"][0]["name"] == "银狼"
            from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
            assert ObservationSnapshot.select(owner, selected, fields={"page", "characters"}).matches(owner)
            # Checked delivery is scoped; unrelated current fields stay masked.
            assert owner._observation_pending_reason == "verification_pending_control"
        else:
            assert selected is None
            assert owner._observation_pending_reason == "verification_pending_control"
    asyncio.run(run())


def test_changed_cache_joins_resident_producer_instead_of_queuing_old_frame(tmp_path):
    from plugin.plugins.hsr_companion.runtime.vision_scheduler import vision_turn
    owner, _, _, _ = dynamic_owner(tmp_path)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels()))
    async def run():
        await produce(owner)
        await produce(owner)
        admitted, release = asyncio.Event(), asyncio.Event()
        async def resident():
            async with vision_turn(owner, "producer"):
                admitted.set()
                await release.wait()
        task = asyncio.create_task(resident())
        await admitted.wait()
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(pixels(True)))
        try:
            # No inference is mocked successful and the real gate is occupied.
            # A cache miss must leave the query budget to the existing producer.
            selected = await asyncio.wait_for(try_reuse(owner, navigation=False, full=True,
                plan=build_query_plan("现在是什么页面？都有谁？")), timeout=.5)
            assert selected is None
            assert not task.done()
        finally:
            release.set()
            await task
    asyncio.run(run())
