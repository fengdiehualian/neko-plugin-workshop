"""Pipeline candidate contracts; synthetic OCR is NOT live recognition acceptance.

Keep the frozen 0.9.5 real-play test plan/run unchanged. These tests exercise
producer -> state -> panel projection / tool -> recovery, across page families.
"""
import asyncio
import copy
import time
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
from plugin.plugins.hsr_companion.runtime.live_refresh import guarded_analysis, refresh_for_query
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
from plugin.plugins.hsr_companion.runtime.query_coordinator import answer_status, needs_followup
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, TEXT, frame, picture
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request, visual


def page_result(page="battle_pass", scene="menu"):
    value = analysis(roles=False, scene=scene)
    value["page"] = {"page_type": page, "label": page}
    value["recognition"]["scene_regions"] = [CONTROL]
    value["screen_facts"] = {
        "facts": [{"kind": "resource_count", "scope": "resource", "label": "星琼", "value": "1600",
                   "evidence": [{"text": "1600", **TEXT}]}],
        "visible_text": [{"text": "星琼 1600", **TEXT}],
    }
    return value


@pytest.mark.parametrize("page,scene", [
    ("exploration_hud", "exploration"), ("team_setup", "menu"), ("warp", "warp"),
    ("character_detail", "menu"), ("relic_detail", "menu"), ("mission_menu", "menu"),
    ("battle_pass", "menu"), ("activity_menu", "menu"),
])
def test_common_page_result_reaches_state_display_and_repeated_query(tmp_path, page, scene):
    owner, values, _ = plugin_fixture(tmp_path)
    calls = []
    payload = picture()
    owner._vision = SimpleNamespace(analyze=lambda data: calls.append(data) or page_result(page, scene))
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
    owner._analysis_game_pid = 1
    owner._observer_task = SimpleNamespace(done=lambda: False)

    async def run():
        selected = await guarded_analysis(owner, payload, helper=True)
        await owner._apply_local_analysis(selected, image_fingerprint="memory", source="assistant_local_ocr")
        display = await ObservationStore(owner).display()
        assert display["current"]["page_type"] == page
        assert display["current"]["screen_facts"]["facts"][0]["value"] == "1600"
        for _ in range(2):
            answer = await refresh_for_query(owner, timeout=2, require_characters=True, require_content=True)
            assert answer is not None, owner._last_refresh_diagnostics
            assert answer["page_type"] == page
            assert answer["screen_facts"]["facts"][0]["value"] == "1600"
            assert answer["reuse"]["ocr_repeated"] is False
            # Missing ticket count must not turn into zero or erase star jade.
            assert len(answer["screen_facts"]["facts"]) == 1
        owner._target_character = lambda: ""
        tool = await owner.hsr_companion_prepare(query="现在有多少星琼和专票")
        assert tool["query_answer"]["state"] == "partial"
        assert tool["screen_context_answer"]["facts"][0]["value"] == "1600"
        assert tool["query_answer"]["resource_fields"]["missing"] == ["星轨专票"]
        assert "observation_display" not in tool  # Archive is panel-only.
        assert len(calls) == 1
        assert not values["last_local_vision"].get("image_bytes")
    asyncio.run(run())


