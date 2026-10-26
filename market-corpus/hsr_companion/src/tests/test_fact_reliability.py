"""Accuracy contracts for the new candidate, NOT real-game accuracy claims."""
import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.navigation_reader import reread_navigation_lines
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key, verify_text_regions
from plugin.plugins.hsr_companion.runtime.diagnostic_reader import diagnostic_inputs
from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
from plugin.plugins.hsr_companion.runtime.live_refresh import (
    fetch_recent_frame,
    guarded_analysis,
    stable_screen_content,
    validate_before_delivery,
)
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot, ObservationStore
from plugin.plugins.hsr_companion.runtime.query_coordinator import needs_followup, satisfied_fields
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import TEXT, frame, picture, text_result
from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def proof(text="向虚照追问现况", region=None):
    return {"text": text, "region": region or TEXT["region"]}


def backend(reads):
    calls = []
    def extract(sample):
        calls.append(sample.size)
        text, score = reads[min(len(calls) - 1, len(reads) - 1)]
        return text, [dict(text=text, score=score, left=4, top=4, right=sample.width - 4, bottom=sample.height - 4)] if text else []
    return SimpleNamespace(extract_text_with_boxes=extract), calls


@pytest.mark.parametrize("reads,accepted", [
    ([("向虚照追问现况", .99)], True),
    ([("向虚照追问现况", .99), ("向虚照追问现状", .99)], False),
    ([("向虚照追问现状", .99)], False),
    ([("向虚照追问现况", .88)], False),
    ([("", .99)], False),
])
def test_new_frame_exact_text_needs_agreeing_native_and_enlarged_reads(reads, accepted):
    reader, calls = backend(reads)
    item = proof()
    result = verify_text_regions(reader, picture("text"), [item])
    assert (proof_key(item) in result) is accepted
    assert len(calls) == (2 if accepted or len(reads) == 2 else 3 if not reads[0][0] or reads[0][1] < .9 else 1)


def test_navigation_distance_only_normalized_for_control_proof():
    reader, _ = backend([("前往任务目标19米", .99)])
    literal = proof("前往任务目标2米")
    control = {**literal, "control": True}
    confirmed = verify_text_regions(reader, picture(), [control, literal])
    assert proof_key(control) in confirmed and proof_key(literal) not in confirmed


def test_oversized_or_missing_geometry_is_not_approved():
    reader, calls = backend([("文字", .99)])
    values = [proof("文字", dict(left=-.1, right=.9, top=.3, bottom=.4)), {"text": "文字"}]
    assert not verify_text_regions(reader, picture(), values)
    assert not calls


def test_text_equality_does_not_override_page_switch(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    value = text_result()
    owner._vision = SimpleNamespace(analyze=lambda data: value,
        verify_text_regions=lambda payload, proofs, **kwargs: {proof_key(p) for p in proofs})
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("page")))
    assert asyncio.run(guarded_analysis(owner, picture(), helper=True)) is None


def test_pixel_mismatch_keeps_exactly_revalidated_fact_not_unrelated_guess():
    value = text_result()
    value["screen_facts"]["facts"] = [dict(kind="quest_objective", scope="quest", value="向虚照追问现况", evidence=[proof()]),
        dict(kind="current_location", scope="location", value="未知地", evidence=[proof("未知地")])]
    def verify(payload, proofs, **kwargs):
        return {proof_key(p) for p in proofs if p["text"] == "向虚照追问现况"}
    selected = stable_screen_content(value, picture(), picture("text"), verifier=verify)
    assert [f["kind"] for f in selected["screen_facts"]["facts"]] == ["quest_objective"]
    assert selected["recognition"]["unstable_facts_removed"] == 1
    assert [f["state"] for f in selected["screen_facts"]["field_checks"]] == ["confirmed", "pending"]