def test_transient_capture_masks_current_but_keeps_data_and_recovers(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    async def run():
        await owner._apply_local_analysis(page_result(), image_fingerprint="a", source="assistant_local_ocr")
        original = copy.deepcopy(values["last_local_vision"])
        epoch = getattr(owner, "_observation_epoch", 0)
        await owner._invalidate_current_observation("capture_unavailable_for_reuse")
        display = await ObservationStore(owner).display()
        assert not display["current"]["fresh"]
        assert not display["current"]["screen_facts"]["facts"]
        assert display["last_successful"]["screen_facts"]["facts"][0]["value"] == "1600"
        assert not display["last_successful"]["fresh"]
        assert values["last_local_vision"] == original
        assert getattr(owner, "_observation_epoch", 0) == epoch
        display["last_successful"]["screen_facts"]["facts"].clear()
        assert (await ObservationStore(owner).display())["last_successful"]["screen_facts"]["facts"]
        await owner._apply_local_analysis(page_result(), image_fingerprint="b", source="assistant_local_ocr")
        assert (await owner._current_visual_evidence())["fresh"]
        assert not owner._observation_pending_reason
    asyncio.run(run())


def test_real_page_change_retains_history_but_never_answers_from_it(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    async def run():
        await owner._apply_local_analysis(page_result(), image_fingerprint="a", source="assistant_local_ocr")
        await owner._invalidate_current_observation("new_page_title")
        display = await ObservationStore(owner).display()
        assert not display["current"]["fresh"]
        assert display["last_successful"]["page_type"] == "battle_pass"
        assert values["last_local_vision"]["invalidated"]
        assert not owner._reusable_observations
        next_page = page_result("mission_menu")
        next_page["screen_facts"] = {"facts": [], "visible_text": []}
        await owner._apply_local_analysis(next_page, image_fingerprint="b", source="assistant_local_ocr")
        current = await owner._current_visual_evidence()
        assert current["page_type"] == "mission_menu"
        assert not current["screen_facts"]["facts"]  # No cross-page currency merge.
    asyncio.run(run())


def test_same_page_preview_does_not_replace_full_read_during_query(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        value = page_result()
        await owner._apply_local_analysis(value, image_fingerprint="a", source="assistant_local_ocr")
        serial = owner._observation_serial
        owner._query_refresh_after = time.time() + 1
        owner._query_refresh_serial = serial
        preview = {**value, "characters": [], "screen_facts": {}, "recognition": {"phase": "scene_only"}}
        await owner._publish_scene_preview(preview)
        assert not getattr(owner, "_scene_preview", None)
        assert owner._observation_serial == serial
        assert owner._committed_observation["screen_facts"]["facts"]
        assert (await ObservationStore(owner).display())["last_successful"]["screen_facts"]["facts"]
    asyncio.run(run())


def test_changed_literal_rejects_full_cache(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._vision = SimpleNamespace(analyze=lambda data: page_result())
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    owner._analysis_game_pid = 1
    owner._observer_task = SimpleNamespace(done=lambda: False)
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        await owner._apply_local_analysis(selected, image_fingerprint="a", source="assistant_local_ocr")
        owner._assistant_client.fetch_frame = lambda seq: frame(picture("text"))
        returned = await refresh_for_query(owner, timeout=.2, require_characters=True, require_content=True)
        assert returned is None or not returned["screen_facts"]["facts"] and not returned["screen_facts"]["visible_text"]
        current = await owner._current_visual_evidence()
        assert current["fresh"] and not current["screen_facts"]["facts"]
        assert not current["screen_facts"]["visible_text"]
        assert (await ObservationStore(owner).display())["last_successful"]["screen_facts"]["facts"]
    asyncio.run(run())


def test_transient_error_can_register_and_deliver_one_recovered_answer(monkeypatch):
    owner, sent = followup_owner(monkeypatch)
    owner._observer_status.update(enabled=True, state="error")
    plan = build_query_plan("现在是什么页面")
    assert needs_followup(plan, visual(ready=False), owner._observer_status)
    async def run():
        armed = followup.arm_followup(owner, query=plan.query, visual=visual(ready=False), asked_at=time.time(),
            scene_only=True, navigation=False, content=False, controlled=False, plan=plan)
        assert armed["pending"]
        await owner._followup_task
        assert len(sent) == 1 and sent[0]["metadata"]["outcome"] == "ready"
    asyncio.run(run())


def test_transient_error_deadline_closes_obligation_without_a_fake_success(monkeypatch):
    owner, sent = followup_owner(monkeypatch, value=visual(ready=False))
    owner._observer_status.update(enabled=True, state="error")
    async def run():
        from dataclasses import replace
        req = replace(request(), expires_at=0)
        owner._pending_answer = req
        await followup.deliver_followup(owner, req)
        assert not sent
        assert owner._followup_status["state"] == "expired"
        assert owner._followup_status["outcome"] == "timeout"
        assert owner._pending_answer is None
    asyncio.run(run())


def test_resource_fields_have_independent_readiness_and_grounding(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        await owner._apply_local_analysis(page_result(), image_fingerprint="a", source="assistant_local_ocr")
        current = await owner._current_visual_evidence()
        star = build_query_plan("现在有多少星琼")
        both = build_query_plan("现在有多少星琼和专票")
        ticket = build_query_plan("我有多少专票")
        assert star.screen and ticket.screen
        assert answer_status(star, current, owner._observer_status)["state"] == "ready"
        status = answer_status(both, current, owner._observer_status)
        assert status["state"] == "partial"
        assert status["resource_fields"] == {"confirmed": ["星琼"], "missing": ["星轨专票"]}
        grounded = build_grounding(both.query, current, owner._catalog, plan=both)
        assert grounded["facts"][0]["value"] == "1600"
        assert any("星轨专票" in item for item in grounded["missing_information"])
        assert not build_grounding(ticket.query, current, owner._catalog, plan=ticket)["facts"]
        assert not build_query_plan("星琼怎么获得").screen  # Knowledge is not account data.
    asyncio.run(run())


def test_pre_suspend_snapshot_cannot_bypass_answer_revocation(tmp_path):
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        await owner._apply_local_analysis(page_result(), image_fingerprint="a", source="assistant_local_ocr")
        selected = ObservationSnapshot.select(owner, await owner._current_visual_evidence())
        assert selected.matches(owner)
        await owner._invalidate_current_observation("capture_error")
        assert not selected.matches(owner)
        assert not (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())