def test_newly_revalidated_text_builds_guard_on_new_frame(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    owner._vision = SimpleNamespace(analyze=lambda data: text_result(),
        verify_text_regions=lambda payload, proofs, **kwargs: {proof_key(p) for p in proofs})
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
    selected = asyncio.run(guarded_analysis(owner, picture(), helper=True))
    assert selected is not None
    assert selected["screen_facts"]["visible_text"][0]["text"] == "规则说明"
    assert selected["recognition"]["text_revalidation_confirmed"] == 1


@pytest.mark.parametrize("query", ["有什么任务？", "现在什么任务", "当前是哪个任务？"])
def test_short_task_question_routes_to_current_quest_not_web_knowledge(query):
    plan = build_query_plan(query)
    assert plan.screen and plan.navigation and plan.fields == {"quest"}


def visual(facts):
    return dict(fresh=True, page_type="exploration_hud", scene={"verified": True}, screen_facts={"facts": facts})


def test_task_name_question_can_use_title_but_next_step_requires_objective():
    current = visual([dict(kind="tracked_quest", value="任务甲")])
    assert satisfied_fields(build_query_plan("有什么任务？"), current) == {"quest"}
    assert not satisfied_fields(build_query_plan("接下来这个任务要做什么？"), current)


def test_missing_fields_need_followup_even_when_worker_completed():
    plan = build_query_plan("有什么任务？")
    assert needs_followup(plan, visual([]), {"enabled": True, "state": "observing"})
    assert not needs_followup(plan, visual([dict(kind="quest_objective", value="找人")]), {"enabled": True})
    assert not needs_followup(plan, {**visual([]), "page_type": "warp"}, {"enabled": True})
    assert not needs_followup(plan, visual([]), {"enabled": False})


def test_diagnosis_does_not_wait_for_busy_sources_or_report_false_disconnection():
    async def blocked():
        await asyncio.Event().wait()
    async def ready():
        return {"fresh": True}
    async def run():
        owner = SimpleNamespace(_assistant_status=blocked, _read_tool_audit=blocked, _current_visual_evidence=ready)
        assistant, audit, evidence, incomplete = await asyncio.wait_for(diagnostic_inputs(owner, timeout=.02), .1)
        assert incomplete == ["assistant", "query_audit"] and not audit and evidence["fresh"]
        values = inputs()
        values.update(assistant=assistant, evidence=evidence)
        result = build_diagnostic_report(**values, incomplete_sources=incomplete)
        assert next(c for c in result["checks"] if c["name"] == "assistant")["detail"] == "not_checked"
        import json
        exported = json.loads(result["copy_text"])
        assert exported["assistant"]["connected"] is None
        assert not exported["diagnostic_read"]["complete"]
    asyncio.run(run())


def test_diagnostic_audit_is_allowlisted_and_distinguishes_answer_from_refresh():
    values = inputs()
    values["last_tool"] = {"refresh_diagnostics": {"outcome": "ready"},
        "answer_status_at_audit": {"state": "unconfirmed", "missing_fields": ["quest", "private secret"], "secret": "private secret"}}
    result = build_diagnostic_report(**values)
    assert result["last_query"]["outcome"] == "ready"
    assert result["last_query"]["answer_state_at_audit"] == "unconfirmed"
    assert result["last_query"]["missing_fields_at_audit"] == ["quest"]
    assert "private secret" not in result["copy_text"]


def test_withdraw_task_preserves_location_page_and_characters(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    value = text_result()
    location_proof = proof("地点", dict(left=.1, right=.4, top=.5, bottom=.6))
    value["screen_facts"]["facts"] = [dict(kind="current_location", scope="location", value="地点", evidence=[location_proof]),
                                      dict(kind="quest_objective", scope="quest", value="目标", evidence=[proof("目标")])]
    async def run():
        await owner._apply_local_analysis(value, image_fingerprint="memory", source="assistant_local_ocr")
        previous = await owner._current_visual_evidence()
        selected = ObservationSnapshot.select(owner, previous, fields={"location"})
        epoch = getattr(owner, "_observation_epoch", 0)
        assert ObservationStore(owner).withdraw_regions({tuple(TEXT["region"][k] for k in ("left", "top", "right", "bottom"))})
        current = await owner._current_visual_evidence()
        assert current["fresh"] and current["page_type"] == previous["page_type"]
        assert [f["kind"] for f in current["screen_facts"]["facts"]] == ["current_location"]
        assert current["characters"] == previous["characters"]
        assert getattr(owner, "_observation_epoch", 0) == epoch
        assert selected.matches(owner)
        task_selected = ObservationSnapshot.select(owner, previous, fields={"quest"})
        assert not task_selected.matches(owner)
    asyncio.run(run())


def test_memory_commit_is_detached_and_global_stop_still_revokes(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    value = text_result()
    async def run():
        await owner._apply_local_analysis(value, image_fingerprint="memory", source="assistant_local_ocr")
        committed = owner._committed_observation
        value["screen_facts"]["visible_text"].clear()
        assert committed["screen_facts"]["visible_text"]
        async def blocked(key):
            await asyncio.Event().wait()
        owner._get_store = blocked
        assert (await asyncio.wait_for(owner._current_visual_evidence(), .1))["fresh"]
        owner._stop_observation_now()
        assert owner._committed_observation is None
    asyncio.run(run())


@pytest.mark.parametrize("readings,expected", [
    ([("二维市", .99), ("二维市", .99)], "二维市"),
    ([("二维市", .99), ("维市", .99)], None),
    ([("二维市", .8)], None),
])
def test_native_navigation_replaces_only_consensus_reads_not_guessed_missing_glyph(readings, expected):
    from PIL import Image
    calls = []
    def recognize(sample):
        calls.append(sample.size)
        return [readings[min(len(calls) - 1, len(readings) - 1)]]
    box = dict(text="维市", score=.99, left=30, right=95, top=8, bottom=28)
    result, meta = reread_navigation_lines(SimpleNamespace(recognize_line=recognize), Image.new("RGB", (1000, 600)), [box])
    if expected:
        assert [b["text"] for b in result] == [expected]
    else:
        # Keep a physical placeholder to prevent row-shifting, not a value.
        assert result and all(b.get("literal_unconfirmed") for b in result)
        screen = extract_screen_facts(result, width=1000, height=600, page={"page_type": "unknown"}, catalog=catalog())
        assert not screen["facts"] and not screen["visible_text"] and not screen["unclassified_text"]
    assert meta["confirmed"] == (1 if expected else 0)
    assert meta["rejected"] == (0 if expected else 1)


def test_native_reread_unavailable_does_not_claim_validation():
    from PIL import Image
    boxes = [dict(text="地点", score=.99, left=30, right=95, top=8, bottom=28)]
    result, meta = reread_navigation_lines(SimpleNamespace(), Image.new("RGB", (1000, 600)), boxes)
    assert result == boxes and not meta["supported"] and not meta["confirmed"]


def test_stale_capture_retries_new_sequence_without_rewriting_time():
    import time
    old = frame(picture(), stale=True)
    old.frame_seq = 5
    recent = frame(picture())
    recent.frame_seq = 6
    calls = []
    def fetch(since):
        calls.append(since)
        return old if len(calls) == 1 else recent
    owner = SimpleNamespace(_assistant_client=SimpleNamespace(fetch_frame=fetch))
    result = asyncio.run(fetch_recent_frame(owner, wait_seconds=.4))
    assert result is recent and calls == [0, 5]
    assert time.time() - old.timestamp >= 10


def test_changed_text_stays_quarantined_without_destroying_independent_page(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    owner._vision = SimpleNamespace(analyze=lambda data: text_result())
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        await owner._apply_local_analysis(selected, image_fingerprint="memory", source="assistant_local_ocr")
        owner._assistant_client.fetch_frame = lambda seq: frame(picture("text"))
        assert not await validate_before_delivery(owner)
        # A second full check cannot become successful by deleting the failed
        # text first. A scoped page question can still use its own proof.
        assert not await validate_before_delivery(owner)
        from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
        assert await validate_before_delivery(owner, plan=build_query_plan("现在是什么页面？"))
        value = await owner._current_visual_evidence()
        assert value["fresh"] and value["page_type"] == "battle_pass"
        assert not value["screen_facts"]["visible_text"]
        assert owner._committed_observation["screen_facts"]["visible_text"]
    asyncio.run(run())


def test_delivery_recheck_uses_same_literal_not_changed_value(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    owner._vision = SimpleNamespace(analyze=lambda data: text_result(),
        verify_text_regions=lambda payload, proofs, **kwargs: {proof_key(p) for p in proofs})
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        await owner._apply_local_analysis(selected, image_fingerprint="memory", source="assistant_local_ocr")
        owner._assistant_client.fetch_frame = lambda seq: frame(picture("text"))
        assert await validate_before_delivery(owner)
        assert (await owner._current_visual_evidence())["screen_facts"]["visible_text"][0]["text"] == "规则说明"
        # An unknown/changed reread may not preserve the old literal.
        owner._vision.verify_text_regions = lambda payload, proofs, **kwargs: set()
        owner._assistant_client.fetch_frame = lambda seq: frame(picture())
        assert not await validate_before_delivery(owner)
        assert not (await owner._current_visual_evidence())["screen_facts"]["visible_text"]
    asyncio.run(run())


def test_completed_refresh_with_missing_task_arms_real_entry_followup(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module("plugin.plugins.hsr_companion")
    owner, _, _ = plugin_fixture(tmp_path)
    current = {**visual([]), "scene": {"verified": True, "label": "探索与任务追踪"},
               "characters": [], "controlled_character": None, "instruction": "only confirmed", "observed_at": ""}
    async def refresh(owner, **kwargs):
        owner._last_refresh_diagnostics = {"outcome": "ready", "stages": []}
        return current
    calls = []
    def arm(owner, **kwargs):
        calls.append(kwargs)
        return {"pending": True, "request_id": "test-only"}
    monkeypatch.setattr(module, "refresh_for_query", refresh)
    monkeypatch.setattr(module, "arm_followup", arm)
    result = asyncio.run(owner.hsr_companion_prepare(query="有什么任务？"))
    assert len(calls) == 1 and calls[0]["plan"].fields == {"quest"}
    assert result["followup"]["pending"]
    assert result["query_answer"]["state"] == "unconfirmed"
    assert result["answer_policy"]["screen_information_is_not_web_search"]


def test_same_page_preview_does_not_authorize_old_roster_after_serial_change(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    value = text_result()
    async def run():
        await owner._apply_local_analysis(value, image_fingerprint="memory", source="assistant_local_ocr")
        selected = ObservationSnapshot.select(owner, await owner._current_visual_evidence(), fields={"visible_text"})
        owner._scene_preview = {**owner._committed_observation, "screen_facts": {"facts": [], "visible_text": []}}
        owner._observation_serial += 1
        assert not selected.matches(owner)
    asyncio.run(run())


def test_backend_gate_prevents_parallel_observation_and_verification():
    import threading

    from plugin.plugins.hsr_companion.adapters.local_vision import serial_ocr
    from plugin.plugins.hsr_companion.adapters.text_reader import ObservationSuperseded
    entered = threading.Event()
    release = threading.Event()
    class Reader:
        def __init__(self):
            self._inference_lock = threading.RLock()
        @serial_ocr
        def read(self):
            entered.set()
            release.wait(1)
    reader = Reader()
    worker = threading.Thread(target=reader.read)
    worker.start()
    try:
        assert entered.wait(.5)
        with pytest.raises(ObservationSuperseded):
            reader.read()
    finally:
        release.set()
        worker.join(1)
    assert not worker.is_alive()


def test_late_delivery_check_does_not_withdraw_new_observation(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    owner._vision = SimpleNamespace(analyze=lambda data: text_result())
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        await owner._apply_local_analysis(selected, image_fingerprint="memory", source="assistant_local_ocr")
        owner._assistant_client.fetch_frame = lambda seq: frame(picture("text"))
        def replace_current(payload, proofs, **kwargs):
            owner._observation_epoch = getattr(owner, "_observation_epoch", 0) + 1
            updated = {**owner._committed_observation, "screen_facts": {"facts": [], "visible_text": [{"text": "NEW PAGE"}]}}
            ObservationStore(owner).commit(updated)
            return set()
        owner._vision.verify_text_regions = replace_current
        assert not await validate_before_delivery(owner)
        assert owner._committed_observation["screen_facts"]["visible_text"] == [{"text": "NEW PAGE"}]
    asyncio.run(run())
